#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import re
import stat
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.vlm import (  # noqa: E402
    SCOUT_OBSERVATION_INSTRUCTION,
    OpenAICompatibleClient,
    build_scout_multi_view_payload,
    model_output_hash,
    validate_scout_multi_view_output,
)


IMMUTABLE_COMMIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
IMMUTABLE_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
VIEW_CODES = (
    "FRONT",
    "BACK",
    "BASE",
    "LEFT_PROFILE",
    "TOP",
    "RIGHT_PROFILE",
    "DETAIL",
    "MARK",
)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _secure_text(path: Path, *, label: str, maximum: int) -> str:
    selected = path.expanduser()
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif selected.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    descriptor = os.open(selected, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{label} must be a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError(f"{label} permissions must be exactly 0600")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise ValueError(f"{label} must be owned by the current user")
        if info.st_size <= 0 or info.st_size > maximum:
            raise ValueError(f"{label} size is invalid")
        raw = os.read(descriptor, maximum + 1)
    finally:
        os.close(descriptor)
    return raw.decode("utf-8").strip()


def _runtime_binding(path: Path) -> dict[str, str]:
    raw = _secure_text(path, label="runtime manifest", maximum=64 * 1024)
    values: dict[str, list[str]] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), []).append(value.strip())

    def one(name: str) -> str:
        candidates = values.get(name, [])
        if len(candidates) != 1 or not candidates[0]:
            raise ValueError(f"runtime manifest requires exactly one {name}")
        return candidates[0]

    source_commit = one("source_commit").lower()
    model = one("model")
    revision = one("model_revision").lower()
    image_candidates = values.get("vllm_image") or values.get("container_image") or []
    runtime_image = image_candidates[0] if image_candidates else ""
    if not IMMUTABLE_COMMIT.fullmatch(source_commit):
        raise ValueError("runtime manifest source_commit is not immutable")
    if not IMMUTABLE_COMMIT.fullmatch(revision):
        raise ValueError("runtime manifest model_revision is not immutable")
    if not IMMUTABLE_IMAGE.fullmatch(runtime_image):
        raise ValueError("runtime manifest VLM image is not digest-pinned")
    return {
        "deployment_git_commit": source_commit,
        "model_profile": (values.get("model_profile") or ["unknown"])[0],
        "model": model,
        "model_source": model,
        "model_revision": revision,
        "runtime_image": runtime_image,
        "runtime_manifest_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "attestation": "OPERATOR_ASSERTED_FROM_LOCAL_PREPARATION_MANIFEST",
    }


def _model_image(path: Path, expected_sha256: str) -> tuple[str, str]:
    source_bytes = path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != expected_sha256:
        raise ValueError("benchmark source image changed while preparing the request")
    with Image.open(io.BytesIO(source_bytes)) as source:
        clean = ImageOps.exif_transpose(source).convert("RGB")
        clean.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        clean.save(buffer, format="JPEG", quality=90, optimize=True)
    payload = buffer.getvalue()
    return (
        "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii"),
        hashlib.sha256(payload).hexdigest(),
    )


def _views(value: str | None, count: int, parser: argparse.ArgumentParser) -> list[str]:
    if value is None:
        return list(VIEW_CODES[:count])
    selected = [item.strip().upper() for item in value.split(",") if item.strip()]
    if len(selected) != count:
        parser.error("--views must contain one comma-separated view per image")
    if any(item not in VIEW_CODES for item in selected):
        parser.error(f"--views may contain only: {', '.join(VIEW_CODES)}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the exact production Scout observation contract against one VLM"
    )
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--runtime-manifest", required=True, type=Path)
    parser.add_argument("--api-key-file", required=True, type=Path)
    parser.add_argument("--ca-cert", type=Path)
    parser.add_argument("--views", help="comma-separated Scout views in image order")
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    parsed_url = urlsplit(args.base_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.query
        or parsed_url.fragment
        or parsed_url.path not in {"", "/"}
    ):
        parser.error("--base-url must be an HTTPS origin without credentials or a path")
    if not 1 <= len(args.images) <= 8:
        parser.error("provide between one and eight images")
    if not 1 <= args.repeats <= 100:
        parser.error("--repeats must be between one and 100")
    for path in args.images:
        if not path.is_file():
            parser.error(f"image does not exist: {path}")
    selected_views = _views(args.views, len(args.images), parser)
    if args.ca_cert is not None and not args.ca_cert.is_file():
        parser.error("--ca-cert does not exist")

    try:
        binding = _runtime_binding(args.runtime_manifest)
        api_key = _secure_text(
            args.api_key_file, label="VLM API key", maximum=16 * 1024
        )
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    if len(api_key) < 32:
        parser.error("VLM API key is invalid")

    input_manifest = []
    model_images = []
    for index, (path, view_code) in enumerate(
        zip(args.images, selected_views, strict=True), start=1
    ):
        raw_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        data_url, model_input_sha256 = _model_image(path, raw_sha256)
        capture_id = f"benchmark-{index:02d}-{raw_sha256[:12]}"
        input_manifest.append(
            {
                "capture_id": capture_id,
                "filename": path.name,
                "view_code": view_code,
                "source_sha256": raw_sha256,
                "sanitized_model_input_sha256": model_input_sha256,
            }
        )
        model_images.append(
            {
                "capture_id": capture_id,
                "view_code": view_code,
                "image_data_url": data_url,
            }
        )

    benchmark_id = "benchmark-" + hashlib.sha256(
        _canonical_json(input_manifest).encode("utf-8")
    ).hexdigest()[:24]
    payload, allowed_captures = build_scout_multi_view_payload(
        binding["model"],
        model_images,
        {
            "job_id": benchmark_id,
            "operator_metadata": {
                "subject_label": "BENCHMARK_OBJECT",
                "source": "OPERATOR_SUPPLIED",
                "verification_status": "UNVERIFIED",
            },
            "instruction": SCOUT_OBSERVATION_INSTRUCTION,
        },
    )
    payload_sha256 = hashlib.sha256(
        _canonical_json(payload).encode("utf-8")
    ).hexdigest()
    verify: bool | str = str(args.ca_cert) if args.ca_cert else True
    endpoint = args.base_url.rstrip("/") + "/v1/chat/completions"
    runs = []
    with httpx.Client(timeout=300, verify=verify) as client:
        for index in range(args.repeats):
            started = time.perf_counter()
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            response.raise_for_status()
            body = response.json()
            response_model, request_id = OpenAICompatibleClient._completion_identity(
                body, response.headers, binding["model"]
            )
            choices = body.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise RuntimeError("completion must return exactly one choice")
            choice = choices[0]
            if choice.get("finish_reason") != "stop":
                raise RuntimeError("completion did not finish with a complete JSON answer")
            content_text = choice.get("message", {}).get("content")
            if not isinstance(content_text, str):
                raise RuntimeError("completion content is missing")
            parsed = OpenAICompatibleClient._parse_json_content(content_text)
            validated = validate_scout_multi_view_output(parsed, allowed_captures)
            runs.append(
                {
                    "run": index + 1,
                    "latency_ms": latency_ms,
                    "request_id": request_id,
                    "model": response_model,
                    "finish_reason": choice["finish_reason"],
                    "usage": body.get("usage"),
                    "output_sha256": model_output_hash(validated),
                    "validated_output": validated,
                }
            )

    latencies = [item["latency_ms"] for item in runs]
    ordered = sorted(latencies)
    p95_index = max(
        0, min(len(ordered) - 1, int(round(0.95 * len(ordered) + 0.5)) - 1)
    )
    result = {
        "schema_version": "relicscope-vlm-benchmark-v2",
        "benchmark_id": benchmark_id,
        "endpoint": args.base_url,
        "runtime_binding": binding,
        "endpoint_attestation": {
            "https_origin": args.base_url,
            "ca_certificate_sha256": (
                hashlib.sha256(args.ca_cert.read_bytes()).hexdigest()
                if args.ca_cert
                else None
            ),
            "response_model_verified": True,
            "runtime_revision_remotely_attested": False,
        },
        "request_payload_sha256": payload_sha256,
        "input_manifest": input_manifest,
        "repeats": args.repeats,
        "latency_ms": {
            "min": min(latencies),
            "median": int(statistics.median(latencies)),
            "p95_observed": ordered[p95_index],
            "max": max(latencies),
        },
        "runs": runs,
        "contract": {
            "production_prompt": True,
            "production_model_options": True,
            "json_schema_validated": True,
            "model_identity_verified": True,
            "request_ids_required": True,
        },
        "limitations": [
            "Measures end-to-end non-streaming request latency on the supplied inputs.",
            "Does not measure TTFT, peak unified memory, concurrency, or domain accuracy.",
            "Promotion still requires a frozen expert-scored evaluation set.",
        ],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
