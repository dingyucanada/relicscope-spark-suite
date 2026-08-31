from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping


REFERENCE_QUALITY_ALGORITHM_ID = "relicscope-reference-image-quality-v1"
REFERENCE_QUALITY_CHECKS = (
    "resolution",
    "exposure",
    "clipping",
    "sharpness",
    "dynamic_range",
)


class ReferenceQualityError(ValueError):
    """Raised when an image-analysis quality gate is incomplete or inconsistent."""


@dataclass(frozen=True)
class ReferenceQualityAssessment:
    algorithm_id: str
    score: float
    passed: bool
    checks: Mapping[str, bool]
    failed_checks: tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["failed_checks"] = list(self.failed_checks)
        return result


def assess_reference_quality(
    quality_gate: Mapping[str, Any],
) -> ReferenceQualityAssessment:
    """Map the versioned RGB quality gate to one deterministic 0..1 score.

    The algorithm intentionally consumes only the five checks emitted by
    :func:`image_analysis.analyze_image`.  It rejects incomplete or internally
    inconsistent envelopes so import, calibration, and runtime retrieval cannot
    silently assign different weights to the same capture.
    """

    if not isinstance(quality_gate, Mapping):
        raise ReferenceQualityError("reference image quality gate must be an object")
    checks = quality_gate.get("checks")
    if not isinstance(checks, Mapping):
        raise ReferenceQualityError("reference image quality checks are unavailable")
    if set(checks) != set(REFERENCE_QUALITY_CHECKS):
        raise ReferenceQualityError(
            "reference image quality checks do not match the versioned algorithm"
        )
    normalized: Dict[str, bool] = {}
    for name in REFERENCE_QUALITY_CHECKS:
        value = checks.get(name)
        if not isinstance(value, bool):
            raise ReferenceQualityError(
                f"reference image quality check {name} must be boolean"
            )
        normalized[name] = value

    failed = tuple(name for name in REFERENCE_QUALITY_CHECKS if not normalized[name])
    declared_failed = quality_gate.get("failed_checks")
    if not isinstance(declared_failed, list) or any(
        not isinstance(name, str) for name in declared_failed
    ):
        raise ReferenceQualityError(
            "reference image failed_checks must be a list of check identifiers"
        )
    if len(declared_failed) != len(set(declared_failed)) or set(declared_failed) != set(
        failed
    ):
        raise ReferenceQualityError(
            "reference image failed_checks are inconsistent with quality checks"
        )

    passed = quality_gate.get("passed")
    if not isinstance(passed, bool) or passed != all(normalized.values()):
        raise ReferenceQualityError(
            "reference image quality decision is inconsistent with quality checks"
        )
    score = round(sum(normalized.values()) / len(REFERENCE_QUALITY_CHECKS), 6)
    return ReferenceQualityAssessment(
        algorithm_id=REFERENCE_QUALITY_ALGORITHM_ID,
        score=score,
        passed=passed,
        checks=normalized,
        failed_checks=failed,
    )


__all__ = [
    "REFERENCE_QUALITY_ALGORITHM_ID",
    "REFERENCE_QUALITY_CHECKS",
    "ReferenceQualityAssessment",
    "ReferenceQualityError",
    "assess_reference_quality",
]
