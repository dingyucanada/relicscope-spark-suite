from __future__ import annotations

import math
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Tuple


SUPPORTED_VIDEO_MIME_TYPES = {
    "video/mp4",
    "video/x-m4v",
    "video/quicktime",
    "video/webm",
    "video/x-msvideo",
}

VIDEO_EXTENSIONS = {
    "ISO-BMFF": ".mp4",
    "WEBM": ".webm",
    "AVI": ".avi",
}


def detect_video_container(prefix: bytes) -> str:
    """Identify the small set of containers accepted by the demo.

    This intentionally validates container signatures only. It does not claim
    that every codec track is decodable; frame extraction remains a separate,
    browser-side step with its own evidence record.
    """

    if len(prefix) >= 12 and prefix[4:8] == b"ftyp":
        return "ISO-BMFF"
    if prefix.startswith(b"\x1aE\xdf\xa3"):
        return "WEBM"
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"AVI ":
        return "AVI"
    raise ValueError("unsupported or damaged video container")


def validate_video_mime(declared_mime: str, detected_container: str) -> str:
    normalized = (declared_mime or "").split(";", 1)[0].strip().lower()
    if normalized not in SUPPORTED_VIDEO_MIME_TYPES:
        raise ValueError(f"unsupported video MIME: {normalized or 'missing'}")
    allowed = {
        "ISO-BMFF": {"video/mp4", "video/x-m4v", "video/quicktime"},
        "WEBM": {"video/webm"},
        "AVI": {"video/x-msvideo"},
    }[detected_container]
    if normalized not in allowed:
        raise ValueError(
            f"declared MIME {normalized} does not match {detected_container} container"
        )
    return normalized


def dhash_distance(first: str, second: str) -> int:
    try:
        return (int(first, 16) ^ int(second, 16)).bit_count()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid perceptual hash") from exc


def _quality_score(frame: Dict[str, Any]) -> float:
    quality = frame["analysis"]["quality_gate"]
    metrics = frame["analysis"]["metrics"]
    checks = quality.get("checks", {})
    check_score = sum(bool(value) for value in checks.values()) / max(len(checks), 1)
    sharpness = min(1.0, float(metrics.get("sharpness_score", 0.0)) / 120.0)
    dynamic_range = min(
        1.0, float(metrics.get("dynamic_range_p01_p99", 0.0)) / 180.0
    )
    clipping = max(
        0.0,
        1.0
        - float(metrics.get("clipped_black_ratio", 0.0))
        - float(metrics.get("clipped_white_ratio", 0.0)),
    )
    return round(0.65 * check_score + 0.15 * sharpness + 0.10 * dynamic_range + 0.10 * clipping, 4)


def _mean_pairwise_dhash(frames: List[Dict[str, Any]]) -> float:
    if len(frames) < 2:
        return 0.0
    distances = []
    for index, first in enumerate(frames):
        for second in frames[index + 1 :]:
            distances.append(
                dhash_distance(
                    first["analysis"]["fingerprint"]["dhash"],
                    second["analysis"]["fingerprint"]["dhash"],
                )
                / 64.0
            )
    return round(mean(distances), 4)


def _capture_consistency(frames: List[Dict[str, Any]]) -> float:
    """Score capture consistency, not object identity or condition stability."""

    if len(frames) < 2:
        return 1.0
    vectors = [frame["analysis"]["fingerprint"]["feature_vector"] for frame in frames]
    dimensions = list(zip(*vectors))
    deviations = []
    for values in dimensions:
        average = mean(values)
        variance = mean((value - average) ** 2 for value in values)
        deviations.append(math.sqrt(variance))
    return round(max(0.0, 1.0 - min(1.0, mean(deviations) * 3.0)), 4)


def _representative_ids(frames: List[Dict[str, Any]], limit: int = 3) -> List[str]:
    usable = [
        frame
        for frame in frames
        if frame["duplicate_of"] is None
        and frame["analysis"]["quality_gate"]["passed"]
    ]
    usable.sort(key=lambda item: item["timestamp_ms"])
    if len(usable) <= limit:
        return [item["id"] for item in usable]

    # Earliest / temporal midpoint / latest creates an intelligible filmstrip
    # while keeping expensive multimodal inference bounded.
    chosen = [usable[0], usable[-1]]
    midpoint = (usable[0]["timestamp_ms"] + usable[-1]["timestamp_ms"]) / 2.0
    middle = min(
        usable[1:-1],
        key=lambda item: (
            abs(item["timestamp_ms"] - midpoint),
            -float(item["quality_score"]),
        ),
    )
    chosen.append(middle)
    chosen.sort(key=lambda item: item["timestamp_ms"])
    return [item["id"] for item in chosen[:limit]]


def summarize_frames(
    frames: Iterable[Dict[str, Any]],
    *,
    duration_ms: int,
    duplicate_distance: int = 4,
) -> Dict[str, Any]:
    """Deduplicate and summarize decoded video frames deterministically."""

    ordered = sorted(frames, key=lambda item: (item["timestamp_ms"], item["id"]))
    unique: List[Dict[str, Any]] = []
    for frame in ordered:
        frame["quality_score"] = _quality_score(frame)
        current_hash = frame["analysis"]["fingerprint"]["dhash"]
        nearest: Optional[Tuple[Dict[str, Any], int]] = None
        for candidate in unique:
            distance = dhash_distance(
                current_hash, candidate["analysis"]["fingerprint"]["dhash"]
            )
            if nearest is None or distance < nearest[1]:
                nearest = (candidate, distance)
        if nearest is not None and nearest[1] <= duplicate_distance:
            frame["duplicate_of"] = nearest[0]["id"]
            frame["duplicate_distance"] = nearest[1]
        else:
            frame["duplicate_of"] = None
            frame["duplicate_distance"] = None
            unique.append(frame)

    representative_ids = _representative_ids(ordered)
    representative_set = set(representative_ids)
    for frame in ordered:
        frame["selected"] = frame["id"] in representative_set
        if frame["duplicate_of"] is not None:
            frame["admission_status"] = "DUPLICATE_SUPPRESSED"
        elif not frame["analysis"]["quality_gate"]["passed"]:
            frame["admission_status"] = "QUALITY_REJECTED"
        else:
            frame["admission_status"] = "ACCEPTED"

    usable = [item for item in ordered if item["admission_status"] == "ACCEPTED"]
    if len(ordered) >= 2:
        temporal_span = max(item["timestamp_ms"] for item in ordered) - min(
            item["timestamp_ms"] for item in ordered
        )
    else:
        temporal_span = 0
    temporal_span_ratio = min(1.0, temporal_span / max(duration_ms, 1))
    diversity = _mean_pairwise_dhash(usable)
    checks = {
        "minimum_usable_frames": len(usable) >= 3,
        "temporal_coverage": temporal_span_ratio >= 0.55,
        "viewpoint_diversity": diversity >= 0.015,
        "representative_frames": len(representative_ids) >= 2,
    }
    return {
        "frames": ordered,
        "representative_frame_ids": representative_ids,
        "summary": {
            "requested_frame_count": len(ordered),
            "usable_frame_count": len(usable),
            "quality_rejected_count": sum(
                item["admission_status"] == "QUALITY_REJECTED" for item in ordered
            ),
            "duplicate_suppressed_count": sum(
                item["admission_status"] == "DUPLICATE_SUPPRESSED"
                for item in ordered
            ),
            "representative_frame_count": len(representative_ids),
            "temporal_span_ratio": round(temporal_span_ratio, 4),
            "viewpoint_diversity_score": diversity,
            "capture_consistency_score": _capture_consistency(usable),
        },
        "quality_gate": {
            "passed": all(checks.values()),
            "checks": checks,
            "failed_checks": [key for key, value in checks.items() if not value],
        },
    }


def next_best_observations(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return operational next-capture advice without making object claims."""

    checks = summary["quality_gate"]["checks"]
    metrics = summary["summary"]
    recommendations: List[Dict[str, Any]] = []
    if not checks["minimum_usable_frames"]:
        recommendations.append(
            {
                "id": "OBS-RGB-QUALITY",
                "priority": 1,
                "label": "补拍稳定的可见光全景",
                "reason": "可用帧不足；使用三脚架、漫射光与固定曝光重新采集。",
                "modality": "RGB",
                "risk_class": "NON_CONTACT",
            }
        )
    if not checks["temporal_coverage"] or not checks["viewpoint_diversity"]:
        recommendations.append(
            {
                "id": "OBS-RGB-ROTATION",
                "priority": 2,
                "label": "补齐器物环绕与底足视角",
                "reason": "当前视频的时间覆盖或视角变化不足，建议补拍口沿、腹部、底足与款识。",
                "modality": "RGB_VIDEO",
                "risk_class": "NON_CONTACT",
            }
        )
    if float(metrics.get("capture_consistency_score", 0.0)) < 0.65:
        recommendations.append(
            {
                "id": "OBS-CALIBRATION",
                "priority": 3,
                "label": "加入色卡、比例尺与光照校准帧",
                "reason": "跨帧采集条件波动较大，校准参照有助于区分拍摄变化与表面差异。",
                "modality": "RGB_CALIBRATION",
                "risk_class": "NON_CONTACT",
            }
        )
    recommendations.extend(
        [
            {
                "id": "OBS-UV-NIR",
                "priority": 4,
                "label": "升级至 UV / NIR 表面响应观察",
                "reason": "可在不接触器物的前提下复核可见光候选区域；结果仍需质量门控。",
                "modality": "UV_NIR",
                "risk_class": "NON_CONTACT_CONTROLLED_LIGHT",
            },
            {
                "id": "OBS-MATERIAL-SCIENCE",
                "priority": 5,
                "label": "由专家审批材料科学检测",
                "reason": "图像与视频不能给出元素、分子结构、内部结构或热释光信息；可按风险预算评估 HSI、Raman、XRF、X-ray/CT 或 TL。",
                "modality": "SCIENTIFIC_INSTRUMENT",
                "risk_class": "EXPERT_APPROVAL_REQUIRED",
            },
        ]
    )
    return recommendations
