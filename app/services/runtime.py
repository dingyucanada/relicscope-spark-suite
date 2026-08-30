from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip("\x00\n ")
    except OSError:
        return None


def _gpu_inventory() -> tuple[str, List[Dict[str, str]], str | None]:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return "UNAVAILABLE_IN_APP_CONTAINER", [], "nvidia-smi not present"
    try:
        completed = subprocess.run(
            [
                executable,
                "--query-gpu=name,driver_version,uuid",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            check=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "UNAVAILABLE_IN_APP_CONTAINER", [], type(exc).__name__

    devices: List[Dict[str, str]] = []
    for line in completed.stdout.splitlines():
        fields = [value.strip() for value in line.split(",", 2)]
        if len(fields) == 3:
            devices.append(
                {"name": fields[0], "driver_version": fields[1], "uuid": fields[2]}
            )
    if not devices:
        return "UNAVAILABLE_IN_APP_CONTAINER", [], "no GPU rows returned"
    return "VERIFIED_BY_NVIDIA_SMI", devices, None


def runtime_snapshot(*, node_id: str, runtime_mode: str) -> Dict[str, Any]:
    """Return conservative host/container facts without inventing UMA memory metrics."""

    gpu_access, devices, error = _gpu_inventory()
    architecture = platform.machine()
    device_tree_model = _read_text("/proc/device-tree/model")
    host_model = device_tree_model or platform.node() or "unknown"
    gb10_verified = any("GB10" in item.get("name", "").upper() for item in devices)
    gb10_arm64_verified = bool(
        gpu_access == "VERIFIED_BY_NVIDIA_SMI"
        and gb10_verified
        and architecture.lower() in {"aarch64", "arm64"}
    )
    spark_hardware_verified = bool(
        gb10_arm64_verified
        and device_tree_model
        and "DGX SPARK" in device_tree_model.upper()
    )
    return {
        "node_id": node_id,
        "runtime_mode": runtime_mode,
        "host_model": host_model,
        "device_tree_model_verified": bool(device_tree_model),
        "operating_system": platform.platform(),
        "architecture": architecture,
        "python": platform.python_version(),
        "container_gpu_visible": bool(os.getenv("NVIDIA_VISIBLE_DEVICES")),
        "gpu_access": gpu_access,
        "gpu_devices": devices,
        "gpu_probe_error": error,
        "device_family": "NVIDIA_GB10" if gb10_verified else "UNVERIFIED",
        "device_family_verified": gb10_verified,
        "gb10_arm64_verified": gb10_arm64_verified,
        "dgx_spark_hardware_verified": spark_hardware_verified,
        "hardware_identity": (
            "VERIFIED_DGX_SPARK_GB10"
            if spark_hardware_verified
            else "LOCAL_GPU_OR_HOST_UNVERIFIED"
        ),
        "memory_accounting": "NOT_REPORTED_FOR_GB10_UNIFIED_MEMORY",
    }
