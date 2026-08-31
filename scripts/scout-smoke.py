#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import mimetypes
import os
import re
import stat
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.vlm import (  # noqa: E402
    SCOUT_MULTI_VIEW_SYSTEM_PROMPT,
    SCOUT_OBSERVATION_INSTRUCTION,
    build_scout_multi_view_payload,
)
from app.scout.schemas import ScoutJobMetadata  # noqa: E402


TERMINAL = {
    "SUCCEEDED",
    "PARTIAL",
    "NEEDS_RECAPTURE",
    "MODEL_UNAVAILABLE",
    "FAILED",
    "CANCELLED",
}
VIEWS = {
    "FRONT",
    "BACK",
    "LEFT_PROFILE",
    "RIGHT_PROFILE",
    "TOP",
    "BASE",
    "INTERIOR",
    "DETAIL",
    "MARK",
    "DAMAGE",
    "OTHER",
}
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IMMUTABLE_COMMIT_PATTERN = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
IMMUTABLE_IMAGE_PATTERN = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: object, *, compact: bool) -> str:
    serialized = (
        _canonical_json(value)
        if compact
        else json.dumps(value, ensure_ascii=False, sort_keys=True)
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_secure_provisioning(path: Path) -> dict[str, str]:
    selected_path = path.expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif selected_path.is_symlink():
        raise ValueError("provisioning path must be a regular file, not a link")
    try:
        descriptor = os.open(selected_path, flags)
    except OSError as exc:
        raise ValueError(f"cannot read provisioning file: {exc.strerror}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("provisioning path must be a regular file, not a link")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("provisioning file permissions must be exactly 0600")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError("provisioning file must be owned by the current user")
        if info.st_size <= 0 or info.st_size > 16 * 1024:
            raise ValueError("provisioning file size is invalid")
        raw_value = os.read(descriptor, 16 * 1024 + 1)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw_value.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("provisioning file is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or value.get("schema_version") != (
        "relicscope-scout-provisioning-v2"
    ):
        raise ValueError("provisioning file schema is invalid")
    required = ("server_url", "device_id", "device_token")
    if not all(isinstance(value.get(key), str) and value[key].strip() for key in required):
        raise ValueError("provisioning file is missing Scout credentials")
    if len(value["device_token"].strip()) < 32:
        raise ValueError("provisioning file contains an invalid device token")
    return {key: value[key].strip() for key in required}


def _sanitize_model_image(raw_bytes: bytes) -> tuple[str, str]:
    with Image.open(io.BytesIO(raw_bytes)) as source:
        clean = ImageOps.exif_transpose(source).convert("RGB")
        clean.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        clean.save(buffer, format="JPEG", quality=90, optimize=True)
    payload = buffer.getvalue()
    return (
        "data:image/jpeg;base64,"
        + base64.b64encode(payload).decode("ascii"),
        hashlib.sha256(payload).hexdigest(),
    )


def _secure_server_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("Scout server URL must be an HTTPS origin without credentials or a path")
    return value.rstrip("/")


def _capture_argument(value: str) -> tuple[str, Path]:
    view, separator, raw_path = value.partition("=")
    normalized_view = view.strip().upper()
    if separator != "=" or normalized_view not in VIEWS or not raw_path.strip():
        raise argparse.ArgumentTypeError("capture must use VIEW=/path/to/image")
    return normalized_view, Path(raw_path).expanduser()


def _validate_success_result(
    envelope: dict,
    *,
    expected_job_id: str,
    metadata: dict,
    submitted: list[dict],
) -> None:
    normalized_metadata = ScoutJobMetadata.model_validate(metadata).model_dump(mode="json")
    result = envelope.get("result")
    if not isinstance(result, dict) or result.get("authenticity_state") != "NOT_ASSESSED":
        raise RuntimeError("successful smoke result is missing the scientific boundary")
    if (
        result.get("job_id") != expected_job_id
        or result.get("subject_label") != normalized_metadata["subject_label"]
        or result.get("subject_label_provenance")
        != "OPERATOR_SUPPLIED_UNVERIFIED"
        or result.get("analysis_mode") != "standard"
    ):
        raise RuntimeError("successful result is not bound to the submitted Scout job")
    operator_metadata = result.get("operator_metadata")
    if not isinstance(operator_metadata, dict) or operator_metadata != {
        "subject_label": normalized_metadata["subject_label"],
        "operator_note": normalized_metadata["operator_note"],
        "source": "OPERATOR_SUPPLIED",
        "verification_status": "UNVERIFIED",
        "used_as_model_conclusion": False,
    }:
        raise RuntimeError("operator metadata is not explicitly marked unverified")
    expected_result_hash = _sha256_json(
        {key: value for key, value in result.items() if key != "result_sha256"},
        compact=True,
    )
    if result.get("result_sha256") != expected_result_hash:
        raise RuntimeError("result_sha256 does not bind the canonical result")

    expected_input_hash = _sha256_json(
        {
            "metadata": normalized_metadata,
            "files": [
                {
                    "client_capture_id": item["client_capture_id"],
                    "filename": item["filename"],
                    "view_code": item["view_code"],
                    "sha256": item["raw_sha256"],
                    "byte_count": len(item["raw_bytes"]),
                }
                for item in submitted
            ],
        },
        compact=True,
    )
    provenance = result.get("compute_provenance")
    if not isinstance(provenance, dict):
        raise RuntimeError("successful result has no compute provenance")
    if provenance.get("input_payload_sha256") != expected_input_hash:
        raise RuntimeError("input payload hash does not bind the submitted bytes and metadata")
    deployment_commit = str(provenance.get("deployment_git_commit", "")).lower()
    if not IMMUTABLE_COMMIT_PATTERN.fullmatch(deployment_commit):
        raise RuntimeError("result is not bound to an immutable deployment Git commit")

    runs = result.get("model_runs")
    if not isinstance(runs, list):
        raise RuntimeError("successful smoke result has no model run evidence")
    successful = [
        run
        for run in runs
        if run.get("available") is True
        and run.get("mode") == "local_vllm"
        and run.get("model_identity_verified") is True
    ]
    if not successful:
        raise RuntimeError("successful smoke result did not prove a local vLLM completion")
    if len(successful) != 1:
        raise RuntimeError("smoke result must contain exactly one successful model run")
    run = successful[0]
    for field in (
        "system_prompt_hash",
        "request_payload_hash",
        "output_hash",
    ):
        if not HASH_PATTERN.fullmatch(str(run.get(field, ""))):
            raise RuntimeError(f"model run is missing a valid {field}")
    if not run.get("request_id"):
        raise RuntimeError("model run is missing the provider request ID")
    runtime_image = str(run.get("runtime_image", "")).lower()
    if not IMMUTABLE_IMAGE_PATTERN.fullmatch(runtime_image):
        raise RuntimeError("model run is not bound to an immutable runtime image")
    model_revision = str(run.get("model_revision", "")).lower()
    if not IMMUTABLE_COMMIT_PATTERN.fullmatch(model_revision):
        raise RuntimeError("model run is not bound to an immutable model revision")
    configured_model = run.get("configured_model")
    if (
        not isinstance(configured_model, str)
        or not configured_model
        or run.get("model") != configured_model
        or run.get("model_source") != configured_model
    ):
        raise RuntimeError("model identity and immutable source are not consistently bound")
    expected_system_prompt_hash = hashlib.sha256(
        SCOUT_MULTI_VIEW_SYSTEM_PROMPT.encode("utf-8")
    ).hexdigest()
    if run.get("system_prompt_hash") != expected_system_prompt_hash:
        raise RuntimeError("system prompt hash does not match the deployed Scout contract")

    source_inputs = run.get("source_inputs") or []
    if len(source_inputs) != len(submitted):
        raise RuntimeError("model source input count does not match submitted captures")
    if len({item.get("capture_id") for item in source_inputs}) != len(source_inputs):
        raise RuntimeError("model source capture identifiers are not unique")
    sanitized_images: list[dict] = []
    for source_input, local in zip(source_inputs, submitted, strict=True):
        if source_input.get("view_code") != local["view_code"]:
            raise RuntimeError("model input order does not match the declared Scout views")
        if source_input.get("source_sha256") != local["raw_sha256"]:
            raise RuntimeError("model source hash does not match the submitted image bytes")
        data_url, sanitized_hash = _sanitize_model_image(local["raw_bytes"])
        if source_input.get("sanitized_model_input_sha256") != sanitized_hash:
            raise RuntimeError("sanitized model input hash cannot be reproduced")
        sanitized_images.append(
            {
                "capture_id": source_input["capture_id"],
                "view_code": source_input["view_code"],
                "image_data_url": data_url,
            }
        )

    records = (result.get("capture_assessment") or {}).get("records") or []
    record_bindings = {
        (item.get("capture_id"), item.get("view_code"), item.get("sha256"))
        for item in records
    }
    bindings = {
        (item["capture_id"], item["view_code"], item["source_sha256"])
        for item in source_inputs
    }
    if not bindings.issubset(record_bindings):
        raise RuntimeError("model source evidence is not bound to capture assessment records")

    expected_payload, _ = build_scout_multi_view_payload(
        configured_model,
        sanitized_images,
        {
            "job_id": expected_job_id,
            "operator_metadata": {
                "subject_label": normalized_metadata["subject_label"],
                "source": "OPERATOR_SUPPLIED",
                "verification_status": "UNVERIFIED",
            },
            "instruction": SCOUT_OBSERVATION_INSTRUCTION,
        },
    )
    if run.get("request_payload_hash") != _sha256_json(
        expected_payload, compact=True
    ):
        raise RuntimeError("request payload hash cannot be reproduced from submitted inputs")

    observations = result.get("visible_observations") or []
    if not observations or any(
        (item.get("capture_id"), item.get("view_code"), local_hash)
        not in bindings
        for item in observations
        for local_hash in [
            next(
                (
                    source["source_sha256"]
                    for source in source_inputs
                    if source["capture_id"] == item.get("capture_id")
                    and source["view_code"] == item.get("view_code")
                ),
                None,
            )
        ]
    ):
        raise RuntimeError("visible observations are empty or not bound to model inputs")
    if any(item.get("model_output_sha256") != run.get("output_hash") for item in observations):
        raise RuntimeError("visible observations are not bound to the model output hash")
    reconstructed_output = {
        "observations": [
            {
                "capture_id": item["capture_id"],
                "view_code": item["view_code"],
                "text": item["text"],
            }
            for item in observations
        ],
        "cross_view_observations": result.get("cross_view_observations") or [],
        "limitations": result.get("model_limitations") or [],
        "capture_issues": result.get("model_capture_issues") or [],
        "ood_risk": result.get("model_ood_risk"),
    }
    if run.get("output_hash") != _sha256_json(reconstructed_output, compact=False):
        raise RuntimeError("model output hash cannot be reproduced from the result")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real Scout-to-Spark API smoke test")
    parser.add_argument(
        "--capture",
        action="append",
        required=True,
        type=_capture_argument,
        metavar="VIEW=FILE",
        help="repeat for each explicitly labelled Scout view",
    )
    parser.add_argument(
        "--provisioning",
        type=Path,
        required=True,
        help="0600 Scout provisioning JSON created by scout-device.py",
    )
    parser.add_argument("--ca-cert", type=Path)
    parser.add_argument(
        "--allow-terminal-status",
        action="append",
        default=[],
        choices=sorted(TERMINAL - {"SUCCEEDED"}),
        help="accept an expected non-success status only for a deliberate fault test",
    )
    parser.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args()

    try:
        provisioning = _read_secure_provisioning(args.provisioning)
    except ValueError as exc:
        parser.error(str(exc))
    server_url = provisioning["server_url"]
    device_id = provisioning["device_id"]
    device_token = provisioning["device_token"]
    try:
        base_url = _secure_server_url(server_url)
    except ValueError as exc:
        parser.error(str(exc))

    captures_by_view = args.capture
    if len(captures_by_view) > 8:
        parser.error("at most eight images are supported by this smoke client")
    declared_views = [view for view, _ in captures_by_view]
    if len(declared_views) != len(set(declared_views)):
        parser.error("Scout smoke view labels must be unique")
    for _, image in captures_by_view:
        if not image.is_file():
            parser.error(f"image does not exist: {image}")

    client_job_id = f"smoke-{uuid4().hex}"
    captures = []
    submitted = []
    files = []
    opened = []
    try:
        for index, (view_code, image) in enumerate(captures_by_view):
            filename = f"capture-{index + 1}{image.suffix.lower()}"
            mime_type = mimetypes.guess_type(image.name)[0] or "application/octet-stream"
            raw_bytes = image.read_bytes()
            handle = io.BytesIO(raw_bytes)
            opened.append(handle)
            files.append(("files", (filename, handle, mime_type)))
            capture = {
                "client_capture_id": f"capture-{uuid4().hex}",
                "filename": filename,
                "view_code": view_code,
                "client_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
            captures.append(capture)
            submitted.append(
                {
                    **capture,
                    "raw_sha256": capture["client_sha256"],
                    "raw_bytes": raw_bytes,
                }
            )
        metadata = {
            "schema_version": "relicscope-scout-job-v2",
            "client_job_id": client_job_id,
            "capture_protocol": "porcelain-v1",
            "analysis_mode": "standard",
            "subject_label": "Scout V2 smoke object",
            "operator_note": "CLI end-to-end smoke test",
            "app_version": "scout-smoke/2.0",
            "device_model": "CLI simulator",
            "captures": captures,
        }
        verify: bool | str = str(args.ca_cert) if args.ca_cert else True
        headers = {
            "X-Scout-Device-ID": device_id,
            "Authorization": f"Bearer {device_token}",
        }
        with httpx.Client(verify=verify, timeout=60) as client:
            response = client.post(
                f"{base_url}/api/v2/scout/jobs",
                headers=headers,
                data={"metadata_json": json.dumps(metadata, ensure_ascii=False)},
                files=files,
            )
            response.raise_for_status()
            accepted = response.json()
            job_id = accepted["job"]["id"]
            deadline = time.monotonic() + args.timeout_seconds
            while time.monotonic() < deadline:
                status_response = client.get(
                    f"{base_url}/api/v2/scout/jobs/{job_id}", headers=headers
                )
                status_response.raise_for_status()
                status = status_response.json()
                print(
                    f"{status['status']:<18} {status['stage']:<24} attempt={status['attempt']}",
                    file=sys.stderr,
                )
                if status["status"] in TERMINAL:
                    if status["status"] != "SUCCEEDED":
                        print(json.dumps({"job": status, "result": None}, ensure_ascii=False, indent=2))
                        return 0 if status["status"] in args.allow_terminal_status else 1
                    result_response = client.get(
                        f"{base_url}/api/v2/scout/jobs/{job_id}/result",
                        headers=headers,
                    )
                    result_response.raise_for_status()
                    envelope = result_response.json()
                    _validate_success_result(
                        envelope,
                        expected_job_id=job_id,
                        metadata=metadata,
                        submitted=submitted,
                    )
                    print(json.dumps(envelope, ensure_ascii=False, indent=2))
                    return 0
                time.sleep(1)
            raise TimeoutError("Scout job did not reach a terminal state")
    finally:
        for handle in opened:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
