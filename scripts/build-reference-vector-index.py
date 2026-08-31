#!/usr/bin/env python3
"""Build an immutable local multimodal-vector index from imported reference data."""

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
from app.services.reference_library import ReferenceLibraryError  # noqa: E402
from app.services.reference_recognition import (  # noqa: E402
    ReferenceRecognitionError,
    ReferenceVectorIndexBuilder,
)


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _environment_integer(name: str, default: str) -> int:
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return value


def _service_key() -> str:
    direct = os.getenv("REFERENCE_EMBEDDING_API_KEY", "").strip()
    if direct:
        return direct
    key_file = os.getenv("REFERENCE_EMBEDDING_API_KEY_FILE", "").strip()
    if not key_file:
        return ""
    path = Path(key_file)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024:
        raise ValueError("REFERENCE_EMBEDDING_API_KEY_FILE is not a safe key file")
    return path.read_text(encoding="utf-8").strip()


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
        raise ValueError("REFERENCE_EMBEDDING_TIMEOUT_SECONDS must be between 0 and 3600")
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
        raise ValueError("missing required local embedding configuration: " + ", ".join(missing))
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
            "REFERENCE_EMBEDDING_MODEL_REVISION must be an immutable 40- or 64-character hex revision"
        )
    return client


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata-index",
        required=True,
        type=Path,
        help="validated reference-library SQLite index",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="immutable compressed vector-index destination",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_integer,
        default=4,
        help="number of images per local embedding request (1-8; default: 4)",
    )
    return parser.parse_args(argv)


async def _build(args: argparse.Namespace) -> dict[str, object]:
    if args.batch_size > 8:
        raise ValueError("--batch-size cannot exceed 8")
    builder = ReferenceVectorIndexBuilder(
        args.metadata_index,
        args.output,
        _embedding_client_from_environment(),
        batch_size=args.batch_size,
    )
    return (await builder.build()).to_dict()


def main(argv: list[str] | None = None) -> int:
    try:
        output = asyncio.run(_build(parse_args(argv)))
    except (OSError, ValueError, ReferenceLibraryError, ReferenceRecognitionError) as exc:
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
