from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import stat
from pathlib import Path

import pytest
from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, PROJECT_ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SMOKE = _load_script("relicscope_scout_smoke", "scout-smoke.py")
DEVICE = _load_script("relicscope_scout_device", "scout-device.py")
BENCHMARK = _load_script("relicscope_scout_benchmark", "benchmark-scout-vlm.py")


class _FakeDeviceStore:
    def __init__(self) -> None:
        self.enroll_calls = 0
        self.enrollment_enabled: list[bool] = []
        self.enabled_changes: list[tuple[str, bool]] = []

    def enroll_device(self, name: str, *, enabled: bool = True) -> dict:
        self.enroll_calls += 1
        self.enrollment_enabled.append(enabled)
        return {
            "device_id": "scout-test-device",
            "device_token": "t" * 43,
            "token_display_policy": "SHOW_ONCE",
            "name": name,
        }

    def set_device_enabled(self, device_id: str, enabled: bool) -> None:
        self.enabled_changes.append((device_id, enabled))


def _provisioning() -> dict:
    return {
        "schema_version": "relicscope-scout-provisioning-v2",
        "server_url": "https://spark.local",
        "device_id": "scout-test-device",
        "device_token": "t" * 43,
    }


def test_smoke_accepts_only_owned_regular_0600_provisioning(tmp_path):
    path = tmp_path / "scout.json"
    path.write_text(json.dumps(_provisioning()), encoding="utf-8")
    path.chmod(0o600)
    assert SMOKE._read_secure_provisioning(path) == {
        "server_url": "https://spark.local",
        "device_id": "scout-test-device",
        "device_token": "t" * 43,
    }

    path.chmod(0o640)
    with pytest.raises(ValueError, match="0600"):
        SMOKE._read_secure_provisioning(path)

    path.chmod(0o600)
    link = tmp_path / "linked.json"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="cannot read|regular file"):
        SMOKE._read_secure_provisioning(link)


def test_enrollment_reserves_output_before_database_change(tmp_path):
    output = tmp_path / "already-exists.json"
    output.write_text("occupied", encoding="utf-8")
    store = _FakeDeviceStore()

    with pytest.raises(FileExistsError):
        DEVICE._enroll_to_file(
            store,
            name="Scout",
            server_url="https://spark.local",
            output=output,
        )
    assert store.enroll_calls == 0
    assert output.read_text(encoding="utf-8") == "occupied"


def test_enrollment_disables_device_when_credential_write_fails(
    tmp_path, monkeypatch
):
    output = tmp_path / "scout.json"
    store = _FakeDeviceStore()

    def fail_write(descriptor: int, value: dict) -> None:
        os.close(descriptor)
        raise OSError("simulated durable-write failure")

    monkeypatch.setattr(DEVICE, "_write_reserved", fail_write)
    with pytest.raises(OSError, match="durable-write"):
        DEVICE._enroll_to_file(
            store,
            name="Scout",
            server_url="https://spark.local",
            output=output,
        )
    assert store.enroll_calls == 1
    assert store.enrollment_enabled == [False]
    assert store.enabled_changes == [("scout-test-device", False)]
    assert not output.exists()


def test_enrollment_writes_credential_with_exact_private_mode(tmp_path):
    output = tmp_path / "scout.json"
    store = _FakeDeviceStore()
    record = DEVICE._enroll_to_file(
        store,
        name="Scout",
        server_url="https://spark.local",
        output=output,
    )
    assert record["device_id"] == "scout-test-device"
    assert store.enrollment_enabled == [False]
    assert store.enabled_changes == [("scout-test-device", True)]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))["device_token"] == "t" * 43


def _image_bytes() -> bytes:
    image = Image.new("RGB", (960, 960), (234, 231, 220))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def test_smoke_model_image_matches_gateway_data_url_contract(tmp_path):
    from app.scout.service import ScoutService

    raw_bytes = _image_bytes()
    source = tmp_path / "source.jpg"
    source.write_bytes(raw_bytes)
    expected_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    smoke_url, smoke_hash = SMOKE._sanitize_model_image(raw_bytes)
    gateway_url, gateway_hash = ScoutService._model_image(source, expected_sha256)

    assert smoke_url.startswith("data:image/jpeg;base64,")
    assert smoke_url == gateway_url
    assert smoke_hash == gateway_hash


def test_benchmark_requires_and_records_immutable_runtime_binding(tmp_path):
    manifest = tmp_path / "runtime-manifest.txt"
    manifest.write_text(
        "\n".join(
            [
                f"source_commit={'a' * 40}",
                "model_profile=nemotron3-nano-omni",
                "model=nvidia/Nemotron-3-Nano-Omni-30B-A3B-Reasoning-NVFP4",
                f"model_revision={'b' * 40}",
                f"container_image=vllm/vllm-openai@sha256:{'c' * 64}",
                f"container_image=caddy@sha256:{'d' * 64}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    manifest.chmod(0o600)

    binding = BENCHMARK._runtime_binding(manifest)

    assert binding["model_revision"] == "b" * 40
    assert binding["runtime_image"] == f"vllm/vllm-openai@sha256:{'c' * 64}"
    assert binding["attestation"] == (
        "OPERATOR_ASSERTED_FROM_LOCAL_PREPARATION_MANIFEST"
    )
    assert len(binding["runtime_manifest_sha256"]) == 64

    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("@sha256:", ":latest#"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="digest-pinned"):
        BENCHMARK._runtime_binding(manifest)


def _proof_fixture() -> tuple[dict, dict, list[dict]]:
    raw_bytes = _image_bytes()
    raw_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    metadata = {
        "schema_version": "relicscope-scout-job-v2",
        "client_job_id": "smoke-test-0001",
        "capture_protocol": "porcelain-v1",
        "analysis_mode": "standard",
        "subject_label": "Smoke object",
        "operator_note": "test",
        "app_version": "scout-smoke/2.0",
        "device_model": "CLI simulator",
        "captures": [
            {
                "client_capture_id": "capture-test-0001",
                "filename": "capture-1.jpg",
                "view_code": "FRONT",
                "client_sha256": raw_sha256,
                "captured_at": "2026-09-01T00:00:00+00:00",
            }
        ],
    }
    submitted = [
        {
            **metadata["captures"][0],
            "raw_sha256": raw_sha256,
            "raw_bytes": raw_bytes,
        }
    ]
    capture_id = "cap-server-0001"
    data_url, sanitized_hash = SMOKE._sanitize_model_image(raw_bytes)
    model = "Qwen/Qwen3-VL-30B-A3B-Instruct-FP8"
    context = {
        "job_id": "job-server-0001",
        "operator_metadata": {
            "subject_label": metadata["subject_label"],
            "source": "OPERATOR_SUPPLIED",
            "verification_status": "UNVERIFIED",
        },
        "instruction": SMOKE.SCOUT_OBSERVATION_INSTRUCTION,
    }
    content = [
        {
            "type": "text",
            "text": (
                "Inspect all views as one object-observation set. Bind every per-view "
                "observation to the exact capture identifier and declared view. "
                "Context metadata: "
                + json.dumps(context, ensure_ascii=False, sort_keys=True)
            ),
        },
        {
            "type": "text",
            "text": f"capture_id={capture_id}; view_code=FRONT",
        },
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SMOKE.SCOUT_MULTI_VIEW_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.0,
        "max_tokens": 1200,
        "response_format": SMOKE.build_scout_multi_view_payload(
            model,
            [
                {
                    "capture_id": capture_id,
                    "view_code": "FRONT",
                    "image_data_url": data_url,
                }
            ],
            context,
        )[0]["response_format"],
    }
    model_output = {
        "observations": [
            {"capture_id": capture_id, "view_code": "FRONT", "text": "可见白色釉面。"}
        ],
        "cross_view_observations": [],
        "limitations": ["仅有一个视角。"],
        "capture_issues": [],
        "ood_risk": "LOW",
    }
    output_hash = SMOKE._sha256_json(model_output, compact=False)
    normalized_metadata = SMOKE.ScoutJobMetadata.model_validate(metadata).model_dump(
        mode="json"
    )
    input_hash = SMOKE._sha256_json(
        {
            "metadata": normalized_metadata,
            "files": [
                {
                    "client_capture_id": "capture-test-0001",
                    "filename": "capture-1.jpg",
                    "view_code": "FRONT",
                    "sha256": raw_sha256,
                    "byte_count": len(raw_bytes),
                }
            ],
        },
        compact=True,
    )
    result = {
        "schema_version": "relicscope-scout-result-v2",
        "job_id": "job-server-0001",
        "subject_label": "Smoke object",
        "subject_label_provenance": "OPERATOR_SUPPLIED_UNVERIFIED",
        "operator_metadata": {
            "subject_label": "Smoke object",
            "operator_note": "test",
            "source": "OPERATOR_SUPPLIED",
            "verification_status": "UNVERIFIED",
            "used_as_model_conclusion": False,
        },
        "analysis_mode": "standard",
        "completed_at": "2026-09-01T00:00:01+00:00",
        "authenticity_state": "NOT_ASSESSED",
        "compute_provenance": {
            "node_id": "spark-a",
            "compute_node_id": "spark-a",
            "runtime_mode": "single-spark",
            "deployment_git_commit": "d" * 40,
            "input_payload_sha256": input_hash,
        },
        "capture_assessment": {
            "records": [
                {
                    "capture_id": capture_id,
                    "view_code": "FRONT",
                    "sha256": raw_sha256,
                }
            ]
        },
        "visible_observations": [
            {
                **model_output["observations"][0],
                "model_output_sha256": output_hash,
            }
        ],
        "cross_view_observations": [],
        "model_limitations": ["仅有一个视角。"],
        "model_capture_issues": [],
        "model_ood_risk": "LOW",
        "model_runs": [
            {
                "available": True,
                "mode": "local_vllm",
                "role": "scout_multi_view",
                "model": model,
                "configured_model": model,
                "model_source": model,
                "model_identity_verified": True,
                "model_identity_verification_scope": "provider_response_name_match",
                "runtime_attestation_scope": "configuration_bound_application_receipt",
                "runtime_image": "nvcr.io/nvidia/vllm@sha256:" + "a" * 64,
                "model_revision": "b" * 40,
                "request_id": "chatcmpl-test",
                "system_prompt_hash": hashlib.sha256(
                    SMOKE.SCOUT_MULTI_VIEW_SYSTEM_PROMPT.encode("utf-8")
                ).hexdigest(),
                "request_payload_hash": SMOKE._sha256_json(
                    request_payload, compact=True
                ),
                "output_hash": output_hash,
                "source_inputs": [
                    {
                        "capture_id": capture_id,
                        "view_code": "FRONT",
                        "source_sha256": raw_sha256,
                        "sanitized_model_input_sha256": sanitized_hash,
                    }
                ],
            }
        ],
    }
    result["result_sha256"] = SMOKE._sha256_json(result, compact=True)
    return {"result": result}, metadata, submitted


def _reseal(envelope: dict) -> None:
    result = envelope["result"]
    result["result_sha256"] = SMOKE._sha256_json(
        {key: value for key, value in result.items() if key != "result_sha256"},
        compact=True,
    )


def test_smoke_recomputes_complete_input_model_output_and_result_proof():
    envelope, metadata, submitted = _proof_fixture()
    SMOKE._validate_success_result(
        envelope,
        expected_job_id="job-server-0001",
        metadata=metadata,
        submitted=submitted,
    )


def test_smoke_accepts_nim_completion_with_explicit_profile_provenance():
    envelope, metadata, submitted = _proof_fixture()
    run = envelope["result"]["model_runs"][0]
    run.update(
        {
            "mode": "local_nim",
            "runtime_provider": "nvidia_nim",
            "model_artifact_kind": "nim_profile",
            "model_artifact_id": run["model_revision"],
            "runtime_image": "nvcr.io/nim/qwen/qwen3.6-35b-a3b@sha256:"
            + "a" * 64,
        }
    )
    _reseal(envelope)

    SMOKE._validate_success_result(
        envelope,
        expected_job_id="job-server-0001",
        metadata=metadata,
        submitted=submitted,
    )

    source_tampered = copy.deepcopy(envelope)
    source_tampered["result"]["model_runs"][0]["source_inputs"][0][
        "source_sha256"
    ] = "f" * 64
    _reseal(source_tampered)
    with pytest.raises(RuntimeError, match="submitted image bytes"):
        SMOKE._validate_success_result(
            source_tampered,
            expected_job_id="job-server-0001",
            metadata=metadata,
            submitted=submitted,
        )

    output_tampered = copy.deepcopy(envelope)
    output_tampered["result"]["visible_observations"][0]["text"] = "tampered"
    _reseal(output_tampered)
    with pytest.raises(RuntimeError, match="model output hash"):
        SMOKE._validate_success_result(
            output_tampered,
            expected_job_id="job-server-0001",
            metadata=metadata,
            submitted=submitted,
        )
