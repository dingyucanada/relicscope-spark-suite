#!/usr/bin/env python3
"""Create a non-importable intake workspace for a controlled reference library.

Generated files contain identifiers and blank collection fields only. They do not
claim that an object, image, right, review, or counterfeit determination exists.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable


DEFAULT_REFERENCE_COUNT = 50
DEFAULT_COUNTERFEIT_COUNT = 10
DEFAULT_VIEWS = ("FRONT", "BACK", "LEFT_PROFILE", "RIGHT_PROFILE", "BASE")


def _ensure_empty_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError(f"output directory must be empty: {path}")


def _write_csv(
    path: Path, fieldnames: Iterable[str], rows: Iterable[dict[str, str]]
) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def scaffold(
    output_dir: Path,
    *,
    reference_count: int = DEFAULT_REFERENCE_COUNT,
    counterfeit_count: int = DEFAULT_COUNTERFEIT_COUNT,
) -> dict[str, int | str]:
    if reference_count < DEFAULT_REFERENCE_COUNT:
        raise ValueError("reference_count must be at least 50")
    if counterfeit_count < DEFAULT_COUNTERFEIT_COUNT:
        raise ValueError("counterfeit_count must be at least 10")

    root = output_dir.resolve()
    _ensure_empty_directory(root)
    for name in (
        "images",
        "sources",
        "rights",
        "reviews",
        "calibrations",
        "counterfeit-evidence",
    ):
        (root / name).mkdir()

    object_rows: list[dict[str, str]] = []
    image_rows: list[dict[str, str]] = []
    for index in range(1, reference_count + 1):
        artifact_id = f"REF-{index:03d}"
        (root / "images" / artifact_id).mkdir()
        object_rows.append(_object_row("REFERENCE_ARTIFACT", artifact_id))
        image_rows.extend(_image_rows("REFERENCE_ARTIFACT", artifact_id))

    for index in range(1, counterfeit_count + 1):
        artifact_id = f"NEG-{index:03d}"
        (root / "images" / artifact_id).mkdir()
        row = _object_row("COUNTERFEIT_RECORD", artifact_id)
        row["notes"] = (
            "Do not label until evidence and expert review are approved."
        )
        object_rows.append(row)
        image_rows.extend(_image_rows("COUNTERFEIT_RECORD", artifact_id))

    _write_csv(root / "object-intake.csv", object_rows[0].keys(), object_rows)
    _write_csv(root / "image-intake.csv", image_rows[0].keys(), image_rows)
    template = {
        "template_only": True,
        "data_classification": "TEMPLATE/PLACEHOLDER",
        "warning": (
            "This intake scaffold is deliberately not a valid import manifest. "
            "Supply approved media, provenance, rights, calibration and expert "
            "review, then build and seal manifest.json with the project validator."
        ),
        "expected_reference_artifact_count": reference_count,
        "minimum_counterfeit_record_count": counterfeit_count,
        "minimum_images_per_artifact": len(DEFAULT_VIEWS),
        "required_view_angles": list(DEFAULT_VIEWS),
        "object_intake": "object-intake.csv",
        "image_intake": "image-intake.csv",
        "manifest_schema": "data/reference_library/manifest.schema.json",
    }
    (root / "intake-template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (root / "README.txt").write_text(
        "RelicScope controlled reference-library intake workspace\n\n"
        "PLACEHOLDER ONLY — this directory cannot be imported as evidence.\n"
        "1. Replace every blank collection field with approved source material.\n"
        "2. Add five or more distinct, quality-passing views for every object.\n"
        "3. Add source, rights, calibration and expert-review evidence files.\n"
        "4. Build manifest.json using the shipped manifest schema.\n"
        "5. Run make reference-verify before import.\n",
        encoding="utf-8",
    )
    return {
        "status": "SCAFFOLDED",
        "output_dir": str(root),
        "reference_artifacts": reference_count,
        "counterfeit_records": counterfeit_count,
        "planned_images": len(image_rows),
    }


def _object_row(record_kind: str, artifact_id: str) -> dict[str, str]:
    return {
        "record_kind": record_kind,
        "artifact_id": artifact_id,
        "physical_object_id": "",
        "display_name": "",
        "ceramic_class": "PORCELAIN",
        "source_institution": "",
        "collection_name": "",
        "accession_number": "",
        "record_locator": "",
        "rights_holder": "",
        "license_identifier": "",
        "expert_review_id": "",
        "reviewer_credential": "",
        "notes": "",
    }


def _image_rows(record_kind: str, artifact_id: str) -> list[dict[str, str]]:
    return [
        {
            "record_kind": record_kind,
            "artifact_id": artifact_id,
            "image_id": f"{artifact_id}-IMG-{index:02d}",
            "angle": angle,
            "relative_path": f"images/{artifact_id}/{angle.lower()}.jpg",
            "sha256": "",
            "captured_at": "",
            "device_id": "",
            "calibration_profile_id": "",
            "independent_capture_batch_id": "",
            "capture_notes": "",
        }
        for index, angle in enumerate(DEFAULT_VIEWS, start=1)
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a blank 50-item reference-library intake workspace."
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--reference-count", type=int, default=DEFAULT_REFERENCE_COUNT)
    parser.add_argument("--counterfeit-count", type=int, default=DEFAULT_COUNTERFEIT_COUNT)
    args = parser.parse_args()
    try:
        result = scaffold(
            args.output_dir,
            reference_count=args.reference_count,
            counterfeit_count=args.counterfeit_count,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
