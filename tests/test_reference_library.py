from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pytest
import numpy as np
from PIL import Image

from app.services.reference_library import (
    COUNTERFEIT,
    COUNTERFEIT_COVERAGE,
    DEFAULT_MINIMUM_COUNTERFEIT_RECORDS,
    DEFAULT_REFERENCE_ARTIFACT_COUNT,
    EVALUATION_BOUNDARY,
    REFERENCE_MANIFEST_SCHEMA_VERSION,
    TEST_DATA_CLASSIFICATION,
    ReferenceIndexValidationError,
    ReferenceLibraryImporter,
    ReferenceLibraryIndex,
    ReferenceManifestValidationError,
    load_manifest,
    seal_manifest,
    sha256_file,
    validate_manifest,
)


FIXED_POLICY_DATE = date(2026, 8, 30)
VIEW_ANGLES = ["FRONT", "BACK", "LEFT_PROFILE", "RIGHT_PROFILE", "BASE"]


@dataclass(frozen=True)
class ReferenceDataset:
    root: Path
    manifest_path: Path
    payload: dict[str, Any]


def _write_fixture_file(root: Path, relative: str, content: bytes) -> tuple[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return relative, sha256_file(path)


def _make_image(root: Path, relative: str, image_number: int) -> tuple[str, str]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    grid = (np.indices((256, 256)).sum(axis=0) // 8) % 2
    pixels = np.empty((256, 256, 3), dtype=np.uint8)
    pixels[grid == 0] = (35, 55, 75)
    pixels[grid == 1] = (205, 225, 195)
    pixels[:8, :8] = (
        30 + image_number % 180,
        30 + (image_number // 180) % 180,
        30 + (image_number * 37) % 180,
    )
    Image.fromarray(pixels, mode="RGB").save(path, format="PNG")
    return relative, sha256_file(path)


def _artifact(
    *,
    root: Path,
    sequence: int,
    status: str,
    source_evidence: tuple[str, str],
    review_evidence: tuple[str, str],
    calibration_evidence: tuple[str, str],
    counterfeit_evidence: tuple[str, str],
) -> dict[str, Any]:
    prefix = "ref" if status == "AUTHENTIC" else "fake"
    artifact_id = f"{prefix}:{sequence:03d}"
    images = []
    for view_index, angle in enumerate(VIEW_ANGLES):
        image_number = sequence * len(VIEW_ANGLES) + view_index
        relative, digest = _make_image(
            root, f"images/{prefix}-{sequence:03d}/{angle.lower()}.png", image_number
        )
        images.append(
            {
                "image_id": f"img:{prefix}:{sequence:03d}:{view_index}",
                "path": relative,
                "sha256": digest,
                "mime_type": "image/png",
                "angle": angle,
                "captured_at": "2026-08-20T08:00:00Z",
                "width": 256,
                "height": 256,
                "calibration": {
                    "device_id": "test-camera-001",
                    "profile_id": "test-calibration-v1",
                    "certificate_path": calibration_evidence[0],
                    "certificate_sha256": calibration_evidence[1],
                    "calibrated_at": "2026-01-01T00:00:00Z",
                    "valid_until": "2027-01-01T00:00:00Z",
                    "color_reference_id": "test-color-target",
                    "scale_reference_id": "test-scale-target",
                    "lighting_setup": "TEST/SYNTHETIC fixed-light fixture",
                },
            }
        )
    is_counterfeit = status == COUNTERFEIT
    admissible_uses = (
        ["SIMILARITY_CONTEXT", "COUNTERFEIT_CROSS_VALIDATION"]
        if is_counterfeit
        else ["IDENTITY_MATCHING", "SIMILARITY_CONTEXT"]
    )
    return {
        "artifact_id": artifact_id,
        "physical_object_id": f"test-object:{prefix}:{sequence:03d}",
        "display_name": f"TEST/SYNTHETIC {prefix} {sequence:03d}",
        "ceramic_class": "PORCELAIN",
        "authenticity_status": status,
        "catalogue_metadata": {
            "culture_or_period": "TEST/SYNTHETIC",
            "maker_or_kiln": "TEST/SYNTHETIC",
            "material": "TEST/SYNTHETIC pixels",
            "technique": "Generated unit-test fixture",
            "dimensions": "256 x 256 test pixels",
            "description": "No real artifact assertion; automated test fixture only.",
        },
        "source": {
            "source_type": "INSTITUTIONAL_COLLECTION",
            "institution": "RelicScope automated tests",
            "collection_name": "TEST/SYNTHETIC fixtures",
            "accession_number": f"TEST-{prefix.upper()}-{sequence:03d}",
            "record_locator": f"test fixture {prefix} {sequence:03d}",
            "evidence_path": source_evidence[0],
            "evidence_sha256": source_evidence[1],
            "retrieved_at": "2026-08-25T08:00:00Z",
        },
        "rights": {
            "rights_holder": "RelicScope automated tests",
            "license_identifier": "TEST-FIXTURE-ONLY-1.0",
            "license_statement": "Generated only for isolated automated tests.",
            "allowed_uses": ["LOCAL_DEMO", "LOCAL_RETRIEVAL"],
            "attribution_required": True,
            "attribution_text": "TEST/SYNTHETIC fixture",
            "valid_from": "2025-01-01",
            "valid_until": "2099-12-31",
        },
        "expert_review": {
            "review_id": f"review:{prefix}:{sequence:03d}",
            "decision": status,
            "reviewer_name": "TEST/SYNTHETIC reviewer",
            "reviewer_credential": "AUTOMATED TEST — NOT AN EXPERT CREDENTIAL",
            "reviewer_institution": "RelicScope automated tests",
            "reviewed_at": "2026-08-26T08:00:00Z",
            "signoff_statement": "Schema test only; no authenticity assertion.",
            "signature_type": "SIGNED_DOCUMENT",
            "dispute_status": "NO_KNOWN_DISPUTE",
            "admissible_uses": admissible_uses,
            "review_document_path": review_evidence[0],
            "review_document_sha256": review_evidence[1],
        },
        "counterfeit_profile": (
            {
                "counterfeit_type": "REPRODUCTION",
                "claimed_identity": "TEST/SYNTHETIC claimed identity",
                "comparison_artifact_ids": ["ref:000"],
                "known_indicators": ["TEST/SYNTHETIC indicator; not a real claim"],
                "evidence_path": counterfeit_evidence[0],
                "evidence_sha256": counterfeit_evidence[1],
            }
            if is_counterfeit
            else None
        ),
        "images": images,
        "record_sha256": "0" * 64,
    }


@pytest.fixture(scope="module")
def reference_dataset(tmp_path_factory: pytest.TempPathFactory) -> ReferenceDataset:
    root = tmp_path_factory.mktemp("reference-library")
    source = _write_fixture_file(
        root, "sources/test-catalogue.txt", b"TEST/SYNTHETIC catalogue evidence\n"
    )
    review = _write_fixture_file(
        root, "reviews/test-signoff.txt", b"TEST/SYNTHETIC review evidence\n"
    )
    calibration = _write_fixture_file(
        root,
        "calibrations/test-profile.txt",
        b"TEST/SYNTHETIC calibration certificate\n",
    )
    counterfeit = _write_fixture_file(
        root,
        "counterfeit-evidence/test-controls.txt",
        b"TEST/SYNTHETIC counterfeit evidence\n",
    )
    references = [
        _artifact(
            root=root,
            sequence=index,
            status="AUTHENTIC",
            source_evidence=source,
            review_evidence=review,
            calibration_evidence=calibration,
            counterfeit_evidence=counterfeit,
        )
        for index in range(DEFAULT_REFERENCE_ARTIFACT_COUNT)
    ]
    counterfeits = [
        _artifact(
            root=root,
            sequence=100 + index,
            status=COUNTERFEIT,
            source_evidence=source,
            review_evidence=review,
            calibration_evidence=calibration,
            counterfeit_evidence=counterfeit,
        )
        for index in range(DEFAULT_MINIMUM_COUNTERFEIT_RECORDS)
    ]
    payload = seal_manifest(
        {
            "schema_version": REFERENCE_MANIFEST_SCHEMA_VERSION,
            "library_id": "test:reference-library:v1",
            "version": "test-v1",
            "created_at": "2026-08-30T08:00:00Z",
            "data_classification": TEST_DATA_CLASSIFICATION,
            "expected_reference_artifact_count": DEFAULT_REFERENCE_ARTIFACT_COUNT,
            "minimum_counterfeit_record_count": DEFAULT_MINIMUM_COUNTERFEIT_RECORDS,
            "minimum_images_per_artifact": len(VIEW_ANGLES),
            "reference_artifacts": references,
            "counterfeit_records": counterfeits,
        }
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return ReferenceDataset(root=root, manifest_path=manifest_path, payload=payload)


def _validate_fixture(payload: dict[str, Any], root: Path) -> dict[str, Any]:
    return validate_manifest(
        payload,
        media_root=root,
        policy_date=FIXED_POLICY_DATE,
        allow_test_data=True,
    )


def test_manifest_requires_explicit_test_data_permission(
    reference_dataset: ReferenceDataset,
) -> None:
    with pytest.raises(
        ReferenceManifestValidationError, match="CONTROLLED_REAL_ARTIFACT_DATA"
    ):
        validate_manifest(
            reference_dataset.payload,
            media_root=reference_dataset.root,
            policy_date=FIXED_POLICY_DATE,
        )


def test_valid_manifest_has_50_references_ten_controls_and_five_views(
    reference_dataset: ReferenceDataset,
) -> None:
    manifest = load_manifest(
        reference_dataset.manifest_path,
        media_root=reference_dataset.root,
        policy_date=FIXED_POLICY_DATE,
        allow_test_data=True,
    )

    assert len(manifest.reference_artifacts) == 50
    assert len(manifest.counterfeit_records) == 10
    assert manifest.image_count == 300
    assert manifest.version.endswith("@" + manifest.content_set_sha256[:12])
    assert all(len(item.images) == 5 for item in manifest.artifacts)
    assert all(
        item.counterfeit_profile is not None
        for item in manifest.counterfeit_records
    )


Mutation = Callable[[dict[str, Any]], None]


def _remove_one_reference(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"].pop()


def _replace_view_with_detail(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["images"][4]["angle"] = "DETAIL"


def _use_unknown_angle(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["images"][0]["angle"] = "UNDERSIDE-ish"


def _break_image_hash(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["images"][0]["sha256"] = "f" * 64


def _remove_license(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["rights"].pop("license_identifier")


def _expire_calibration(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["images"][0]["calibration"][
        "valid_until"
    ] = "2026-02-01T00:00:00Z"


def _mismatch_expert_decision(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["expert_review"]["decision"] = COUNTERFEIT


def _mark_active_dispute(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["expert_review"][
        "dispute_status"
    ] = "ACTIVE_DISPUTE"


def _remove_identity_admission(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["expert_review"]["admissible_uses"] = [
        "SIMILARITY_CONTEXT"
    ]


def _unknown_counterfeit_comparison(payload: dict[str, Any]) -> None:
    payload["counterfeit_records"][0]["counterfeit_profile"][
        "comparison_artifact_ids"
    ] = ["ref:not-in-library"]


def _duplicate_physical_id(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][1]["physical_object_id"] = payload[
        "reference_artifacts"
    ][0]["physical_object_id"]


def _unsafe_image_path(payload: dict[str, Any]) -> None:
    payload["reference_artifacts"][0]["images"][0]["path"] = "../outside.png"


@pytest.mark.parametrize(
    "mutation, expected_issue",
    [
        (_remove_one_reference, "exactly 50"),
        (_replace_view_with_detail, "distinct view angles"),
        (_use_unknown_angle, "controlled angle enum"),
        (_break_image_hash, "does not match"),
        (_remove_license, "license_identifier"),
        (_expire_calibration, "expired before image capture"),
        (_mismatch_expert_decision, "must match authenticity_status"),
        (_mark_active_dispute, "not admissible"),
        (_remove_identity_admission, "must include IDENTITY_MATCHING"),
        (_unknown_counterfeit_comparison, "unknown authentic references"),
        (_duplicate_physical_id, "duplicate physical_object_id"),
        (_unsafe_image_path, "unsafe path segment"),
    ],
)
def test_fail_closed_semantic_and_file_validation(
    reference_dataset: ReferenceDataset,
    mutation: Mutation,
    expected_issue: str,
) -> None:
    payload = copy.deepcopy(reference_dataset.payload)
    mutation(payload)
    payload = seal_manifest(payload)

    with pytest.raises(ReferenceManifestValidationError) as error:
        _validate_fixture(payload, reference_dataset.root)

    assert expected_issue in str(error.value)


def test_unsealed_manifest_tampering_is_rejected(
    reference_dataset: ReferenceDataset,
) -> None:
    payload = copy.deepcopy(reference_dataset.payload)
    payload["reference_artifacts"][0]["display_name"] = "tampered after sealing"

    with pytest.raises(ReferenceManifestValidationError) as error:
        _validate_fixture(payload, reference_dataset.root)

    assert "record_sha256" in str(error.value)
    assert "manifest_sha256" in str(error.value)


def test_import_persists_verified_local_index_metadata_and_features(
    reference_dataset: ReferenceDataset, tmp_path: Path
) -> None:
    index_path = tmp_path / "reference-library.sqlite3"
    importer = ReferenceLibraryImporter(
        policy_date=FIXED_POLICY_DATE,
        clock=lambda: "2026-08-30T09:00:00Z",
        allow_test_data=True,
    )

    result = importer.import_manifest(
        reference_dataset.manifest_path,
        index_path,
        media_root=reference_dataset.root,
    )
    metadata = ReferenceLibraryIndex(index_path).metadata()

    assert result.reference_artifact_count == 50
    assert result.counterfeit_record_count == 10
    assert result.image_count == 300
    assert len(result.index_file_sha256) == 64
    assert metadata["manifest_sha256"] == reference_dataset.payload["manifest_sha256"]
    assert metadata["reference_artifact_count"] == 50
    assert metadata["counterfeit_record_count"] == 10
    assert metadata["image_count"] == 300
    assert metadata["counterfeit_coverage"] == COUNTERFEIT_COVERAGE
    assert metadata["evaluation_boundary"] == EVALUATION_BOUNDARY
    assert metadata["reference_quality_algorithm_id"] == (
        "relicscope-reference-image-quality-v1"
    )

    index = ReferenceLibraryIndex(index_path)
    counterfeit_images = list(index.iter_images(authenticity_status=COUNTERFEIT))
    assert len(counterfeit_images) == 50
    assert len(counterfeit_images[0]["diagnostic_vector"]) == 8
    assert len(counterfeit_images[0]["fingerprint_id"]) == 64
    assert counterfeit_images[0]["quality"]["reference_quality"]["score"] == 1.0
    artifact = index.get_artifact("fake:100")
    assert artifact["counterfeit_profile"]["comparison_artifact_ids"] == ["ref:000"]
    assert artifact["expert_review"]["admissible_uses"] == [
        "SIMILARITY_CONTEXT",
        "COUNTERFEIT_CROSS_VALIDATION",
    ]
    image_bytes = index.read_image_bytes("img:fake:100:0")
    assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")


def test_import_rejects_a_countable_view_that_fails_quality_gate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "low-quality-reference"
    source = _write_fixture_file(root, "sources/source.txt", b"test source\n")
    review = _write_fixture_file(root, "reviews/review.txt", b"test review\n")
    calibration = _write_fixture_file(
        root, "calibrations/profile.txt", b"test calibration\n"
    )
    counterfeit = _write_fixture_file(
        root, "counterfeit-evidence/control.txt", b"test control\n"
    )
    artifact = _artifact(
        root=root,
        sequence=1,
        status="AUTHENTIC",
        source_evidence=source,
        review_evidence=review,
        calibration_evidence=calibration,
        counterfeit_evidence=counterfeit,
    )
    failed_image = artifact["images"][0]
    failed_path = root / failed_image["path"]
    Image.new("RGB", (256, 256), "black").save(failed_path, format="PNG")
    failed_image["sha256"] = sha256_file(failed_path)
    payload = seal_manifest(
        {
            "schema_version": REFERENCE_MANIFEST_SCHEMA_VERSION,
            "library_id": "test:low-quality-reference",
            "version": "test-v1",
            "created_at": "2026-08-30T08:00:00Z",
            "data_classification": TEST_DATA_CLASSIFICATION,
            "expected_reference_artifact_count": 1,
            "minimum_counterfeit_record_count": 0,
            "minimum_images_per_artifact": 5,
            "reference_artifacts": [artifact],
            "counterfeit_records": [],
        }
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    index_path = tmp_path / "should-not-exist.sqlite3"

    with pytest.raises(
        ReferenceManifestValidationError,
        match="countable reference view failed the image quality gate",
    ):
        ReferenceLibraryImporter(
            expected_reference_artifact_count=1,
            minimum_counterfeit_records=0,
            policy_date=FIXED_POLICY_DATE,
            allow_test_data=True,
        ).import_manifest(manifest_path, index_path, media_root=root)

    assert not index_path.exists()


def test_failed_reimport_preserves_previous_index(
    reference_dataset: ReferenceDataset, tmp_path: Path
) -> None:
    index_path = tmp_path / "reference-library.sqlite3"
    importer = ReferenceLibraryImporter(
        policy_date=FIXED_POLICY_DATE,
        allow_test_data=True,
    )
    importer.import_manifest(
        reference_dataset.manifest_path,
        index_path,
        media_root=reference_dataset.root,
    )
    before = sha256_file(index_path)
    invalid_payload = copy.deepcopy(reference_dataset.payload)
    _unknown_counterfeit_comparison(invalid_payload)
    invalid_payload = seal_manifest(invalid_payload)
    invalid_manifest = tmp_path / "invalid-manifest.json"
    invalid_manifest.write_text(json.dumps(invalid_payload), encoding="utf-8")

    with pytest.raises(ReferenceManifestValidationError):
        importer.import_manifest(
            invalid_manifest, index_path, media_root=reference_dataset.root
        )

    assert sha256_file(index_path) == before
    assert ReferenceLibraryIndex(index_path).metadata()["reference_artifact_count"] == 50


def test_import_refuses_symbolic_link_output(
    reference_dataset: ReferenceDataset, tmp_path: Path
) -> None:
    target = tmp_path / "existing.sqlite3"
    target.write_bytes(b"must-not-be-replaced")
    linked_index = tmp_path / "reference-library.sqlite3"
    linked_index.symlink_to(target)

    importer = ReferenceLibraryImporter(
        policy_date=FIXED_POLICY_DATE,
        allow_test_data=True,
    )
    with pytest.raises(ReferenceIndexValidationError, match="symbolic-link"):
        importer.import_manifest(
            reference_dataset.manifest_path,
            linked_index,
            media_root=reference_dataset.root,
        )

    assert target.read_bytes() == b"must-not-be-replaced"


def test_index_row_tampering_fails_closed(
    reference_dataset: ReferenceDataset, tmp_path: Path
) -> None:
    index_path = tmp_path / "reference-library.sqlite3"
    ReferenceLibraryImporter(
        policy_date=FIXED_POLICY_DATE,
        allow_test_data=True,
    ).import_manifest(
        reference_dataset.manifest_path,
        index_path,
        media_root=reference_dataset.root,
    )
    with sqlite3.connect(index_path) as connection:
        connection.execute(
            "UPDATE artifacts SET display_name = ? WHERE artifact_id = ?",
            ("tampered", "ref:000"),
        )

    with pytest.raises(ReferenceIndexValidationError, match="payload hash mismatch"):
        ReferenceLibraryIndex(index_path).metadata()


def test_schema_document_is_valid_json() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "reference_library"
        / "manifest.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$schema"].endswith("2020-12/schema")
    assert schema["properties"]["schema_version"]["const"] == (
        REFERENCE_MANIFEST_SCHEMA_VERSION
    )
