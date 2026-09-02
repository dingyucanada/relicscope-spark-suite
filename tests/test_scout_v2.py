from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier, Lock, local
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.scout.auth import hash_device_token, verify_device_token
from app.scout.schemas import ScoutJobMetadata
from app.scout.service import IncomingCapture, ScoutStorageReserveError
from app.scout.store import ScoutCapacityError, ScoutConflict
from app.scout_main import ScoutPreAuthMiddleware, create_scout_app
from app.services.image_analysis import decode_image
from app.services.vlm import validate_scout_multi_view_output


class ScoutVisionStub:
    def __init__(self, available: bool = True) -> None:
        self.model = "test-scout-vlm"
        self.available = available
        self.calls = 0
        self.last_metadata: dict | None = None

    async def health(self, name: str):
        return {
            "name": name,
            "status": "online" if self.available else "degraded",
            "detail": "test Scout model",
            "model": self.model,
            "model_identity_verified": self.available,
        }
    async def vision_observe(self, image_data_url, metadata):
        raise AssertionError("Scout V2 must use one multi-view model call")

    async def vision_observe_many(self, images, metadata):
        self.calls += 1
        self.last_metadata = metadata
        if not self.available:
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": "scout_multi_view",
                "model": self.model,
                "error": "NotConfigured",
                "prompt_hash": "1" * 64,
                "system_prompt_hash": "1" * 64,
                "request_payload_hash": "3" * 64,
            }
        return {
            "available": True,
            "mode": "local_test_stub",
            "model": self.model,
            "configured_model": self.model,
            "model_identity_verified": True,
            "model_source": "test/source",
            "model_revision": "a" * 40,
            "runtime_image": "test/runtime@sha256:" + "b" * 64,
            "request_id": f"request-{self.calls}",
            "prompt_hash": "2" * 64,
            "system_prompt_hash": "2" * 64,
            "request_payload_hash": "3" * 64,
            "output_hash": f"{self.calls:064x}",
            "latency_ms": 2,
            "output": {
                "observations": [
                    {
                        "capture_id": item["capture_id"],
                        "view_code": item["view_code"],
                        "text": f"{item['view_code']} 视角可见蓝白相间纹饰。",
                    }
                    for item in images
                ],
                "cross_view_observations": ["多视角均显示连续的蓝白纹饰。"],
                "limitations": ["仅依据现场 RGB 图像。"],
                "capture_issues": [],
                "ood_risk": "LOW",
            },
        }


class ExplodingScoutVisionStub(ScoutVisionStub):
    async def vision_observe_many(self, images, metadata):
        raise RuntimeError("simulated model pipeline failure")


class TransientCompletionScoutVisionStub(ScoutVisionStub):
    async def vision_observe_many(self, images, metadata):
        if self.calls == 0:
            self.calls += 1
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": "scout_multi_view",
                "model": self.model,
                "error": "ReadTimeout",
                "system_prompt_hash": "1" * 64,
                "request_payload_hash": "2" * 64,
            }
        return await super().vision_observe_many(images, metadata)


class RecoverableCompletionScoutVisionStub(ScoutVisionStub):
    def __init__(self) -> None:
        super().__init__(available=True)
        self.fail_completions = True

    async def vision_observe_many(self, images, metadata):
        if self.fail_completions:
            self.calls += 1
            return {
                "available": False,
                "mode": "deterministic_fallback",
                "role": "scout_multi_view",
                "model": self.model,
                "error": "InvalidModelOutput",
                "system_prompt_hash": "1" * 64,
                "request_payload_hash": "2" * 64,
            }
        return await super().vision_observe_many(images, metadata)


class HighRiskScoutVisionStub(ScoutVisionStub):
    async def vision_observe_many(self, images, metadata):
        value = await super().vision_observe_many(images, metadata)
        value["output"]["ood_risk"] = "HIGH"
        return value


def _image_bytes(seed: int, *, low_quality: bool = False) -> bytes:
    if low_quality:
        array = np.zeros((320, 320, 3), dtype=np.uint8)
    else:
        y, x = np.indices((512, 512))
        checker = ((x // (11 + seed) + y // (13 + seed)) % 2) * 190 + 25
        array = np.stack(
            [checker, np.roll(checker, seed + 2, axis=0), 255 - checker], axis=2
        ).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(array).save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def _oriented_jpeg_bytes() -> bytes:
    image = Image.new("RGB", (640, 960), color=(230, 230, 230))
    exif = Image.Exif()
    exif[274] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92, exif=exif)
    return buffer.getvalue()


def _metadata(filenames: list[str], *, mode: str = "standard") -> dict:
    views = ["FRONT", "BACK", "BASE", "LEFT_PROFILE", "TOP"]
    return {
        "schema_version": "relicscope-scout-job-v2",
        "client_job_id": f"client-{uuid4().hex}",
        "capture_protocol": "porcelain-v1",
        "analysis_mode": mode,
        "subject_label": "测试现场器物",
        "operator_note": "端到端测试",
        "app_version": "2.0-test",
        "device_model": "Android test device",
        "captures": [
            {
                "client_capture_id": f"capture-{uuid4().hex}",
                "filename": filename,
                "view_code": views[index],
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "device_quality": {
                    "algorithm": "scout-android-quality-v1",
                    "passed": True,
                    "blur_score": 60,
                    "brightness_mean": 127,
                    "object_coverage": 0.7,
                    "failed_checks": [],
                },
            }
            for index, filename in enumerate(filenames)
        ],
    }


def _headers(enrollment: dict) -> dict[str, str]:
    return {
        "X-Scout-Device-ID": enrollment["device_id"],
        "Authorization": f"Bearer {enrollment['device_token']}",
    }


def _wait_terminal(client: TestClient, job_id: str, headers: dict[str, str]) -> dict:
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        response = client.get(f"/api/v2/scout/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        job = response.json()
        if job["status"] in {
            "SUCCEEDED",
            "PARTIAL",
            "NEEDS_RECAPTURE",
            "MODEL_UNAVAILABLE",
            "FAILED",
            "CANCELLED",
        }:
            return job
        time.sleep(0.03)
    raise AssertionError("Scout job did not complete")


def _application(app_settings, tmp_path, model):
    settings = replace(
        app_settings,
        scout_enabled=True,
        scout_require_auth=True,
        scout_media_dir=tmp_path / "runtime" / "scout-media",
        scout_worker_poll_seconds=0.05,
        scout_model_retry_base_seconds=0.05,
        scout_min_free_bytes=64 * 1024**2,
        service_version="2.0-test",
    )
    return create_scout_app(settings, model_client=model)


def test_device_token_is_salted_and_verifiable():
    salt, token_hash = hash_device_token("x" * 48)
    assert verify_device_token("x" * 48, salt, token_hash)
    assert not verify_device_token("y" * 48, salt, token_hash)


def test_unauthenticated_ingest_is_rejected_before_body_read(app_settings, tmp_path):
    application = _application(app_settings, tmp_path, ScoutVisionStub())
    inner_called = False

    async def inner(scope, receive, send):
        nonlocal inner_called
        inner_called = True

    async def receive():
        raise AssertionError("unauthenticated request body must not be read")

    sent = []

    async def send(message):
        sent.append(message)

    middleware = ScoutPreAuthMiddleware(
        inner,
        application.state.scout_service.settings,
        application.state.scout_store,
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "https",
        "method": "POST",
        "path": "/api/v2/scout/jobs",
        "raw_path": b"/api/v2/scout/jobs",
        "query_string": b"",
        "headers": [(b"content-length", b"100000000")],
        "client": ("192.0.2.5", 12345),
        "server": ("scout.spark.local", 8443),
        "state": {},
    }
    asyncio.run(middleware(scope, receive, send))
    assert inner_called is False
    assert sent[0]["status"] == 401


def test_scout_job_runs_durable_authenticated_pipeline(app_settings, tmp_path):
    model = ScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Test 01")
    headers = _headers(enrollment)
    filenames = ["front.jpg", "back.jpg", "base.jpg"]
    metadata = _metadata(filenames)
    files = [
        ("files", (filename, _image_bytes(index), "image/jpeg"))
        for index, filename in enumerate(filenames)
    ]

    with TestClient(application) as client:
        assert client.get("/api/v2/scout/capabilities").status_code == 401
        capability = client.get("/api/v2/scout/capabilities", headers=headers)
        assert capability.status_code == 200
        assert capability.json()["optional_extensions"]["agent"] == "NOT_IN_CRITICAL_PATH"

        accepted = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=files,
        )
        assert accepted.status_code == 202
        assert accepted.json()["created"] is True
        job_id = accepted.json()["job"]["id"]
        job = _wait_terminal(client, job_id, headers)
        assert job["status"] == "SUCCEEDED"

        result = client.get(
            f"/api/v2/scout/jobs/{job_id}/result", headers=headers
        ).json()["result"]
        assert result["authenticity_state"] == "NOT_ASSESSED"
        assert result["subject_label_provenance"] == (
            "OPERATOR_SUPPLIED_UNVERIFIED"
        )
        assert result["operator_metadata"] == {
            "subject_label": "测试现场器物",
            "operator_note": "端到端测试",
            "source": "OPERATOR_SUPPLIED",
            "verification_status": "UNVERIFIED",
            "used_as_model_conclusion": False,
        }
        assert len(result["visible_observations"]) == 3
        assert result["cross_view_observations"] == ["多视角均显示连续的蓝白纹饰。"]
        assert result["model_runs"][0]["multi_view"] is True
        assert result["model_runs"][0]["batch_size"] == 3
        assert result["model_runs"][0]["system_prompt_hash"] == "2" * 64
        assert result["model_runs"][0]["request_payload_hash"] == "3" * 64
        assert len(result["model_runs"][0]["source_inputs"]) == 3
        assert [
            item["view_code"] for item in result["model_runs"][0]["source_inputs"]
        ] == ["FRONT", "BACK", "BASE"]
        assert result["optional_extensions"] == {
            "reference_library_used": False,
            "rag_used": False,
            "agent_used": False,
        }
        assert len(result["result_sha256"]) == 64
        assert model.calls == 1
        assert model.last_metadata == {
            "job_id": job_id,
            "operator_metadata": {
                "subject_label": "测试现场器物",
                "source": "OPERATOR_SUPPLIED",
                "verification_status": "UNVERIFIED",
            },
            "instruction": (
                "将所有合格视角作为同一器物的一组观察输入；仅记录可见形态、"
                "纹饰、釉面、底足、款识、保存状态与跨视角一致性。不得输出真伪、"
                "断代、窑口、作者或价格结论。操作员标签未经验证，不可作为模型观察"
                "或结论依据。"
            ),
        }

        replay = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[
                ("files", (filename, _image_bytes(index), "image/jpeg"))
                for index, filename in enumerate(filenames)
            ],
        )
        assert replay.status_code == 202
        assert replay.json()["created"] is False
        assert replay.json()["job"]["id"] == job_id
        assert model.calls == 1


def test_scout_metadata_rejects_unsupported_quality_tokens_and_controls(
    app_settings, tmp_path
):
    application = _application(app_settings, tmp_path, ScoutVisionStub())
    enrollment = application.state.scout_store.enroll_device("Scout Metadata Validation")
    headers = _headers(enrollment)

    mutations = [
        lambda value: value["captures"][0]["device_quality"].update(
            algorithm="unregistered-quality-v9"
        ),
        lambda value: value["captures"][0]["device_quality"].update(
            failed_checks=["arbitrary_operator_claim"]
        ),
        lambda value: value.update(subject_label="标签\n伪装为模型结论"),
        lambda value: value.update(operator_note="备注\u202e隐藏方向"),
    ]

    with TestClient(application) as client:
        for index, mutate in enumerate(mutations):
            metadata = _metadata([f"front-{index}.jpg"])
            mutate(metadata)
            response = client.post(
                "/api/v2/scout/jobs",
                headers=headers,
                data={"metadata_json": json.dumps(metadata)},
                files=[
                    (
                        "files",
                        (f"front-{index}.jpg", _image_bytes(index + 80), "image/jpeg"),
                    )
                ],
            )
            assert response.status_code == 422


def test_multi_view_output_must_bind_every_observation_to_an_input_capture():
    allowed = {"cap-front": "FRONT"}
    valid = {
        "observations": [
            {
                "capture_id": "cap-front",
                "view_code": "FRONT",
                "text": "正面可见蓝色纹饰。",
            }
        ],
        "cross_view_observations": [],
        "limitations": ["当前只有一个视角。"],
        "capture_issues": [],
        "ood_risk": "MEDIUM",
    }
    assert validate_scout_multi_view_output(valid, allowed) is valid

    invalid = dict(valid)
    invalid["observations"] = [
        {
            "capture_id": "cap-invented",
            "view_code": "BASE",
            "text": "虚构输入。",
        }
    ]
    try:
        validate_scout_multi_view_output(invalid, allowed)
    except ValueError as exc:
        assert "allowed capture" in str(exc)
    else:
        raise AssertionError("unbound model observations must be rejected")

    missing = {
        **valid,
        "observations": [],
        "capture_issues": [],
    }
    try:
        validate_scout_multi_view_output(missing, allowed)
    except ValueError as exc:
        assert "every Scout input" in str(exc)
    else:
        raise AssertionError("empty model output must not pass the Scout contract")


def test_scout_guardrail_rejects_tentative_dating_kiln_and_imitation_claims():
    allowed = {"cap-front": "FRONT"}
    for text in (
        "器物可能为明代景德镇窑烧造。",
        "纹饰呈清代康熙时期风格。",
        "疑似现代仿制。",
    ):
        value = {
            "observations": [
                {"capture_id": "cap-front", "view_code": "FRONT", "text": text}
            ],
            "cross_view_observations": [],
            "limitations": [],
            "capture_issues": [],
            "ood_risk": "LOW",
        }
        try:
            validate_scout_multi_view_output(value, allowed)
        except ValueError as exc:
            assert "conclusion boundary" in str(exc)
        else:
            raise AssertionError(f"unsafe Scout conclusion was accepted: {text}")


def test_exif_orientation_is_applied_before_quality_and_model_pixels():
    decoded = decode_image(_oriented_jpeg_bytes())
    assert decoded.image.size == (960, 640)


def test_low_quality_job_requests_recapture_without_model(app_settings, tmp_path):
    model = ScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Test 02")
    headers = _headers(enrollment)
    metadata = _metadata(["dark.jpg"])

    with TestClient(application) as client:
        accepted = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[("files", ("dark.jpg", _image_bytes(0, low_quality=True), "image/jpeg"))],
        )
        job_id = accepted.json()["job"]["id"]
        job = _wait_terminal(client, job_id, headers)
        assert job["status"] == "NEEDS_RECAPTURE"
        result = client.get(
            f"/api/v2/scout/jobs/{job_id}/result", headers=headers
        ).json()["result"]
        assert result["visible_observations"] == []
        assert result["capture_assessment"]["recapture_requests"]
        assert result["analysis_mode"] == "standard"
        assert result["compute_provenance"]["input_payload_sha256"]
        assert result["optional_extensions"]["agent_used"] is False
        assert result["model_runs"] == []
        assert model.calls == 0


def test_client_declared_image_hash_must_match_server_bytes(app_settings, tmp_path):
    model = ScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Hash Test")
    headers = _headers(enrollment)
    metadata = _metadata(["front.jpg"])
    metadata["captures"][0]["client_sha256"] = "0" * 64

    with TestClient(application) as client:
        response = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[("files", ("front.jpg", _image_bytes(7), "image/jpeg"))],
        )
        assert response.status_code == 422
        assert "SHA-256" in response.json()["detail"]
        assert model.calls == 0


def test_gateway_health_distinguishes_model_degradation(app_settings, tmp_path):
    application = _application(app_settings, tmp_path, ScoutVisionStub(available=False))

    with TestClient(application) as client:
        response = client.get("/api/v2/scout/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert response.json()["operational_status"] == "DEGRADED"
        assert response.json()["model_ready"] is False


def test_worker_stops_and_readiness_fails_if_failure_cannot_be_persisted(
    app_settings, tmp_path
):
    application = _application(app_settings, tmp_path, ExplodingScoutVisionStub())
    enrollment = application.state.scout_store.enroll_device("Scout Worker Fault")
    headers = _headers(enrollment)
    metadata = _metadata(["front.jpg"])

    def fail_to_record(*args, **kwargs):
        raise RuntimeError("simulated storage failure")

    application.state.scout_store.fail_job = fail_to_record
    with TestClient(application) as client:
        response = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[("files", ("front.jpg", _image_bytes(8), "image/jpeg"))],
        )
        assert response.status_code == 202
        deadline = time.monotonic() + 4
        health = None
        while time.monotonic() < deadline:
            health = client.get("/api/v2/scout/health")
            if health.status_code == 503:
                break
            time.sleep(0.03)
        assert health is not None and health.status_code == 503
        assert health.json()["queue_worker"] == "stopped"
        assert health.json()["queue_worker_error"] == "RuntimeError"


def test_transient_completion_failure_retries_same_durable_job(app_settings, tmp_path):
    model = TransientCompletionScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Retry")
    headers = _headers(enrollment)
    filenames = ["front.jpg", "back.jpg", "base.jpg"]
    metadata = _metadata(filenames)
    with TestClient(application) as client:
        response = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[
                ("files", (name, _image_bytes(index + 20), "image/jpeg"))
                for index, name in enumerate(filenames)
            ],
        )
        job_id = response.json()["job"]["id"]
        terminal = _wait_terminal(client, job_id, headers)
        assert terminal["status"] == "SUCCEEDED"
        assert terminal["attempt"] == 2
        assert model.calls == 2
        result = client.get(
            f"/api/v2/scout/jobs/{job_id}/result", headers=headers
        ).json()["result"]
        assert [item["available"] for item in result["model_runs"]] == [False, True]
        assert [item["attempt"] for item in result["model_runs"]] == [1, 2]
        events = client.get(
            f"/api/v2/scout/jobs/{job_id}/events", headers=headers
        ).json()["events"]
        assert any(item["event_type"] == "MODEL_RETRY_SCHEDULED" for item in events)


def test_recovery_reuses_durable_success_without_duplicate_model_call(
    app_settings, tmp_path
):
    model = ScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Crash Recovery")
    filenames = ["front.jpg", "back.jpg", "base.jpg"]
    metadata = ScoutJobMetadata.model_validate(_metadata(filenames))
    job, created = application.state.scout_service.create_job(
        enrollment["device_id"],
        metadata,
        [
            IncomingCapture(name, "image/jpeg", _image_bytes(index + 90))
            for index, name in enumerate(filenames)
        ],
    )
    assert created is True
    claimed = application.state.scout_store.claim_next_job()
    assert claimed is not None and claimed["attempt"] == 0

    original_record = application.state.scout_store.record_model_attempt

    def record_then_stop(job_id, proof, **kwargs):
        original_record(job_id, proof, **kwargs)
        raise SystemExit("simulated process stop after durable model outcome")

    application.state.scout_store.record_model_attempt = record_then_stop
    with np.testing.assert_raises(SystemExit):
        asyncio.run(application.state.scout_service._process_job(claimed))
    application.state.scout_store.record_model_attempt = original_record

    assert model.calls == 1
    assert application.state.scout_store.recover_incomplete_jobs() == 1
    recovered = application.state.scout_store.claim_next_job()
    assert recovered is not None and recovered["id"] == job["id"]
    assert recovered["attempt"] == 1
    asyncio.run(application.state.scout_service._process_job(recovered))

    terminal = application.state.scout_store.get_job(job["id"])
    assert terminal["status"] == "SUCCEEDED"
    assert terminal["attempt"] == 1
    assert model.calls == 1
    assert "validated_output" not in terminal["result"]["model_runs"][0]


def test_recovery_counts_unknown_started_call_without_exceeding_budget(
    app_settings, tmp_path
):
    model = ScoutVisionStub()
    settings = replace(app_settings, scout_model_max_attempts=1)
    application = _application(settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Unknown Outcome")
    metadata = ScoutJobMetadata.model_validate(_metadata(["front.jpg"]))
    job, _ = application.state.scout_service.create_job(
        enrollment["device_id"],
        metadata,
        [IncomingCapture("front.jpg", "image/jpeg", _image_bytes(101))],
    )
    first_claim = application.state.scout_store.claim_next_job()
    assert first_claim is not None and first_claim["attempt"] == 0
    assert application.state.scout_store.recover_incomplete_jobs() == 1
    second_claim = application.state.scout_store.claim_next_job()
    assert second_claim is not None and second_claim["attempt"] == 0

    attempt = application.state.scout_store.begin_model_attempt(
        job["id"],
        {"multi_view": True, "batch_size": 1, "source_inputs": []},
        max_attempts=1,
    )
    assert attempt == 1
    assert application.state.scout_store.recover_incomplete_jobs() == 1
    recovered = application.state.scout_store.claim_next_job()
    assert recovered is not None and recovered["attempt"] == 1
    asyncio.run(application.state.scout_service._process_job(recovered))

    terminal = application.state.scout_store.get_job(job["id"])
    assert terminal["status"] == "MODEL_UNAVAILABLE"
    assert terminal["attempt"] == 1
    assert model.calls == 0
    assert terminal["result"]["model_runs"][0]["outcome_state"] == (
        "UNKNOWN_AFTER_RESTART"
    )
    assert terminal["result"]["model_runs"][0]["error"] == (
        "OutcomeUnknownAfterRestart"
    )


def test_media_tamper_fails_before_model_call(app_settings, tmp_path):
    model = ScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Integrity")
    metadata_value = _metadata(["front.jpg"])
    metadata = ScoutJobMetadata.model_validate(metadata_value)
    job, _ = application.state.scout_service.create_job(
        enrollment["device_id"],
        metadata,
        [IncomingCapture("front.jpg", "image/jpeg", _image_bytes(31))],
    )
    capture = application.state.scout_store.list_captures(job["id"])[0]
    Path(capture["path"]).write_bytes(_image_bytes(32))

    with TestClient(application) as client:
        terminal = _wait_terminal(client, job["id"], _headers(enrollment))
        assert terminal["status"] == "FAILED"
        assert terminal["error_code"] == "MEDIA_INTEGRITY_FAILURE"
        assert model.calls == 0
        result_response = client.get(
            f"/api/v2/scout/jobs/{job['id']}/result", headers=_headers(enrollment)
        )
        assert result_response.status_code == 409


def test_model_unavailable_job_can_retry_in_place_after_operator_fix(
    app_settings, tmp_path
):
    model = RecoverableCompletionScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Recovery")
    headers = _headers(enrollment)
    filenames = ["front.jpg", "back.jpg", "base.jpg"]
    metadata = _metadata(filenames)
    with TestClient(application) as client:
        response = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[
                ("files", (name, _image_bytes(index + 50), "image/jpeg"))
                for index, name in enumerate(filenames)
            ],
        )
        job_id = response.json()["job"]["id"]
        assert _wait_terminal(client, job_id, headers)["status"] == "MODEL_UNAVAILABLE"
        assert model.calls == 3

        model.fail_completions = False
        retried = client.post(
            f"/api/v2/scout/jobs/{job_id}/retry", headers=headers
        )
        assert retried.status_code == 200
        assert retried.json()["id"] == job_id
        assert retried.json()["status"] == "QUEUED"
        assert _wait_terminal(client, job_id, headers)["status"] == "SUCCEEDED"
        result = client.get(
            f"/api/v2/scout/jobs/{job_id}/result", headers=headers
        ).json()["result"]
        assert len(result["model_runs"]) == 4
        assert [item["available"] for item in result["model_runs"]] == [
            False,
            False,
            False,
            True,
        ]
        assert [item["attempt"] for item in result["model_runs"]] == [1, 2, 3, 4]


def test_successful_terminal_state_cannot_be_overwritten(app_settings, tmp_path):
    model = ScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Terminal")
    headers = _headers(enrollment)
    filenames = ["front.jpg", "back.jpg", "base.jpg"]
    metadata = _metadata(filenames)
    with TestClient(application) as client:
        response = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[
                ("files", (name, _image_bytes(index + 41), "image/jpeg"))
                for index, name in enumerate(filenames)
            ],
        )
        job_id = response.json()["job"]["id"]
        assert _wait_terminal(client, job_id, headers)["status"] == "SUCCEEDED"
        with np.testing.assert_raises(ScoutConflict):
            application.state.scout_store.fail_job(
                job_id, "LATE_FAILURE", "must not overwrite success"
            )
        persisted = client.get(f"/api/v2/scout/jobs/{job_id}", headers=headers).json()
        assert persisted["status"] == "SUCCEEDED"
        assert persisted["result_available"] is True


def test_high_ood_model_output_cannot_be_marked_success(app_settings, tmp_path):
    model = HighRiskScoutVisionStub()
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout OOD")
    headers = _headers(enrollment)
    filenames = ["front.jpg", "back.jpg", "base.jpg"]
    with TestClient(application) as client:
        response = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(_metadata(filenames))},
            files=[
                ("files", (name, _image_bytes(index + 60), "image/jpeg"))
                for index, name in enumerate(filenames)
            ],
        )
        terminal = _wait_terminal(client, response.json()["job"]["id"], headers)
        assert terminal["status"] == "PARTIAL"


def test_outstanding_quota_allows_idempotent_replay_but_rejects_new_job(
    app_settings, tmp_path
):
    settings = replace(
        app_settings,
        scout_enabled=True,
        scout_require_auth=True,
        scout_media_dir=tmp_path / "runtime" / "scout-media",
        scout_worker_poll_seconds=0.05,
        scout_min_free_bytes=64 * 1024**2,
        scout_max_outstanding_jobs_per_device=1,
        service_version="2.0-test",
    )
    application = create_scout_app(
        settings, model_client=ScoutVisionStub(available=False)
    )
    enrollment = application.state.scout_store.enroll_device("Scout Quota")
    headers = _headers(enrollment)
    metadata = _metadata(["front.jpg"])

    def submit(client, payload, *, image_seed=71):
        return client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(payload)},
            files=[("files", ("front.jpg", _image_bytes(image_seed), "image/jpeg"))],
        )

    with TestClient(application) as client:
        first = submit(client, metadata)
        assert first.status_code == 202 and first.json()["created"] is True
        replay = submit(client, metadata)
        assert replay.status_code == 202 and replay.json()["created"] is False
        second_metadata = _metadata(["front.jpg"])
        blocked_bytes = _image_bytes(72)
        blocked_hash = hashlib.sha256(blocked_bytes).hexdigest()
        blocked = submit(client, second_metadata, image_seed=72)
        assert blocked.status_code == 429
        blocked_path = (
            settings.scout_media_dir
            / "objects"
            / blocked_hash[:2]
            / f"{blocked_hash}.jpg"
        )
        assert not blocked_path.exists()


def test_concurrent_same_hash_ingest_cannot_delete_accepted_job_media(
    app_settings, tmp_path
):
    settings = replace(
        app_settings,
        scout_enabled=True,
        scout_require_auth=True,
        scout_media_dir=tmp_path / "runtime" / "scout-media",
        scout_min_free_bytes=64 * 1024**2,
        scout_max_outstanding_jobs_per_device=1,
        service_version="2.0-test",
    )
    application = create_scout_app(settings, model_client=ScoutVisionStub())
    enrollment = application.state.scout_store.enroll_device("Scout Shared Media")
    shared_bytes = _image_bytes(111)
    barrier = Barrier(2)

    def submit(_sequence: int):
        metadata = ScoutJobMetadata.model_validate(_metadata(["front.jpg"]))
        barrier.wait(timeout=5)
        try:
            job, created = application.state.scout_service.create_job(
                enrollment["device_id"],
                metadata,
                [IncomingCapture("front.jpg", "image/jpeg", shared_bytes)],
            )
            return "created", job["id"], created
        except ScoutCapacityError:
            return "capacity", None, None

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(submit, (1, 2)))

    assert sorted(item[0] for item in outcomes) == ["capacity", "created"]
    accepted_id = next(item[1] for item in outcomes if item[0] == "created")
    captures = application.state.scout_store.list_captures(accepted_id)
    assert len(captures) == 1
    media_path = Path(captures[0]["path"])
    assert media_path.is_file()
    assert hashlib.sha256(media_path.read_bytes()).hexdigest() == captures[0]["sha256"]


def test_concurrent_ingests_cannot_jointly_consume_storage_reserve(
    app_settings, tmp_path, monkeypatch
):
    reserve = 64 * 1024**2
    first_bytes = _image_bytes(121)
    second_bytes = _image_bytes(122)
    simulated = {
        "free": reserve + max(len(first_bytes), len(second_bytes)) + 1,
    }
    state_lock = Lock()
    pre_lock_reads = Barrier(2)
    lock_state = local()
    settings = replace(
        app_settings,
        scout_enabled=True,
        scout_require_auth=True,
        scout_media_dir=tmp_path / "runtime" / "scout-media",
        scout_min_free_bytes=reserve,
        scout_max_outstanding_jobs_per_device=4,
        service_version="2.0-test",
    )
    application = create_scout_app(settings, model_client=ScoutVisionStub())
    service = application.state.scout_service
    enrollment = application.state.scout_store.enroll_device("Scout Storage Race")
    original_lock = service._ingest_publication_lock
    original_write = service._write_content_addressed

    @contextmanager
    def tracked_publication_lock():
        with original_lock():
            lock_state.held = True
            try:
                yield
            finally:
                lock_state.held = False

    def simulated_disk_usage(_path):
        # With the vulnerable implementation both callers reach this point before
        # taking the publication lock, so force them to observe the same value.
        if not getattr(lock_state, "held", False):
            pre_lock_reads.wait(timeout=5)
        with state_lock:
            free = simulated["free"]
        return SimpleNamespace(total=4 * reserve, used=0, free=free)

    def tracked_write(path, payload):
        created = original_write(path, payload)
        if created:
            with state_lock:
                simulated["free"] -= len(payload)
        return created

    monkeypatch.setattr(service, "_ingest_publication_lock", tracked_publication_lock)
    monkeypatch.setattr(service, "_write_content_addressed", tracked_write)
    monkeypatch.setattr("app.scout.service.shutil.disk_usage", simulated_disk_usage)

    def submit(sequence: int, payload: bytes):
        metadata = ScoutJobMetadata.model_validate(_metadata([f"view-{sequence}.jpg"]))
        try:
            job, _created = service.create_job(
                enrollment["device_id"],
                metadata,
                [IncomingCapture(f"view-{sequence}.jpg", "image/jpeg", payload)],
            )
            return "created", job["id"]
        except ScoutStorageReserveError:
            return "storage-reserve", None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(submit, 1, first_bytes),
            executor.submit(submit, 2, second_bytes),
        )
        outcomes = [future.result(timeout=10) for future in futures]

    assert sorted(item[0] for item in outcomes) == ["created", "storage-reserve"]
    accepted_id = next(item[1] for item in outcomes if item[0] == "created")
    assert len(application.state.scout_store.list_captures(accepted_id)) == 1
    object_files = [
        path
        for path in (settings.scout_media_dir / "objects").rglob("*")
        if path.is_file()
    ]
    assert len(object_files) == 1


def test_existing_content_addressed_media_requires_no_new_storage_reserve(
    app_settings, tmp_path, monkeypatch
):
    reserve = 64 * 1024**2
    shared_bytes = _image_bytes(123)
    settings = replace(
        app_settings,
        scout_enabled=True,
        scout_require_auth=True,
        scout_media_dir=tmp_path / "runtime" / "scout-media",
        scout_min_free_bytes=reserve,
        scout_max_outstanding_jobs_per_device=4,
        service_version="2.0-test",
    )
    application = create_scout_app(settings, model_client=ScoutVisionStub())
    service = application.state.scout_service
    enrollment = application.state.scout_store.enroll_device("Scout Media Reuse")

    first_metadata = ScoutJobMetadata.model_validate(_metadata(["first.jpg"]))
    first, created = service.create_job(
        enrollment["device_id"],
        first_metadata,
        [IncomingCapture("first.jpg", "image/jpeg", shared_bytes)],
    )
    assert created is True
    first_capture = application.state.scout_store.list_captures(first["id"])[0]
    object_path = Path(first_capture["path"])
    assert object_path.is_file()

    monkeypatch.setattr(
        "app.scout.service.shutil.disk_usage",
        lambda _path: SimpleNamespace(total=4 * reserve, used=0, free=reserve),
    )
    second_metadata = ScoutJobMetadata.model_validate(_metadata(["second.jpg"]))
    second, created = service.create_job(
        enrollment["device_id"],
        second_metadata,
        [IncomingCapture("second.jpg", "image/jpeg", shared_bytes)],
    )

    assert created is True
    assert second["id"] != first["id"]
    second_capture = application.state.scout_store.list_captures(second["id"])[0]
    assert Path(second_capture["path"]) == object_path
    assert len(
        [
            path
            for path in (settings.scout_media_dir / "objects").rglob("*")
            if path.is_file()
        ]
    ) == 1


def test_device_revocation_and_cross_device_isolation(app_settings, tmp_path):
    application = _application(app_settings, tmp_path, ScoutVisionStub())
    first = application.state.scout_store.enroll_device("Scout A")
    second = application.state.scout_store.enroll_device("Scout B")
    headers = _headers(first)
    metadata = _metadata(["front.jpg"])

    with TestClient(application) as client:
        accepted = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[("files", ("front.jpg", _image_bytes(2), "image/jpeg"))],
        )
        job_id = accepted.json()["job"]["id"]
        assert (
            client.get(f"/api/v2/scout/jobs/{job_id}", headers=_headers(second)).status_code
            == 404
        )
        application.state.scout_store.set_device_enabled(first["device_id"], False)
        assert client.get("/api/v2/scout/me", headers=headers).status_code == 401


def test_model_cold_start_keeps_job_and_idempotency_until_model_recovers(
    app_settings, tmp_path
):
    model = ScoutVisionStub(available=False)
    application = _application(app_settings, tmp_path, model)
    enrollment = application.state.scout_store.enroll_device("Scout Test 03")
    headers = _headers(enrollment)
    metadata = _metadata(["front.jpg"])

    with TestClient(application) as client:
        accepted = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(metadata)},
            files=[("files", ("front.jpg", _image_bytes(3), "image/jpeg"))],
        )
        job_id = accepted.json()["job"]["id"]
        time.sleep(0.15)
        waiting = client.get(f"/api/v2/scout/jobs/{job_id}", headers=headers).json()
        assert waiting["status"] in {"QUEUED", "RETRY_WAIT"}
        assert model.calls == 0

        model.available = True
        application.state.scout_service._model_health_cached_at = 0
        application.state.scout_service.wake()
        assert _wait_terminal(client, job_id, headers)["status"] == "PARTIAL"
        assert model.calls == 1

        conflicting = dict(metadata)
        conflicting["subject_label"] = "changed immutable subject"
        response = client.post(
            "/api/v2/scout/jobs",
            headers=headers,
            data={"metadata_json": json.dumps(conflicting)},
            files=[("files", ("front.jpg", _image_bytes(3), "image/jpeg"))],
        )
        assert response.status_code == 409
