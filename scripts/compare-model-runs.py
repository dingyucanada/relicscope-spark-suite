#!/usr/bin/env python3
"""Compare frozen-input native-video runs without auto-promoting a winner."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable


SHA256_RE = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
IMMUTABLE_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.IGNORECASE)
EXPECTED_PROFILE_SOURCES = {
    "qwen3-vl": "Qwen/Qwen3-VL-30B-A3B-Instruct",
    "nemotron-omni": ("nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4"),
}


def _load(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    sidecar = path.with_name(f"{path.name}.sha256")
    if not sidecar.is_file():
        raise ValueError(f"acceptance checksum sidecar is missing: {sidecar}")
    sidecar_text = sidecar.read_text(encoding="utf-8")
    match = re.fullmatch(
        rf"([0-9a-fA-F]{{64}})  {re.escape(path.name)}\n", sidecar_text
    )
    if match is None:
        raise ValueError(f"invalid acceptance checksum sidecar: {sidecar}")
    expected = _require_sha256("acceptance file sidecar", match.group(1))
    actual = hashlib.sha256(raw).hexdigest()
    if expected != actual:
        raise ValueError(f"acceptance checksum mismatch: {path}")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _require_sha256(label: str, value: Any) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not an exact hexadecimal SHA-256 digest")
    return value.lower()


def _require_immutable_revision(label: str, value: Any) -> str:
    if not isinstance(value, str) or IMMUTABLE_REVISION_RE.fullmatch(value) is None:
        raise ValueError(f"{label} is not an immutable 40- or 64-hex commit revision")
    return value.lower()


def _native_run(value: dict[str, Any]) -> dict[str, Any]:
    runs = [
        run
        for run in value.get("model_runs", [])
        if run.get("role") == "native_video_multimodal_observation"
    ]
    if len(runs) != 1:
        raise ValueError("acceptance result must contain exactly one native-video run")
    return runs[0]


def _string_values(value: Any) -> Iterable[str]:
    """Yield natural-language values only; JSON field names are not model prose."""

    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _string_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _string_values(item)


def _language_metrics(output: dict[str, Any]) -> dict[str, Any]:
    natural_language_fields = (
        "observations",
        "temporal_observations",
        "suggested_regions",
        "limitations",
    )
    text = "\n".join(
        text
        for field in natural_language_fields
        for text in _string_values(output.get(field))
    )
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return {
        "cjk_characters": cjk,
        "latin_characters": latin,
        "cjk_share": round(cjk / max(cjk + latin, 1), 4),
    }


def _runtime_gpu_uuids(runtime: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(item.get("uuid") or "").strip()
            for item in runtime.get("gpu_devices", [])
            if str(item.get("uuid") or "").strip()
        }
    )


def _metrics(
    value: dict[str, Any], *, expected_phase: str, expected_profile: str
) -> dict[str, Any]:
    if value.get("schema") != "relicscope.single-spark-live.v1":
        raise ValueError("unsupported or missing acceptance schema")
    if value.get("phase") != expected_phase:
        raise ValueError(
            f"acceptance phase mismatch: expected {expected_phase}, "
            f"got {value.get('phase')}"
        )
    if value.get("profile") != expected_profile:
        raise ValueError(
            f"acceptance profile mismatch: expected {expected_profile}, "
            f"got {value.get('profile')}"
        )
    if (
        value.get("integrity_valid") is not True
        or value.get("audit_verified") is not True
    ):
        raise ValueError(
            "acceptance evidence did not pass integrity and audit verification"
        )
    evidence_binding = _require_sha256(
        "acceptance evidence binding", value.get("evidence_binding_sha256")
    )
    acceptance_tool_sha256 = _require_sha256(
        "acceptance tool hash", value.get("acceptance_tool_sha256")
    )

    run = _native_run(value)
    output = run.get("output")
    if not isinstance(output, dict):
        raise ValueError("native-video run is missing a structured output object")
    if run.get("status") != "SUCCESS" or run.get("mode") != "local_vllm":
        raise ValueError("native-video run was not a successful local vLLM execution")
    if run.get("finish_reason") != "stop":
        raise ValueError(
            "native-video completion did not finish with finish_reason=stop"
        )
    if run.get("model_identity_verified") is not True:
        raise ValueError("native-video completion did not verify model identity")
    if (
        not isinstance(run.get("provider_request_id"), str)
        or not run["provider_request_id"].strip()
    ):
        raise ValueError("native-video completion is missing its request identifier")
    input_hash = _require_sha256("native-video input hash", run.get("input_hash"))
    output_hash = _require_sha256("native-video output hash", run.get("output_hash"))
    _require_sha256("native-video template hash", run.get("template_hash"))

    expected_model = str(value.get("expected_model") or "")
    if not expected_model:
        raise ValueError("acceptance record is missing expected_model")
    model = str(run.get("model") or "")
    if model != expected_model or run.get("configured_model") != expected_model:
        raise ValueError("acceptance record and native-video served model disagree")

    runtime = value.get("runtime") or {}
    attestation = value.get("host_attestation") or {}
    expected_source = EXPECTED_PROFILE_SOURCES[expected_profile]
    profile_values = {
        value.get("profile"),
        run.get("model_profile"),
        runtime.get("model_profile"),
        attestation.get("model_profile"),
    }
    if profile_values != {expected_profile}:
        raise ValueError("acceptance layers disagree about the model profile")
    model_values = {
        expected_model,
        model,
        run.get("configured_model"),
        runtime.get("configured_model"),
        attestation.get("served_model"),
    }
    if model_values != {expected_model}:
        raise ValueError("acceptance layers disagree about the served model alias")
    source_values = {
        run.get("model_source"),
        runtime.get("model_source"),
        attestation.get("model_source"),
    }
    if source_values != {expected_source}:
        raise ValueError(
            f"{expected_profile} evidence is not bound to the expected model source"
        )

    model_revision = _require_immutable_revision(
        "native-video model revision", run.get("model_revision")
    )
    revision_values = {
        model_revision,
        _require_immutable_revision(
            "runtime model revision", runtime.get("model_revision")
        ),
        _require_immutable_revision(
            "attested model revision", attestation.get("model_revision")
        ),
    }
    if revision_values != {model_revision}:
        raise ValueError(
            "acceptance layers disagree about the immutable model revision"
        )

    source_commit = _require_immutable_revision(
        "native-video deployment commit", run.get("deployment_git_commit")
    )
    source_commits = {
        source_commit,
        _require_immutable_revision(
            "runtime deployment commit", runtime.get("deployment_git_commit")
        ),
        _require_immutable_revision(
            "attested source commit", attestation.get("source_commit")
        ),
    }
    if source_commits != {source_commit}:
        raise ValueError(
            "acceptance layers disagree about the application source commit"
        )

    gpu_uuids = _runtime_gpu_uuids(runtime)
    if not gpu_uuids or attestation.get("gpu_uuids") != gpu_uuids:
        raise ValueError(
            "acceptance layers did not bind the same non-empty GPU UUID set"
        )
    hardware_fingerprint = _require_sha256(
        "hardware fingerprint", attestation.get("hardware_fingerprint_sha256")
    )
    expected_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "architecture": runtime.get("architecture"),
                "device_family": runtime.get("device_family"),
                "gpu_uuids": gpu_uuids,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if hardware_fingerprint != expected_fingerprint:
        raise ValueError("hardware fingerprint does not match the runtime GPU evidence")

    runtime_networks = attestation.get("runtime_networks") or []
    network_egress_blocked = bool(runtime_networks) and all(
        item.get("internal") is True for item in runtime_networks
    )
    if not network_egress_blocked:
        raise ValueError("model runtime network was not attested as internal-only")
    vision_image_id = attestation.get("vision_image_id")
    if (
        not isinstance(vision_image_id, str)
        or not vision_image_id.startswith("sha256:")
        or SHA256_RE.fullmatch(vision_image_id.removeprefix("sha256:")) is None
    ):
        raise ValueError("vision runtime image is not bound to an immutable image ID")

    return {
        "session_id": value.get("session_id"),
        "video_id": value.get("video_id"),
        "profile": expected_profile,
        "model": model,
        "model_source": expected_source,
        "model_revision": model_revision,
        "source_commit": source_commit,
        "vision_image_id": vision_image_id,
        "gpu_uuids": gpu_uuids,
        "hardware_fingerprint_sha256": hardware_fingerprint,
        "network_egress_blocked": True,
        "integrity_valid": True,
        "audit_verified": True,
        "evidence_binding_sha256": evidence_binding,
        "acceptance_tool_sha256": acceptance_tool_sha256,
        "status": run.get("status"),
        "mode": run.get("mode"),
        "finish_reason": run.get("finish_reason"),
        "model_identity_verified": True,
        "provider_request_id_present": True,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "latency_ms": run.get("latency_ms"),
        "token_usage": run.get("token_usage", {}),
        "observation_count": len(output.get("observations", [])),
        "temporal_observation_count": len(output.get("temporal_observations", [])),
        "suggested_region_count": len(output.get("suggested_regions", [])),
        "limitation_count": len(output.get("limitations", [])),
        "ood_risk": output.get("ood_risk"),
        "language": _language_metrics(output),
        "output": output,
    }


def _build_comparison(
    baseline_value: dict[str, Any], candidate_value: dict[str, Any]
) -> dict[str, Any]:
    baseline = _metrics(
        baseline_value, expected_phase="baseline", expected_profile="qwen3-vl"
    )
    candidate = _metrics(
        candidate_value,
        expected_phase="candidate-native-video",
        expected_profile="nemotron-omni",
    )
    same_input = baseline["input_hash"] == candidate["input_hash"]
    same_session = bool(
        baseline["session_id"] and baseline["session_id"] == candidate["session_id"]
    )
    same_video = bool(
        baseline["video_id"] and baseline["video_id"] == candidate["video_id"]
    )
    same_source_commit = baseline["source_commit"] == candidate["source_commit"]
    same_acceptance_tool = (
        baseline["acceptance_tool_sha256"] == candidate["acceptance_tool_sha256"]
    )
    same_runtime_image = bool(
        baseline["vision_image_id"]
        and baseline["vision_image_id"] == candidate["vision_image_id"]
    )
    same_gpu = baseline["gpu_uuids"] == candidate["gpu_uuids"]
    same_hardware_fingerprint = (
        baseline["hardware_fingerprint_sha256"]
        == candidate["hardware_fingerprint_sha256"]
    )
    distinct_model_builds = (
        baseline["profile"] != candidate["profile"]
        and baseline["model"] != candidate["model"]
        and (baseline["model_source"], baseline["model_revision"])
        != (candidate["model_source"], candidate["model_revision"])
    )
    candidate_machine_eligible = all(
        [
            same_input,
            same_session,
            same_video,
            same_source_commit,
            same_acceptance_tool,
            same_runtime_image,
            same_gpu,
            same_hardware_fingerprint,
            distinct_model_builds,
            candidate["temporal_observation_count"] >= 1,
            candidate["limitation_count"] >= 1,
            candidate["ood_risk"] in {"LOW", "MEDIUM", "HIGH"},
        ]
    )
    return {
        "schema": "relicscope.model-ab-scorecard.v1",
        "comparison_design": {
            "same_input_required": True,
            "same_input_verified": same_input,
            "same_session_verified": same_session,
            "same_video_record_verified": same_video,
            "same_application_source_commit": same_source_commit,
            "same_acceptance_tool": same_acceptance_tool,
            "same_vllm_runtime_image": same_runtime_image,
            "same_gpu_uuid_required": True,
            "same_gpu_uuid_verified": same_gpu,
            "same_hardware_fingerprint_verified": same_hardware_fingerprint,
            "distinct_model_builds_verified": distinct_model_builds,
            "execution": "SEQUENTIAL_SINGLE_DGX_SPARK",
            "simultaneous_model_residency": False,
            "baseline_role": "Chinese ceramic image/video baseline",
            "candidate_role": "English-first native-video analysis candidate",
        },
        "baseline": baseline,
        "candidate": candidate,
        "promotion_gate": {
            "machine_eligible": candidate_machine_eligible,
            "decision": (
                "EXPERT_REVIEW_REQUIRED" if candidate_machine_eligible else "HOLD"
            ),
            "automatic_promotion": False,
            "winner": "UNDECIDED",
            "required_expert_dimensions": [
                "可见事实忠实度",
                "跨视角与时间信息增益",
                "英文原始输出的清晰度与术语准确性",
                "可见观察、假设与限制的分离",
                "禁限结论与幻觉风险",
                "候选区域和下一步建议的可操作性",
            ],
            "promotion_rule": (
                "候选模型先通过同输入、同设备、不可变版本、结构化输出、"
                "边界和可追溯性门槛；再由至少一名文物/材料专家和一名"
                "模型工程负责人签字。"
            ),
        },
        "expert_review_template": {
            "approved": False,
            "reviewers": [],
            "candidate_language_policy": (
                "保留 Nemotron 英文原始输出；如需中文，翻译必须作为独立、"
                "可追溯的模型运行，不得覆盖原始证据。"
            ),
            "scores_1_to_5": {
                "visible_fact_fidelity": None,
                "temporal_information_gain": None,
                "English_original_clarity_and_terminology": None,
                "observation_hypothesis_separation": None,
                "boundary_safety": None,
                "actionability": None,
            },
            "critical_errors": [],
            "notes": "",
        },
    }


def _write_with_sidecar(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_text(rendered, encoding="utf-8")
    path.chmod(0o600)
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    sidecar.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = _build_comparison(_load(args.baseline), _load(args.candidate))
    _write_with_sidecar(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
