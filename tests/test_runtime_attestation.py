from __future__ import annotations

from app.services import runtime


def _patch_gpu(monkeypatch, *, host_model: str):
    monkeypatch.setattr(
        runtime,
        "_gpu_inventory",
        lambda: (
            "VERIFIED_BY_NVIDIA_SMI",
            [
                {
                    "name": "NVIDIA GB10",
                    "driver_version": "test-driver",
                    "uuid": "GPU-test",
                }
            ],
            None,
        ),
    )
    monkeypatch.setattr(runtime, "_read_text", lambda _path: host_model)
    monkeypatch.setattr(runtime.platform, "machine", lambda: "aarch64")


def test_dgx_spark_attestation_requires_host_product_identity(monkeypatch):
    _patch_gpu(monkeypatch, host_model="Generic ARM64 GB10 workstation")

    snapshot = runtime.runtime_snapshot(
        node_id="spark-single",
        runtime_mode="single-spark",
    )

    assert snapshot["gb10_arm64_verified"] is True
    assert snapshot["dgx_spark_hardware_verified"] is False
    assert snapshot["hardware_identity"] == "LOCAL_GPU_OR_HOST_UNVERIFIED"


def test_dgx_spark_attestation_accepts_device_tree_product(monkeypatch):
    _patch_gpu(monkeypatch, host_model="NVIDIA DGX Spark")

    snapshot = runtime.runtime_snapshot(
        node_id="spark-single",
        runtime_mode="single-spark",
    )

    assert snapshot["gb10_arm64_verified"] is True
    assert snapshot["dgx_spark_hardware_verified"] is True
    assert snapshot["hardware_identity"] == "VERIFIED_DGX_SPARK_GB10"


def test_hostname_cannot_substitute_for_device_tree_product_identity(monkeypatch):
    _patch_gpu(monkeypatch, host_model="")
    monkeypatch.setattr(runtime.platform, "node", lambda: "my-dgx-spark")

    snapshot = runtime.runtime_snapshot(
        node_id="spark-single",
        runtime_mode="single-spark",
    )

    assert snapshot["host_model"] == "my-dgx-spark"
    assert snapshot["device_tree_model_verified"] is False
    assert snapshot["gb10_arm64_verified"] is True
    assert snapshot["dgx_spark_hardware_verified"] is False
