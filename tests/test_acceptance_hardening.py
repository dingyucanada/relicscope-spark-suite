from __future__ import annotations

import hashlib
import importlib.util
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


acceptance = _load_script(
    "scripts/spark-live-acceptance.py", "test_spark_live_acceptance"
)


def _valid_run():
    return {
        "status": "SUCCESS",
        "mode": "local_vllm",
        "model": "served-model",
        "configured_model": "served-model",
        "model_profile": "qwen3-vl",
        "model_source": "Qwen/Qwen3-VL-30B-A3B-Instruct",
        "model_revision": "1" * 40,
        "deployment_git_commit": "2" * 40,
        "model_identity_verified": True,
        "provider_request_id": "chatcmpl-test",
        "finish_reason": "stop",
        "template_hash": "3" * 64,
        "input_hash": "4" * 64,
        "output_hash": "5" * 64,
    }


@pytest.mark.parametrize(
    "bad_value",
    [None, 1, ["a" * 64], "a" * 63, "a" * 65, "g" * 64, " " + "a" * 64],
)
def test_exact_sha256_rejects_wrong_type_length_or_alphabet(bad_value):
    with pytest.raises(RuntimeError, match="exact hexadecimal SHA-256"):
        acceptance._require_sha256("test", bad_value)


def test_exact_sha256_accepts_and_normalizes_hex_case():
    assert acceptance._require_sha256("test", "A" * 64) == "a" * 64


@pytest.mark.parametrize(
    "bad_value",
    [None, 1, "main", "unknown", "a" * 7, "a" * 39, "a" * 41, "g" * 40],
)
def test_immutable_revision_rejects_symbolic_short_or_non_string_values(bad_value):
    with pytest.raises(RuntimeError, match="immutable"):
        acceptance._require_immutable_revision("test", bad_value)


def test_real_run_requires_clean_stop_and_all_provenance_fields():
    acceptance._assert_real_run(_valid_run(), "served-model", "qwen3-vl")
    for finish_reason in ("length", "tool_calls", None, "STOP"):
        changed = _valid_run()
        changed["finish_reason"] = finish_reason
        with pytest.raises(RuntimeError, match="finish_reason=stop"):
            acceptance._assert_real_run(changed, "served-model", "qwen3-vl")


def test_real_run_rejects_non_hex_hash_even_when_length_is_64():
    changed = _valid_run()
    changed["output_hash"] = "z" * 64
    with pytest.raises(RuntimeError, match="output_hash"):
        acceptance._assert_real_run(changed, "served-model", "qwen3-vl")


def test_envelope_requires_both_integrity_and_audit_verification():
    valid = {
        "integrity": {"valid": True, "binding_sha256": "a" * 64},
        "audit_verified": True,
    }
    assert acceptance._assert_envelope_integrity(valid)["valid"] is True
    for changed in (
        {**valid, "audit_verified": False},
        {**valid, "integrity": {"valid": False, "binding_sha256": "a" * 64}},
        {**valid, "integrity": {"valid": True, "binding_sha256": "x" * 64}},
    ):
        with pytest.raises(RuntimeError):
            acceptance._assert_envelope_integrity(changed)


def test_health_uses_current_endpoint_identity_field(monkeypatch):
    monkeypatch.setattr(
        acceptance,
        "_call",
        lambda *args, **kwargs: {
            "mode": "single-spark",
            "compute_runtime": {
                "endpoint_identity_ready": True,
                "gpu_access": "VERIFIED_BY_NVIDIA_SMI",
                "dgx_spark_hardware_verified": True,
                "configured_model": "served-model",
            },
        },
    )
    assert (
        acceptance._health("http://127.0.0.1:8088", "served-model")["compute_runtime"][
            "endpoint_identity_ready"
        ]
        is True
    )


def test_repository_synthetic_fixtures_require_both_manifests():
    root = PROJECT_ROOT / "demo_media"
    proof = acceptance._verify_fixture_manifest(
        root / "SHA256SUMS",
        [root / "reference.png", root / "synthetic_orbit.mp4"],
    )
    assert proof["classification"] == "DEMO_SYNTHETIC_MANIFEST_VERIFIED"
    assert proof["contains_real_artifact_media"] is False
    assert all(item["manifest_verified"] for item in proof["files"])


def test_arbitrary_checksum_manifest_cannot_claim_synthetic_fixture(tmp_path):
    media = tmp_path / "video.mp4"
    media.write_bytes(b"not the repository fixture")
    manifest = tmp_path / "SHA256SUMS"
    manifest.write_text(
        f"{hashlib.sha256(media.read_bytes()).hexdigest()}  {media.name}\n",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="fixed synthetic fixture manifest"):
        acceptance._verify_fixture_manifest(manifest, [media])


def test_checksum_manifest_rejects_duplicate_and_traversal_entries(tmp_path):
    digest = "a" * 64
    duplicate = tmp_path / "duplicate"
    duplicate.write_text(f"{digest}  x\n{digest}  x\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="duplicate"):
        acceptance._load_checksum_manifest(duplicate)
    traversal = tmp_path / "traversal"
    traversal.write_text(f"{digest}  ../x\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsafe"):
        acceptance._load_checksum_manifest(traversal)


def test_acceptance_write_emits_exact_sidecar_and_private_permissions(tmp_path):
    output = tmp_path / "acceptance.json"
    acceptance._write(output, {"ok": True})
    raw = output.read_bytes()
    expected = hashlib.sha256(raw).hexdigest()
    sidecar = output.with_name(f"{output.name}.sha256")
    assert sidecar.read_text(encoding="utf-8") == f"{expected}  {output.name}\n"
    assert output.stat().st_mode & 0o777 == 0o600
    assert sidecar.stat().st_mode & 0o777 == 0o600
