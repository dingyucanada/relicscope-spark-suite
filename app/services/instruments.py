from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


REPLAY_PROFILES: Dict[str, Dict[str, Any]] = {
    "raman_low_snr": {
        "profile": "raman_low_snr",
        "modality": "Raman",
        "actual_load": {"photochemical": 0.18},
        "telemetry_valid": {"photochemical": True},
        "quality_metrics": {
            "device_ready": True,
            "calibration_valid": True,
            "snr_db": 12.0,
            "min_snr_db": 18.0,
            "saturation_ratio": 0.0,
            "region_match_valid": True,
            "spatial_registration_valid": True,
            "coverage_ratio": 0.98,
            "min_coverage_ratio": 0.90,
            "integrity_valid": True,
            "reference_applicability": True,
        },
        "finding": "返回光谱信噪比不足，结果不进入命题更新。",
        "evidence_status": "uncertain",
        "demo_data": True,
        "source_category": "DEMO/SYNTHETIC",
        "device": {"id": "REPLAY-RAMAN-01", "adapter": "replay-v1", "calibration": "DEMO-CAL-01"},
    },
    "hsi_material_anomaly": {
        "profile": "hsi_material_anomaly",
        "modality": "HSI",
        "actual_load": {"photochemical": 0.05},
        "telemetry_valid": {"photochemical": True},
        "quality_metrics": {
            "device_ready": True,
            "calibration_valid": True,
            "snr_db": 24.0,
            "min_snr_db": 18.0,
            "saturation_ratio": 0.006,
            "region_match_valid": True,
            "spatial_registration_valid": True,
            "coverage_ratio": 0.97,
            "min_coverage_ratio": 0.90,
            "integrity_valid": True,
            "reference_applicability": True,
        },
        "finding": "R1 材料响应与声明年代参考范围存在冲突，建议专家复核。",
        "evidence_status": "conflict",
        "demo_data": True,
        "source_category": "DEMO/SYNTHETIC",
        "device": {"id": "REPLAY-HSI-01", "adapter": "replay-v1", "calibration": "DEMO-CAL-02"},
    },
    "uv_valid": {
        "profile": "uv_valid",
        "modality": "UV",
        "actual_load": {"photochemical": 0.04},
        "telemetry_valid": {"photochemical": True},
        "quality_metrics": {
            "device_ready": True,
            "calibration_valid": True,
            "snr_db": 22.0,
            "min_snr_db": 18.0,
            "saturation_ratio": 0.01,
            "region_match_valid": True,
            "spatial_registration_valid": True,
            "coverage_ratio": 0.96,
            "min_coverage_ratio": 0.90,
            "integrity_valid": True,
            "reference_applicability": True,
        },
        "finding": "观察到局部荧光差异，证据状态保持不确定。",
        "evidence_status": "uncertain",
        "demo_data": True,
        "source_category": "DEMO/SYNTHETIC",
        "device": {"id": "REPLAY-UV-01", "adapter": "replay-v1", "calibration": "DEMO-CAL-03"},
    },
    "raman_missing_telemetry": {
        "profile": "raman_missing_telemetry",
        "modality": "Raman",
        "actual_load": {},
        "telemetry_valid": {"photochemical": False},
        "quality_metrics": {
            "device_ready": False,
            "calibration_valid": True,
            "snr_db": 0.0,
            "min_snr_db": 18.0,
            "saturation_ratio": 0.0,
            "region_match_valid": True,
            "spatial_registration_valid": True,
            "coverage_ratio": 0.0,
            "min_coverage_ratio": 0.90,
            "integrity_valid": False,
            "reference_applicability": True,
        },
        "finding": "遥测不可验证；按预留风险保守结算并锁定风险通道。",
        "evidence_status": "uncertain",
        "demo_data": True,
        "source_category": "DEMO/SYNTHETIC",
        "device": {"id": "REPLAY-RAMAN-01", "adapter": "replay-v1", "calibration": "DEMO-CAL-01"},
    },
}


class ReplayInstrumentAdapter:
    """Replaceable instrument adapter used for the one-day demo.

    A real device adapter only needs to return the same payload shape from its
    telemetry and data-quality pipeline.
    """

    def execute(self, action: Dict[str, Any], profile_name: str = "") -> Dict[str, Any]:
        selected_profile = profile_name or action.get("default_replay_profile", "")
        if selected_profile not in REPLAY_PROFILES:
            raise ValueError(f"unknown replay profile: {selected_profile}")
        result = deepcopy(REPLAY_PROFILES[selected_profile])
        if result["modality"] != action["modality"]:
            raise ValueError(
                f"profile {selected_profile} does not match modality {action['modality']}"
            )
        result["action_id"] = action["id"]
        result["region_id"] = action["region_id"]
        result["protocol"] = "P01-ACTIVE-SENSING-DEMO-V1"
        return result


def quality_gate(result: Dict[str, Any]) -> Dict[str, Any]:
    metrics = result["quality_metrics"]
    checks = {
        "device_ready": bool(metrics.get("device_ready")),
        "calibration": bool(metrics.get("calibration_valid")),
        "signal_to_noise": float(metrics.get("snr_db", 0.0))
        >= float(metrics.get("min_snr_db", 18.0)),
        "not_saturated": float(metrics.get("saturation_ratio", 1.0)) <= 0.02,
        "region_match": bool(metrics.get("region_match_valid")),
        "spatial_registration": bool(metrics.get("spatial_registration_valid")),
        "coverage": float(metrics.get("coverage_ratio", 0.0))
        >= float(metrics.get("min_coverage_ratio", 0.90)),
        "integrity": bool(metrics.get("integrity_valid")),
        "reference_applicability": bool(metrics.get("reference_applicability")),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failed_checks": [name for name, passed in checks.items() if not passed],
    }
