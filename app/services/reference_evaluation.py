from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from .artifact_retrieval import (
    ArtifactReference,
    ArtifactRetrievalEngine,
    EmbeddedView,
    ReferenceKind,
    RetrievalThresholds,
    embedded_views_from_verified_run,
)
from .image_analysis import analyze_image, decode_image
from .image_embedding import EmbeddingImage, LocalImageEmbeddingClient
from .reference_library import (
    ANGLE_VALUES,
    ReferenceIndexValidationError,
    ReferenceLibraryIndex,
    canonical_json,
)
from .reference_recognition import (
    REFERENCE_CALIBRATION_SCHEMA_VERSION,
    LoadedReferenceVectorIndex,
    load_reference_vector_index,
)
from .reference_quality import (
    REFERENCE_QUALITY_ALGORITHM_ID,
    ReferenceQualityError,
    assess_reference_quality,
)


REFERENCE_EVALUATION_MANIFEST_SCHEMA_VERSION = (
    "relicscope-reference-evaluation-manifest-v1"
)
REFERENCE_EVALUATION_RESULT_SCHEMA_VERSION = "relicscope-reference-evaluation-result-v1"
EVALUATION_RESULT_HASH_ALGORITHM = (
    "sha256-canonical-json-excluding-evaluation-and-calibration-hashes-v1"
)
MINIMUM_REFERENCE_PHYSICAL_OBJECTS = 50
MINIMUM_OPEN_SET_QUERIES = 20
EVALUATION_BOUNDARY = (
    "Metrics are measurements on the supplied frozen independent-reshoot set only. "
    "They are not independent certification, field-accuracy claims, or conclusions "
    "about authenticity, age, kiln, maker, value, grade, or legal status."
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{2,127}$")
_SUPPORTED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})
_MAX_MANIFEST_BYTES = 8 * 1024 * 1024
_MAX_IMAGE_BYTES = 64 * 1024 * 1024

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "evaluation_id",
    "version",
    "library_id",
    "library_manifest_sha256",
    "embedding_index_payload_sha256",
    "embedding_index_archive_sha256",
    "protocol",
    "queries",
}
_PROTOCOL_FIELDS = {
    "protocol_id",
    "independent_reshoots",
    "exact_media_reuse_excluded",
    "capture_batch_attestation",
}
_QUERY_FIELDS = {
    "query_id",
    "capture_batch_id",
    "expected_artifact_id",
    "images",
}
_IMAGE_FIELDS = {"path", "sha256", "mime", "angle"}


class ReferenceEvaluationError(RuntimeError):
    """Raised when a frozen evaluation cannot be trusted or completed."""


@dataclass(frozen=True)
class EvaluationImage:
    relative_path: str
    sha256: str
    mime_type: str
    angle: str
    content: bytes
    quality: float
    quality_checks: Mapping[str, bool]


@dataclass(frozen=True)
class EvaluationQuery:
    query_id: str
    capture_batch_id: str
    expected_artifact_id: Optional[str]
    expected_physical_object_id: Optional[str]
    images: tuple[EvaluationImage, ...]


@dataclass(frozen=True)
class FrozenEvaluationManifest:
    payload: Mapping[str, Any]
    manifest_sha256: str
    queries: tuple[EvaluationQuery, ...]
    protocol_id: str
    capture_batch_attestation: str


@dataclass(frozen=True)
class _RawQueryResult:
    query: EvaluationQuery
    views: tuple[EmbeddedView, ...]
    top_candidates: tuple[Mapping[str, Any], ...]
    top_artifact_id: Optional[str]
    top_score: float
    runner_up_margin: float
    coverage: float
    quality: float
    complementary_angle_count: int
    counterfeit_conflict: bool
    top1_correct: bool
    top5_correct: bool
    per_view_top1: tuple[tuple[str, bool], ...]
    embedding_run: Mapping[str, Any]


def _reject_nonstandard_constant(value: str) -> None:
    raise ReferenceEvaluationError(f"non-finite JSON number is forbidden: {value}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def evaluation_result_hash_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the canonical, seal-verifiable evaluation measurement envelope.

    Every unsigned top-level field is bound. Only the evaluation hash itself and
    the later seal hash are excluded, avoiding a circular digest while ensuring
    that edits to metrics, thresholds, protocol, detailed query results, notes, or
    bindings invalidate ``evaluation_result_sha256``.
    """

    return {
        str(key): value
        for key, value in payload.items()
        if key not in {"evaluation_result_sha256", "calibration_record_sha256"}
    }


def calculate_evaluation_result_sha256(payload: Mapping[str, Any]) -> str:
    return _sha256_json(evaluation_result_hash_payload(payload))


def _strict_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    unknown = sorted(set(value) - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise ReferenceEvaluationError(f"{label} fields are invalid ({'; '.join(details)})")


def _required_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ReferenceEvaluationError(f"{label} must be a canonical identifier")
    if _IDENTIFIER.fullmatch(value) is None:
        raise ReferenceEvaluationError(f"{label} has an invalid identifier format")
    return value


def _required_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or value != value.lower():
        raise ReferenceEvaluationError(f"{label} must be a lowercase SHA-256")
    if _SHA256.fullmatch(value) is None:
        raise ReferenceEvaluationError(f"{label} must be a lowercase SHA-256")
    return value


def _safe_relative_path(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ReferenceEvaluationError(f"{label} must be a non-empty relative path")
    if "\\" in value:
        raise ReferenceEvaluationError(f"{label} must use POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReferenceEvaluationError(f"{label} contains an unsafe path")
    return path


def _read_regular_file(root: Path, relative: PurePosixPath, label: str) -> bytes:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise ReferenceEvaluationError(f"{label} is unavailable") from exc
        if stat.S_ISLNK(mode):
            raise ReferenceEvaluationError(f"{label} must not traverse a symbolic link")
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ReferenceEvaluationError(f"{label} escapes the evaluation media root") from exc
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise ReferenceEvaluationError(f"{label} must identify a regular file")
    if resolved.stat().st_size > _MAX_IMAGE_BYTES:
        raise ReferenceEvaluationError(f"{label} exceeds the 64 MiB safety limit")
    try:
        return resolved.read_bytes()
    except OSError as exc:
        raise ReferenceEvaluationError(f"{label} could not be read") from exc


def _quality_from_content(content: bytes, declared_mime: str) -> tuple[float, Dict[str, bool]]:
    try:
        decoded = decode_image(content)
    except ValueError as exc:
        raise ReferenceEvaluationError("evaluation image is damaged or unsupported") from exc
    if decoded.detected_mime != declared_mime:
        raise ReferenceEvaluationError(
            "evaluation image MIME does not match its decoded media type"
        )
    diagnostics = analyze_image(decoded.image, decoded.sha256)
    try:
        assessment = assess_reference_quality(diagnostics.get("quality_gate", {}))
    except ReferenceQualityError as exc:
        raise ReferenceEvaluationError(
            "evaluation image quality checks are unavailable"
        ) from exc
    return assessment.score, dict(assessment.checks)


def _load_json(path: Path) -> Dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReferenceEvaluationError(
            "evaluation manifest must be a regular non-symbolic-link file"
        )
    if path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise ReferenceEvaluationError("evaluation manifest exceeds the 8 MiB limit")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_nonstandard_constant,
        )
    except ReferenceEvaluationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReferenceEvaluationError("unable to read evaluation manifest JSON") from exc
    if not isinstance(payload, dict):
        raise ReferenceEvaluationError("evaluation manifest must be a JSON object")
    return payload


def _reference_hashes(loaded: LoadedReferenceVectorIndex) -> set[str]:
    hashes: set[str] = set()
    for reference in loaded.references:
        for view in reference.views:
            if view.input_sha256 is None:
                raise ReferenceEvaluationError(
                    "reference vector index lacks an original-media hash binding"
                )
            hashes.add(view.input_sha256)
    return hashes


def _catalog_artifacts(
    references: Sequence[ArtifactReference],
) -> Dict[str, ArtifactReference]:
    return {
        reference.artifact_id: reference
        for reference in references
        if reference.kind == ReferenceKind.CATALOG_ARTIFACT
    }


def load_frozen_evaluation_manifest(
    manifest_path: Path | str,
    *,
    library_metadata: Mapping[str, Any],
    loaded_vector_index: LoadedReferenceVectorIndex,
    indexed_reference_hashes: set[str],
) -> FrozenEvaluationManifest:
    path = Path(manifest_path)
    payload = _load_json(path)
    _strict_fields(payload, _TOP_LEVEL_FIELDS, "evaluation manifest")
    if payload.get("schema_version") != REFERENCE_EVALUATION_MANIFEST_SCHEMA_VERSION:
        raise ReferenceEvaluationError("unsupported evaluation manifest schema_version")
    _required_identifier(payload.get("evaluation_id"), "evaluation_id")
    _required_identifier(payload.get("version"), "version")
    if payload.get("library_id") != library_metadata.get("library_id"):
        raise ReferenceEvaluationError("evaluation library_id binding mismatch")
    bindings = {
        "library_manifest_sha256": library_metadata.get("manifest_sha256"),
        "embedding_index_payload_sha256": loaded_vector_index.metadata.get(
            "index_payload_sha256"
        ),
        "embedding_index_archive_sha256": loaded_vector_index.archive_sha256,
    }
    for field, expected in bindings.items():
        actual = _required_hash(payload.get(field), field)
        if actual != expected:
            raise ReferenceEvaluationError(f"evaluation {field} binding mismatch")

    protocol = payload.get("protocol")
    if not isinstance(protocol, dict):
        raise ReferenceEvaluationError("evaluation protocol must be an object")
    _strict_fields(protocol, _PROTOCOL_FIELDS, "evaluation protocol")
    protocol_id = _required_identifier(protocol.get("protocol_id"), "protocol_id")
    if protocol.get("independent_reshoots") is not True:
        raise ReferenceEvaluationError(
            "protocol.independent_reshoots must explicitly be true"
        )
    if protocol.get("exact_media_reuse_excluded") is not True:
        raise ReferenceEvaluationError(
            "protocol.exact_media_reuse_excluded must explicitly be true"
        )
    attestation = protocol.get("capture_batch_attestation")
    if (
        not isinstance(attestation, str)
        or attestation != attestation.strip()
        or len(attestation) < 20
        or len(attestation) > 1000
    ):
        raise ReferenceEvaluationError(
            "protocol.capture_batch_attestation must be a 20-1000 character statement"
        )

    queries_payload = payload.get("queries")
    if not isinstance(queries_payload, list) or len(queries_payload) < 2:
        raise ReferenceEvaluationError("evaluation requires at least two queries")
    catalog = _catalog_artifacts(loaded_vector_index.references)
    root = path.parent.resolve()
    query_ids: set[str] = set()
    image_paths: set[str] = set()
    image_hashes: set[str] = set()
    artifact_batch_pairs: set[tuple[str, str]] = set()
    queries: list[EvaluationQuery] = []
    in_library_count = 0
    open_set_count = 0

    for query_position, query_payload in enumerate(queries_payload):
        label = f"queries[{query_position}]"
        if not isinstance(query_payload, dict):
            raise ReferenceEvaluationError(f"{label} must be an object")
        _strict_fields(query_payload, _QUERY_FIELDS, label)
        query_id = _required_identifier(query_payload.get("query_id"), f"{label}.query_id")
        if query_id in query_ids:
            raise ReferenceEvaluationError("duplicate evaluation query_id")
        query_ids.add(query_id)
        capture_batch_id = _required_identifier(
            query_payload.get("capture_batch_id"), f"{label}.capture_batch_id"
        )
        expected = query_payload.get("expected_artifact_id")
        physical_object_id: Optional[str]
        if expected is None:
            open_set_count += 1
            physical_object_id = None
        else:
            expected = _required_identifier(expected, f"{label}.expected_artifact_id")
            reference = catalog.get(expected)
            if reference is None:
                raise ReferenceEvaluationError(
                    f"{label}.expected_artifact_id is not a catalog artifact"
                )
            physical_object_id = reference.metadata.get("physical_object_id")
            if not isinstance(physical_object_id, str) or not physical_object_id.strip():
                raise ReferenceEvaluationError(
                    f"catalog artifact {expected} lacks a physical_object_id binding"
                )
            pair = (expected, capture_batch_id)
            if pair in artifact_batch_pairs:
                raise ReferenceEvaluationError(
                    "one physical object/capture batch must be represented by one query"
                )
            artifact_batch_pairs.add(pair)
            in_library_count += 1

        images_payload = query_payload.get("images")
        if not isinstance(images_payload, list) or not 1 <= len(images_payload) <= 8:
            raise ReferenceEvaluationError(f"{label}.images must contain 1-8 images")
        images: list[EvaluationImage] = []
        for image_position, image_payload in enumerate(images_payload):
            image_label = f"{label}.images[{image_position}]"
            if not isinstance(image_payload, dict):
                raise ReferenceEvaluationError(f"{image_label} must be an object")
            _strict_fields(image_payload, _IMAGE_FIELDS, image_label)
            relative = _safe_relative_path(image_payload.get("path"), f"{image_label}.path")
            relative_text = relative.as_posix()
            if relative_text in image_paths:
                raise ReferenceEvaluationError("duplicate evaluation image path")
            image_paths.add(relative_text)
            declared_hash = _required_hash(
                image_payload.get("sha256"), f"{image_label}.sha256"
            )
            if declared_hash in image_hashes:
                raise ReferenceEvaluationError("duplicate evaluation image SHA-256")
            if declared_hash in indexed_reference_hashes:
                raise ReferenceEvaluationError(
                    "evaluation image reuses indexed reference media"
                )
            mime = image_payload.get("mime")
            if mime not in _SUPPORTED_MIME:
                raise ReferenceEvaluationError(f"{image_label}.mime is unsupported")
            angle = image_payload.get("angle")
            if angle not in ANGLE_VALUES:
                raise ReferenceEvaluationError(f"{image_label}.angle is invalid")
            content = _read_regular_file(root, relative, f"{image_label}.path")
            actual_hash = _sha256_bytes(content)
            if actual_hash != declared_hash:
                raise ReferenceEvaluationError(
                    f"{image_label}.sha256 does not match the image bytes"
                )
            if actual_hash in indexed_reference_hashes:
                raise ReferenceEvaluationError(
                    "evaluation image reuses indexed reference media"
                )
            image_hashes.add(actual_hash)
            quality, quality_checks = _quality_from_content(content, str(mime))
            images.append(
                EvaluationImage(
                    relative_path=relative_text,
                    sha256=actual_hash,
                    mime_type=str(mime),
                    angle=str(angle),
                    content=content,
                    quality=quality,
                    quality_checks=quality_checks,
                )
            )
        queries.append(
            EvaluationQuery(
                query_id=query_id,
                capture_batch_id=capture_batch_id,
                expected_artifact_id=expected,
                expected_physical_object_id=physical_object_id,
                images=tuple(images),
            )
        )

    if in_library_count < 1 or open_set_count < 1:
        raise ReferenceEvaluationError(
            "evaluation requires at least one in-library and one open-set query"
        )
    return FrozenEvaluationManifest(
        payload=payload,
        manifest_sha256=_sha256_json(payload),
        queries=tuple(queries),
        protocol_id=protocol_id,
        capture_batch_attestation=attestation,
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator < 1:
        raise ReferenceEvaluationError("evaluation metric denominator is zero")
    return round(numerator / denominator, 12)


def _metric_value(value: float) -> float:
    return round(float(value), 8)


def _candidate_values(
    values: Sequence[float],
    *,
    lower: float,
    upper: float,
    anchors: Sequence[float],
    limit: int,
) -> tuple[float, ...]:
    candidates = {round(min(upper, max(lower, float(value))), 6) for value in anchors}
    for value in values:
        clipped = min(upper, max(lower, float(value)))
        candidates.add(round(clipped, 6))
        if clipped < upper:
            candidates.add(round(min(upper, clipped + 0.000001), 6))
    ordered = sorted(candidates)
    if len(ordered) <= limit:
        return tuple(ordered)
    positions = np.linspace(0, len(ordered) - 1, num=limit, dtype=np.int64)
    return tuple(ordered[int(position)] for position in positions)


def _select_thresholds(
    raw: Sequence[_RawQueryResult],
    *,
    target_far: float,
    policy_suffix: str,
) -> tuple[RetrievalThresholds, Dict[str, Any]]:
    expected = np.asarray(
        [item.query.expected_artifact_id is not None for item in raw], dtype=bool
    )
    top_correct = np.asarray([item.top1_correct for item in raw], dtype=bool)
    scores = np.asarray([item.top_score for item in raw], dtype=np.float64)
    margins = np.asarray([item.runner_up_margin for item in raw], dtype=np.float64)
    coverages = np.asarray([item.coverage for item in raw], dtype=np.float64)
    qualities = np.asarray([item.quality for item in raw], dtype=np.float64)
    complementary_angle_counts = np.asarray(
        [item.complementary_angle_count for item in raw], dtype=np.int64
    )
    no_conflict = np.asarray(
        [not item.counterfeit_conflict for item in raw], dtype=bool
    )
    default = RetrievalThresholds()
    complementary_capture_pass = (
        complementary_angle_counts >= default.same_artifact_min_complementary_angles
    )
    score_candidates = _candidate_values(
        scores,
        lower=-1.0,
        upper=1.0,
        anchors=(-1.0, default.related_min_score, default.same_artifact_min_score, 1.0),
        limit=24,
    )
    margin_candidates = _candidate_values(
        margins,
        lower=0.0,
        upper=2.0,
        anchors=(0.0, default.same_artifact_min_margin, 2.0),
        limit=16,
    )
    coverage_candidates = _candidate_values(
        coverages,
        lower=0.0,
        upper=1.0,
        anchors=(0.0, default.related_min_coverage, default.same_artifact_min_coverage, 1.0),
        limit=12,
    )
    quality_candidates = _candidate_values(
        qualities,
        lower=0.0,
        upper=1.0,
        anchors=(0.0, default.related_min_quality, default.same_artifact_min_quality, 1.0),
        limit=12,
    )
    in_count = int(expected.sum())
    open_count = int((~expected).sum())
    best_feasible: Optional[tuple[tuple[Any, ...], tuple[float, ...], np.ndarray]] = None
    best_any: Optional[tuple[tuple[Any, ...], tuple[float, ...], np.ndarray]] = None
    candidates_evaluated = 0

    for score_threshold in score_candidates:
        score_pass = scores >= score_threshold
        for margin_threshold in margin_candidates:
            score_margin_pass = score_pass & (margins >= margin_threshold)
            for coverage_threshold in coverage_candidates:
                partial = score_margin_pass & (coverages >= coverage_threshold)
                for quality_threshold in quality_candidates:
                    accepted = (
                        partial
                        & (qualities >= quality_threshold)
                        & complementary_capture_pass
                        & no_conflict
                    )
                    candidates_evaluated += 1
                    correct_count = int(np.sum(accepted & expected & top_correct))
                    false_accept_count = int(np.sum(accepted & ~expected))
                    far = false_accept_count / open_count
                    thresholds = (
                        score_threshold,
                        margin_threshold,
                        coverage_threshold,
                        quality_threshold,
                    )
                    conservative = (
                        score_threshold,
                        margin_threshold,
                        coverage_threshold,
                        quality_threshold,
                    )
                    feasible_rank = (
                        correct_count,
                        -false_accept_count,
                        *conservative,
                    )
                    any_rank = (
                        -false_accept_count,
                        correct_count,
                        *conservative,
                    )
                    candidate = (feasible_rank, thresholds, accepted.copy())
                    if far <= target_far + 1e-12 and (
                        best_feasible is None or feasible_rank > best_feasible[0]
                    ):
                        best_feasible = candidate
                    if best_any is None or any_rank > best_any[0]:
                        best_any = (any_rank, thresholds, accepted.copy())

    selected = best_feasible or best_any
    if selected is None:
        raise ReferenceEvaluationError("threshold search produced no candidate")
    _, values, accepted = selected
    score_threshold, margin_threshold, coverage_threshold, quality_threshold = values
    correct_count = int(np.sum(accepted & expected & top_correct))
    false_accept_count = int(np.sum(accepted & ~expected))
    wrong_identity_accept_count = int(np.sum(accepted & expected & ~top_correct))
    far = false_accept_count / open_count
    thresholds = RetrievalThresholds(
        policy_id=f"relicscope-evaluation-suggestion-v1-{policy_suffix[:12]}",
        same_artifact_min_score=score_threshold,
        same_artifact_min_margin=margin_threshold,
        same_artifact_min_coverage=coverage_threshold,
        same_artifact_min_quality=quality_threshold,
        same_artifact_min_complementary_angles=(
            default.same_artifact_min_complementary_angles
        ),
        related_min_score=min(default.related_min_score, score_threshold),
        related_min_coverage=min(default.related_min_coverage, coverage_threshold),
        related_min_quality=min(default.related_min_quality, quality_threshold),
        view_match_min_similarity=default.view_match_min_similarity,
        minimum_view_quality=default.minimum_view_quality,
        counterfeit_alert_min_score=default.counterfeit_alert_min_score,
        counterfeit_alert_min_coverage=default.counterfeit_alert_min_coverage,
        counterfeit_alert_min_quality=default.counterfeit_alert_min_quality,
        view_score_weight=default.view_score_weight,
    )
    return thresholds, {
        "accepted": accepted.tolist(),
        "correct_accept_count": correct_count,
        "false_accept_count": false_accept_count,
        "wrong_identity_accept_count": wrong_identity_accept_count,
        "in_library_query_count": in_count,
        "open_set_query_count": open_count,
        "target_far": target_far,
        "observed_far": far,
        "target_far_met": far <= target_far + 1e-12,
        "minimum_complementary_angle_count": (
            default.same_artifact_min_complementary_angles
        ),
        "candidates_evaluated": candidates_evaluated,
        "objective": (
            "maximize correctly accepted in-library queries subject to FAR; then "
            "minimize false accepts and prefer conservative gates"
        ),
    }


class ReferenceRecognitionEvaluator:
    """Run a frozen independent-reshoot evaluation against an immutable local index."""

    def __init__(
        self,
        *,
        metadata_index_path: Path | str,
        vector_index_path: Path | str,
        embedding_client: LocalImageEmbeddingClient,
        target_far: float = 0.02,
        top_k: int = 5,
        minimum_reference_physical_objects: int = MINIMUM_REFERENCE_PHYSICAL_OBJECTS,
        minimum_open_set_queries: int = MINIMUM_OPEN_SET_QUERIES,
    ) -> None:
        self.metadata_index_path = Path(metadata_index_path)
        self.vector_index_path = Path(vector_index_path)
        self.embedding_client = embedding_client
        self.target_far = float(target_far)
        self.top_k = int(top_k)
        self.minimum_reference_physical_objects = int(
            minimum_reference_physical_objects
        )
        self.minimum_open_set_queries = int(minimum_open_set_queries)
        if not math.isfinite(self.target_far) or not 0.0 <= self.target_far <= 1.0:
            raise ValueError("target FAR must be finite and between 0 and 1")
        if not 1 <= self.top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        if self.minimum_reference_physical_objects < MINIMUM_REFERENCE_PHYSICAL_OBJECTS:
            raise ValueError(
                "minimum reference physical objects cannot be below the safety floor "
                f"of {MINIMUM_REFERENCE_PHYSICAL_OBJECTS}"
            )
        if self.minimum_open_set_queries < MINIMUM_OPEN_SET_QUERIES:
            raise ValueError(
                "minimum open-set queries cannot be below the safety floor "
                f"of {MINIMUM_OPEN_SET_QUERIES}"
            )
        if not self.embedding_client.immutable_identity_configured:
            raise ValueError("an immutable private embedding identity is required")

    async def evaluate(self, manifest_path: Path | str) -> Dict[str, Any]:
        metadata_index = ReferenceLibraryIndex(self.metadata_index_path)
        try:
            library_metadata = metadata_index.metadata()
            indexed_rows = list(metadata_index.iter_images())
        except ReferenceIndexValidationError as exc:
            raise ReferenceEvaluationError(
                "reference metadata index failed integrity validation"
            ) from exc
        loaded = load_reference_vector_index(
            self.vector_index_path,
            self.metadata_index_path,
            expected_model=self.embedding_client.model,
            expected_model_source=self.embedding_client.model_source,
            expected_model_revision=self.embedding_client.model_revision,
            expected_dimension=self.embedding_client.expected_dimension,
        )
        row_hashes = {
            _required_hash(row.get("sha256"), "indexed reference image SHA-256")
            for row in indexed_rows
        }
        vector_hashes = _reference_hashes(loaded)
        if row_hashes != vector_hashes:
            raise ReferenceEvaluationError(
                "reference metadata/vector image-hash coverage is inconsistent"
            )
        frozen = load_frozen_evaluation_manifest(
            manifest_path,
            library_metadata=library_metadata,
            loaded_vector_index=loaded,
            indexed_reference_hashes=row_hashes,
        )
        catalog = _catalog_artifacts(loaded.references)
        catalog_physical_objects: Dict[str, str] = {}
        for artifact_id, reference in catalog.items():
            physical_object_id = reference.metadata.get("physical_object_id")
            if not isinstance(physical_object_id, str) or not physical_object_id.strip():
                raise ReferenceEvaluationError(
                    f"catalog artifact {artifact_id} lacks a physical_object_id binding"
                )
            if physical_object_id in catalog_physical_objects:
                raise ReferenceEvaluationError(
                    "catalog physical_object_id values must be unique for frozen "
                    "instance-recognition calibration"
                )
            catalog_physical_objects[physical_object_id] = artifact_id
        if len(catalog_physical_objects) < self.minimum_reference_physical_objects:
            raise ReferenceEvaluationError(
                "calibration requires at least "
                f"{self.minimum_reference_physical_objects} distinct catalog physical objects"
            )
        covered_physical_objects = {
            query.expected_physical_object_id
            for query in frozen.queries
            if query.expected_physical_object_id is not None
        }
        missing_objects = sorted(set(catalog_physical_objects) - covered_physical_objects)
        if missing_objects:
            preview = ", ".join(missing_objects[:5])
            raise ReferenceEvaluationError(
                "frozen evaluation must include an independent reshoot for every "
                f"catalog physical object; missing {len(missing_objects)} ({preview})"
            )
        open_set_query_count = sum(
            query.expected_artifact_id is None for query in frozen.queries
        )
        if open_set_query_count < self.minimum_open_set_queries:
            raise ReferenceEvaluationError(
                "calibration requires at least "
                f"{self.minimum_open_set_queries} independent open-set queries"
            )
        raw_policy = RetrievalThresholds(policy_id="relicscope-evaluation-raw-v1")
        engine = ArtifactRetrievalEngine(
            loaded.references,
            thresholds=raw_policy,
            backend="numpy",
            embedding_space_id=(
                f"{loaded.metadata['model_source']}@{loaded.metadata['model_revision']}"
            ),
            reference_library_id=str(loaded.metadata["library_id"]),
            catalog_manifest_sha256=str(loaded.metadata["manifest_sha256"]),
            calibration_record_sha256="0" * 64,
            embedding_model_source=str(loaded.metadata["model_source"]),
            embedding_model_revision=str(loaded.metadata["model_revision"]),
        )
        if engine.backend_name != "numpy-exact-cosine":
            raise ReferenceEvaluationError("evaluation requires the exact NumPy backend")
        raw_results: list[_RawQueryResult] = []
        retrieval_top_k = max(5, self.top_k)
        for query in frozen.queries:
            raw_results.append(
                await self._evaluate_query(query, engine, retrieval_top_k)
            )

        thresholds, selection = _select_thresholds(
            raw_results,
            target_far=self.target_far,
            policy_suffix=frozen.manifest_sha256,
        )
        accepted = [bool(value) for value in selection.pop("accepted")]
        in_library = [item for item in raw_results if item.query.expected_artifact_id]
        open_set = [item for item in raw_results if item.query.expected_artifact_id is None]
        top1 = _rate(sum(item.top1_correct for item in in_library), len(in_library))
        top5 = _rate(sum(item.top5_correct for item in in_library), len(in_library))
        correct_accepts = sum(
            accepted[index] and item.top1_correct
            for index, item in enumerate(raw_results)
            if item.query.expected_artifact_id is not None
        )
        false_accepts = sum(
            accepted[index]
            for index, item in enumerate(raw_results)
            if item.query.expected_artifact_id is None
        )
        far = _rate(false_accepts, len(open_set))
        frr = round(1.0 - _rate(correct_accepts, len(in_library)), 12)
        per_view_counts: Dict[str, list[int]] = {}
        for item in in_library:
            for angle, correct in item.per_view_top1:
                counts = per_view_counts.setdefault(angle, [0, 0])
                counts[0] += int(correct)
                counts[1] += 1
        per_view_recall = {
            angle: _rate(counts[0], counts[1])
            for angle, counts in sorted(per_view_counts.items())
        }
        metrics = {
            "top1": top1,
            "top5": top5,
            "far": far,
            "frr": frr,
            "open_set_rejection_rate": round(1.0 - far, 12),
            "per_view_recall": per_view_recall,
        }
        query_outputs = [
            self._query_output(item, accepted[index])
            for index, item in enumerate(raw_results)
        ]
        capture_binding = [
            {
                "query_id": query.query_id,
                "capture_batch_id": query.capture_batch_id,
                "expected_artifact_id": query.expected_artifact_id,
                "expected_physical_object_id": query.expected_physical_object_id,
                "image_sha256s": [image.sha256 for image in query.images],
            }
            for query in sorted(frozen.queries, key=lambda item: item.query_id)
        ]
        independent_capture_hash = _sha256_json(capture_binding)
        threshold_payload = asdict(thresholds)
        evaluation_details = {
            "schema_version": REFERENCE_EVALUATION_RESULT_SCHEMA_VERSION,
            "frozen_evaluation_manifest_sha256": frozen.manifest_sha256,
            "independent_capture_batch_sha256": independent_capture_hash,
            "reference_library_id": loaded.metadata["library_id"],
            "library_manifest_sha256": loaded.metadata["manifest_sha256"],
            "embedding_index_payload_sha256": loaded.metadata["index_payload_sha256"],
            "embedding_index_archive_sha256": loaded.archive_sha256,
            "embedding_model_source": loaded.metadata["model_source"],
            "embedding_model_revision": loaded.metadata["model_revision"],
            "instruction_sha256": loaded.metadata["instruction_sha256"],
            "reference_quality_algorithm_id": loaded.metadata[
                "reference_quality_algorithm_id"
            ],
            "backend": engine.backend_name,
            "raw_scoring_policy": asdict(raw_policy),
            "threshold_selection": selection,
            "metrics": metrics,
            "thresholds": threshold_payload,
            "calibration_qualification": {
                "minimum_reference_physical_objects": self.minimum_reference_physical_objects,
                "catalog_physical_object_count": len(catalog_physical_objects),
                "covered_catalog_physical_object_count": len(
                    covered_physical_objects
                ),
                "all_catalog_physical_objects_covered": True,
                "minimum_open_set_queries": self.minimum_open_set_queries,
                "open_set_query_count": len(open_set),
            },
            "per_view_counts": {
                angle: {"top1_correct": counts[0], "query_view_count": counts[1]}
                for angle, counts in sorted(per_view_counts.items())
            },
            "queries": query_outputs,
            "capture_batch_binding": capture_binding,
            "protocol_interpretation": (
                "Evaluation query media are held out as complete physical-object/capture-"
                "batch groups and were independently reshot. The corresponding catalog "
                "object remains in the gallery because this protocol measures instance "
                "recognition, not recognition of an unseen object."
            ),
            "metric_definitions": {
                "top1": "unthresholded catalog Top-1 recall for in-library reshoots",
                "top5": "unthresholded catalog Top-5 recall for in-library reshoots",
                "far": "open-set queries that passed all suggested identity gates",
                "frr": "in-library queries not correctly accepted by all suggested gates",
                "per_view_recall": "single-view unthresholded Top-1 recall by declared angle",
            },
            "boundary": EVALUATION_BOUNDARY,
        }
        physical_objects = {
            item.query.expected_physical_object_id
            for item in in_library
            if item.query.expected_physical_object_id
        }
        output = {
            "schema_version": REFERENCE_CALIBRATION_SCHEMA_VERSION,
            "calibration_status": "CALIBRATED",
            "created_at": _utc_now(),
            "evaluation_result_hash_algorithm": EVALUATION_RESULT_HASH_ALGORITHM,
            "library_manifest_sha256": loaded.metadata["manifest_sha256"],
            "embedding_index_payload_sha256": loaded.metadata["index_payload_sha256"],
            "embedding_model_source": loaded.metadata["model_source"],
            "embedding_model_revision": loaded.metadata["model_revision"],
            "instruction_sha256": loaded.metadata["instruction_sha256"],
            "reference_quality_algorithm_id": loaded.metadata[
                "reference_quality_algorithm_id"
            ],
            "frozen_evaluation_manifest_sha256": frozen.manifest_sha256,
            "independent_capture_batch_sha256": independent_capture_hash,
            "evaluation_protocol": {
                "protocol_id": frozen.protocol_id,
                "held_out_by_physical_object": True,
                "held_out_physical_object_count": len(physical_objects),
                "independent_reshoots": True,
                "independent_reshoot_query_count": len(frozen.queries),
                "exact_media_reuse_excluded": True,
                "in_library_query_count": len(in_library),
                "open_set_negative_count": len(open_set),
            },
            "metrics": metrics,
            "thresholds": threshold_payload,
            "evaluation_details": evaluation_details,
            "capture_batch_attestation": frozen.capture_batch_attestation,
            "boundary": EVALUATION_BOUNDARY,
            "notes": (
                "This unsigned record is a deterministic threshold suggestion from the "
                "declared frozen dataset. Review governance and measurements before sealing."
            ),
        }
        output["evaluation_result_sha256"] = calculate_evaluation_result_sha256(output)
        return output

    async def _evaluate_query(
        self,
        query: EvaluationQuery,
        engine: ArtifactRetrievalEngine,
        retrieval_top_k: int,
    ) -> _RawQueryResult:
        inputs = tuple(
            EmbeddingImage(
                content=image.content,
                mime_type=image.mime_type,
                sha256=image.sha256,
            )
            for image in query.images
        )
        run = await self.embedding_client.embed(inputs)
        if run.get("available") is not True:
            raise ReferenceEvaluationError(
                f"verified embedding unavailable for query {query.query_id}"
            )
        expected_input_hashes = [item.sha256 for item in inputs]
        if run.get("input_hashes") != expected_input_hashes:
            raise ReferenceEvaluationError(
                f"embedding input binding mismatch for query {query.query_id}"
            )
        try:
            views = embedded_views_from_verified_run(
                run,
                view_ids=[
                    f"{query.query_id}:view:{position + 1}"
                    for position in range(len(inputs))
                ],
                qualities=[image.quality for image in query.images],
                angles=[image.angle for image in query.images],
            )
        except (ValueError, RuntimeError) as exc:
            raise ReferenceEvaluationError(
                f"verified embedding unavailable for query {query.query_id}"
            ) from exc
        result = engine.retrieve(views, top_k=retrieval_top_k)
        if result.exact_media_hash_matches:
            raise ReferenceEvaluationError(
                "evaluation query matched original indexed media; evaluation stopped"
            )
        candidates = tuple(
            {
                "rank": rank,
                "artifact_id": hit.artifact_id,
                "score": _metric_value(hit.score),
                "runner_up_margin": None,
                "coverage": _metric_value(hit.coverage),
                "quality": _metric_value(hit.quality_score),
                "complementary_angle_count": len(hit.complementary_angles),
                "complementary_angles": list(hit.complementary_angles),
                "view_score": _metric_value(hit.view_score),
                "centroid_score": (
                    _metric_value(hit.centroid_score)
                    if hit.centroid_score is not None
                    else None
                ),
            }
            for rank, hit in enumerate(result.catalog_hits[: self.top_k], start=1)
        )
        if result.catalog_hits:
            top = result.catalog_hits[0]
            top_id: Optional[str] = top.artifact_id
            top_score = float(top.score)
            coverage = float(top.coverage)
            quality = float(top.quality_score)
            complementary_angle_count = len(top.complementary_angles)
            margin = (
                float(top.score - result.catalog_hits[1].score)
                if len(result.catalog_hits) > 1
                else 2.0
            )
        else:
            top_id = None
            top_score = -1.0
            coverage = 0.0
            quality = 0.0
            complementary_angle_count = 0
            margin = 0.0
        if candidates:
            first = dict(candidates[0])
            first["runner_up_margin"] = _metric_value(margin)
            candidates = (first, *candidates[1:])
        expected = query.expected_artifact_id
        ranked_ids = [hit.artifact_id for hit in result.catalog_hits]
        per_view: list[tuple[str, bool]] = []
        if expected is not None:
            for view, image in zip(views, query.images, strict=True):
                view_result = engine.retrieve((view,), top_k=1)
                view_top = (
                    view_result.catalog_hits[0].artifact_id
                    if view_result.catalog_hits
                    else None
                )
                per_view.append((image.angle, view_top == expected))
        embedding_run = {
            key: value
            for key, value in run.items()
            if key not in {"vectors", "output_hashes"}
        }
        return _RawQueryResult(
            query=query,
            views=views,
            top_candidates=candidates,
            top_artifact_id=top_id,
            top_score=top_score,
            runner_up_margin=margin,
            coverage=coverage,
            quality=quality,
            complementary_angle_count=complementary_angle_count,
            counterfeit_conflict=result.counterfeit_signal.competes_with_top_catalog,
            top1_correct=expected is not None and top_id == expected,
            top5_correct=expected is not None and expected in ranked_ids[:5],
            per_view_top1=tuple(per_view),
            embedding_run=embedding_run,
        )

    @staticmethod
    def _query_output(item: _RawQueryResult, accepted: bool) -> Dict[str, Any]:
        expected = item.query.expected_artifact_id
        accepted_id = item.top_artifact_id if accepted else None
        if expected is None and accepted:
            decision = "OPEN_SET_FALSE_ACCEPT"
        elif expected is None:
            decision = "OPEN_SET_REJECTED"
        elif accepted and item.top1_correct:
            decision = "CORRECT_IDENTITY_ACCEPT"
        elif accepted:
            decision = "WRONG_IDENTITY_ACCEPT"
        else:
            decision = "IN_LIBRARY_REJECTED"
        return {
            "query_id": item.query.query_id,
            "capture_batch_id": item.query.capture_batch_id,
            "expected_artifact_id": expected,
            "expected_physical_object_id": item.query.expected_physical_object_id,
            "image_count": len(item.query.images),
            "angles": [image.angle for image in item.query.images],
            "image_sha256s": [image.sha256 for image in item.query.images],
            "raw_top_k": list(item.top_candidates),
            "raw_top1_artifact_id": item.top_artifact_id,
            "raw_top1_score": _metric_value(item.top_score),
            "raw_runner_up_margin": _metric_value(item.runner_up_margin),
            "raw_coverage": _metric_value(item.coverage),
            "raw_quality": _metric_value(item.quality),
            "raw_complementary_angle_count": item.complementary_angle_count,
            "counterfeit_conflict": item.counterfeit_conflict,
            "top1_correct": item.top1_correct,
            "top5_correct": item.top5_correct,
            "accepted_candidate_id": accepted_id,
            "decision": decision,
            "quality_diagnostics": [
                {
                    "path": image.relative_path,
                    "quality": _metric_value(image.quality),
                    "quality_algorithm_id": REFERENCE_QUALITY_ALGORITHM_ID,
                    "checks": dict(sorted(image.quality_checks.items())),
                }
                for image in item.query.images
            ],
            "embedding_run": dict(item.embedding_run),
        }


def write_evaluation_output(path: Path | str, payload: Mapping[str, Any]) -> Path:
    destination = Path(path)
    if destination.exists() and destination.is_symlink():
        raise ReferenceEvaluationError(
            "refusing to replace a symbolic-link evaluation output"
        )
    resolved = destination.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved.name}.", suffix=".tmp", dir=resolved.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, resolved)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return resolved


__all__ = [
    "EVALUATION_BOUNDARY",
    "EVALUATION_RESULT_HASH_ALGORITHM",
    "MINIMUM_OPEN_SET_QUERIES",
    "MINIMUM_REFERENCE_PHYSICAL_OBJECTS",
    "REFERENCE_EVALUATION_MANIFEST_SCHEMA_VERSION",
    "REFERENCE_EVALUATION_RESULT_SCHEMA_VERSION",
    "EvaluationImage",
    "EvaluationQuery",
    "FrozenEvaluationManifest",
    "ReferenceEvaluationError",
    "ReferenceRecognitionEvaluator",
    "calculate_evaluation_result_sha256",
    "evaluation_result_hash_payload",
    "load_frozen_evaluation_manifest",
    "write_evaluation_output",
]
