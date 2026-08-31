#!/usr/bin/env python3
"""Validate and atomically import a controlled local ceramic reference library."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.reference_library import (  # noqa: E402
    DEFAULT_MINIMUM_COUNTERFEIT_RECORDS,
    DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT,
    DEFAULT_REFERENCE_ARTIFACT_COUNT,
    ReferenceLibraryError,
    ReferenceLibraryImporter,
    load_manifest,
)


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ISO date (YYYY-MM-DD)") from exc


def _non_negative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must not be negative")
    return parsed


def _positive_integer(value: str) -> int:
    parsed = _non_negative_integer(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="manifest JSON path")
    parser.add_argument(
        "--media-root",
        type=Path,
        help="root for manifest-relative media/evidence paths (default: manifest directory)",
    )
    parser.add_argument(
        "--index",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "reference-library.sqlite3",
        help="local SQLite index destination",
    )
    parser.add_argument(
        "--expected-reference-count",
        type=_positive_integer,
        default=DEFAULT_REFERENCE_ARTIFACT_COUNT,
        help=f"required authentic reference count (default: {DEFAULT_REFERENCE_ARTIFACT_COUNT})",
    )
    parser.add_argument(
        "--minimum-counterfeit-records",
        type=_non_negative_integer,
        default=DEFAULT_MINIMUM_COUNTERFEIT_RECORDS,
        help=(
            "required documented counterfeit controls "
            f"(default: {DEFAULT_MINIMUM_COUNTERFEIT_RECORDS})"
        ),
    )
    parser.add_argument(
        "--minimum-images-per-artifact",
        type=_positive_integer,
        default=DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT,
        help=(
            "required distinct countable views per artifact "
            f"(minimum/default: {DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT})"
        ),
    )
    parser.add_argument(
        "--policy-date",
        type=_iso_date,
        help="license-policy date (default: current UTC date)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="run every validation without creating or replacing the index",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.minimum_images_per_artifact < DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT:
        raise SystemExit(
            f"--minimum-images-per-artifact cannot be below "
            f"{DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT}"
        )
    media_root = args.media_root or args.manifest.parent
    try:
        if args.verify_only:
            manifest = load_manifest(
                args.manifest,
                media_root=media_root,
                expected_reference_artifact_count=args.expected_reference_count,
                minimum_counterfeit_records=args.minimum_counterfeit_records,
                minimum_images_per_artifact=args.minimum_images_per_artifact,
                policy_date=args.policy_date,
            )
            output = {
                "status": "VALID",
                "library_id": manifest.library_id,
                "library_version": manifest.version,
                "manifest_sha256": manifest.manifest_sha256,
                "reference_artifact_count": len(manifest.reference_artifacts),
                "counterfeit_record_count": len(manifest.counterfeit_records),
                "image_count": manifest.image_count,
            }
        else:
            importer = ReferenceLibraryImporter(
                expected_reference_artifact_count=args.expected_reference_count,
                minimum_counterfeit_records=args.minimum_counterfeit_records,
                minimum_images_per_artifact=args.minimum_images_per_artifact,
                policy_date=args.policy_date,
            )
            output = importer.import_manifest(
                args.manifest, args.index, media_root=media_root
            ).as_dict()
    except ReferenceLibraryError as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

