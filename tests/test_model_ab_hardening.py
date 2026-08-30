from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script(relative_path: str, module_name: str):
    path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


comparison = _load_script("scripts/compare-model-runs.py", "test_compare_model_runs")


def _fingerprint(gpu_uuid: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "architecture": "aarch64",
                "device_family": "NVIDIA_GB10",
                "gpu_uuids": [gpu_uuid],
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _record(profile: str, *, gpu_uuid: str = "GPU-TEST-GB10"):
    if profile == "qwen3-vl":
        phase = "baseline"
        model = "qwen3_vl_30b_a3b"
        source = "Qwen/Qwen3-VL-30B-A3B-Instruct"
        revision = "1" * 40
        output = {
            "observations": ["可见青花纹饰"],
            "temporal_observations": ["环绕视角覆盖器身"],
            "suggested_regions": [{"label": "R1", "reason": "底足清楚"}],
            "limitations": ["仅限可见信息"],
            "ood_risk": "LOW",
        }
    else:
        phase = "candidate-native-video"
        model = "nemotron_3_nano_omni"
        source = "nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4"
        revision = "2" * 40
        output = {
            "observations": ["Visible blue decoration on a white glazed body."],
            "temporal_observations": ["The orbit reveals the foot and shoulder."],
            "suggested_regions": [{"label": "R1", "reason": "Inspect foot wear."}],
            "limitations": ["Visible-light observation only."],
            "ood_risk": "LOW",
        }
    commit = "f" * 40
    runtime = {
        "model_profile": profile,
        "configured_model": model,
        "model_source": source,
        "model_revision": revision,
        "deployment_git_commit": commit,
        "architecture": "aarch64",
        "device_family": "NVIDIA_GB10",
        "gpu_devices": [{"uuid": gpu_uuid, "name": "NVIDIA GB10"}],
    }
    attestation = {
        "model_profile": profile,
        "served_model": model,
        "model_source": source,
        "model_revision": revision,
        "source_commit": commit,
        "vision_image_id": "sha256:" + "9" * 64,
        "gpu_uuids": [gpu_uuid],
        "hardware_fingerprint_sha256": _fingerprint(gpu_uuid),
        "runtime_networks": [{"name": "private", "internal": True}],
    }
    run = {
        "role": "native_video_multimodal_observation",
        "status": "SUCCESS",
        "mode": "local_vllm",
        "finish_reason": "stop",
        "model_identity_verified": True,
        "provider_request_id": f"request-{profile}",
        "model": model,
        "configured_model": model,
        "model_profile": profile,
        "model_source": source,
        "model_revision": revision,
        "deployment_git_commit": commit,
        "template_hash": "3" * 64,
        "input_hash": "4" * 64,
        "output_hash": "5" * 64,
        "latency_ms": 100,
        "token_usage": {"completion_tokens": 20},
        "output": output,
    }
    return {
        "schema": "relicscope.single-spark-live.v1",
        "phase": phase,
        "profile": profile,
        "expected_model": model,
        "session_id": "RS-ONE",
        "video_id": "VID-ONE",
        "integrity_valid": True,
        "audit_verified": True,
        "evidence_binding_sha256": "6" * 64,
        "acceptance_tool_sha256": "7" * 64,
        "runtime": runtime,
        "host_attestation": attestation,
        "model_runs": [run],
    }


def test_valid_pair_is_machine_eligible_but_never_auto_promoted():
    scorecard = comparison._build_comparison(
        _record("qwen3-vl"), _record("nemotron-omni")
    )
    assert scorecard["promotion_gate"] == {
        **scorecard["promotion_gate"],
        "machine_eligible": True,
        "decision": "EXPERT_REVIEW_REQUIRED",
        "automatic_promotion": False,
        "winner": "UNDECIDED",
    }
    assert scorecard["comparison_design"]["same_gpu_uuid_verified"] is True
    assert (
        "English_original_clarity_and_terminology"
        in scorecard["expert_review_template"]["scores_1_to_5"]
    )


def test_different_gpu_is_a_hold_even_when_each_record_is_internally_valid():
    scorecard = comparison._build_comparison(
        _record("qwen3-vl", gpu_uuid="GPU-A"),
        _record("nemotron-omni", gpu_uuid="GPU-B"),
    )
    assert scorecard["comparison_design"]["same_gpu_uuid_verified"] is False
    assert scorecard["promotion_gate"]["decision"] == "HOLD"


@pytest.mark.parametrize("profile", ["qwen3-vl", "nemotron-omni"])
def test_both_sides_require_clean_stop_integrity_and_audit(profile):
    record = _record(profile)
    record["model_runs"][0]["finish_reason"] = "length"
    args = {
        "expected_phase": record["phase"],
        "expected_profile": profile,
    }
    with pytest.raises(ValueError, match="finish_reason=stop"):
        comparison._metrics(record, **args)

    record = _record(profile)
    record["audit_verified"] = False
    with pytest.raises(ValueError, match="integrity and audit"):
        comparison._metrics(record, **args)


def test_profile_source_revision_and_gpu_fingerprint_must_agree_across_layers():
    record = _record("nemotron-omni")
    record["host_attestation"]["model_revision"] = "unknown"
    with pytest.raises(ValueError, match="immutable"):
        comparison._metrics(
            record,
            expected_phase="candidate-native-video",
            expected_profile="nemotron-omni",
        )

    record = _record("nemotron-omni")
    record["host_attestation"]["hardware_fingerprint_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="hardware fingerprint"):
        comparison._metrics(
            record,
            expected_phase="candidate-native-video",
            expected_profile="nemotron-omni",
        )


def test_language_metrics_count_nested_prose_values_but_not_schema_keys_or_enums():
    metrics = comparison._language_metrics(
        {
            "中文字段名": "ignored because field is outside the prose allowlist",
            "observations": ["青花"],
            "temporal_observations": ["blue"],
            "suggested_regions": [],
            "limitations": [],
            "ood_risk": "LOW",
        }
    )
    assert metrics == {
        "cjk_characters": 2,
        "latin_characters": 4,
        "cjk_share": 0.3333,
    }


def test_acceptance_loader_requires_and_verifies_exact_sidecar(tmp_path):
    path = tmp_path / "baseline.json"
    raw = (json.dumps(_record("qwen3-vl"), ensure_ascii=False) + "\n").encode()
    path.write_bytes(raw)
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n", encoding="utf-8"
    )
    assert comparison._load(path)["profile"] == "qwen3-vl"

    path.write_bytes(raw + b" ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        comparison._load(path)


def test_scorecard_writer_emits_checksum_sidecar(tmp_path):
    path = tmp_path / "scorecard.json"
    comparison._write_with_sidecar(path, {"ok": True})
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.with_name(f"{path.name}.sha256").read_text() == (
        f"{expected}  {path.name}\n"
    )
