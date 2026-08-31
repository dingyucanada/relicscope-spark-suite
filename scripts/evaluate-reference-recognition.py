#!/usr/bin/env python3
"""Measure frozen reshoot retrieval and emit an unsigned calibration suggestion."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.image_embedding import LocalImageEmbeddingClient  # noqa: E402
from app.services.reference_evaluation import (  # noqa: E402
    MINIMUM_OPEN_SET_QUERIES,
    MINIMUM_REFERENCE_PHYSICAL_OBJECTS,
    ReferenceEvaluationError,
    ReferenceRecognitionEvaluator,
    write_evaluation_output,
)
from app.services.reference_library import ReferenceLibraryError  # noqa: E402
from app.services.reference_recognition import ReferenceRecognitionError  # noqa: E402


def _service_key() -> str:
    direct = os.getenv("REFERENCE_EMBEDDING_API_KEY", "").strip()
    key_file = os.getenv("REFERENCE_EMBEDDING_API_KEY_FILE", "").strip()
    if direct and key_file:
        raise ValueError(
            "set only one of REFERENCE_EMBEDDING_API_KEY or "
            "REFERENCE_EMBEDDING_API_KEY_FILE"
        )
    if direct:
        return direct
    if not key_file:
        return ""
    path = Path(key_file)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024:
        raise ValueError("REFERENCE_EMBEDDING_API_KEY_FILE is not a safe regular file")
    return path.read_text(encoding="utf-8").strip()


def _environment_integer(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default).strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _embedding_client_from_environment() -> LocalImageEmbeddingClient:
    base_url = os.getenv("REFERENCE_EMBEDDING_BASE_URL", "").strip()
    model = os.getenv("REFERENCE_EMBEDDING_MODEL", "qwen3_vl_embedding_2b").strip()
    model_source = os.getenv(
        "REFERENCE_EMBEDDING_MODEL_SOURCE", "Qwen/Qwen3-VL-Embedding-2B"
    ).strip()
    revision = os.getenv("REFERENCE_EMBEDDING_MODEL_REVISION", "").strip().lower()
    dimension = _environment_integer("REFERENCE_EMBEDDING_DIMENSION", "2048")
    timeout_seconds = float(
        os.getenv("REFERENCE_EMBEDDING_TIMEOUT_SECONDS", "180").strip()
    )
    if not 0.0 < timeout_seconds <= 3600.0:
        raise ValueError(
            "REFERENCE_EMBEDDING_TIMEOUT_SECONDS must be between 0 and 3600"
        )
    api_key = _service_key()
    missing = [
        name
        for name, value in (
            ("REFERENCE_EMBEDDING_BASE_URL", base_url),
            ("REFERENCE_EMBEDDING_API_KEY or REFERENCE_EMBEDDING_API_KEY_FILE", api_key),
            ("REFERENCE_EMBEDDING_MODEL", model),
            ("REFERENCE_EMBEDDING_MODEL_SOURCE", model_source),
            ("REFERENCE_EMBEDDING_MODEL_REVISION", revision),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "missing required local embedding configuration: " + ", ".join(missing)
        )
    client = LocalImageEmbeddingClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        model_source=model_source,
        model_revision=revision,
        expected_dimension=dimension,
        timeout_seconds=timeout_seconds,
    )
    if not client.immutable_identity_configured:
        raise ValueError(
            "REFERENCE_EMBEDDING_MODEL_REVISION must be an immutable 40- or "
            "64-character hexadecimal revision"
        )
    return client


def _far(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("target FAR must be numeric") from exc
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("target FAR must be between 0 and 1")
    return parsed


def _top_k(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("top-k must be an integer") from exc
    if not 1 <= parsed <= 10:
        raise argparse.ArgumentTypeError("top-k must be between 1 and 10")
    return parsed


def _minimum_count(value: str, *, floor: int, label: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be an integer") from exc
    if parsed < floor:
        raise argparse.ArgumentTypeError(f"{label} cannot be below {floor}")
    return parsed


def _minimum_reference_objects(value: str) -> int:
    return _minimum_count(
        value,
        floor=MINIMUM_REFERENCE_PHYSICAL_OBJECTS,
        label="minimum reference objects",
    )


def _minimum_open_set(value: str) -> int:
    return _minimum_count(
        value,
        floor=MINIMUM_OPEN_SET_QUERIES,
        label="minimum open-set queries",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "manifest",
        type=Path,
        help=(
            "strict frozen evaluation manifest; image paths are resolved relative "
            "to this file"
        ),
    )
    parser.add_argument(
        "--metadata-index",
        required=True,
        type=Path,
        help="validated reference-library SQLite index",
    )
    parser.add_argument(
        "--vector-index",
        required=True,
        type=Path,
        help="immutable local image-vector index",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="unsigned calibration JSON accepted by seal-reference-calibration.py",
    )
    parser.add_argument(
        "--target-far",
        type=_far,
        default=0.02,
        help="maximum measured open-set false-accept rate (default: 0.02)",
    )
    parser.add_argument(
        "--top-k",
        type=_top_k,
        default=5,
        help="number of raw ranked catalog candidates to retain (default: 5)",
    )
    parser.add_argument(
        "--minimum-reference-objects",
        type=_minimum_reference_objects,
        default=MINIMUM_REFERENCE_PHYSICAL_OBJECTS,
        help=(
            "minimum distinct catalog physical objects, all of which require an "
            f"independent reshoot (safety floor: {MINIMUM_REFERENCE_PHYSICAL_OBJECTS})"
        ),
    )
    parser.add_argument(
        "--minimum-open-set",
        type=_minimum_open_set,
        default=MINIMUM_OPEN_SET_QUERIES,
        help=(
            "minimum independent open-set queries "
            f"(safety floor: {MINIMUM_OPEN_SET_QUERIES})"
        ),
    )
    return parser.parse_args(argv)


async def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    evaluator = ReferenceRecognitionEvaluator(
        metadata_index_path=args.metadata_index,
        vector_index_path=args.vector_index,
        embedding_client=_embedding_client_from_environment(),
        target_far=args.target_far,
        top_k=args.top_k,
        minimum_reference_physical_objects=args.minimum_reference_objects,
        minimum_open_set_queries=args.minimum_open_set,
    )
    payload = await evaluator.evaluate(args.manifest)
    destination = write_evaluation_output(args.output, payload)
    return {
        "status": "MEASURED_CALIBRATION_SUGGESTION_WRITTEN",
        "output_path": str(destination),
        "frozen_evaluation_manifest_sha256": payload[
            "frozen_evaluation_manifest_sha256"
        ],
        "evaluation_result_sha256": payload["evaluation_result_sha256"],
        "policy_id": payload["thresholds"]["policy_id"],
        "target_far": args.target_far,
        "target_far_met": payload["evaluation_details"]["threshold_selection"][
            "target_far_met"
        ],
        "seal_command": (
            "python scripts/seal-reference-calibration.py "
            f"{destination} --output PATH/TO/reference-calibration.json"
        ),
        "boundary": payload["boundary"],
    }


def main(argv: list[str] | None = None) -> int:
    try:
        output = asyncio.run(_evaluate(parse_args(argv)))
    except (
        OSError,
        ValueError,
        ReferenceEvaluationError,
        ReferenceLibraryError,
        ReferenceRecognitionError,
    ) as exc:
        print(
            json.dumps(
                {"status": "REJECTED", "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
