#!/usr/bin/env python3
"""Validate and seal a measured reference-recognition calibration record."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.artifact_retrieval import RetrievalThresholds  # noqa: E402
from app.services.reference_evaluation import (  # noqa: E402
    EVALUATION_RESULT_HASH_ALGORITHM,
    calculate_evaluation_result_sha256,
)
from app.services.reference_library import canonical_json  # noqa: E402
from app.services.reference_recognition import (  # noqa: E402
    REFERENCE_CALIBRATION_SCHEMA_VERSION,
)
from app.services.reference_quality import (  # noqa: E402
    REFERENCE_QUALITY_ALGORITHM_ID,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REQUIRED_BINDINGS = {
    "library_manifest_sha256": _SHA256,
    "embedding_index_payload_sha256": _SHA256,
    "embedding_model_source": None,
    "embedding_model_revision": _REVISION,
    "instruction_sha256": _SHA256,
    "reference_quality_algorithm_id": None,
    "frozen_evaluation_manifest_sha256": _SHA256,
    "independent_capture_batch_sha256": _SHA256,
    "evaluation_result_sha256": _SHA256,
}
_REQUIRED_METRICS = (
    "top1",
    "top5",
    "far",
    "frr",
    "open_set_rejection_rate",
)


class CalibrationSealError(ValueError):
    pass


def _reject_nonstandard_constant(value: str) -> None:
    raise CalibrationSealError(f"non-finite JSON number is forbidden: {value}")


def _load_unsigned(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CalibrationSealError("unsigned calibration input must be a regular non-symlink file")
    if path.stat().st_size > 1024 * 1024:
        raise CalibrationSealError("unsigned calibration input exceeds 1 MiB")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CalibrationSealError("unable to read unsigned calibration JSON") from exc
    if not isinstance(payload, dict):
        raise CalibrationSealError("unsigned calibration JSON must be an object")
    return payload


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise CalibrationSealError(f"{label} must be a positive integer")
    return value


def _finite_rate(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationSealError(f"{label} must be numeric")
    rate = float(value)
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        raise CalibrationSealError(f"{label} must be finite and between 0 and 1")
    return rate


def _validate_unsigned(payload: Mapping[str, Any]) -> None:
    if "calibration_record_sha256" in payload:
        raise CalibrationSealError(
            "input must be unsigned; remove calibration_record_sha256 before sealing"
        )
    if payload.get("schema_version") != REFERENCE_CALIBRATION_SCHEMA_VERSION:
        raise CalibrationSealError("unsupported or missing calibration schema_version")
    if payload.get("calibration_status") != "CALIBRATED":
        raise CalibrationSealError("calibration_status must be CALIBRATED")
    for field, pattern in _REQUIRED_BINDINGS.items():
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise CalibrationSealError(f"{field} is required")
        if value != value.strip():
            raise CalibrationSealError(f"{field} must not contain surrounding whitespace")
        if pattern is not None and (
            value != value.lower() or pattern.fullmatch(value) is None
        ):
            raise CalibrationSealError(
                f"{field} has an invalid canonical lowercase hash/revision"
            )
    if payload.get("evaluation_result_hash_algorithm") != EVALUATION_RESULT_HASH_ALGORITHM:
        raise CalibrationSealError(
            "evaluation_result_hash_algorithm is missing or unsupported"
        )
    if payload.get("reference_quality_algorithm_id") != REFERENCE_QUALITY_ALGORITHM_ID:
        raise CalibrationSealError(
            "reference_quality_algorithm_id is missing or unsupported"
        )
    if payload.get("evaluation_result_sha256") != calculate_evaluation_result_sha256(
        payload
    ):
        raise CalibrationSealError(
            "evaluation_result_sha256 does not bind the complete measured result"
        )

    protocol = payload.get("evaluation_protocol")
    if not isinstance(protocol, dict):
        raise CalibrationSealError("evaluation_protocol is required")
    if not isinstance(protocol.get("protocol_id"), str) or not protocol["protocol_id"].strip():
        raise CalibrationSealError("evaluation_protocol.protocol_id is required")
    for field in (
        "held_out_by_physical_object",
        "independent_reshoots",
        "exact_media_reuse_excluded",
    ):
        if protocol.get(field) is not True:
            raise CalibrationSealError(f"evaluation_protocol.{field} must be true")
    _positive_int(
        protocol.get("held_out_physical_object_count"),
        "evaluation_protocol.held_out_physical_object_count",
    )
    _positive_int(
        protocol.get("independent_reshoot_query_count"),
        "evaluation_protocol.independent_reshoot_query_count",
    )
    _positive_int(
        protocol.get("in_library_query_count"),
        "evaluation_protocol.in_library_query_count",
    )
    _positive_int(
        protocol.get("open_set_negative_count"),
        "evaluation_protocol.open_set_negative_count",
    )

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise CalibrationSealError("measured metrics are required")
    measured = {
        field: _finite_rate(metrics.get(field), f"metrics.{field}")
        for field in _REQUIRED_METRICS
    }
    if measured["top5"] < measured["top1"]:
        raise CalibrationSealError("metrics.top5 cannot be below metrics.top1")
    if not math.isclose(
        measured["open_set_rejection_rate"],
        1.0 - measured["far"],
        abs_tol=1e-6,
    ):
        raise CalibrationSealError(
            "metrics.open_set_rejection_rate must equal 1 - metrics.far"
        )
    per_view = metrics.get("per_view_recall")
    if not isinstance(per_view, dict) or not per_view:
        raise CalibrationSealError("metrics.per_view_recall must contain measured view results")
    for angle, rate in per_view.items():
        if not isinstance(angle, str) or not angle.strip():
            raise CalibrationSealError("metrics.per_view_recall contains an invalid angle")
        _finite_rate(rate, f"metrics.per_view_recall.{angle}")

    thresholds = payload.get("thresholds")
    if not isinstance(thresholds, dict):
        raise CalibrationSealError("thresholds are required")
    if set(thresholds) != set(RetrievalThresholds.__dataclass_fields__):
        raise CalibrationSealError("every versioned threshold field must be explicit")
    try:
        RetrievalThresholds(**thresholds)
    except (TypeError, ValueError) as exc:
        raise CalibrationSealError("thresholds are invalid") from exc


def _atomic_write_json(destination: Path, payload: Mapping[str, Any]) -> None:
    resolved = destination.resolve()
    if destination.is_symlink():
        raise CalibrationSealError("refusing to replace a symbolic-link calibration record")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved.name}.", suffix=".tmp", dir=resolved.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, resolved)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _seal(input_path: Path, output_path: Path) -> dict[str, Any]:
    payload = _load_unsigned(input_path)
    _validate_unsigned(payload)
    sealed = dict(payload)
    sealed["calibration_record_sha256"] = hashlib.sha256(
        canonical_json(sealed).encode("utf-8")
    ).hexdigest()
    _atomic_write_json(output_path, sealed)
    return {
        "status": "SEALED",
        "output_path": str(output_path.resolve()),
        "calibration_record_sha256": sealed["calibration_record_sha256"],
        "policy_id": sealed["thresholds"]["policy_id"],
        "boundary": "Sealing records supplied measurements; it does not create or certify accuracy claims.",
    }


def _template() -> dict[str, Any]:
    return {
        "schema_version": REFERENCE_CALIBRATION_SCHEMA_VERSION,
        "calibration_status": "TEMPLATE_NOT_CALIBRATED",
        "created_at": None,
        "library_manifest_sha256": None,
        "embedding_index_payload_sha256": None,
        "embedding_model_source": None,
        "embedding_model_revision": None,
        "instruction_sha256": None,
        "reference_quality_algorithm_id": REFERENCE_QUALITY_ALGORITHM_ID,
        "frozen_evaluation_manifest_sha256": None,
        "independent_capture_batch_sha256": None,
        "evaluation_result_sha256": None,
        "evaluation_protocol": {
            "protocol_id": None,
            "held_out_by_physical_object": None,
            "held_out_physical_object_count": 0,
            "independent_reshoots": None,
            "independent_reshoot_query_count": 0,
            "exact_media_reuse_excluded": None,
            "in_library_query_count": 0,
            "open_set_negative_count": 0,
        },
        "metrics": {
            "top1": None,
            "top5": None,
            "far": None,
            "frr": None,
            "open_set_rejection_rate": None,
            "per_view_recall": {},
        },
        "thresholds": {
            field: None
            for field in RetrievalThresholds.__dataclass_fields__
        },
        "notes": (
            "Fill only from a frozen held-out evaluation with independent reshoots and "
            "real open-set negatives. This template is intentionally unusable until measured."
        ),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_json", nargs="?", type=Path, help="unsigned measured calibration JSON")
    parser.add_argument("--output", type=Path, help="sealed calibration JSON destination")
    parser.add_argument(
        "--template",
        type=Path,
        metavar="OUTPUT",
        help="write an intentionally unavailable blank calibration template",
    )
    args = parser.parse_args(argv)
    if args.template is not None:
        if args.input_json is not None or args.output is not None:
            parser.error("--template cannot be combined with INPUT_JSON or --output")
    elif args.input_json is None or args.output is None:
        parser.error("INPUT_JSON and --output are required unless --template is used")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.template is not None:
            _atomic_write_json(args.template, _template())
            output = {
                "status": "TEMPLATE_NOT_CALIBRATED",
                "output_path": str(args.template.resolve()),
                "ready": False,
            }
        else:
            output = _seal(args.input_json, args.output)
    except (OSError, CalibrationSealError) as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
