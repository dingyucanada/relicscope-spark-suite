from __future__ import annotations

import asyncio
import base64
import fcntl
import hashlib
import io
import os
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

from PIL import Image, ImageOps

from app.config import Settings
from app.services.image_analysis import analyze_image, decode_image
from app.services.vlm import SCOUT_OBSERVATION_INSTRUCTION
from app.store import canonical_json, utc_now

from .protocol import capture_protocol
from .schemas import ScoutJobMetadata
from .store import ScoutCapacityError, ScoutConflict, ScoutStore


ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}


class ScoutMediaIntegrityError(RuntimeError):
    pass


class ScoutStorageReserveError(RuntimeError):
    pass


class VisionClient(Protocol):
    model: str

    async def health(self, name: str) -> dict[str, Any]: ...

    async def vision_observe(
        self, image_data_url: str, metadata: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def vision_observe_many(
        self, images: list[dict[str, Any]], metadata: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class IncomingCapture:
    filename: str
    mime_type: str
    raw_bytes: bytes


def _result_hash(value: dict[str, Any]) -> str:
    payload = dict(value)
    payload.pop("result_sha256", None)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _terminal_summary(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "client_job_id": job["client_job_id"],
        "device_id": job["device_id"],
        "status": job["status"],
        "stage": job["stage"],
        "attempt": job["attempt"],
        "capture_count": job["capture_count"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "completed_at": job["completed_at"],
        "result_available": job["result_available"],
        "error_code": job["error_code"],
        "error_detail": job["error_detail"],
        "next_attempt_at": job["next_attempt_at"],
    }


class ScoutService:
    """Durable Scout-to-Spark workflow with a deterministic control path."""

    def __init__(
        self,
        settings: Settings,
        store: ScoutStore,
        model_provider: Callable[[], VisionClient],
    ) -> None:
        self.settings = settings
        self.store = store
        self._model_provider = model_provider
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._stopping = False
        self._worker_error: str | None = None
        self._model_health_lock = asyncio.Lock()
        self._model_health_cached: dict[str, Any] | None = None
        self._model_health_cached_at = 0.0

    async def start(self) -> None:
        if not self.settings.scout_enabled or self._worker is not None:
            return
        self.store.recover_incomplete_jobs(
            max_attempts=self.settings.scout_model_max_attempts,
            retry_base_seconds=self.settings.scout_model_retry_base_seconds,
        )
        self._stopping = False
        self._worker_error = None
        self._worker = asyncio.create_task(
            self._worker_loop(), name="relicscope-scout-worker"
        )

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._worker is not None:
            await self._worker
            self._worker = None

    def wake(self) -> None:
        self._wake.set()

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema_version": "relicscope-scout-capabilities-v2",
            "service_version": self.settings.service_version,
            "runtime_mode": self.settings.runtime_mode,
            "node_id": self.settings.node_id,
            "compute_node_id": self.settings.compute_node_id,
            "transport": {
                "api": "HTTPS_REQUIRED_OUTSIDE_LOOPBACK",
                "wifi": True,
                "usb_network": True,
                "direct_model_access": False,
            },
            "jobs": {
                "analysis_modes": ["standard"],
                "max_images": self.settings.scout_max_images_per_job,
                "supported_image_mime_types": sorted(ALLOWED_IMAGE_MIME_TYPES),
                "durable_queue": True,
                "idempotency": "client_job_id + immutable payload SHA-256",
            },
            "pipeline": [
                "INGEST_VALIDATION",
                "QUALITY_CHECK",
                "MULTIMODAL_OBSERVATION",
                "RESULT_ASSEMBLY",
            ],
            "optional_extensions": {
                "reference_library": "DISABLED_BY_DEFAULT",
                "rag": "DISABLED_BY_DEFAULT",
                "agent": "NOT_IN_CRITICAL_PATH",
                "scientific_instruments": "FUTURE_ADAPTERS",
            },
            "capture_protocol": capture_protocol(),
            "boundary": {
                "authenticity_state": "NOT_ASSESSED",
                "message": (
                    "当前 V2 只完成现场采集、质量复核、本地可见特征观察和结构化记录。"
                ),
            },
        }

    @staticmethod
    def summarize_job(job: dict[str, Any]) -> dict[str, Any]:
        return _terminal_summary(job)

    def retry_model_unavailable_job(
        self, job_id: str, device_id: str
    ) -> dict[str, Any]:
        # Manual retry is an operator escape hatch after bounded automatic retries.
        # Reuse the server-configured retry interval, with a small production floor,
        # to prevent one authenticated device from rapidly requeueing terminal jobs.
        cooldown_seconds = max(5.0, self.settings.scout_model_retry_base_seconds)
        return self.store.retry_model_unavailable_job(
            job_id,
            device_id,
            max_outstanding_jobs=(
                self.settings.scout_max_outstanding_jobs_per_device
            ),
            cooldown_seconds=cooldown_seconds,
        )

    def _result_base(
        self, job: dict[str, Any], request: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "schema_version": "relicscope-scout-result-v2",
            "job_id": job["id"],
            "subject_label": request["subject_label"],
            "subject_label_provenance": "OPERATOR_SUPPLIED_UNVERIFIED",
            "operator_metadata": {
                "subject_label": request["subject_label"],
                "operator_note": request["operator_note"],
                "source": "OPERATOR_SUPPLIED",
                "verification_status": "UNVERIFIED",
                "used_as_model_conclusion": False,
            },
            "analysis_mode": request["analysis_mode"],
            "completed_at": utc_now(),
            "cross_view_observations": [],
            "model_limitations": [],
            "model_capture_issues": [],
            "model_ood_risk": None,
            "model_runs": [],
            "compute_provenance": {
                "node_id": self.settings.node_id,
                "compute_node_id": self.settings.compute_node_id,
                "runtime_mode": self.settings.runtime_mode,
                "deployment_git_commit": self.settings.deployment_git_commit,
                "input_payload_sha256": job["payload_sha256"],
            },
            "optional_extensions": {
                "reference_library_used": False,
                "rag_used": False,
                "agent_used": False,
            },
            "authenticity_state": "NOT_ASSESSED",
        }

    def _content_path(self, sha256: str, mime_type: str) -> Path:
        extension = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }[mime_type]
        return self.settings.scout_media_dir / "objects" / sha256[:2] / f"{sha256}{extension}"

    @staticmethod
    def _write_content_addressed(path: Path, payload: bytes) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != path.stem:
                raise ValueError("existing Scout media failed content-address verification")
            return False
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                # A hard link publishes a fully written same-filesystem inode without
                # overwriting a concurrent publisher's object.
                os.link(temporary, path)
            except FileExistsError:
                if hashlib.sha256(path.read_bytes()).hexdigest() != path.stem:
                    raise ValueError(
                        "concurrent Scout media failed content-address verification"
                    )
                return False
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return True
        finally:
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def _ingest_publication_lock(self):
        """Serialize media publication, DB reference creation, and orphan cleanup."""

        self.settings.scout_media_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.settings.scout_media_dir / ".ingest-publication.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def create_job(
        self,
        device_id: str,
        metadata: ScoutJobMetadata,
        uploads: list[IncomingCapture],
    ) -> tuple[dict[str, Any], bool]:
        if not self.settings.scout_enabled:
            raise ValueError("Scout V2 is disabled")
        if len(uploads) != len(metadata.captures):
            raise ValueError("capture metadata and uploaded files must have equal counts")
        if len(uploads) > self.settings.scout_max_images_per_job:
            raise ValueError("Scout job exceeds configured image count")
        upload_by_filename: dict[str, IncomingCapture] = {}
        for upload in uploads:
            if upload.filename in upload_by_filename:
                raise ValueError("uploaded filenames must be unique")
            upload_by_filename[upload.filename] = upload
        if set(upload_by_filename) != {item.filename for item in metadata.captures}:
            raise ValueError("uploaded filenames do not match immutable metadata")

        prepared: list[dict[str, Any]] = []
        immutable_files: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for ordinal, capture_metadata in enumerate(metadata.captures):
            upload = upload_by_filename[capture_metadata.filename]
            if upload.mime_type not in ALLOWED_IMAGE_MIME_TYPES:
                raise ValueError("unsupported Scout image MIME type")
            if not upload.raw_bytes or len(upload.raw_bytes) > self.settings.max_upload_bytes:
                raise ValueError("Scout image is empty or exceeds configured size")
            decoded = decode_image(upload.raw_bytes)
            if decoded.detected_mime != upload.mime_type:
                raise ValueError("declared and detected Scout image MIME types differ")
            if (
                capture_metadata.client_sha256 is not None
                and capture_metadata.client_sha256 != decoded.sha256
            ):
                raise ValueError("Scout client and server image SHA-256 values differ")
            if decoded.sha256 in seen_hashes:
                raise ValueError("duplicate image bytes do not count as separate captures")
            seen_hashes.add(decoded.sha256)
            server_analysis = analyze_image(decoded.image, decoded.sha256)
            server_analysis["orientation_normalized"] = True
            media_path = self._content_path(decoded.sha256, decoded.detected_mime)
            prepared.append(
                {
                    "id": f"cap-{uuid4().hex}",
                    "client_capture_id": capture_metadata.client_capture_id,
                    "ordinal": ordinal,
                    "filename": capture_metadata.filename,
                    "view_code": capture_metadata.view_code,
                    "mime_type": decoded.detected_mime,
                    "sha256": decoded.sha256,
                    "byte_count": len(upload.raw_bytes),
                    "width": decoded.image.width,
                    "height": decoded.image.height,
                    "path": str(media_path),
                    "captured_at": capture_metadata.captured_at.isoformat(),
                    "device_quality": capture_metadata.device_quality.model_dump(
                        mode="json"
                    )
                    if capture_metadata.device_quality
                    else None,
                    "server_quality": server_analysis,
                    "raw_bytes": upload.raw_bytes,
                }
            )
            immutable_files.append(
                {
                    "client_capture_id": capture_metadata.client_capture_id,
                    "filename": capture_metadata.filename,
                    "view_code": capture_metadata.view_code,
                    "sha256": decoded.sha256,
                    "byte_count": len(upload.raw_bytes),
                }
            )

        immutable_request = metadata.model_dump(mode="json")
        payload_sha256 = hashlib.sha256(
            canonical_json(
                {"metadata": immutable_request, "files": immutable_files}
            ).encode("utf-8")
        ).hexdigest()
        existing = self.store.find_idempotent_job(device_id, metadata.client_job_id)
        if existing is not None:
            if existing["payload_sha256"] != payload_sha256:
                raise ScoutConflict(
                    "client_job_id already exists with different immutable input"
                )
            return existing, False

        with self._ingest_publication_lock():
            # The reserve check and media publication must share one lock domain.
            # Otherwise concurrent ingests can all observe the same free-space value,
            # pass independently, and together consume the protected reserve.
            # Content-addressed objects already present on disk do not require a
            # second allocation.  Counting only missing objects prevents a valid
            # metadata-only job from being rejected at the reserve boundary.
            incoming_bytes = sum(
                capture["byte_count"]
                for capture in prepared
                if not Path(capture["path"]).exists()
            )
            free_bytes = shutil.disk_usage(self.settings.scout_media_dir).free
            if free_bytes - incoming_bytes < self.settings.scout_min_free_bytes:
                raise ScoutStorageReserveError(
                    "Spark data volume free-space reserve reached"
                )
            new_media_paths: list[Path] = []
            for capture in prepared:
                path = Path(capture["path"])
                if self._write_content_addressed(path, capture.pop("raw_bytes")):
                    new_media_paths.append(path)
            try:
                job, created = self.store.create_job(
                    device_id=device_id,
                    client_job_id=metadata.client_job_id,
                    payload_sha256=payload_sha256,
                    request=immutable_request,
                    captures=prepared,
                    max_outstanding_jobs=(
                        self.settings.scout_max_outstanding_jobs_per_device
                    ),
                )
            except (ScoutCapacityError, ScoutConflict):
                # The publication lock prevents another ingest from acquiring a DB
                # reference between creation and cleanup.
                for path in new_media_paths:
                    path.unlink(missing_ok=True)
                raise
        if created:
            self.wake()
        return job, created

    async def health(self) -> dict[str, Any]:
        model_health = await self._model_health("scout-vision")
        worker_running = self._worker is not None and not self._worker.done()
        gateway_ready = self.settings.scout_enabled and worker_running
        model_ready = model_health.get("status") == "online"
        storage = shutil.disk_usage(self.settings.scout_media_dir)
        storage_ready = storage.free >= self.settings.scout_min_free_bytes
        return {
            "status": "ready" if gateway_ready else "disabled",
            "operational_status": (
                "READY"
                if gateway_ready and model_ready and storage_ready
                else "DEGRADED"
            ),
            "service": "RelicScope Scout Gateway V2",
            "service_version": self.settings.service_version,
            "node_id": self.settings.node_id,
            "runtime_mode": self.settings.runtime_mode,
            "queue_worker": "running" if worker_running else "stopped",
            "queue_worker_error": self._worker_error,
            "model_ready": model_ready,
            "model": model_health,
            "storage": {
                "ready": storage_ready,
                "free_bytes": storage.free,
                "total_bytes": storage.total,
                "minimum_free_bytes": self.settings.scout_min_free_bytes,
            },
            "checked_at": utc_now(),
        }

    async def _model_health(self, name: str) -> dict[str, Any]:
        now = time.monotonic()
        if (
            self._model_health_cached is not None
            and now - self._model_health_cached_at < 2.0
        ):
            return dict(self._model_health_cached)
        async with self._model_health_lock:
            now = time.monotonic()
            if (
                self._model_health_cached is not None
                and now - self._model_health_cached_at < 2.0
            ):
                return dict(self._model_health_cached)
            value = await self._model_provider().health(name)
            self._model_health_cached = dict(value)
            self._model_health_cached_at = time.monotonic()
            return value

    async def _worker_loop(self) -> None:
        while not self._stopping:
            try:
                model_health = await self._model_health("scout-worker-vision")
            except Exception:
                model_health = {"status": "degraded"}
            if model_health.get("status") != "online":
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self.settings.scout_worker_poll_seconds,
                    )
                except TimeoutError:
                    pass
                continue
            try:
                job = self.store.claim_next_job()
                self._worker_error = None
            except Exception as exc:
                self._worker_error = type(exc).__name__
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self.settings.scout_worker_poll_seconds,
                    )
                except TimeoutError:
                    pass
                continue
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(), timeout=self.settings.scout_worker_poll_seconds
                    )
                except TimeoutError:
                    pass
                continue
            try:
                await self._process_job(job)
            except Exception as exc:
                try:
                    error_code = (
                        "MEDIA_INTEGRITY_FAILURE"
                        if isinstance(exc, ScoutMediaIntegrityError)
                        else "PIPELINE_FAILURE"
                    )
                    self.store.fail_job(
                        job["id"], error_code, type(exc).__name__
                    )
                except Exception as store_exc:
                    self._worker_error = type(store_exc).__name__
                    # Continuing would strand a RUNNING job while health still appeared
                    # normal. Stop the worker so readiness fails and an operator restarts
                    # after repairing storage; startup recovery will requeue the job.
                    return

    @staticmethod
    def _model_image(path: Path, expected_sha256: str) -> tuple[str, str]:
        try:
            source_bytes = path.read_bytes()
        except OSError as exc:
            raise ScoutMediaIntegrityError("Scout source media is unavailable") from exc
        if hashlib.sha256(source_bytes).hexdigest() != expected_sha256:
            raise ScoutMediaIntegrityError("Scout source media hash changed after ingest")
        try:
            with Image.open(io.BytesIO(source_bytes)) as source:
                clean = ImageOps.exif_transpose(source).convert("RGB")
                clean.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                buffer = io.BytesIO()
                clean.save(buffer, format="JPEG", quality=90, optimize=True)
        except Exception as exc:
            raise ScoutMediaIntegrityError(
                "Scout source media cannot be decoded after ingest"
            ) from exc
        payload = buffer.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}", digest

    async def _process_job(self, job: dict[str, Any]) -> None:
        captures = self.store.list_captures(job["id"])
        request = job["request"]
        quality_records = []
        accepted = []
        recapture = []
        for capture in captures:
            quality = capture["server_quality"]["quality_gate"]
            record = {
                "capture_id": capture["id"],
                "client_capture_id": capture["client_capture_id"],
                "view_code": capture["view_code"],
                "sha256": capture["sha256"],
                "width": capture["width"],
                "height": capture["height"],
                "server_passed": quality["passed"],
                "server_failed_checks": quality["failed_checks"],
                "device_quality": capture["device_quality"],
            }
            quality_records.append(record)
            if quality["passed"]:
                accepted.append(capture)
            else:
                recapture.append(
                    {
                        "capture_id": capture["id"],
                        "view_code": capture["view_code"],
                        "reasons": quality["failed_checks"],
                    }
                )

        if not accepted:
            result = {
                **self._result_base(job, request),
                "capture_assessment": {
                    "accepted": 0,
                    "total": len(captures),
                    "records": quality_records,
                    "recapture_requests": recapture,
                },
                "visible_observations": [],
                "next_actions": ["按 Scout 提示重新拍摄清晰、曝光稳定的视角。"],
                "boundary": "图像未通过服务器复核，因此没有调用生成模型。",
            }
            result["result_sha256"] = _result_hash(result)
            self.store.complete_job(job["id"], "NEEDS_RECAPTURE", result)
            return

        minimum_standard_views = capture_protocol()["required_for_standard"]
        distinct_views = {item["view_code"] for item in accepted}
        self.store.update_stage(
            job["id"],
            "MULTIMODAL_OBSERVATION",
            {"accepted_captures": len(accepted), "distinct_views": len(distinct_views)},
        )

        durable_model_runs = self.store.model_attempts(job["id"])
        visible_observations: list[dict[str, Any]] = []
        cross_view_observations: list[str] = []
        model_limitations: list[str] = []
        model_capture_issues: list[dict[str, str]] = []
        model_ood_risk: str | None = None
        if request["analysis_mode"] == "standard":
            model = self._model_provider()
            prepared_images: list[dict[str, Any]] = []
            source_inputs: list[dict[str, Any]] = []
            for capture in accepted:
                data_url, model_input_sha256 = self._model_image(
                    Path(capture["path"]), capture["sha256"]
                )
                prepared_images.append(
                    {
                        "capture_id": capture["id"],
                        "view_code": capture["view_code"],
                        "image_data_url": data_url,
                    }
                )
                source_inputs.append(
                    {
                        "capture_id": capture["id"],
                        "view_code": capture["view_code"],
                        "source_sha256": capture["sha256"],
                        "sanitized_model_input_sha256": model_input_sha256,
                    }
                )
            model_context = {
                "job_id": job["id"],
                "operator_metadata": {
                    "subject_label": request["subject_label"],
                    "source": "OPERATOR_SUPPLIED",
                    "verification_status": "UNVERIFIED",
                },
                "instruction": SCOUT_OBSERVATION_INSTRUCTION,
            }
            current_attempt = int(job["attempt"])
            attempt_base = int(job.get("attempt_base", 0))
            latest_current = next(
                (
                    item
                    for item in reversed(durable_model_runs)
                    if item.get("attempt") == current_attempt
                ),
                None,
            )
            output: dict[str, Any]
            if (
                latest_current is not None
                and latest_current.get("available") is True
                and isinstance(latest_current.get("validated_output"), dict)
            ):
                # A prior process durably stored the validated answer and then stopped
                # before result assembly. Reuse it; issuing a second model call would
                # destroy exactly-once attempt accounting.
                output = {
                    **latest_current,
                    "output": latest_current["validated_output"],
                }
            else:
                cycle_attempts = current_attempt - attempt_base
                should_call = cycle_attempts < self.settings.scout_model_max_attempts
                if should_call:
                    attempt = self.store.begin_model_attempt(
                        job["id"],
                        {
                            "multi_view": True,
                            "batch_size": len(prepared_images),
                            "source_inputs": source_inputs,
                        },
                        max_attempts=self.settings.scout_model_max_attempts,
                    )
                else:
                    attempt = None
                if attempt is None:
                    if latest_current is None:
                        raise ScoutConflict(
                            "model attempt budget is exhausted without a durable outcome"
                        )
                    if latest_current.get("available") is True:
                        raise ScoutConflict(
                            "durable successful model outcome is missing validated output"
                        )
                    output = dict(latest_current)
                else:
                    job["attempt"] = attempt
                    output = await model.vision_observe_many(
                        prepared_images,
                        model_context,
                    )
                    proof = {
                        key: output.get(key)
                        for key in (
                            "available",
                            "mode",
                            "role",
                            "model",
                            "configured_model",
                            "model_identity_verified",
                            "model_identity_verification_scope",
                            "runtime_provider",
                            "runtime_attestation_scope",
                            "runtime_image",
                            "model_source",
                            "model_artifact_kind",
                            "model_artifact_id",
                            "model_revision",
                            "request_id",
                            "prompt_hash",
                            "system_prompt_hash",
                            "request_payload_hash",
                            "output_hash",
                            "latency_ms",
                            "usage",
                            "finish_reason",
                            "error",
                        )
                        if key in output
                    }
                    proof.update(
                        {
                            "attempt": attempt,
                            "multi_view": True,
                            "batch_size": len(prepared_images),
                            "source_inputs": source_inputs,
                            "validated_output": (
                                output.get("output")
                                if output.get("available") is True
                                else None
                            ),
                        }
                    )
                    cycle_attempt = attempt - attempt_base
                    retry_delay = None
                    if (
                        output.get("available") is not True
                        and cycle_attempt < self.settings.scout_model_max_attempts
                    ):
                        retry_delay = min(
                            self.settings.scout_model_retry_base_seconds
                            * (2 ** max(cycle_attempt - 1, 0)),
                            300.0,
                        )
                    self.store.record_model_attempt(
                        job["id"], proof, retry_delay_seconds=retry_delay
                    )
                    durable_model_runs = self.store.model_attempts(job["id"])
                    if retry_delay is not None:
                        return
            if output.get("available") is not True and self.store.get_job(
                job["id"]
            )["status"] == "RETRY_WAIT":
                return
            if output.get("available") is True:
                model_output = output.get("output", {})
                for observation in model_output.get("observations", []):
                    visible_observations.append(
                        {
                            **observation,
                            "model_output_sha256": output.get("output_hash"),
                        }
                    )
                cross_view_observations.extend(
                    model_output.get("cross_view_observations", [])
                )
                model_limitations.extend(model_output.get("limitations", []))
                model_capture_issues.extend(model_output.get("capture_issues", []))
                model_ood_risk = model_output.get("ood_risk")

        self.store.update_stage(job["id"], "RESULT_ASSEMBLY")
        model_runs = [
            {key: value for key, value in item.items() if key != "validated_output"}
            for item in durable_model_runs
        ]
        successful_runs = [item for item in model_runs if item.get("available") is True]
        if not successful_runs or model_runs[-1].get("available") is not True:
            terminal_status = "MODEL_UNAVAILABLE"
        elif (
            recapture
            or len(distinct_views) < minimum_standard_views
            or not visible_observations
            or bool(model_capture_issues)
            or model_ood_risk == "HIGH"
        ):
            terminal_status = "PARTIAL"
        else:
            terminal_status = "SUCCEEDED"

        next_actions = []
        if recapture:
            next_actions.append("补拍未通过服务器质量复核的视角。")
        if len(distinct_views) < minimum_standard_views:
            next_actions.append(
                f"标准观察建议至少 {minimum_standard_views} 个不同视角；当前为 {len(distinct_views)} 个。"
            )
        if terminal_status == "MODEL_UNAVAILABLE":
            next_actions.append("检查 Spark 本地 VLM 服务后重试；系统没有生成替代性结论。")
        if model_capture_issues:
            next_actions.append("按模型标记的视角问题复核原图；必要时补拍对应视角。")
        if model_ood_risk == "HIGH":
            next_actions.append("输入超出当前模型稳定范围；由专家复核并补充标准视角。")
        if not next_actions:
            next_actions.append("由专家复核可见观察；需要时补拍底足、款识或损伤细节。")

        result = {
            **self._result_base(job, request),
            "capture_assessment": {
                "accepted": len(accepted),
                "total": len(captures),
                "distinct_views": sorted(distinct_views),
                "records": quality_records,
                "recapture_requests": recapture,
            },
            "visible_observations": visible_observations,
            "cross_view_observations": list(dict.fromkeys(cross_view_observations)),
            "model_limitations": list(dict.fromkeys(model_limitations)),
            "model_capture_issues": model_capture_issues,
            "model_ood_risk": model_ood_risk,
            "model_runs": model_runs,
            "next_actions": next_actions,
            "boundary": (
                "本结果是现场图像质量与可见特征观察记录，不构成真伪、年代、窑口、"
                "作者、价值或法律结论。器物标签和备注是未经验证的操作员输入，不属于"
                "模型观察。"
            ),
        }
        result["result_sha256"] = _result_hash(result)
        self.store.complete_job(job["id"], terminal_status, result)
