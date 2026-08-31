from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote

from .image_analysis import analyze_image, decode_image
from .reference_quality import (
    REFERENCE_QUALITY_ALGORITHM_ID,
    ReferenceQualityError,
    assess_reference_quality,
)


REFERENCE_MANIFEST_SCHEMA_VERSION = "relicscope-reference-library-manifest-v1"
REFERENCE_INDEX_SCHEMA_VERSION = "relicscope-reference-library-index-v1"
REFERENCE_INDEX_ALGORITHM = "relicscope-reference-metadata-index-v1"
DEFAULT_REFERENCE_ARTIFACT_COUNT = 50
DEFAULT_MINIMUM_COUNTERFEIT_RECORDS = 10
DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT = 5
REAL_DATA_CLASSIFICATION = "CONTROLLED_REAL_ARTIFACT_DATA"
TEST_DATA_CLASSIFICATION = "TEST/SYNTHETIC"
COUNTERFEIT_COVERAGE = "DEMO_LIMITED"
EVALUATION_BOUNDARY = "IN_LIBRARY_CONTROLS_NOT_HELD_OUT_EVALUATION"

AUTHENTIC = "AUTHENTIC"
COUNTERFEIT = "COUNTERFEIT"

ANGLE_VALUES = frozenset(
    {
        "FRONT",
        "BACK",
        "LEFT_PROFILE",
        "RIGHT_PROFILE",
        "TOP",
        "BASE",
        "INTERIOR",
        "FRONT_LEFT_45",
        "FRONT_RIGHT_45",
        "BACK_LEFT_45",
        "BACK_RIGHT_45",
        "DETAIL",
        "MARK",
        "DAMAGE",
    }
)
COUNTABLE_VIEW_ANGLES = frozenset(
    {
        "FRONT",
        "BACK",
        "LEFT_PROFILE",
        "RIGHT_PROFILE",
        "TOP",
        "BASE",
        "INTERIOR",
        "FRONT_LEFT_45",
        "FRONT_RIGHT_45",
        "BACK_LEFT_45",
        "BACK_RIGHT_45",
    }
)

_CERAMIC_CLASSES = {
    "PORCELAIN",
    "STONEWARE",
    "EARTHENWARE",
    "OTHER_CERAMIC",
}
_SOURCE_TYPES = {
    "MUSEUM_COLLECTION",
    "INSTITUTIONAL_COLLECTION",
    "PRIVATE_COLLECTION",
    "LAW_ENFORCEMENT_REFERENCE",
    "EXPERT_STUDY_COLLECTION",
}
_ALLOWED_USES = {
    "LOCAL_DEMO",
    "LOCAL_RETRIEVAL",
    "MODEL_EVALUATION",
    "MODEL_TRAINING",
}
_REQUIRED_IMPORT_USES = {"LOCAL_DEMO", "LOCAL_RETRIEVAL"}
_SIGNATURE_TYPES = {
    "DIGITAL_SIGNATURE",
    "SIGNED_DOCUMENT",
    "INSTITUTIONAL_APPROVAL",
}
_DISPUTE_STATES = {
    "NO_KNOWN_DISPUTE",
    "RESOLVED",
    "ACTIVE_DISPUTE",
}
_ADMISSIBLE_USES = {
    "IDENTITY_MATCHING",
    "SIMILARITY_CONTEXT",
    "COUNTERFEIT_CROSS_VALIDATION",
}
_COUNTERFEIT_TYPES = {
    "REPRODUCTION",
    "FORGED_MARK",
    "ARTIFICIALLY_AGED",
    "COMPOSITE",
    "MISATTRIBUTED",
    "OTHER_DOCUMENTED_COUNTERFEIT",
}
_MIME_TO_FORMAT = {
    "image/jpeg": "JPEG",
    "image/png": "PNG",
    "image/webp": "WEBP",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}$")
_MAX_IMAGE_BYTES = 64 * 1024 * 1024
_MAX_EVIDENCE_BYTES = 32 * 1024 * 1024

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "library_id",
    "version",
    "created_at",
    "data_classification",
    "expected_reference_artifact_count",
    "minimum_counterfeit_record_count",
    "minimum_images_per_artifact",
    "content_set_sha256",
    "manifest_sha256",
    "reference_artifacts",
    "counterfeit_records",
}
_ARTIFACT_FIELDS = {
    "artifact_id",
    "physical_object_id",
    "display_name",
    "ceramic_class",
    "authenticity_status",
    "catalogue_metadata",
    "source",
    "rights",
    "expert_review",
    "counterfeit_profile",
    "images",
    "record_sha256",
}
_CATALOGUE_FIELDS = {
    "culture_or_period",
    "maker_or_kiln",
    "material",
    "technique",
    "dimensions",
    "description",
}
_SOURCE_FIELDS = {
    "source_type",
    "institution",
    "collection_name",
    "accession_number",
    "record_locator",
    "evidence_path",
    "evidence_sha256",
    "retrieved_at",
}
_RIGHTS_FIELDS = {
    "rights_holder",
    "license_identifier",
    "license_statement",
    "allowed_uses",
    "attribution_required",
    "attribution_text",
    "valid_from",
    "valid_until",
}
_EXPERT_REVIEW_FIELDS = {
    "review_id",
    "decision",
    "reviewer_name",
    "reviewer_credential",
    "reviewer_institution",
    "reviewed_at",
    "signoff_statement",
    "signature_type",
    "dispute_status",
    "admissible_uses",
    "review_document_path",
    "review_document_sha256",
}
_COUNTERFEIT_PROFILE_FIELDS = {
    "counterfeit_type",
    "claimed_identity",
    "comparison_artifact_ids",
    "known_indicators",
    "evidence_path",
    "evidence_sha256",
}
_IMAGE_FIELDS = {
    "image_id",
    "path",
    "sha256",
    "mime_type",
    "angle",
    "captured_at",
    "width",
    "height",
    "calibration",
}
_CALIBRATION_FIELDS = {
    "device_id",
    "profile_id",
    "certificate_path",
    "certificate_sha256",
    "calibrated_at",
    "valid_until",
    "color_reference_id",
    "scale_reference_id",
    "lighting_setup",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ReferenceLibraryError(RuntimeError):
    pass


class ReferenceManifestValidationError(ReferenceLibraryError):
    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = list(issues)
        super().__init__("invalid reference-library manifest: " + "; ".join(self.issues))


class ReferenceIndexValidationError(ReferenceLibraryError):
    pass


@dataclass(frozen=True)
class ReferenceImage:
    image_id: str
    path: str
    sha256: str
    mime_type: str
    angle: str
    captured_at: str
    width: int
    height: int
    calibration: Mapping[str, Any]


@dataclass(frozen=True)
class ReferenceArtifact:
    artifact_id: str
    physical_object_id: str
    display_name: str
    ceramic_class: str
    authenticity_status: str
    record_kind: str
    catalogue_metadata: Mapping[str, Any]
    source: Mapping[str, Any]
    rights: Mapping[str, Any]
    expert_review: Mapping[str, Any]
    counterfeit_profile: Optional[Mapping[str, Any]]
    images: Tuple[ReferenceImage, ...]
    record_sha256: str


@dataclass(frozen=True)
class ReferenceManifest:
    schema_version: str
    library_id: str
    version: str
    created_at: str
    data_classification: str
    expected_reference_artifact_count: int
    minimum_counterfeit_record_count: int
    minimum_images_per_artifact: int
    content_set_sha256: str
    manifest_sha256: str
    reference_artifacts: Tuple[ReferenceArtifact, ...]
    counterfeit_records: Tuple[ReferenceArtifact, ...]

    @property
    def artifacts(self) -> Tuple[ReferenceArtifact, ...]:
        return self.reference_artifacts + self.counterfeit_records

    @property
    def image_count(self) -> int:
        return sum(len(item.images) for item in self.artifacts)


@dataclass(frozen=True)
class ReferenceImportResult:
    index_path: Path
    library_id: str
    library_version: str
    manifest_sha256: str
    index_file_sha256: str
    reference_artifact_count: int
    counterfeit_record_count: int
    image_count: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": "IMPORTED",
            "index_path": str(self.index_path),
            "library_id": self.library_id,
            "library_version": self.library_version,
            "manifest_sha256": self.manifest_sha256,
            "index_file_sha256": self.index_file_sha256,
            "reference_artifact_count": self.reference_artifact_count,
            "counterfeit_record_count": self.counterfeit_record_count,
            "image_count": self.image_count,
            "counterfeit_coverage": COUNTERFEIT_COVERAGE,
            "evaluation_boundary": EVALUATION_BOUNDARY,
        }


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _check_exact_fields(
    value: Mapping[str, Any], allowed: set[str], path: str, issues: List[str]
) -> None:
    missing = sorted(allowed - set(value))
    unknown = sorted(set(value) - allowed)
    if missing:
        issues.append(f"{path} missing fields: {', '.join(missing)}")
    if unknown:
        issues.append(f"{path} unknown fields: {', '.join(unknown)}")


def _required_string(
    value: Mapping[str, Any], field: str, path: str, issues: List[str]
) -> None:
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        issues.append(f"{path}.{field} must be a non-empty string")


def _required_identifier(
    value: Mapping[str, Any], field: str, path: str, issues: List[str]
) -> None:
    _required_string(value, field, path, issues)
    item = value.get(field)
    if isinstance(item, str) and item.strip() and not _IDENTIFIER.fullmatch(item):
        issues.append(f"{path}.{field} has an invalid identifier format")


def _required_string_list(
    value: Mapping[str, Any], field: str, path: str, issues: List[str]
) -> None:
    item = value.get(field)
    if (
        not isinstance(item, list)
        or not item
        or any(not isinstance(member, str) or not member.strip() for member in item)
    ):
        issues.append(f"{path}.{field} must be a non-empty string list")


def _validate_sha256(value: Any, path: str, issues: List[str]) -> None:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        issues.append(f"{path} must be a lowercase SHA-256")


def _parse_datetime(value: Any, path: str, issues: List[str]) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        issues.append(f"{path} must be an ISO datetime with timezone")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        issues.append(f"{path} must be an ISO datetime with timezone")
        return None
    if parsed.utcoffset() is None:
        issues.append(f"{path} must include a timezone")
        return None
    return parsed


def _parse_date(value: Any, path: str, issues: List[str]) -> Optional[date]:
    if not isinstance(value, str) or not value:
        issues.append(f"{path} must be an ISO date")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        issues.append(f"{path} must be an ISO date")
        return None


def _validate_relative_path(value: Any, path: str, issues: List[str]) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{path} must be a non-empty relative POSIX path")
        return None
    if "\x00" in value:
        issues.append(f"{path} contains a null byte")
        return None
    if "\\" in value:
        issues.append(f"{path} must use POSIX separators")
        return None
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or value != candidate.as_posix():
        issues.append(f"{path} must be a normalized relative POSIX path")
        return None
    if any(part in {"", ".", ".."} for part in candidate.parts):
        issues.append(f"{path} contains an unsafe path segment")
        return None
    return candidate.as_posix()


def _resolve_controlled_file(
    root: Path,
    relative: str,
    *,
    path_label: str,
    issues: List[str],
) -> Optional[Path]:
    root = root.resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            issues.append(f"{path_label} must not traverse a symbolic link")
            return None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        issues.append(f"{path_label} does not exist")
        return None
    try:
        resolved.relative_to(root)
    except ValueError:
        issues.append(f"{path_label} escapes the media root")
        return None
    try:
        mode = resolved.stat().st_mode
    except OSError:
        issues.append(f"{path_label} cannot be inspected")
        return None
    if not stat.S_ISREG(mode):
        issues.append(f"{path_label} must point to a regular file")
        return None
    return resolved


def _verify_hashed_file(
    root: Path,
    relative: Any,
    expected_sha256: Any,
    *,
    path_label: str,
    digest_label: str,
    maximum_bytes: int,
    issues: List[str],
    cache: Dict[Tuple[str, str], Optional[Path]],
) -> Optional[Path]:
    normalized = _validate_relative_path(relative, path_label, issues)
    _validate_sha256(expected_sha256, digest_label, issues)
    if normalized is None or not isinstance(expected_sha256, str) or not _HEX64.fullmatch(
        expected_sha256
    ):
        return None
    cache_key = (normalized, expected_sha256)
    if cache_key in cache:
        return cache[cache_key]
    resolved = _resolve_controlled_file(
        root, normalized, path_label=path_label, issues=issues
    )
    if resolved is None:
        cache[cache_key] = None
        return None
    try:
        size = resolved.stat().st_size
        if size <= 0:
            issues.append(f"{path_label} must not be empty")
            cache[cache_key] = None
            return None
        if size > maximum_bytes:
            issues.append(f"{path_label} exceeds the size limit")
            cache[cache_key] = None
            return None
        actual = sha256_file(resolved)
    except OSError:
        issues.append(f"{path_label} cannot be read")
        cache[cache_key] = None
        return None
    if actual != expected_sha256:
        issues.append(f"{digest_label} does not match {path_label}")
        cache[cache_key] = None
        return None
    cache[cache_key] = resolved
    return resolved


def _record_sha256(record: Mapping[str, Any]) -> str:
    payload = copy.deepcopy(dict(record))
    payload.pop("record_sha256", None)
    return sha256_json(payload)


def _content_set_sha256(payload: Mapping[str, Any]) -> str:
    members: List[Dict[str, str]] = []
    for field, kind in (
        ("reference_artifacts", "REFERENCE"),
        ("counterfeit_records", "COUNTERFEIT"),
    ):
        rows = payload.get(field)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            members.append(
                {
                    "record_kind": kind,
                    "artifact_id": str(row.get("artifact_id", "")),
                    "record_sha256": str(row.get("record_sha256", "")),
                }
            )
    members.sort(key=lambda item: (item["record_kind"], item["artifact_id"]))
    return sha256_json(members)


def _manifest_sha256(payload: Mapping[str, Any]) -> str:
    candidate = copy.deepcopy(dict(payload))
    candidate.pop("manifest_sha256", None)
    return sha256_json(candidate)


def seal_manifest(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a content-addressed copy after file hashes were supplied by the curator.

    Sealing does not certify that media, catalogue assertions, permissions or expert
    decisions are genuine. Import validation still verifies every referenced local file.
    """

    sealed = copy.deepcopy(dict(payload))
    for field in ("reference_artifacts", "counterfeit_records"):
        rows = sealed.get(field)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    row["record_sha256"] = _record_sha256(row)
    sealed["content_set_sha256"] = _content_set_sha256(sealed)
    base_version = str(sealed.get("version", "")).split("@", 1)[0]
    sealed["version"] = f"{base_version}@{sealed['content_set_sha256'][:12]}"
    sealed["manifest_sha256"] = _manifest_sha256(sealed)
    return sealed


def _validate_catalogue(value: Any, path: str, issues: List[str]) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _CATALOGUE_FIELDS, path, issues)
    for field in sorted(_CATALOGUE_FIELDS):
        _required_string(value, field, path, issues)


def _validate_source(
    value: Any,
    path: str,
    *,
    media_root: Optional[Path],
    verify_files: bool,
    issues: List[str],
    file_cache: Dict[Tuple[str, str], Optional[Path]],
) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _SOURCE_FIELDS, path, issues)
    for field in (
        "institution",
        "collection_name",
        "accession_number",
        "record_locator",
    ):
        _required_string(value, field, path, issues)
    if value.get("source_type") not in _SOURCE_TYPES:
        issues.append(f"{path}.source_type is not allowed")
    _parse_datetime(value.get("retrieved_at"), f"{path}.retrieved_at", issues)
    if verify_files and media_root is not None:
        _verify_hashed_file(
            media_root,
            value.get("evidence_path"),
            value.get("evidence_sha256"),
            path_label=f"{path}.evidence_path",
            digest_label=f"{path}.evidence_sha256",
            maximum_bytes=_MAX_EVIDENCE_BYTES,
            issues=issues,
            cache=file_cache,
        )
    else:
        _validate_relative_path(value.get("evidence_path"), f"{path}.evidence_path", issues)
        _validate_sha256(value.get("evidence_sha256"), f"{path}.evidence_sha256", issues)


def _validate_rights(
    value: Any,
    path: str,
    *,
    policy_date: date,
    issues: List[str],
) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _RIGHTS_FIELDS, path, issues)
    for field in (
        "rights_holder",
        "license_identifier",
        "license_statement",
        "attribution_text",
    ):
        _required_string(value, field, path, issues)
    uses = value.get("allowed_uses")
    _required_string_list(value, "allowed_uses", path, issues)
    if isinstance(uses, list):
        string_uses = [member for member in uses if isinstance(member, str)]
        unknown = sorted(
            member for member in string_uses if member not in _ALLOWED_USES
        )
        if unknown:
            issues.append(f"{path}.allowed_uses contains unsupported values: {', '.join(unknown)}")
        missing_uses = sorted(_REQUIRED_IMPORT_USES - set(string_uses))
        if missing_uses:
            issues.append(
                f"{path}.allowed_uses does not permit: {', '.join(missing_uses)}"
            )
        if len(string_uses) != len(set(string_uses)):
            issues.append(f"{path}.allowed_uses must not contain duplicates")
    if not isinstance(value.get("attribution_required"), bool):
        issues.append(f"{path}.attribution_required must be boolean")
    valid_from = _parse_date(value.get("valid_from"), f"{path}.valid_from", issues)
    valid_until = _parse_date(value.get("valid_until"), f"{path}.valid_until", issues)
    if valid_from and valid_until:
        if valid_until < valid_from:
            issues.append(f"{path}.valid_until precedes valid_from")
        if policy_date < valid_from or policy_date > valid_until:
            issues.append(f"{path} is not valid on policy date {policy_date.isoformat()}")


def _validate_expert_review(
    value: Any,
    path: str,
    *,
    expected_decision: str,
    manifest_created_at: Optional[datetime],
    media_root: Optional[Path],
    verify_files: bool,
    issues: List[str],
    file_cache: Dict[Tuple[str, str], Optional[Path]],
) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _EXPERT_REVIEW_FIELDS, path, issues)
    _required_identifier(value, "review_id", path, issues)
    for field in (
        "reviewer_name",
        "reviewer_credential",
        "reviewer_institution",
        "signoff_statement",
    ):
        _required_string(value, field, path, issues)
    if value.get("decision") != expected_decision:
        issues.append(f"{path}.decision must match authenticity_status")
    if value.get("signature_type") not in _SIGNATURE_TYPES:
        issues.append(f"{path}.signature_type is not allowed")
    dispute_status = value.get("dispute_status")
    if dispute_status not in _DISPUTE_STATES:
        issues.append(f"{path}.dispute_status is not allowed")
    elif dispute_status == "ACTIVE_DISPUTE":
        issues.append(f"{path}.dispute_status is not admissible for the reference index")
    _required_string_list(value, "admissible_uses", path, issues)
    admissible_uses = value.get("admissible_uses")
    if isinstance(admissible_uses, list):
        string_uses = [member for member in admissible_uses if isinstance(member, str)]
        unknown = sorted(
            member
            for member in string_uses
            if member not in _ADMISSIBLE_USES
        )
        if unknown:
            issues.append(
                f"{path}.admissible_uses contains unsupported values: "
                + ", ".join(unknown)
            )
        if len(string_uses) != len(set(string_uses)):
            issues.append(f"{path}.admissible_uses must not contain duplicates")
        required_use = (
            "IDENTITY_MATCHING"
            if expected_decision == AUTHENTIC
            else "COUNTERFEIT_CROSS_VALIDATION"
        )
        if required_use not in string_uses:
            issues.append(f"{path}.admissible_uses must include {required_use}")
        forbidden_use = (
            "COUNTERFEIT_CROSS_VALIDATION"
            if expected_decision == AUTHENTIC
            else "IDENTITY_MATCHING"
        )
        if forbidden_use in string_uses:
            issues.append(f"{path}.admissible_uses must not include {forbidden_use}")
    reviewed_at = _parse_datetime(value.get("reviewed_at"), f"{path}.reviewed_at", issues)
    if reviewed_at and manifest_created_at and reviewed_at > manifest_created_at:
        issues.append(f"{path}.reviewed_at is later than manifest created_at")
    if verify_files and media_root is not None:
        _verify_hashed_file(
            media_root,
            value.get("review_document_path"),
            value.get("review_document_sha256"),
            path_label=f"{path}.review_document_path",
            digest_label=f"{path}.review_document_sha256",
            maximum_bytes=_MAX_EVIDENCE_BYTES,
            issues=issues,
            cache=file_cache,
        )
    else:
        _validate_relative_path(
            value.get("review_document_path"), f"{path}.review_document_path", issues
        )
        _validate_sha256(
            value.get("review_document_sha256"),
            f"{path}.review_document_sha256",
            issues,
        )


def _validate_calibration(
    value: Any,
    path: str,
    *,
    captured_at: Optional[datetime],
    media_root: Optional[Path],
    verify_files: bool,
    issues: List[str],
    file_cache: Dict[Tuple[str, str], Optional[Path]],
) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _CALIBRATION_FIELDS, path, issues)
    for field in (
        "device_id",
        "profile_id",
        "color_reference_id",
        "scale_reference_id",
        "lighting_setup",
    ):
        _required_string(value, field, path, issues)
    calibrated_at = _parse_datetime(
        value.get("calibrated_at"), f"{path}.calibrated_at", issues
    )
    valid_until = _parse_datetime(value.get("valid_until"), f"{path}.valid_until", issues)
    if calibrated_at and valid_until and valid_until < calibrated_at:
        issues.append(f"{path}.valid_until precedes calibrated_at")
    if captured_at and calibrated_at and calibrated_at > captured_at:
        issues.append(f"{path}.calibrated_at is later than image capture")
    if captured_at and valid_until and captured_at > valid_until:
        issues.append(f"{path} had expired before image capture")
    if verify_files and media_root is not None:
        _verify_hashed_file(
            media_root,
            value.get("certificate_path"),
            value.get("certificate_sha256"),
            path_label=f"{path}.certificate_path",
            digest_label=f"{path}.certificate_sha256",
            maximum_bytes=_MAX_EVIDENCE_BYTES,
            issues=issues,
            cache=file_cache,
        )
    else:
        _validate_relative_path(
            value.get("certificate_path"), f"{path}.certificate_path", issues
        )
        _validate_sha256(
            value.get("certificate_sha256"), f"{path}.certificate_sha256", issues
        )


def _validate_image(
    value: Any,
    path: str,
    *,
    manifest_created_at: Optional[datetime],
    media_root: Optional[Path],
    verify_files: bool,
    issues: List[str],
    file_cache: Dict[Tuple[str, str], Optional[Path]],
) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _IMAGE_FIELDS, path, issues)
    _required_identifier(value, "image_id", path, issues)
    normalized_path = _validate_relative_path(value.get("path"), f"{path}.path", issues)
    _validate_sha256(value.get("sha256"), f"{path}.sha256", issues)
    mime_type = value.get("mime_type")
    if mime_type not in _MIME_TO_FORMAT:
        issues.append(f"{path}.mime_type is not supported")
    if value.get("angle") not in ANGLE_VALUES:
        issues.append(f"{path}.angle is not in the controlled angle enum")
    captured_at = _parse_datetime(value.get("captured_at"), f"{path}.captured_at", issues)
    if captured_at and manifest_created_at and captured_at > manifest_created_at:
        issues.append(f"{path}.captured_at is later than manifest created_at")
    for dimension in ("width", "height"):
        item = value.get(dimension)
        if not isinstance(item, int) or isinstance(item, bool) or item < 32:
            issues.append(f"{path}.{dimension} must be an integer of at least 32")
    _validate_calibration(
        value.get("calibration"),
        f"{path}.calibration",
        captured_at=captured_at,
        media_root=media_root,
        verify_files=verify_files,
        issues=issues,
        file_cache=file_cache,
    )
    if (
        verify_files
        and media_root is not None
        and normalized_path is not None
        and isinstance(value.get("sha256"), str)
        and _HEX64.fullmatch(value["sha256"])
    ):
        resolved = _verify_hashed_file(
            media_root,
            normalized_path,
            value["sha256"],
            path_label=f"{path}.path",
            digest_label=f"{path}.sha256",
            maximum_bytes=_MAX_IMAGE_BYTES,
            issues=issues,
            cache=file_cache,
        )
        if resolved is not None:
            try:
                decoded = decode_image(resolved.read_bytes())
            except (OSError, ValueError):
                issues.append(f"{path}.path is not a supported, decodable reference image")
            else:
                if decoded.detected_mime != mime_type:
                    issues.append(f"{path}.mime_type does not match the decoded image")
                if decoded.detected_format != _MIME_TO_FORMAT.get(mime_type):
                    issues.append(f"{path}.mime_type has an inconsistent image format")
                if decoded.image.width != value.get("width"):
                    issues.append(f"{path}.width does not match the decoded image")
                if decoded.image.height != value.get("height"):
                    issues.append(f"{path}.height does not match the decoded image")


def _validate_counterfeit_profile(
    value: Any,
    path: str,
    *,
    reference_ids: set[str],
    media_root: Optional[Path],
    verify_files: bool,
    issues: List[str],
    file_cache: Dict[Tuple[str, str], Optional[Path]],
) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object for a counterfeit record")
        return
    _check_exact_fields(value, _COUNTERFEIT_PROFILE_FIELDS, path, issues)
    if value.get("counterfeit_type") not in _COUNTERFEIT_TYPES:
        issues.append(f"{path}.counterfeit_type is not allowed")
    _required_string(value, "claimed_identity", path, issues)
    for field in ("comparison_artifact_ids", "known_indicators"):
        _required_string_list(value, field, path, issues)
        members = value.get(field)
        if isinstance(members, list):
            string_members = [member for member in members if isinstance(member, str)]
            if len(string_members) != len(set(string_members)):
                issues.append(f"{path}.{field} must not contain duplicates")
    comparison_ids = value.get("comparison_artifact_ids")
    if isinstance(comparison_ids, list):
        unknown = sorted(
            member
            for member in comparison_ids
            if isinstance(member, str) and member not in reference_ids
        )
        if unknown:
            issues.append(
                f"{path}.comparison_artifact_ids contains unknown authentic references: "
                + ", ".join(unknown)
            )
    if verify_files and media_root is not None:
        _verify_hashed_file(
            media_root,
            value.get("evidence_path"),
            value.get("evidence_sha256"),
            path_label=f"{path}.evidence_path",
            digest_label=f"{path}.evidence_sha256",
            maximum_bytes=_MAX_EVIDENCE_BYTES,
            issues=issues,
            cache=file_cache,
        )
    else:
        _validate_relative_path(value.get("evidence_path"), f"{path}.evidence_path", issues)
        _validate_sha256(value.get("evidence_sha256"), f"{path}.evidence_sha256", issues)


def _validate_artifact(
    value: Any,
    path: str,
    *,
    record_kind: str,
    reference_ids: set[str],
    minimum_images: int,
    manifest_created_at: Optional[datetime],
    policy_date: date,
    media_root: Optional[Path],
    verify_files: bool,
    issues: List[str],
    file_cache: Dict[Tuple[str, str], Optional[Path]],
) -> None:
    if not _is_mapping(value):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _ARTIFACT_FIELDS, path, issues)
    _required_identifier(value, "artifact_id", path, issues)
    _required_identifier(value, "physical_object_id", path, issues)
    _required_string(value, "display_name", path, issues)
    if value.get("ceramic_class") not in _CERAMIC_CLASSES:
        issues.append(f"{path}.ceramic_class is not allowed")
    expected_status = AUTHENTIC if record_kind == "REFERENCE" else COUNTERFEIT
    if value.get("authenticity_status") != expected_status:
        issues.append(
            f"{path}.authenticity_status must be {expected_status} for {record_kind} records"
        )
    _validate_catalogue(value.get("catalogue_metadata"), f"{path}.catalogue_metadata", issues)
    _validate_source(
        value.get("source"),
        f"{path}.source",
        media_root=media_root,
        verify_files=verify_files,
        issues=issues,
        file_cache=file_cache,
    )
    _validate_rights(
        value.get("rights"), f"{path}.rights", policy_date=policy_date, issues=issues
    )
    _validate_expert_review(
        value.get("expert_review"),
        f"{path}.expert_review",
        expected_decision=expected_status,
        manifest_created_at=manifest_created_at,
        media_root=media_root,
        verify_files=verify_files,
        issues=issues,
        file_cache=file_cache,
    )
    if record_kind == "REFERENCE":
        if value.get("counterfeit_profile") is not None:
            issues.append(f"{path}.counterfeit_profile must be null for authentic references")
    else:
        _validate_counterfeit_profile(
            value.get("counterfeit_profile"),
            f"{path}.counterfeit_profile",
            reference_ids=reference_ids,
            media_root=media_root,
            verify_files=verify_files,
            issues=issues,
            file_cache=file_cache,
        )
    images = value.get("images")
    if not isinstance(images, list):
        issues.append(f"{path}.images must be a list")
    else:
        if len(images) < minimum_images:
            issues.append(f"{path}.images must contain at least {minimum_images} images")
        for index, image in enumerate(images):
            _validate_image(
                image,
                f"{path}.images[{index}]",
                manifest_created_at=manifest_created_at,
                media_root=media_root,
                verify_files=verify_files,
                issues=issues,
                file_cache=file_cache,
            )
        unique_angles = {
            image.get("angle")
            for image in images
            if isinstance(image, dict) and image.get("angle") in COUNTABLE_VIEW_ANGLES
        }
        if len(unique_angles) < minimum_images:
            issues.append(
                f"{path}.images must cover at least {minimum_images} distinct view angles; "
                "DETAIL, MARK and DAMAGE do not count"
            )
    digest = value.get("record_sha256")
    _validate_sha256(digest, f"{path}.record_sha256", issues)
    if isinstance(digest, str) and _HEX64.fullmatch(digest):
        if digest != _record_sha256(value):
            issues.append(f"{path}.record_sha256 does not match the record")


def validate_manifest(
    payload: Mapping[str, Any],
    *,
    media_root: Path | str | None = None,
    expected_reference_artifact_count: int = DEFAULT_REFERENCE_ARTIFACT_COUNT,
    minimum_counterfeit_records: int = DEFAULT_MINIMUM_COUNTERFEIT_RECORDS,
    minimum_images_per_artifact: int = DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT,
    policy_date: Optional[date] = None,
    verify_files: bool = True,
    allow_test_data: bool = False,
) -> Dict[str, Any]:
    """Strictly validate a real-artifact reference manifest and all local evidence.

    Validation is fail-closed: no normalized payload is returned when any issue is
    found. File verification cannot be disabled unless the caller explicitly sets
    ``verify_files=False``; the importer never disables it.
    """

    issues: List[str] = []
    if not isinstance(payload, dict):
        raise ReferenceManifestValidationError(["manifest root must be an object"])
    _check_exact_fields(payload, _TOP_LEVEL_FIELDS, "manifest", issues)
    if payload.get("schema_version") != REFERENCE_MANIFEST_SCHEMA_VERSION:
        issues.append("manifest.schema_version is unsupported")
    _required_identifier(payload, "library_id", "manifest", issues)
    _required_string(payload, "version", "manifest", issues)
    version = payload.get("version")
    if isinstance(version, str):
        version_base, separator, _ = version.partition("@")
        if not separator or not version_base.strip():
            issues.append("manifest.version must have a non-empty base and content suffix")
    manifest_created_at = _parse_datetime(
        payload.get("created_at"), "manifest.created_at", issues
    )
    classification = payload.get("data_classification")
    if classification != REAL_DATA_CLASSIFICATION:
        if classification != TEST_DATA_CLASSIFICATION or not allow_test_data:
            issues.append(
                "manifest.data_classification must be CONTROLLED_REAL_ARTIFACT_DATA"
            )
    if not isinstance(expected_reference_artifact_count, int) or isinstance(
        expected_reference_artifact_count, bool
    ) or expected_reference_artifact_count < 1:
        raise ValueError("expected_reference_artifact_count must be a positive integer")
    if not isinstance(minimum_counterfeit_records, int) or isinstance(
        minimum_counterfeit_records, bool
    ) or minimum_counterfeit_records < 0:
        raise ValueError("minimum_counterfeit_records must be a non-negative integer")
    if not isinstance(minimum_images_per_artifact, int) or isinstance(
        minimum_images_per_artifact, bool
    ) or minimum_images_per_artifact < DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT:
        raise ValueError("minimum_images_per_artifact must be at least five")

    declared_reference_count = payload.get("expected_reference_artifact_count")
    if declared_reference_count != expected_reference_artifact_count:
        issues.append(
            "manifest.expected_reference_artifact_count must equal the active import policy "
            f"({expected_reference_artifact_count})"
        )
    declared_counterfeit_minimum = payload.get("minimum_counterfeit_record_count")
    if (
        not isinstance(declared_counterfeit_minimum, int)
        or isinstance(declared_counterfeit_minimum, bool)
        or declared_counterfeit_minimum < minimum_counterfeit_records
    ):
        issues.append(
            "manifest.minimum_counterfeit_record_count is below the active import policy "
            f"({minimum_counterfeit_records})"
        )
    declared_image_minimum = payload.get("minimum_images_per_artifact")
    if (
        not isinstance(declared_image_minimum, int)
        or isinstance(declared_image_minimum, bool)
        or declared_image_minimum < minimum_images_per_artifact
    ):
        issues.append(
            "manifest.minimum_images_per_artifact is below the active import policy "
            f"({minimum_images_per_artifact})"
        )
    effective_minimum_images = (
        declared_image_minimum
        if isinstance(declared_image_minimum, int)
        and not isinstance(declared_image_minimum, bool)
        and declared_image_minimum >= minimum_images_per_artifact
        else minimum_images_per_artifact
    )

    references = payload.get("reference_artifacts")
    counterfeits = payload.get("counterfeit_records")
    if not isinstance(references, list):
        issues.append("manifest.reference_artifacts must be a list")
        references = []
    elif len(references) != expected_reference_artifact_count:
        issues.append(
            "manifest.reference_artifacts must contain exactly "
            f"{expected_reference_artifact_count} records"
        )
    effective_counterfeit_minimum = max(
        minimum_counterfeit_records,
        declared_counterfeit_minimum
        if isinstance(declared_counterfeit_minimum, int)
        and not isinstance(declared_counterfeit_minimum, bool)
        else 0,
    )
    if not isinstance(counterfeits, list):
        issues.append("manifest.counterfeit_records must be a list")
        counterfeits = []
    elif len(counterfeits) < effective_counterfeit_minimum:
        issues.append(
            "manifest.counterfeit_records must contain at least "
            f"{effective_counterfeit_minimum} records"
        )

    reference_ids = {
        item.get("artifact_id")
        for item in references
        if isinstance(item, dict) and isinstance(item.get("artifact_id"), str)
    }
    requested_root = Path(media_root) if media_root is not None else None
    root = requested_root.resolve() if requested_root is not None else None
    if verify_files:
        if root is None:
            issues.append("media_root is required when verify_files is enabled")
        elif requested_root is not None and requested_root.is_symlink():
            issues.append("media_root must be an existing non-symlink directory")
        elif not root.is_dir():
            issues.append("media_root must be an existing non-symlink directory")
    current_policy_date = policy_date or datetime.now(timezone.utc).date()
    file_cache: Dict[Tuple[str, str], Optional[Path]] = {}
    for index, artifact in enumerate(references):
        _validate_artifact(
            artifact,
            f"manifest.reference_artifacts[{index}]",
            record_kind="REFERENCE",
            reference_ids=reference_ids,
            minimum_images=effective_minimum_images,
            manifest_created_at=manifest_created_at,
            policy_date=current_policy_date,
            media_root=root,
            verify_files=verify_files,
            issues=issues,
            file_cache=file_cache,
        )
    for index, artifact in enumerate(counterfeits):
        _validate_artifact(
            artifact,
            f"manifest.counterfeit_records[{index}]",
            record_kind="COUNTERFEIT",
            reference_ids=reference_ids,
            minimum_images=effective_minimum_images,
            manifest_created_at=manifest_created_at,
            policy_date=current_policy_date,
            media_root=root,
            verify_files=verify_files,
            issues=issues,
            file_cache=file_cache,
        )

    artifact_ids: List[str] = []
    physical_ids: List[str] = []
    review_ids: List[str] = []
    image_ids: List[str] = []
    image_paths: List[str] = []
    image_digests: List[str] = []
    for artifact in [*references, *counterfeits]:
        if not isinstance(artifact, dict):
            continue
        if isinstance(artifact.get("artifact_id"), str):
            artifact_ids.append(artifact["artifact_id"])
        if isinstance(artifact.get("physical_object_id"), str):
            physical_ids.append(artifact["physical_object_id"])
        expert_review = artifact.get("expert_review")
        if isinstance(expert_review, dict) and isinstance(
            expert_review.get("review_id"), str
        ):
            review_ids.append(expert_review["review_id"])
        images = artifact.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            if isinstance(image.get("image_id"), str):
                image_ids.append(image["image_id"])
            if isinstance(image.get("path"), str):
                image_paths.append(image["path"])
            if isinstance(image.get("sha256"), str):
                image_digests.append(image["sha256"])
    for label, values in (
        ("artifact_id", artifact_ids),
        ("physical_object_id", physical_ids),
        ("expert review_id", review_ids),
        ("image_id", image_ids),
        ("image path", image_paths),
        ("image SHA-256", image_digests),
    ):
        duplicates = sorted(
            item for item, count in Counter(values).items() if count > 1
        )
        if duplicates:
            issues.append(f"manifest contains duplicate {label} values: {', '.join(duplicates)}")

    content_digest = payload.get("content_set_sha256")
    _validate_sha256(content_digest, "manifest.content_set_sha256", issues)
    if isinstance(content_digest, str) and _HEX64.fullmatch(content_digest):
        if content_digest != _content_set_sha256(payload):
            issues.append("manifest.content_set_sha256 does not match its records")
        version = payload.get("version")
        if isinstance(version, str) and not version.endswith("@" + content_digest[:12]):
            issues.append("manifest.version is not bound to content_set_sha256")
    manifest_digest = payload.get("manifest_sha256")
    _validate_sha256(manifest_digest, "manifest.manifest_sha256", issues)
    if isinstance(manifest_digest, str) and _HEX64.fullmatch(manifest_digest):
        if manifest_digest != _manifest_sha256(payload):
            issues.append("manifest.manifest_sha256 does not match the manifest")

    if issues:
        raise ReferenceManifestValidationError(issues)
    return copy.deepcopy(dict(payload))


def _artifact_from_dict(value: Mapping[str, Any], record_kind: str) -> ReferenceArtifact:
    images = tuple(
        ReferenceImage(
            image_id=item["image_id"],
            path=item["path"],
            sha256=item["sha256"],
            mime_type=item["mime_type"],
            angle=item["angle"],
            captured_at=item["captured_at"],
            width=item["width"],
            height=item["height"],
            calibration=copy.deepcopy(item["calibration"]),
        )
        for item in value["images"]
    )
    return ReferenceArtifact(
        artifact_id=value["artifact_id"],
        physical_object_id=value["physical_object_id"],
        display_name=value["display_name"],
        ceramic_class=value["ceramic_class"],
        authenticity_status=value["authenticity_status"],
        record_kind=record_kind,
        catalogue_metadata=copy.deepcopy(value["catalogue_metadata"]),
        source=copy.deepcopy(value["source"]),
        rights=copy.deepcopy(value["rights"]),
        expert_review=copy.deepcopy(value["expert_review"]),
        counterfeit_profile=copy.deepcopy(value["counterfeit_profile"]),
        images=images,
        record_sha256=value["record_sha256"],
    )


def _manifest_from_dict(payload: Mapping[str, Any]) -> ReferenceManifest:
    return ReferenceManifest(
        schema_version=payload["schema_version"],
        library_id=payload["library_id"],
        version=payload["version"],
        created_at=payload["created_at"],
        data_classification=payload["data_classification"],
        expected_reference_artifact_count=payload[
            "expected_reference_artifact_count"
        ],
        minimum_counterfeit_record_count=payload[
            "minimum_counterfeit_record_count"
        ],
        minimum_images_per_artifact=payload["minimum_images_per_artifact"],
        content_set_sha256=payload["content_set_sha256"],
        manifest_sha256=payload["manifest_sha256"],
        reference_artifacts=tuple(
            _artifact_from_dict(item, "REFERENCE")
            for item in payload["reference_artifacts"]
        ),
        counterfeit_records=tuple(
            _artifact_from_dict(item, "COUNTERFEIT")
            for item in payload["counterfeit_records"]
        ),
    )


def load_manifest(
    path: Path | str,
    *,
    media_root: Path | str | None = None,
    expected_reference_artifact_count: int = DEFAULT_REFERENCE_ARTIFACT_COUNT,
    minimum_counterfeit_records: int = DEFAULT_MINIMUM_COUNTERFEIT_RECORDS,
    minimum_images_per_artifact: int = DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT,
    policy_date: Optional[date] = None,
    allow_test_data: bool = False,
) -> ReferenceManifest:
    manifest_path = Path(path)
    if manifest_path.is_symlink():
        raise ReferenceManifestValidationError(
            ["manifest path must not be a symbolic link"]
        )
    try:
        with manifest_path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReferenceManifestValidationError(
            [f"unable to load manifest: {type(exc).__name__}"]
        ) from exc
    root = Path(media_root) if media_root is not None else manifest_path.parent
    validated = validate_manifest(
        payload,
        media_root=root,
        expected_reference_artifact_count=expected_reference_artifact_count,
        minimum_counterfeit_records=minimum_counterfeit_records,
        minimum_images_per_artifact=minimum_images_per_artifact,
        policy_date=policy_date,
        verify_files=True,
        allow_test_data=allow_test_data,
    )
    return _manifest_from_dict(validated)


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _index_payload(connection: sqlite3.Connection) -> Dict[str, Any]:
    artifact_columns = (
        "artifact_id",
        "record_kind",
        "physical_object_id",
        "display_name",
        "ceramic_class",
        "authenticity_status",
        "catalogue_json",
        "source_json",
        "rights_json",
        "expert_review_json",
        "counterfeit_profile_json",
        "record_sha256",
        "image_count",
    )
    image_columns = (
        "image_id",
        "artifact_id",
        "relative_path",
        "sha256",
        "mime_type",
        "angle",
        "captured_at",
        "width",
        "height",
        "calibration_json",
        "fingerprint_id",
        "dhash",
        "diagnostic_vector_json",
        "quality_json",
    )
    artifacts = [
        {column: row[column] for column in artifact_columns}
        for row in connection.execute(
            f"SELECT {', '.join(artifact_columns)} FROM artifacts ORDER BY artifact_id"
        )
    ]
    images = [
        {column: row[column] for column in image_columns}
        for row in connection.execute(
            f"SELECT {', '.join(image_columns)} FROM reference_images ORDER BY image_id"
        )
    ]
    return {"artifacts": artifacts, "reference_images": images}


def _metadata_digest(metadata: Mapping[str, str]) -> str:
    payload = dict(metadata)
    payload.pop("metadata_sha256", None)
    return sha256_json(payload)


class ReferenceLibraryIndex:
    """Read-only access to a validated, local SQLite reference metadata index."""

    _REQUIRED_METADATA_KEYS = {
        "index_schema_version",
        "index_algorithm",
        "diagnostic_feature_algorithm",
        "reference_quality_algorithm_id",
        "library_id",
        "library_version",
        "manifest_schema_version",
        "manifest_sha256",
        "content_set_sha256",
        "data_classification",
        "source_manifest_path",
        "media_root",
        "imported_at",
        "expected_reference_artifact_count",
        "minimum_counterfeit_record_count",
        "minimum_images_per_artifact",
        "reference_artifact_count",
        "counterfeit_record_count",
        "image_count",
        "counterfeit_coverage",
        "evaluation_boundary",
        "index_payload_sha256",
        "metadata_sha256",
    }

    def __init__(self, index_path: Path | str) -> None:
        self.index_path = Path(index_path)

    def _checked_path(self) -> Path:
        if self.index_path.is_symlink():
            raise ReferenceIndexValidationError("reference index must not be a symbolic link")
        if not self.index_path.is_file():
            raise ReferenceIndexValidationError("reference index does not exist")
        return self.index_path

    def _connection(self) -> sqlite3.Connection:
        try:
            return _connect_read_only(self._checked_path())
        except sqlite3.Error as exc:
            raise ReferenceIndexValidationError("unable to open reference index") from exc

    def metadata(self, *, verify_integrity: bool = True) -> Dict[str, Any]:
        with self._connection() as connection:
            try:
                rows = connection.execute(
                    "SELECT key, value FROM library_metadata ORDER BY key"
                ).fetchall()
            except sqlite3.Error as exc:
                raise ReferenceIndexValidationError(
                    "reference index metadata is unavailable"
                ) from exc
            metadata = {str(row["key"]): str(row["value"]) for row in rows}
            missing = sorted(self._REQUIRED_METADATA_KEYS - set(metadata))
            unknown = sorted(set(metadata) - self._REQUIRED_METADATA_KEYS)
            if missing or unknown:
                raise ReferenceIndexValidationError(
                    "reference index metadata keys are invalid"
                )
            if metadata["index_schema_version"] != REFERENCE_INDEX_SCHEMA_VERSION:
                raise ReferenceIndexValidationError("unsupported reference index schema")
            if metadata["metadata_sha256"] != _metadata_digest(metadata):
                raise ReferenceIndexValidationError("reference index metadata hash mismatch")
            if verify_integrity:
                try:
                    payload_digest = sha256_json(_index_payload(connection))
                except sqlite3.Error as exc:
                    raise ReferenceIndexValidationError(
                        "reference index tables are invalid"
                    ) from exc
                if payload_digest != metadata["index_payload_sha256"]:
                    raise ReferenceIndexValidationError(
                        "reference index payload hash mismatch"
                    )
                try:
                    expected_reference_count = int(
                        metadata["reference_artifact_count"]
                    )
                    expected_counterfeit_count = int(
                        metadata["counterfeit_record_count"]
                    )
                    expected_image_count = int(metadata["image_count"])
                except (TypeError, ValueError) as exc:
                    raise ReferenceIndexValidationError(
                        "reference index count metadata is not numeric"
                    ) from exc
                counts = connection.execute(
                    """
                    SELECT
                        SUM(CASE WHEN record_kind = 'REFERENCE' THEN 1 ELSE 0 END)
                            AS reference_count,
                        SUM(CASE WHEN record_kind = 'COUNTERFEIT' THEN 1 ELSE 0 END)
                            AS counterfeit_count,
                        (SELECT COUNT(*) FROM reference_images) AS image_count
                    FROM artifacts
                    """
                ).fetchone()
                if counts is None or (
                    int(counts["reference_count"] or 0) != expected_reference_count
                    or int(counts["counterfeit_count"] or 0)
                    != expected_counterfeit_count
                    or int(counts["image_count"] or 0) != expected_image_count
                ):
                    raise ReferenceIndexValidationError(
                        "reference index row counts do not match metadata"
                    )
        result: Dict[str, Any] = dict(metadata)
        for key in (
            "expected_reference_artifact_count",
            "minimum_counterfeit_record_count",
            "minimum_images_per_artifact",
            "reference_artifact_count",
            "counterfeit_record_count",
            "image_count",
        ):
            try:
                result[key] = int(result[key])
            except (TypeError, ValueError) as exc:
                raise ReferenceIndexValidationError(
                    f"reference index metadata {key} is not an integer"
                ) from exc
        return result

    def get_artifact(self, artifact_id: str) -> Dict[str, Any]:
        self.metadata()
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
            if row is None:
                raise KeyError(artifact_id)
            images = connection.execute(
                "SELECT * FROM reference_images WHERE artifact_id = ? ORDER BY image_id",
                (artifact_id,),
            ).fetchall()
        result = dict(row)
        for field in (
            "catalogue_json",
            "source_json",
            "rights_json",
            "expert_review_json",
            "counterfeit_profile_json",
        ):
            result[field.removesuffix("_json")] = (
                json.loads(result.pop(field)) if result[field] is not None else None
            )
        result["images"] = [self._decode_image_row(item) for item in images]
        return result

    @staticmethod
    def _decode_image_row(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for field in (
            "calibration_json",
            "diagnostic_vector_json",
            "quality_json",
        ):
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        return result

    def iter_images(
        self, *, authenticity_status: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        if authenticity_status not in {None, AUTHENTIC, COUNTERFEIT}:
            raise ValueError("authenticity_status must be AUTHENTIC or COUNTERFEIT")
        self.metadata()
        query = """
            SELECT reference_images.*, artifacts.record_kind,
                   artifacts.physical_object_id, artifacts.display_name,
                   artifacts.authenticity_status, artifacts.record_sha256
            FROM reference_images
            JOIN artifacts USING (artifact_id)
        """
        parameters: Tuple[str, ...] = ()
        if authenticity_status is not None:
            query += " WHERE artifacts.authenticity_status = ?"
            parameters = (authenticity_status,)
        query += " ORDER BY reference_images.image_id"
        with self._connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        for row in rows:
            yield self._decode_image_row(row)

    def read_image_bytes(
        self, image_id: str, *, media_root: Path | str | None = None
    ) -> bytes:
        """Read one indexed image after rechecking path, hash, format and dimensions.

        Embedding builders should use this method rather than opening
        ``relative_path`` directly. The returned bytes are bound to the image row and
        the already-verified reference-index payload.
        """

        metadata = self.metadata()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT image_id, relative_path, sha256, mime_type, width, height
                FROM reference_images WHERE image_id = ?
                """,
                (image_id,),
            ).fetchone()
        if row is None:
            raise KeyError(image_id)
        requested_root = (
            Path(media_root) if media_root is not None else Path(metadata["media_root"])
        )
        if requested_root.is_symlink():
            raise ReferenceIndexValidationError("media root must not be a symbolic link")
        root = requested_root.resolve()
        issues: List[str] = []
        resolved = _verify_hashed_file(
            root,
            row["relative_path"],
            row["sha256"],
            path_label=f"reference_images[{image_id}].relative_path",
            digest_label=f"reference_images[{image_id}].sha256",
            maximum_bytes=_MAX_IMAGE_BYTES,
            issues=issues,
            cache={},
        )
        if resolved is None:
            raise ReferenceIndexValidationError("; ".join(issues))
        try:
            content = resolved.read_bytes()
            decoded = decode_image(content)
        except (OSError, ValueError) as exc:
            raise ReferenceIndexValidationError(
                "indexed reference image is no longer decodable"
            ) from exc
        if (
            decoded.detected_mime != row["mime_type"]
            or decoded.image.width != int(row["width"])
            or decoded.image.height != int(row["height"])
        ):
            raise ReferenceIndexValidationError(
                "indexed reference image metadata no longer matches the file"
            )
        return content


class ReferenceLibraryImporter:
    """Validate completely, then atomically replace a local reference index."""

    def __init__(
        self,
        *,
        expected_reference_artifact_count: int = DEFAULT_REFERENCE_ARTIFACT_COUNT,
        minimum_counterfeit_records: int = DEFAULT_MINIMUM_COUNTERFEIT_RECORDS,
        minimum_images_per_artifact: int = DEFAULT_MINIMUM_IMAGES_PER_ARTIFACT,
        policy_date: Optional[date] = None,
        clock: Callable[[], str] = utc_now,
        allow_test_data: bool = False,
    ) -> None:
        self.expected_reference_artifact_count = expected_reference_artifact_count
        self.minimum_counterfeit_records = minimum_counterfeit_records
        self.minimum_images_per_artifact = minimum_images_per_artifact
        self.policy_date = policy_date
        self.clock = clock
        self.allow_test_data = allow_test_data

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            PRAGMA journal_mode = DELETE;
            CREATE TABLE library_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE artifacts (
                artifact_id TEXT PRIMARY KEY,
                record_kind TEXT NOT NULL CHECK(record_kind IN ('REFERENCE', 'COUNTERFEIT')),
                physical_object_id TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                ceramic_class TEXT NOT NULL,
                authenticity_status TEXT NOT NULL CHECK(
                    authenticity_status IN ('AUTHENTIC', 'COUNTERFEIT')
                ),
                catalogue_json TEXT NOT NULL,
                source_json TEXT NOT NULL,
                rights_json TEXT NOT NULL,
                expert_review_json TEXT NOT NULL,
                counterfeit_profile_json TEXT,
                record_sha256 TEXT NOT NULL UNIQUE,
                image_count INTEGER NOT NULL CHECK(image_count >= 5)
            );
            CREATE TABLE reference_images (
                image_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                relative_path TEXT NOT NULL UNIQUE,
                sha256 TEXT NOT NULL UNIQUE,
                mime_type TEXT NOT NULL,
                angle TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                calibration_json TEXT NOT NULL,
                fingerprint_id TEXT NOT NULL UNIQUE,
                dhash TEXT NOT NULL,
                diagnostic_vector_json TEXT NOT NULL,
                quality_json TEXT NOT NULL,
                FOREIGN KEY(artifact_id) REFERENCES artifacts(artifact_id)
            );
            CREATE INDEX idx_artifacts_status
                ON artifacts(authenticity_status, artifact_id);
            CREATE INDEX idx_reference_images_artifact_angle
                ON reference_images(artifact_id, angle);
            CREATE INDEX idx_reference_images_dhash
                ON reference_images(dhash);
            """
        )

    @staticmethod
    def _insert_artifact(
        connection: sqlite3.Connection,
        artifact: ReferenceArtifact,
        *,
        media_root: Path,
    ) -> None:
        connection.execute(
            """
            INSERT INTO artifacts (
                artifact_id, record_kind, physical_object_id, display_name,
                ceramic_class, authenticity_status, catalogue_json, source_json,
                rights_json, expert_review_json, counterfeit_profile_json,
                record_sha256, image_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.record_kind,
                artifact.physical_object_id,
                artifact.display_name,
                artifact.ceramic_class,
                artifact.authenticity_status,
                canonical_json(artifact.catalogue_metadata),
                canonical_json(artifact.source),
                canonical_json(artifact.rights),
                canonical_json(artifact.expert_review),
                (
                    canonical_json(artifact.counterfeit_profile)
                    if artifact.counterfeit_profile is not None
                    else None
                ),
                artifact.record_sha256,
                len(artifact.images),
            ),
        )
        for image in artifact.images:
            path_issues: List[str] = []
            image_path = _resolve_controlled_file(
                media_root,
                image.path,
                path_label=f"images[{image.image_id}].path",
                issues=path_issues,
            )
            if image_path is None:
                raise ReferenceManifestValidationError(path_issues)
            raw_bytes = image_path.read_bytes()
            decoded = decode_image(raw_bytes)
            if (
                decoded.sha256 != image.sha256
                or decoded.detected_mime != image.mime_type
                or decoded.image.width != image.width
                or decoded.image.height != image.height
            ):
                raise ReferenceManifestValidationError(
                    [f"image changed while importing: {image.image_id}"]
                )
            analysis = analyze_image(decoded.image, decoded.sha256)
            try:
                quality = assess_reference_quality(analysis["quality_gate"])
            except ReferenceQualityError as exc:
                raise ReferenceManifestValidationError(
                    [f"image quality envelope is invalid: {image.image_id}"]
                ) from exc
            if image.angle in COUNTABLE_VIEW_ANGLES and not quality.passed:
                failed = ", ".join(quality.failed_checks)
                raise ReferenceManifestValidationError(
                    [
                        f"countable reference view failed the image quality gate: "
                        f"{image.image_id} ({failed})"
                    ]
                )
            fingerprint = analysis["fingerprint"]
            connection.execute(
                """
                INSERT INTO reference_images (
                    image_id, artifact_id, relative_path, sha256, mime_type, angle,
                    captured_at, width, height, calibration_json, fingerprint_id,
                    dhash, diagnostic_vector_json, quality_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    image.image_id,
                    artifact.artifact_id,
                    image.path,
                    image.sha256,
                    image.mime_type,
                    image.angle,
                    image.captured_at,
                    image.width,
                    image.height,
                    canonical_json(image.calibration),
                    fingerprint["id"],
                    fingerprint["dhash"],
                    canonical_json(fingerprint["feature_vector"]),
                    canonical_json(
                        {
                            "metrics": analysis["metrics"],
                            "quality_gate": analysis["quality_gate"],
                            "reference_quality": quality.to_dict(),
                        }
                    ),
                ),
            )

    def import_manifest(
        self,
        manifest_path: Path | str,
        index_path: Path | str,
        *,
        media_root: Path | str | None = None,
    ) -> ReferenceImportResult:
        requested_manifest = Path(manifest_path)
        if requested_manifest.is_symlink():
            raise ReferenceManifestValidationError(
                ["manifest path must not be a symbolic link"]
            )
        manifest_file = requested_manifest.resolve()
        requested_root = Path(media_root) if media_root is not None else manifest_file.parent
        if requested_root.is_symlink():
            raise ReferenceManifestValidationError(
                ["media_root must not be a symbolic link"]
            )
        root = requested_root.resolve()
        manifest = load_manifest(
            manifest_file,
            media_root=root,
            expected_reference_artifact_count=self.expected_reference_artifact_count,
            minimum_counterfeit_records=self.minimum_counterfeit_records,
            minimum_images_per_artifact=self.minimum_images_per_artifact,
            policy_date=self.policy_date,
            allow_test_data=self.allow_test_data,
        )
        requested_destination = Path(index_path)
        if requested_destination.is_symlink():
            raise ReferenceIndexValidationError(
                "refusing to replace a symbolic-link reference index"
            )
        destination = requested_destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        imported_at = self.clock()
        try:
            connection = sqlite3.connect(temporary)
            connection.row_factory = sqlite3.Row
            try:
                self._initialize_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                for artifact in manifest.artifacts:
                    self._insert_artifact(connection, artifact, media_root=root)
                payload_digest = sha256_json(_index_payload(connection))
                metadata = {
                    "index_schema_version": REFERENCE_INDEX_SCHEMA_VERSION,
                    "index_algorithm": REFERENCE_INDEX_ALGORITHM,
                    "diagnostic_feature_algorithm": "relicscope-visual-fingerprint-v1",
                    "reference_quality_algorithm_id": (
                        REFERENCE_QUALITY_ALGORITHM_ID
                    ),
                    "library_id": manifest.library_id,
                    "library_version": manifest.version,
                    "manifest_schema_version": manifest.schema_version,
                    "manifest_sha256": manifest.manifest_sha256,
                    "content_set_sha256": manifest.content_set_sha256,
                    "data_classification": manifest.data_classification,
                    "source_manifest_path": str(manifest_file),
                    "media_root": str(root),
                    "imported_at": imported_at,
                    "expected_reference_artifact_count": str(
                        self.expected_reference_artifact_count
                    ),
                    "minimum_counterfeit_record_count": str(
                        self.minimum_counterfeit_records
                    ),
                    "minimum_images_per_artifact": str(
                        self.minimum_images_per_artifact
                    ),
                    "reference_artifact_count": str(len(manifest.reference_artifacts)),
                    "counterfeit_record_count": str(len(manifest.counterfeit_records)),
                    "image_count": str(manifest.image_count),
                    "counterfeit_coverage": COUNTERFEIT_COVERAGE,
                    "evaluation_boundary": EVALUATION_BOUNDARY,
                    "index_payload_sha256": payload_digest,
                }
                metadata["metadata_sha256"] = _metadata_digest(metadata)
                connection.executemany(
                    "INSERT INTO library_metadata(key, value) VALUES (?, ?)",
                    sorted(metadata.items()),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            ReferenceLibraryIndex(temporary).metadata()
            os.replace(temporary, destination)
            try:
                directory_fd = os.open(destination.parent, os.O_RDONLY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return ReferenceImportResult(
            index_path=destination,
            library_id=manifest.library_id,
            library_version=manifest.version,
            manifest_sha256=manifest.manifest_sha256,
            index_file_sha256=sha256_file(destination),
            reference_artifact_count=len(manifest.reference_artifacts),
            counterfeit_record_count=len(manifest.counterfeit_records),
            image_count=manifest.image_count,
        )
