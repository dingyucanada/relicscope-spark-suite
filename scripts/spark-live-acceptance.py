#!/usr/bin/env python3
"""Create durable proof that the live single-Spark model processed real media."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import re
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
IMMUTABLE_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.IGNORECASE)


def _require_sha256(label: str, value: Any) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RuntimeError(f"{label} is not an exact hexadecimal SHA-256 digest")
    return value.lower()


def _require_immutable_revision(label: str, value: Any) -> str:
    if not isinstance(value, str) or IMMUTABLE_REVISION_RE.fullmatch(value) is None:
        raise RuntimeError(f"{label} is not an immutable 40- or 64-hex commit revision")
    return value.lower()


def _generated_at_utc() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _acceptance_tool_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _call(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    timeout: int = 900,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def _multipart(
    fields: dict[str, str], filename: str, mime_type: str, file_bytes: bytes
) -> tuple[bytes, str]:
    boundary = f"----RelicScope{secrets.token_hex(16)}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    chunks.extend(
        [
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode(),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
    )
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _latest_model_run(session: dict[str, Any], role: str) -> dict[str, Any]:
    for run in reversed(session.get("model_runs", [])):
        if run.get("role") == role:
            return run
    raise RuntimeError(f"model run was not recorded: {role}")


def _assert_real_run(
    run: dict[str, Any], expected_model: str, expected_profile: str
) -> None:
    if run.get("status") != "SUCCESS" or run.get("mode") != "local_vllm":
        raise RuntimeError(f"model did not execute through local vLLM: {run}")
    if run.get("model") != expected_model:
        raise RuntimeError(
            f"served model mismatch: expected {expected_model}, got {run.get('model')}"
        )
    if run.get("configured_model") != expected_model:
        raise RuntimeError("model run did not bind the configured served-model alias")
    if run.get("model_profile") != expected_profile:
        raise RuntimeError(
            f"model profile mismatch: expected {expected_profile}, "
            f"got {run.get('model_profile')}"
        )
    if run.get("model_identity_verified") is not True:
        raise RuntimeError(
            "completion response did not verify the configured model identity"
        )
    if (
        not isinstance(run.get("provider_request_id"), str)
        or not run["provider_request_id"].strip()
    ):
        raise RuntimeError("completion did not return a request identifier")
    if run.get("finish_reason") != "stop":
        raise RuntimeError("completion did not finish with finish_reason=stop")
    if not isinstance(run.get("model_source"), str) or not run["model_source"].strip():
        raise RuntimeError("model run is missing its model source")
    _require_immutable_revision("model revision", run.get("model_revision"))
    _require_immutable_revision(
        "deployment Git commit", run.get("deployment_git_commit")
    )
    _require_sha256("prompt/template hash", run.get("template_hash"))
    for key in ("input_hash", "output_hash"):
        _require_sha256(f"model run {key}", run.get(key))


def _assert_envelope_integrity(value: dict[str, Any]) -> dict[str, Any]:
    integrity = value.get("integrity") or {}
    if integrity.get("valid") is not True:
        raise RuntimeError("state-bound evidence integrity verification failed")
    if value.get("audit_verified") is not True:
        raise RuntimeError("state-bound audit-chain verification failed")
    _require_sha256("evidence binding", integrity.get("binding_sha256"))
    return integrity


def _load_checksum_manifest(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"fixture checksum manifest is missing: {path}")
    entries: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise RuntimeError(
                f"invalid checksum manifest line {line_number}: expected digest and path"
            )
        digest, relative_name = parts
        relative_name = relative_name.lstrip("*")
        _require_sha256(f"fixture checksum at manifest line {line_number}", digest)
        relative_path = Path(relative_name)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise RuntimeError(
                f"unsafe fixture path at manifest line {line_number}: {relative_name}"
            )
        normalized = relative_path.as_posix()
        if normalized in entries:
            raise RuntimeError(f"duplicate fixture manifest entry: {normalized}")
        entries[normalized] = digest.lower()
    if not entries:
        raise RuntimeError("fixture checksum manifest is empty")
    return entries


def _verify_fixture_manifest(
    manifest_path: Path, selected_paths: list[Path]
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    canonical_manifest = (
        Path(__file__).resolve().parents[1] / "demo_media" / "SHA256SUMS"
    ).resolve()
    if manifest_path != canonical_manifest:
        raise RuntimeError(
            "formal acceptance only permits the repository's fixed synthetic "
            "fixture manifest"
        )
    root = manifest_path.parent
    entries = _load_checksum_manifest(manifest_path)
    provenance_path = root / "manifest.json"
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "synthetic fixture provenance manifest is unreadable"
        ) from exc
    if (
        not isinstance(provenance, dict)
        or provenance.get("schema") != "relicscope.synthetic-media.v1"
        or provenance.get("provenance") != "DEMO/SYNTHETIC"
        or provenance.get("contains_real_artifact_media") is not False
    ):
        raise RuntimeError("fixture provenance does not prove synthetic demo media")
    provenance_entries = {
        item.get("path"): item.get("sha256")
        for item in provenance.get("files", [])
        if isinstance(item, dict)
    }
    verified_files = []
    for selected_path in selected_paths:
        resolved = selected_path.resolve()
        try:
            relative_name = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise RuntimeError(
                f"formal acceptance fixture is outside manifest root: {resolved}"
            ) from exc
        expected = entries.get(relative_name)
        if expected is None:
            raise RuntimeError(
                f"formal acceptance fixture is not listed in checksum manifest: "
                f"{relative_name}"
            )
        if provenance_entries.get(relative_name) != expected:
            raise RuntimeError(
                f"fixture checksum and provenance disagree: {relative_name}"
            )
        actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(
                f"formal acceptance fixture checksum mismatch: {relative_name}"
            )
        verified_files.append(
            {
                "path": relative_name,
                "sha256": actual,
                "manifest_verified": True,
            }
        )
    return {
        "classification": "DEMO_SYNTHETIC_MANIFEST_VERIFIED",
        "manifest_name": manifest_path.name,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "provenance_manifest_name": provenance_path.name,
        "provenance_manifest_sha256": hashlib.sha256(
            provenance_path.read_bytes()
        ).hexdigest(),
        "provenance_schema": provenance["schema"],
        "contains_real_artifact_media": False,
        "files": verified_files,
    }


def _health(base_url: str, expected_model: str) -> dict[str, Any]:
    health = _call(base_url, "GET", "/api/health", timeout=20)
    runtime = health.get("compute_runtime", {})
    if health.get("mode") != "single-spark":
        raise RuntimeError(f"expected single-spark mode, got {health.get('mode')}")
    if runtime.get("endpoint_identity_ready") is not True:
        raise RuntimeError(f"local model identity is not ready: {runtime}")
    if runtime.get("gpu_access") != "VERIFIED_BY_NVIDIA_SMI":
        raise RuntimeError(
            "application container did not verify the local NVIDIA GPU with nvidia-smi: "
            f"{runtime}"
        )
    if runtime.get("dgx_spark_hardware_verified") is not True:
        raise RuntimeError(
            "GPU execution was visible, but ARM64 + NVIDIA GB10 DGX Spark identity "
            f"was not verified: {runtime}"
        )
    if runtime.get("configured_model") != expected_model:
        raise RuntimeError(
            f"health model mismatch: expected {expected_model}, "
            f"got {runtime.get('configured_model')}"
        )
    return health


def _host_attestation(
    vision_container_id: str, runtime: dict[str, Any]
) -> dict[str, Any]:
    try:
        inspected = json.loads(
            subprocess.run(
                ["docker", "inspect", vision_container_id],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            ).stdout
        )[0]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
        raise RuntimeError("unable to inspect the live vision container") from exc

    labels = inspected.get("Config", {}).get("Labels") or {}
    expected_labels = {
        "ai.relicscope.model.profile": runtime.get("model_profile"),
        "ai.relicscope.model.source": runtime.get("model_source"),
        "ai.relicscope.model.revision": runtime.get("model_revision"),
        "ai.relicscope.model.served": runtime.get("configured_model"),
    }
    for key, expected in expected_labels.items():
        if not expected or expected == "unknown" or labels.get(key) != expected:
            raise RuntimeError(
                f"live container label mismatch for {key}: "
                f"expected {expected}, got {labels.get(key)}"
            )

    source_commit = _require_immutable_revision(
        "application source commit", runtime.get("deployment_git_commit")
    )
    image_commit = _require_immutable_revision(
        "vLLM image source commit",
        labels.get("org.opencontainers.image.revision"),
    )
    if image_commit != source_commit:
        raise RuntimeError(
            "application and vLLM images were not built from the same source commit"
        )
    model_revision = _require_immutable_revision(
        "live model revision", runtime.get("model_revision")
    )

    gpu_uuids = sorted(
        {
            str(item.get("uuid") or "").strip()
            for item in runtime.get("gpu_devices", [])
            if str(item.get("uuid") or "").strip()
        }
    )
    if not gpu_uuids:
        raise RuntimeError("runtime did not expose a GPU UUID for hardware binding")
    hardware_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "architecture": runtime.get("architecture"),
                "device_family": runtime.get("device_family"),
                "gpu_uuids": gpu_uuids,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    networks = sorted(
        (inspected.get("NetworkSettings", {}).get("Networks") or {}).keys()
    )
    if not networks:
        raise RuntimeError("vision container has no attached private runtime network")
    network_attestations = []
    for network_name in networks:
        try:
            network = json.loads(
                subprocess.run(
                    ["docker", "network", "inspect", network_name],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                ).stdout
            )[0]
        except (OSError, subprocess.SubprocessError, ValueError, IndexError) as exc:
            raise RuntimeError(
                f"unable to inspect Docker network {network_name}"
            ) from exc
        internal = network.get("Internal") is True
        if not internal:
            raise RuntimeError(
                f"runtime network {network_name} permits external egress; expected Internal=true"
            )
        network_attestations.append({"name": network_name, "internal": internal})

    published_ports = {
        port: bindings
        for port, bindings in (
            inspected.get("NetworkSettings", {}).get("Ports") or {}
        ).items()
        if bindings
    }
    if published_ports:
        raise RuntimeError("vision model container unexpectedly publishes a host port")
    device_requests = inspected.get("HostConfig", {}).get("DeviceRequests") or []
    gpu_requested = any(
        request.get("Driver") in {"", "nvidia"}
        and any("gpu" in group for group in request.get("Capabilities", []))
        for request in device_requests
    )
    if not gpu_requested:
        raise RuntimeError("vision container was not created with a Docker GPU request")

    vision_image_id = inspected.get("Image")
    if (
        not isinstance(vision_image_id, str)
        or not vision_image_id.startswith("sha256:")
        or SHA256_RE.fullmatch(vision_image_id.removeprefix("sha256:")) is None
    ):
        raise RuntimeError("vision container is not bound to an immutable image ID")

    return {
        "vision_container_id": str(inspected.get("Id") or vision_container_id),
        "vision_image_id": vision_image_id,
        "source_commit": source_commit,
        "model_profile": runtime.get("model_profile"),
        "model_source": runtime.get("model_source"),
        "model_revision": model_revision,
        "served_model": runtime.get("configured_model"),
        "gpu_uuids": gpu_uuids,
        "hardware_fingerprint_sha256": hardware_fingerprint,
        "gpu_device_request": True,
        "model_port_published": False,
        "runtime_networks": network_attestations,
    }


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_text(rendered, encoding="utf-8")
    path.chmod(0o600)
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    sidecar.chmod(0o600)


def _run_summary(run: dict[str, Any]) -> dict[str, Any]:
    return {
        key: run.get(key)
        for key in (
            "run_id",
            "role",
            "node_id",
            "model",
            "configured_model",
            "model_profile",
            "model_source",
            "model_revision",
            "deployment_git_commit",
            "model_identity_verified",
            "provider_request_id",
            "template_hash",
            "started_at",
            "completed_at",
            "latency_ms",
            "status",
            "mode",
            "input_hash",
            "output_hash",
            "token_usage",
            "finish_reason",
            "output",
        )
    }


def baseline(args: argparse.Namespace) -> dict[str, Any]:
    health = _health(args.base_url, args.expected_model)
    host_attestation = _host_attestation(
        args.vision_container_id, health["compute_runtime"]
    )
    fixture_provenance = _verify_fixture_manifest(
        args.fixture_manifest, [args.image, args.video]
    )
    image_bytes = args.image.read_bytes()
    video_bytes = args.video.read_bytes()
    created = _call(
        args.base_url,
        "POST",
        "/api/sessions",
        payload={
            "artifact_name": "单 Spark A/B 合成青花瓷样本",
            "operator": "RelicScope Live Acceptance",
            "institution": "RelicScope Local Lab",
            "claim": {
                "period": "待核验",
                "kiln": "待核验",
                "material": "陶瓷",
                "provenance_note": "DEMO/SYNTHETIC；模型 A/B 验收",
            },
        },
    )
    session_id = created["session"]["id"]
    image_result = _call(
        args.base_url,
        "POST",
        f"/api/sessions/{session_id}/images/analyze",
        payload={
            "filename": args.image.name,
            "mime_type": mimetypes.guess_type(args.image.name)[0] or "image/png",
            "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            "modality": "RGB",
            "region_id": "R1",
        },
    )
    image_run = _latest_model_run(image_result["session"], "multimodal_observation")
    _assert_real_run(image_run, args.expected_model, args.profile)

    multipart, content_type = _multipart(
        {
            "modality": "RGB_VIDEO",
            "region_id": "R1",
            "duration_ms": str(args.duration_ms),
            "capture_note": "single-Spark frozen A/B fixture",
        },
        args.video.name,
        mimetypes.guess_type(args.video.name)[0] or "video/mp4",
        video_bytes,
    )
    registered = _call(
        args.base_url,
        "POST",
        f"/api/sessions/{session_id}/videos/register",
        body=multipart,
        content_type=content_type,
    )
    video_id = registered["session"]["videos"][-1]["id"]
    native = _call(
        args.base_url,
        "POST",
        f"/api/sessions/{session_id}/videos/{video_id}/native-analyze",
    )
    video_run = _latest_model_run(
        native["session"], "native_video_multimodal_observation"
    )
    _assert_real_run(video_run, args.expected_model, args.profile)
    report_result = _call(args.base_url, "POST", f"/api/sessions/{session_id}/report")
    report_run = _latest_model_run(report_result["session"], "evidence_report_summary")
    _assert_real_run(report_run, args.expected_model, args.profile)
    integrity = _assert_envelope_integrity(report_result)
    return {
        "schema": "relicscope.single-spark-live.v1",
        "generated_at_utc": _generated_at_utc(),
        "acceptance_tool_sha256": _acceptance_tool_sha256(),
        "phase": "baseline",
        "profile": args.profile,
        "expected_model": args.expected_model,
        "session_id": session_id,
        "video_id": video_id,
        "image_fixture_sha256": hashlib.sha256(image_bytes).hexdigest(),
        "video_fixture_sha256": hashlib.sha256(video_bytes).hexdigest(),
        "fixture_provenance": fixture_provenance,
        "runtime": health.get("compute_runtime"),
        "host_attestation": host_attestation,
        "model_runs": [
            _run_summary(image_run),
            _run_summary(video_run),
            _run_summary(report_run),
        ],
        "evidence_binding_sha256": integrity.get("binding_sha256"),
        "integrity_valid": True,
        "audit_verified": True,
    }


def candidate(args: argparse.Namespace) -> dict[str, Any]:
    health = _health(args.base_url, args.expected_model)
    host_attestation = _host_attestation(
        args.vision_container_id, health["compute_runtime"]
    )
    result = _call(
        args.base_url,
        "POST",
        f"/api/sessions/{args.session_id}/videos/{args.video_id}/native-analyze",
    )
    run = _latest_model_run(result["session"], "native_video_multimodal_observation")
    _assert_real_run(run, args.expected_model, args.profile)
    integrity = _assert_envelope_integrity(result)
    return {
        "schema": "relicscope.single-spark-live.v1",
        "generated_at_utc": _generated_at_utc(),
        "acceptance_tool_sha256": _acceptance_tool_sha256(),
        "phase": "candidate-native-video",
        "profile": args.profile,
        "expected_model": args.expected_model,
        "session_id": args.session_id,
        "video_id": args.video_id,
        "runtime": health.get("compute_runtime"),
        "host_attestation": host_attestation,
        "model_runs": [_run_summary(run)],
        "integrity_valid": True,
        "audit_verified": True,
        "evidence_binding_sha256": integrity.get("binding_sha256"),
    }


def finalize(args: argparse.Namespace) -> dict[str, Any]:
    health = _health(args.base_url, args.expected_model)
    host_attestation = _host_attestation(
        args.vision_container_id, health["compute_runtime"]
    )
    result = _call(args.base_url, "POST", f"/api/sessions/{args.session_id}/report")
    run = _latest_model_run(result["session"], "evidence_report_summary")
    _assert_real_run(run, args.expected_model, args.profile)
    integrity = _assert_envelope_integrity(result)
    report = result["session"]["last_report"]
    report_sha256 = _require_sha256(
        "final report hash", report.get("integrity", {}).get("report_sha256")
    )
    return {
        "schema": "relicscope.single-spark-live.v1",
        "generated_at_utc": _generated_at_utc(),
        "acceptance_tool_sha256": _acceptance_tool_sha256(),
        "phase": "final-report",
        "profile": args.profile,
        "expected_model": args.expected_model,
        "session_id": args.session_id,
        "runtime": health.get("compute_runtime"),
        "host_attestation": host_attestation,
        "model_runs": [_run_summary(run)],
        "all_session_model_runs": [
            _run_summary(item) for item in result["session"].get("model_runs", [])
        ],
        "report_id": report.get("report_id"),
        "report_sha256": report_sha256,
        "evidence_binding_sha256": integrity.get("binding_sha256"),
        "integrity_valid": True,
        "audit_verified": True,
    }


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    value = argparse.ArgumentParser(description=__doc__)
    sub = value.add_subparsers(dest="command", required=True)

    def common(command: str) -> argparse.ArgumentParser:
        item = sub.add_parser(command)
        item.add_argument("--base-url", default="http://127.0.0.1:8088")
        item.add_argument("--profile", required=True)
        item.add_argument("--expected-model", required=True)
        item.add_argument("--vision-container-id", required=True)
        item.add_argument("--output", type=Path, required=True)
        return item

    first = common("baseline")
    first.add_argument("--image", type=Path, default=root / "demo_media/reference.png")
    first.add_argument(
        "--video", type=Path, default=root / "demo_media/synthetic_orbit.mp4"
    )
    first.add_argument(
        "--fixture-manifest",
        type=Path,
        default=root / "demo_media/SHA256SUMS",
        help="checksum allowlist for the formal synthetic acceptance fixtures",
    )
    first.add_argument("--duration-ms", type=int, default=3000)
    second = common("candidate")
    second.add_argument("--session-id", required=True)
    second.add_argument("--video-id", required=True)
    final = common("finalize")
    final.add_argument("--session-id", required=True)
    return value


def main() -> int:
    args = parser().parse_args()
    if args.command == "baseline":
        for path in (args.image, args.video):
            if not path.is_file():
                raise FileNotFoundError(path)
        result = baseline(args)
    elif args.command == "candidate":
        result = candidate(args)
    else:
        result = finalize(args)
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ACCEPTANCE FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
