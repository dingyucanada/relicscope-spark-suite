from __future__ import annotations

import pytest

from app.services.reference_quality import (
    REFERENCE_QUALITY_ALGORITHM_ID,
    ReferenceQualityError,
    assess_reference_quality,
)


def _gate(*, failed: tuple[str, ...] = ()) -> dict:
    checks = {
        name: name not in failed
        for name in (
            "resolution",
            "exposure",
            "clipping",
            "sharpness",
            "dynamic_range",
        )
    }
    return {
        "passed": not failed,
        "checks": checks,
        "failed_checks": list(failed),
    }


def test_reference_quality_uses_one_versioned_zero_to_one_scale() -> None:
    passing = assess_reference_quality(_gate())
    partial = assess_reference_quality(_gate(failed=("sharpness", "exposure")))

    assert passing.algorithm_id == REFERENCE_QUALITY_ALGORITHM_ID
    assert passing.score == 1.0
    assert passing.passed is True
    assert partial.score == 0.6
    assert partial.passed is False
    assert partial.failed_checks == ("exposure", "sharpness")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["checks"].pop("resolution"),
        lambda value: value.update(passed=True),
        lambda value: value.update(failed_checks=[]),
        lambda value: value["checks"].update(exposure="yes"),
    ],
)
def test_reference_quality_rejects_incomplete_or_inconsistent_gates(mutation) -> None:
    gate = _gate(failed=("exposure",))
    mutation(gate)

    with pytest.raises(ReferenceQualityError):
        assess_reference_quality(gate)
