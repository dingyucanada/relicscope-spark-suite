from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

import numpy as np


SIMILARITY_LIMITATION = (
    "This result describes image-embedding similarity within the indexed reference "
    "library only. A same-artifact candidate, a related candidate, or similarity to "
    "a registered counterfeit exemplar is not an authenticity, date, kiln, maker, "
    "value, grade, or legal appraisal conclusion."
)
AUTHENTICITY_NOT_ASSESSED = "NOT_ASSESSED"
COUNTERFEIT_SIGNAL_LIMITATION = (
    "Similarity to a registered counterfeit exemplar is a cross-check signal only; "
    "it does not by itself establish that the query object is counterfeit or genuine."
)

# Only acquisition angles that describe a complementary whole-object viewpoint
# can support a same-physical-object decision.  Detail, mark, damage and missing
# labels remain useful retrieval inputs, but they cannot manufacture viewpoint
# coverage.
_COMPLEMENTARY_VIEW_ANGLES = frozenset(
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
_ANGLE_ALIASES = {
    "LEFT": "LEFT_PROFILE",
    "RIGHT": "RIGHT_PROFILE",
    "BOTTOM": "BASE",
    "UNDERSIDE": "BASE",
    "REVERSE": "BACK",
}


class BackendUnavailable(RuntimeError):
    """Raised when an optional local vector-search backend cannot be used."""


class EmbeddingRunUnavailable(RuntimeError):
    """Raised when a verified local image-embedding run is unavailable or invalid."""


class ReferenceKind(str, Enum):
    CATALOG_ARTIFACT = "catalog_artifact"
    KNOWN_COUNTERFEIT = "known_counterfeit"


class NegativeReviewStatus(str, Enum):
    VERIFIED = "verified"
    PROVISIONAL = "provisional"
    DISPUTED = "disputed"
    REJECTED = "rejected"


@dataclass(frozen=True)
class NegativeReferenceControl:
    """Explicit governance state for a registered counterfeit exemplar set."""

    record_id: str
    review_status: NegativeReviewStatus
    admissible_for_signal: bool
    signal_weight: float = 1.0

    def __post_init__(self) -> None:
        if not str(self.record_id).strip():
            raise ValueError("negative reference record_id must not be empty")
        status = NegativeReviewStatus(self.review_status)
        weight = float(self.signal_weight)
        if not math.isfinite(weight) or not 0.0 <= weight <= 1.0:
            raise ValueError("negative reference signal_weight must be between 0 and 1")
        if self.admissible_for_signal and status in {
            NegativeReviewStatus.DISPUTED,
            NegativeReviewStatus.REJECTED,
        }:
            raise ValueError(
                "disputed or rejected negative references cannot raise signals"
            )
        object.__setattr__(self, "record_id", str(self.record_id).strip())
        object.__setattr__(self, "review_status", status)
        object.__setattr__(self, "signal_weight", weight)


@dataclass(frozen=True)
class EmbeddedView:
    """One locally produced image embedding and its acquisition quality score."""

    view_id: str
    vector: Sequence[float]
    quality: float = 1.0
    angle: Optional[str] = None
    input_sha256: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.view_id).strip():
            raise ValueError("view_id must not be empty")
        if (
            not math.isfinite(float(self.quality))
            or not 0.0 <= float(self.quality) <= 1.0
        ):
            raise ValueError("view quality must be finite and between 0 and 1")
        object.__setattr__(self, "view_id", str(self.view_id).strip())
        object.__setattr__(self, "quality", float(self.quality))
        object.__setattr__(self, "vector", tuple(float(value) for value in self.vector))
        object.__setattr__(
            self,
            "input_sha256",
            _validate_optional_sha256(self.input_sha256, label="view input_sha256"),
        )
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class ArtifactReference:
    """A catalog object or a provenance-checked counterfeit reference set."""

    artifact_id: str
    views: Sequence[EmbeddedView]
    kind: ReferenceKind = ReferenceKind.CATALOG_ARTIFACT
    negative_control: Optional[NegativeReferenceControl] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        artifact_id = str(self.artifact_id).strip()
        if not artifact_id:
            raise ValueError("artifact_id must not be empty")
        views = tuple(self.views)
        if not views:
            raise ValueError("an artifact reference must contain at least one view")
        view_ids = [view.view_id for view in views]
        if len(view_ids) != len(set(view_ids)):
            raise ValueError(f"duplicate view_id in artifact reference {artifact_id}")
        object.__setattr__(self, "artifact_id", artifact_id)
        object.__setattr__(self, "views", views)
        kind = ReferenceKind(self.kind)
        if kind == ReferenceKind.KNOWN_COUNTERFEIT and self.negative_control is None:
            raise ValueError(
                "known counterfeit references require explicit negative_control"
            )
        if kind == ReferenceKind.CATALOG_ARTIFACT and self.negative_control is not None:
            raise ValueError("catalog references cannot carry negative_control")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class RetrievalThresholds:
    """Versioned decision policy; defaults are demo values, not field calibration."""

    policy_id: str = "relicscope-demo-open-set-v1-unvalidated"
    same_artifact_min_score: float = 0.86
    same_artifact_min_margin: float = 0.05
    same_artifact_min_coverage: float = 0.60
    same_artifact_min_quality: float = 0.55
    same_artifact_min_complementary_angles: int = 3
    related_min_score: float = 0.62
    related_min_coverage: float = 0.20
    related_min_quality: float = 0.30
    view_match_min_similarity: float = 0.76
    minimum_view_quality: float = 0.25
    counterfeit_alert_min_score: float = 0.80
    counterfeit_alert_min_coverage: float = 0.34
    counterfeit_alert_min_quality: float = 0.40
    view_score_weight: float = 0.80

    def __post_init__(self) -> None:
        if not str(self.policy_id).strip():
            raise ValueError("policy_id must not be empty")
        similarity_fields = (
            "same_artifact_min_score",
            "related_min_score",
            "view_match_min_similarity",
            "counterfeit_alert_min_score",
        )
        unit_fields = (
            "same_artifact_min_coverage",
            "same_artifact_min_quality",
            "related_min_coverage",
            "related_min_quality",
            "minimum_view_quality",
            "counterfeit_alert_min_coverage",
            "counterfeit_alert_min_quality",
            "view_score_weight",
        )
        for name in similarity_fields:
            value = float(getattr(self, name))
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and between -1 and 1")
        for name in unit_fields:
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be finite and between 0 and 1")
        margin = float(self.same_artifact_min_margin)
        if not math.isfinite(margin) or not 0.0 <= margin <= 2.0:
            raise ValueError("same_artifact_min_margin must be between 0 and 2")
        angle_count = self.same_artifact_min_complementary_angles
        if (
            isinstance(angle_count, bool)
            or not isinstance(angle_count, int)
            or not 1 <= angle_count <= len(_COMPLEMENTARY_VIEW_ANGLES)
        ):
            raise ValueError(
                "same_artifact_min_complementary_angles must be an integer between "
                f"1 and {len(_COMPLEMENTARY_VIEW_ANGLES)}"
            )
        if self.same_artifact_min_score < self.related_min_score:
            raise ValueError(
                "same-artifact score threshold must be at least the related threshold"
            )
        if self.same_artifact_min_coverage < self.related_min_coverage:
            raise ValueError(
                "same-artifact coverage threshold must be at least the related threshold"
            )
        if self.same_artifact_min_quality < self.related_min_quality:
            raise ValueError(
                "same-artifact quality threshold must be at least the related threshold"
            )


@dataclass(frozen=True)
class SearchBatch:
    scores: np.ndarray
    indices: np.ndarray


class VectorSearchBackend(Protocol):
    name: str

    def build(self, vectors: np.ndarray) -> None: ...

    def search(self, query_vectors: np.ndarray, top_k: int) -> SearchBatch: ...


def _as_normalized_matrix(values: Any, *, label: str) -> np.ndarray:
    try:
        matrix = np.asarray(values, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain numeric vectors") from exc
    if matrix.ndim != 2 or matrix.shape[0] < 1 or matrix.shape[1] < 1:
        raise ValueError(f"{label} must be a non-empty 2D vector matrix")
    if not np.all(np.isfinite(matrix)):
        raise ValueError(f"{label} contains a non-finite value")
    norms = np.linalg.norm(matrix, axis=1)
    if np.any(norms <= 1e-12):
        raise ValueError(f"{label} contains a zero-length vector")
    return np.ascontiguousarray(matrix / norms[:, None], dtype=np.float32)


class NumpyCosineBackend:
    """Exact local cosine search used as the dependency-light fallback."""

    name = "numpy-exact-cosine"

    def __init__(self) -> None:
        self._vectors: Optional[np.ndarray] = None

    def build(self, vectors: np.ndarray) -> None:
        self._vectors = _as_normalized_matrix(vectors, label="index vectors")

    def search(self, query_vectors: np.ndarray, top_k: int) -> SearchBatch:
        if self._vectors is None:
            raise RuntimeError("vector backend has not been built")
        queries = _as_normalized_matrix(query_vectors, label="query vectors")
        if queries.shape[1] != self._vectors.shape[1]:
            raise ValueError("query and reference vector dimensions do not match")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        limit = min(int(top_k), self._vectors.shape[0])
        similarities = np.clip(queries @ self._vectors.T, -1.0, 1.0)
        indices = np.argsort(-similarities, axis=1, kind="stable")[:, :limit]
        scores = np.take_along_axis(similarities, indices, axis=1)
        return SearchBatch(
            scores=np.asarray(scores, dtype=np.float32),
            indices=np.asarray(indices, dtype=np.int64),
        )


class FaissCosineBackend:
    """Adapter for optional FAISS exact inner-product search on normalized vectors."""

    name = "faiss-index-flat-ip"

    def __init__(self, module: Any = None) -> None:
        if module is None:
            try:
                import faiss as module  # type: ignore[import-not-found]
            except ImportError as exc:
                raise BackendUnavailable("FAISS is not installed") from exc
        self._faiss = module
        self._index: Any = None
        self._dimension: Optional[int] = None

    def build(self, vectors: np.ndarray) -> None:
        normalized = _as_normalized_matrix(vectors, label="index vectors")
        try:
            index = self._faiss.IndexFlatIP(int(normalized.shape[1]))
            index.add(normalized)
        except Exception as exc:
            raise BackendUnavailable(
                f"FAISS initialization failed: {type(exc).__name__}"
            ) from exc
        self._index = index
        self._dimension = int(normalized.shape[1])

    def search(self, query_vectors: np.ndarray, top_k: int) -> SearchBatch:
        if self._index is None or self._dimension is None:
            raise RuntimeError("vector backend has not been built")
        queries = _as_normalized_matrix(query_vectors, label="query vectors")
        if queries.shape[1] != self._dimension:
            raise ValueError("query and reference vector dimensions do not match")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        limit = min(int(top_k), int(self._index.ntotal))
        scores, indices = self._index.search(queries, limit)
        return SearchBatch(
            scores=np.asarray(np.clip(scores, -1.0, 1.0), dtype=np.float32),
            indices=np.asarray(indices, dtype=np.int64),
        )


class CuVSCosineBackend:
    """Adapter for optional cuVS brute-force cosine distance."""

    name = "cuvs-brute-force-cosine"

    def __init__(
        self, *, brute_force_module: Any = None, cupy_module: Any = None
    ) -> None:
        if brute_force_module is None:
            try:
                from cuvs.neighbors import brute_force as brute_force_module  # type: ignore[import-not-found]
            except ImportError as exc:
                raise BackendUnavailable("cuVS is not installed") from exc
        if cupy_module is None:
            try:
                import cupy as cupy_module  # type: ignore[import-not-found]
            except ImportError as exc:
                raise BackendUnavailable(
                    "CuPy is required by the cuVS adapter"
                ) from exc
        self._brute_force = brute_force_module
        self._cupy = cupy_module
        self._index: Any = None
        self._dimension: Optional[int] = None
        self._size = 0

    def build(self, vectors: np.ndarray) -> None:
        normalized = _as_normalized_matrix(vectors, label="index vectors")
        try:
            dataset = self._cupy.asarray(normalized)
            self._index = self._brute_force.build(dataset, metric="cosine")
        except Exception as exc:
            raise BackendUnavailable(
                f"cuVS initialization failed: {type(exc).__name__}"
            ) from exc
        self._dimension = int(normalized.shape[1])
        self._size = int(normalized.shape[0])

    def search(self, query_vectors: np.ndarray, top_k: int) -> SearchBatch:
        if self._index is None or self._dimension is None:
            raise RuntimeError("vector backend has not been built")
        queries = _as_normalized_matrix(query_vectors, label="query vectors")
        if queries.shape[1] != self._dimension:
            raise ValueError("query and reference vector dimensions do not match")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        limit = min(int(top_k), self._size)
        try:
            distances, neighbors = self._brute_force.search(
                self._index, self._cupy.asarray(queries), limit
            )
            cosine_distances = self._cupy.asnumpy(distances)
            indices = self._cupy.asnumpy(neighbors)
        except Exception as exc:
            raise BackendUnavailable(
                f"cuVS search failed: {type(exc).__name__}"
            ) from exc
        # cuVS defines cosine distance as 1 - cosine similarity. Convert and
        # re-sort defensively so every backend exposes descending cosine scores.
        scores = np.clip(1.0 - np.asarray(cosine_distances), -1.0, 1.0)
        order = np.argsort(-scores, axis=1, kind="stable")
        return SearchBatch(
            scores=np.asarray(
                np.take_along_axis(scores, order, axis=1), dtype=np.float32
            ),
            indices=np.asarray(
                np.take_along_axis(indices, order, axis=1), dtype=np.int64
            ),
        )


class AutoCosineBackend:
    """Prefer installed mature local backends and fail closed to exact NumPy search."""

    def __init__(self) -> None:
        self._delegate: Optional[VectorSearchBackend] = None
        self.fallback_reasons: List[str] = []

    @property
    def name(self) -> str:
        return (
            self._delegate.name if self._delegate is not None else "auto-uninitialized"
        )

    def build(self, vectors: np.ndarray) -> None:
        attempts: Tuple[Callable[[], VectorSearchBackend], ...] = (
            CuVSCosineBackend,
            FaissCosineBackend,
            NumpyCosineBackend,
        )
        self.fallback_reasons = []
        for factory in attempts:
            try:
                backend = factory()
                backend.build(vectors)
                self._delegate = backend
                return
            except Exception as exc:
                self.fallback_reasons.append(f"{factory.__name__}:{type(exc).__name__}")
        raise BackendUnavailable("no vector-search backend could be initialized")

    def search(self, query_vectors: np.ndarray, top_k: int) -> SearchBatch:
        if self._delegate is None:
            raise RuntimeError("vector backend has not been built")
        return self._delegate.search(query_vectors, top_k)


def create_cosine_backend(name: str = "auto") -> VectorSearchBackend:
    normalized = str(name).strip().lower().replace("_", "-")
    if normalized in {"auto", "local-auto"}:
        return AutoCosineBackend()
    if normalized in {"numpy", "numpy-exact", "exact"}:
        return NumpyCosineBackend()
    if normalized in {"faiss", "faiss-flat", "faiss-flat-ip"}:
        return FaissCosineBackend()
    if normalized in {"cuvs", "cuvs-brute-force"}:
        return CuVSCosineBackend()
    raise ValueError(f"unsupported cosine backend: {name}")


class LocalImageEmbeddingAdapter(Protocol):
    """Injection point for an approved local OpenCLIP/DINO/Transformers encoder."""

    name: str
    model_id: str
    networked: bool

    def encode(self, images: Sequence[Any]) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True)
class CallableLocalImageEmbeddingAdapter:
    """Wrap a local framework callable without coupling retrieval to its package."""

    name: str
    model_id: str
    encoder: Callable[[Sequence[Any]], Sequence[Sequence[float]]]
    networked: bool = False

    def __post_init__(self) -> None:
        if self.networked:
            raise ValueError("the image retrieval encoder adapter must be local")
        if not self.name.strip() or not self.model_id.strip():
            raise ValueError("encoder name and model_id are required")

    def encode(self, images: Sequence[Any]) -> Sequence[Sequence[float]]:
        return self.encoder(images)


def encode_image_views(
    images: Sequence[Any],
    *,
    encoder: LocalImageEmbeddingAdapter,
    view_ids: Optional[Sequence[str]] = None,
    qualities: Optional[Sequence[float]] = None,
    angles: Optional[Sequence[Optional[str]]] = None,
    input_sha256s: Optional[Sequence[Optional[str]]] = None,
) -> Tuple[EmbeddedView, ...]:
    """Encode multiple photos through a caller-supplied, explicitly local adapter."""

    items = tuple(images)
    if not items:
        raise ValueError("at least one image is required")
    if getattr(encoder, "networked", True):
        raise ValueError(
            "networked image encoders are not accepted by the local retrieval core"
        )
    ids = (
        tuple(view_ids)
        if view_ids is not None
        else tuple(f"view-{index + 1}" for index in range(len(items)))
    )
    quality_values = tuple(qualities) if qualities is not None else (1.0,) * len(items)
    angle_values = tuple(angles) if angles is not None else (None,) * len(items)
    if input_sha256s is None:
        hash_values = tuple(
            hashlib.sha256(bytes(item)).hexdigest()
            if isinstance(item, (bytes, bytearray, memoryview))
            else None
            for item in items
        )
    else:
        hash_values = tuple(input_sha256s)
    if not (
        len(ids)
        == len(quality_values)
        == len(angle_values)
        == len(hash_values)
        == len(items)
    ):
        raise ValueError(
            "image, view_id, quality, angle, and input hash counts must match"
        )
    raw_vectors = encoder.encode(items)
    vectors = tuple(raw_vectors)
    if len(vectors) != len(items):
        raise ValueError("local image encoder returned an unexpected vector count")
    return tuple(
        EmbeddedView(
            view_id=ids[index],
            vector=vectors[index],
            quality=quality_values[index],
            angle=angle_values[index],
            input_sha256=hash_values[index],
            metadata={"encoder": encoder.name, "model_id": encoder.model_id},
        )
        for index in range(len(items))
    )


def embedded_views_from_verified_run(
    run: Mapping[str, Any],
    *,
    view_ids: Sequence[str],
    qualities: Optional[Sequence[float]] = None,
    angles: Optional[Sequence[Optional[str]]] = None,
) -> Tuple[EmbeddedView, ...]:
    """Bridge a validated ``LocalImageEmbeddingClient.embed`` result into views.

    This function performs no I/O. The async loopback client remains responsible
    for obtaining and verifying the model response; this bridge rechecks the
    immutable identity and input/output bindings before retrieval consumes it.
    """

    if run.get("available") is not True or run.get("status") != "SUCCESS":
        raise EmbeddingRunUnavailable("EMBEDDING_UNAVAILABLE")
    if run.get("model_identity_verified") is not True:
        raise EmbeddingRunUnavailable("embedding model identity was not verified")
    model = _required_run_text(run, "model")
    model_source = _required_run_text(run, "model_source")
    model_revision = _required_run_text(run, "model_revision").lower()
    if len(model_revision) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in model_revision
    ):
        raise EmbeddingRunUnavailable("embedding model revision is not immutable")
    request_id = _required_run_text(run, "request_id")
    instruction_sha256 = _validate_optional_sha256(
        _required_run_text(run, "instruction_sha256"),
        label="embedding instruction_sha256",
    )
    vectors = tuple(run.get("vectors", ()))
    input_hashes = tuple(run.get("input_hashes", ()))
    output_hashes = tuple(run.get("output_hashes", ()))
    ids = tuple(view_ids)
    quality_values = tuple(qualities) if qualities is not None else (1.0,) * len(ids)
    angle_values = tuple(angles) if angles is not None else (None,) * len(ids)
    if (
        not (
            len(vectors)
            == len(input_hashes)
            == len(output_hashes)
            == len(ids)
            == len(quality_values)
            == len(angle_values)
        )
        or not ids
    ):
        raise EmbeddingRunUnavailable("embedding run item counts do not match")
    try:
        declared_dimension = int(run.get("dimension"))
    except (TypeError, ValueError) as exc:
        raise EmbeddingRunUnavailable("embedding run dimension is invalid") from exc

    result: List[EmbeddedView] = []
    for index, vector in enumerate(vectors):
        try:
            raw_vector = np.asarray(vector, dtype=np.float64)
            normalized = _as_normalized_matrix([vector], label="embedding run vector")[
                0
            ]
        except (TypeError, ValueError) as exc:
            raise EmbeddingRunUnavailable(str(exc)) from exc
        if len(normalized) != declared_dimension:
            raise EmbeddingRunUnavailable("embedding run vector dimension mismatch")
        raw_norm = float(np.linalg.norm(raw_vector))
        if not 0.995 <= raw_norm <= 1.005:
            raise EmbeddingRunUnavailable("embedding run vector is not unit-normalized")
        input_hash = _validate_optional_sha256(
            str(input_hashes[index]), label="embedding input_sha256"
        )
        output_hash = _validate_optional_sha256(
            str(output_hashes[index]), label="embedding output_sha256"
        )
        if output_hash != _vector_sha256(vector):
            raise EmbeddingRunUnavailable("embedding output SHA-256 mismatch")
        result.append(
            EmbeddedView(
                view_id=ids[index],
                vector=vector,
                quality=quality_values[index],
                angle=angle_values[index],
                input_sha256=input_hash,
                metadata={
                    "encoder": model,
                    "model_source": model_source,
                    "model_revision": model_revision,
                    "request_id": request_id,
                    "instruction_sha256": instruction_sha256,
                    "output_sha256": output_hash,
                },
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class MatchedViewScore:
    query_view_id: str
    reference_view_id: str
    query_angle: Optional[str]
    reference_angle: Optional[str]
    cosine_similarity: float
    query_quality: float
    reference_quality: float
    passes_view_match: bool
    angle_compatible: bool
    counts_for_complementary_coverage: bool


@dataclass(frozen=True)
class RetrievalHit:
    artifact_id: str
    kind: ReferenceKind
    score: float
    view_score: float
    centroid_score: Optional[float]
    component_weights: Mapping[str, float]
    max_cosine_similarity: float
    coverage: float
    query_view_coverage: float
    distinct_reference_coverage: float
    similarity_coverage: float
    complementary_angles: Tuple[str, ...]
    quality_score: float
    query_quality: float
    matched_reference_quality: float
    matched_views: Tuple[MatchedViewScore, ...]
    metadata: Mapping[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SameArtifactDecision:
    accepted: bool
    status: str
    artifact_id: Optional[str]
    score: Optional[float]
    runner_up_margin: Optional[float]
    calibration_required: bool
    gates: Mapping[str, bool]
    reason_codes: Tuple[str, ...]
    audit_flags: Tuple[str, ...]
    interpretation: str


@dataclass(frozen=True)
class RelatedRetrievalDecision:
    accepted: bool
    status: str
    qualifying_artifact_ids: Tuple[str, ...]
    top_candidate_gates: Mapping[str, bool]
    reason_codes: Tuple[str, ...]
    interpretation: str


@dataclass(frozen=True)
class CounterfeitSimilaritySignal:
    triggered: bool
    strength: str
    reference_id: Optional[str]
    score: Optional[float]
    weighted_score: Optional[float]
    evidence_weight: Optional[float]
    review_record_id: Optional[str]
    review_status: Optional[str]
    catalog_score_delta: Optional[float]
    competes_with_top_catalog: bool
    gates: Mapping[str, bool]
    excluded_reference_count: int
    reason_codes: Tuple[str, ...]
    interpretation: str = COUNTERFEIT_SIGNAL_LIMITATION


@dataclass(frozen=True)
class QueryViewAudit:
    view_id: str
    input_sha256: Optional[str]
    embedding_sha256: str
    quality: float
    angle: Optional[str]


@dataclass(frozen=True)
class ExactMediaHashMatch:
    artifact_id: str
    query_view_id: str
    reference_view_id: str
    input_sha256: str


@dataclass(frozen=True)
class RetrievalResult:
    reference_library_id: str
    catalog_manifest_sha256: Optional[str]
    index_sha256: str
    calibration_record_sha256: Optional[str]
    embedding_space_id: str
    embedding_model_source: Optional[str]
    embedding_model_revision: Optional[str]
    backend: str
    backend_fallback_reasons: Tuple[str, ...]
    policy_id: str
    query_view_count: int
    query_views: Tuple[QueryViewAudit, ...]
    requested_top_k: int
    catalog_hits: Tuple[RetrievalHit, ...]
    counterfeit_hits: Tuple[RetrievalHit, ...]
    same_artifact: SameArtifactDecision
    related: RelatedRetrievalDecision
    counterfeit_signal: CounterfeitSimilaritySignal
    exact_media_hash_matches: Tuple[ExactMediaHashMatch, ...]
    open_set_rejected: bool
    calibration_required: bool
    authenticity_state: str = AUTHENTICITY_NOT_ASSESSED
    limitation: str = SIMILARITY_LIMITATION

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-ready audit envelope for API/report integration."""

        return asdict(self)


@dataclass(frozen=True)
class _IndexedView:
    artifact_position: int
    reference_view_position: int


class ArtifactRetrievalEngine:
    """Local multi-view cosine retrieval with explicit open-set decisions."""

    def __init__(
        self,
        references: Sequence[ArtifactReference],
        *,
        thresholds: Optional[RetrievalThresholds] = None,
        backend: str | VectorSearchBackend = "numpy",
        embedding_space_id: str = "unspecified-local-image-embedding",
        reference_library_id: str = "unspecified-reference-library",
        catalog_manifest_sha256: Optional[str] = None,
        calibration_record_sha256: Optional[str] = None,
        embedding_model_source: Optional[str] = None,
        embedding_model_revision: Optional[str] = None,
    ) -> None:
        self.thresholds = thresholds or RetrievalThresholds()
        self.embedding_space_id = str(embedding_space_id).strip()
        if not self.embedding_space_id:
            raise ValueError("embedding_space_id must not be empty")
        if self.embedding_space_id == "relicscope-visual-fingerprint-v1":
            raise ValueError(
                "the eight-metric visual fingerprint is not an identity embedding space"
            )
        self.reference_library_id = str(reference_library_id).strip()
        if not self.reference_library_id:
            raise ValueError("reference_library_id must not be empty")
        self.catalog_manifest_sha256 = _validate_optional_sha256(
            catalog_manifest_sha256, label="catalog_manifest_sha256"
        )
        self.calibration_record_sha256 = _validate_optional_sha256(
            calibration_record_sha256, label="calibration_record_sha256"
        )
        self.embedding_model_source = _clean_optional_text(embedding_model_source)
        self.embedding_model_revision = _clean_optional_text(embedding_model_revision)
        self._backend = (
            create_cosine_backend(backend) if isinstance(backend, str) else backend
        )
        self._references: Tuple[ArtifactReference, ...] = ()
        self._indexed_views: Tuple[_IndexedView, ...] = ()
        self._view_matrix: Optional[np.ndarray] = None
        self._dimension = 0
        self._index_sha256 = ""
        self.replace_references(references)

    @property
    def backend_name(self) -> str:
        return self._backend.name

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def reference_count(self) -> int:
        return len(self._references)

    def replace_references(self, references: Sequence[ArtifactReference]) -> None:
        values = tuple(references)
        if not values:
            raise ValueError("at least one artifact reference is required")
        keys = [(reference.kind, reference.artifact_id) for reference in values]
        if len(keys) != len(set(keys)):
            raise ValueError(
                "artifact reference IDs must be unique within each reference kind"
            )

        raw_vectors: List[Sequence[float]] = []
        indexed_views: List[_IndexedView] = []
        for artifact_position, reference in enumerate(values):
            for view_position, view in enumerate(reference.views):
                raw_vectors.append(view.vector)
                indexed_views.append(
                    _IndexedView(
                        artifact_position=artifact_position,
                        reference_view_position=view_position,
                    )
                )
        matrix = _as_normalized_matrix(raw_vectors, label="reference vectors")
        if matrix.shape[1] == 8:
            raise ValueError(
                "eight-dimensional image-analysis fingerprints are quality metadata, "
                "not formal identity embeddings"
            )
        self._backend.build(matrix)
        self._references = values
        self._indexed_views = tuple(indexed_views)
        self._view_matrix = matrix
        self._dimension = int(matrix.shape[1])
        self._index_sha256 = self._calculate_index_sha256(values)

    def retrieve_images(
        self,
        images: Sequence[Any],
        *,
        encoder: LocalImageEmbeddingAdapter,
        view_ids: Optional[Sequence[str]] = None,
        qualities: Optional[Sequence[float]] = None,
        angles: Optional[Sequence[Optional[str]]] = None,
        input_sha256s: Optional[Sequence[Optional[str]]] = None,
        top_k: int = 5,
    ) -> RetrievalResult:
        if encoder.model_id != self.embedding_space_id:
            raise ValueError(
                "query encoder model_id does not match the indexed embedding space"
            )
        query_views = encode_image_views(
            images,
            encoder=encoder,
            view_ids=view_ids,
            qualities=qualities,
            angles=angles,
            input_sha256s=input_sha256s,
        )
        return self.retrieve(query_views, top_k=top_k)

    def retrieve(
        self, query_views: Sequence[EmbeddedView], *, top_k: int = 5
    ) -> RetrievalResult:
        queries = tuple(query_views)
        if not queries:
            raise ValueError("at least one query view is required")
        query_ids = [view.view_id for view in queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query view IDs must be unique")
        if top_k < 1:
            raise ValueError("top_k must be positive")
        raw_query_vectors = [view.vector for view in queries]
        query_matrix = _as_normalized_matrix(raw_query_vectors, label="query vectors")
        if query_matrix.shape[1] != self._dimension:
            raise ValueError("query and reference vector dimensions do not match")
        if self._view_matrix is None:
            raise RuntimeError("retrieval index is not initialized")

        # Search every reference view so multi-view aggregation and coverage remain
        # exact. FAISS/cuVS can replace this with candidate pruning at larger scale
        # after a measured recall acceptance test.
        batch = self._backend.search(query_matrix, len(self._indexed_views))
        full_scores = self._reconstruct_similarity_matrix(batch, len(queries))
        hits = tuple(
            self._score_reference(
                artifact_position,
                reference,
                queries,
                query_matrix,
                full_scores,
            )
            for artifact_position, reference in enumerate(self._references)
        )
        catalog_ranked = tuple(
            sorted(
                (hit for hit in hits if hit.kind == ReferenceKind.CATALOG_ARTIFACT),
                key=lambda hit: (-hit.score, hit.artifact_id),
            )
        )
        counterfeit_ranked = tuple(
            sorted(
                (hit for hit in hits if hit.kind == ReferenceKind.KNOWN_COUNTERFEIT),
                key=lambda hit: (-hit.score, hit.artifact_id),
            )
        )

        exact_media_matches = self._exact_media_hash_matches(queries)
        counterfeit_signal = self._counterfeit_signal(
            catalog_ranked, counterfeit_ranked
        )
        same_artifact = self._same_artifact_decision(
            catalog_ranked, counterfeit_signal, exact_media_matches
        )
        related = self._related_decision(catalog_ranked)
        fallback_reasons = tuple(getattr(self._backend, "fallback_reasons", ()))
        return RetrievalResult(
            reference_library_id=self.reference_library_id,
            catalog_manifest_sha256=self.catalog_manifest_sha256,
            index_sha256=self._index_sha256,
            calibration_record_sha256=self.calibration_record_sha256,
            embedding_space_id=self.embedding_space_id,
            embedding_model_source=self.embedding_model_source,
            embedding_model_revision=self.embedding_model_revision,
            backend=self._backend.name,
            backend_fallback_reasons=fallback_reasons,
            policy_id=self.thresholds.policy_id,
            query_view_count=len(queries),
            query_views=tuple(
                QueryViewAudit(
                    view_id=view.view_id,
                    input_sha256=view.input_sha256,
                    embedding_sha256=_vector_sha256(view.vector),
                    quality=view.quality,
                    angle=view.angle,
                )
                for view in queries
            ),
            requested_top_k=int(top_k),
            catalog_hits=catalog_ranked[:top_k],
            counterfeit_hits=counterfeit_ranked[:top_k],
            same_artifact=same_artifact,
            related=related,
            counterfeit_signal=counterfeit_signal,
            exact_media_hash_matches=exact_media_matches,
            open_set_rejected=not same_artifact.accepted,
            calibration_required=self.calibration_record_sha256 is None,
        )

    def _reconstruct_similarity_matrix(
        self, batch: SearchBatch, query_count: int
    ) -> np.ndarray:
        scores = np.asarray(batch.scores, dtype=np.float32)
        indices = np.asarray(batch.indices, dtype=np.int64)
        expected_shape = (query_count, len(self._indexed_views))
        if scores.shape != expected_shape or indices.shape != expected_shape:
            raise RuntimeError(
                "vector backend did not return the requested complete result set"
            )
        full = np.full(expected_shape, -np.inf, dtype=np.float32)
        for query_position in range(query_count):
            row_indices = indices[query_position]
            if np.any(row_indices < 0) or np.any(row_indices >= expected_shape[1]):
                raise RuntimeError("vector backend returned an invalid reference index")
            if len(set(int(value) for value in row_indices)) != expected_shape[1]:
                raise RuntimeError(
                    "vector backend returned duplicate or missing reference indices"
                )
            full[query_position, row_indices] = scores[query_position]
        if not np.all(np.isfinite(full)):
            raise RuntimeError("vector backend returned a non-finite similarity")
        return full

    def _calculate_index_sha256(self, references: Tuple[ArtifactReference, ...]) -> str:
        records: List[Dict[str, Any]] = []
        for reference in sorted(
            references, key=lambda item: (item.kind.value, item.artifact_id)
        ):
            control = reference.negative_control
            records.append(
                {
                    "artifact_id": reference.artifact_id,
                    "kind": reference.kind.value,
                    "negative_control": (
                        {
                            "record_id": control.record_id,
                            "review_status": control.review_status.value,
                            "admissible_for_signal": control.admissible_for_signal,
                            "signal_weight": control.signal_weight,
                        }
                        if control is not None
                        else None
                    ),
                    "views": [
                        {
                            "view_id": view.view_id,
                            "quality": view.quality,
                            "angle": view.angle,
                            "input_sha256": view.input_sha256,
                            "embedding_sha256": _vector_sha256(view.vector),
                        }
                        for view in sorted(
                            reference.views, key=lambda item: item.view_id
                        )
                    ],
                }
            )
        payload = {
            "embedding_space_id": self.embedding_space_id,
            "reference_library_id": self.reference_library_id,
            "records": records,
        }
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _exact_media_hash_matches(
        self, queries: Tuple[EmbeddedView, ...]
    ) -> Tuple[ExactMediaHashMatch, ...]:
        library_hashes: Dict[str, List[Tuple[str, str]]] = {}
        for reference in self._references:
            for view in reference.views:
                if view.input_sha256:
                    library_hashes.setdefault(view.input_sha256, []).append(
                        (reference.artifact_id, view.view_id)
                    )
        matches: List[ExactMediaHashMatch] = []
        for query in queries:
            if not query.input_sha256:
                continue
            for artifact_id, reference_view_id in library_hashes.get(
                query.input_sha256, ()
            ):
                matches.append(
                    ExactMediaHashMatch(
                        artifact_id=artifact_id,
                        query_view_id=query.view_id,
                        reference_view_id=reference_view_id,
                        input_sha256=query.input_sha256,
                    )
                )
        return tuple(
            sorted(
                matches,
                key=lambda item: (
                    item.artifact_id,
                    item.query_view_id,
                    item.reference_view_id,
                ),
            )
        )

    def _score_reference(
        self,
        artifact_position: int,
        reference: ArtifactReference,
        queries: Tuple[EmbeddedView, ...],
        query_matrix: np.ndarray,
        full_scores: np.ndarray,
    ) -> RetrievalHit:
        global_positions = [
            index
            for index, indexed in enumerate(self._indexed_views)
            if indexed.artifact_position == artifact_position
        ]
        candidate_scores = full_scores[:, global_positions]
        local_best_positions = np.argmax(candidate_scores, axis=1)
        best_scores = candidate_scores[np.arange(len(queries)), local_best_positions]

        query_quality_values = np.asarray(
            [view.quality for view in queries], dtype=np.float64
        )
        score_weights = query_quality_values.copy()
        if float(score_weights.sum()) <= 1e-12:
            score_weights = np.ones(len(queries), dtype=np.float64)
        view_score = float(
            np.average(best_scores.astype(np.float64), weights=score_weights)
        )

        matched_views: List[MatchedViewScore] = []
        matched_reference_qualities: List[float] = []
        visual_match_matrix = np.zeros(candidate_scores.shape, dtype=bool)
        angle_coverage_matrix = np.zeros(candidate_scores.shape, dtype=bool)
        for query_position, query in enumerate(queries):
            for reference_position, reference_view in enumerate(reference.views):
                visual_match_matrix[query_position, reference_position] = bool(
                    candidate_scores[query_position, reference_position]
                    >= self.thresholds.view_match_min_similarity
                    and query.quality >= self.thresholds.minimum_view_quality
                    and reference_view.quality >= self.thresholds.minimum_view_quality
                )
                angle_coverage_matrix[query_position, reference_position] = bool(
                    visual_match_matrix[query_position, reference_position]
                    and _angles_are_compatible(query.angle, reference_view.angle)
                )
        for query_position, query in enumerate(queries):
            local_position = int(local_best_positions[query_position])
            reference_view = reference.views[local_position]
            similarity = float(best_scores[query_position])
            passes = bool(
                similarity >= self.thresholds.view_match_min_similarity
                and query.quality >= self.thresholds.minimum_view_quality
                and reference_view.quality >= self.thresholds.minimum_view_quality
            )
            angle_compatible = _angles_are_compatible(query.angle, reference_view.angle)
            matched_reference_qualities.append(reference_view.quality)
            matched_views.append(
                MatchedViewScore(
                    query_view_id=query.view_id,
                    reference_view_id=reference_view.view_id,
                    query_angle=query.angle,
                    reference_angle=reference_view.angle,
                    cosine_similarity=similarity,
                    query_quality=query.quality,
                    reference_quality=reference_view.quality,
                    passes_view_match=passes,
                    angle_compatible=angle_compatible,
                    counts_for_complementary_coverage=passes and angle_compatible,
                )
            )

        query_quality = float(np.mean(query_quality_values))
        matched_reference_quality = float(
            np.average(np.asarray(matched_reference_qualities), weights=score_weights)
        )
        quality_score = min(query_quality, matched_reference_quality)
        similarity_coverage = float(np.mean(np.any(visual_match_matrix, axis=1)))
        declared_query_angles = {
            angle
            for angle in (
                _canonical_complementary_angle(view.angle) for view in queries
            )
            if angle is not None
        }
        declared_reference_angles = {
            angle
            for angle in (
                _canonical_complementary_angle(view.angle) for view in reference.views
            )
            if angle is not None
        }
        complementary_angles = tuple(
            sorted(
                {
                    _canonical_complementary_angle(queries[query_position].angle)
                    for query_position, reference_position in zip(
                        *np.nonzero(angle_coverage_matrix)
                    )
                    if _canonical_complementary_angle(
                        reference.views[int(reference_position)].angle
                    )
                    == _canonical_complementary_angle(
                        queries[int(query_position)].angle
                    )
                }
                - {None}
            )
        )
        query_view_coverage = (
            len(complementary_angles) / len(declared_query_angles)
            if declared_query_angles
            else 0.0
        )
        # Coverage is measured against distinct, declared whole-object angles in
        # the controlled reference set. Repeated FRONT images and UNSPECIFIED or
        # DETAIL captures therefore never masquerade as complementary evidence.
        distinct_reference_coverage = (
            len(complementary_angles) / len(declared_reference_angles)
            if declared_reference_angles
            else 0.0
        )
        coverage = min(query_view_coverage, distinct_reference_coverage)

        reference_matrix = self._view_matrix[global_positions]  # type: ignore[index]
        query_centroid = _weighted_centroid(query_matrix, query_quality_values)
        reference_centroid = _weighted_centroid(
            reference_matrix,
            np.asarray([view.quality for view in reference.views], dtype=np.float64),
        )
        centroid_score: Optional[float]
        if query_centroid is None or reference_centroid is None:
            centroid_score = None
            view_weight = 1.0
            centroid_weight = 0.0
        else:
            centroid_score = float(
                np.clip(np.dot(query_centroid, reference_centroid), -1.0, 1.0)
            )
            view_weight = self.thresholds.view_score_weight
            centroid_weight = round(1.0 - view_weight, 12)
        aggregate_score = view_weight * view_score
        if centroid_score is not None:
            aggregate_score += centroid_weight * centroid_score

        return RetrievalHit(
            artifact_id=reference.artifact_id,
            kind=reference.kind,
            score=float(aggregate_score),
            view_score=view_score,
            centroid_score=centroid_score,
            component_weights={"best_view": view_weight, "centroid": centroid_weight},
            max_cosine_similarity=float(np.max(best_scores)),
            coverage=float(coverage),
            query_view_coverage=query_view_coverage,
            distinct_reference_coverage=float(distinct_reference_coverage),
            similarity_coverage=similarity_coverage,
            complementary_angles=complementary_angles,
            quality_score=quality_score,
            query_quality=query_quality,
            matched_reference_quality=matched_reference_quality,
            matched_views=tuple(matched_views),
            metadata=dict(reference.metadata),
        )

    def _counterfeit_signal(
        self,
        catalog_ranked: Tuple[RetrievalHit, ...],
        counterfeit_ranked: Tuple[RetrievalHit, ...],
    ) -> CounterfeitSimilaritySignal:
        controls_by_id = {
            reference.artifact_id: reference.negative_control
            for reference in self._references
            if reference.kind == ReferenceKind.KNOWN_COUNTERFEIT
        }
        eligible: List[Tuple[float, RetrievalHit, NegativeReferenceControl]] = []
        excluded = 0
        for hit in counterfeit_ranked:
            control = controls_by_id.get(hit.artifact_id)
            if control is None or not control.admissible_for_signal:
                excluded += 1
                continue
            eligible.append((hit.score * control.signal_weight, hit, control))
        eligible.sort(key=lambda item: (-item[0], item[1].artifact_id))
        if not eligible:
            return CounterfeitSimilaritySignal(
                triggered=False,
                strength="NONE",
                reference_id=None,
                score=None,
                weighted_score=None,
                evidence_weight=None,
                review_record_id=None,
                review_status=None,
                catalog_score_delta=None,
                competes_with_top_catalog=False,
                gates={"score": False, "coverage": False, "quality": False},
                excluded_reference_count=excluded,
                reason_codes=(
                    "NO_ADMISSIBLE_NEGATIVE_REFERENCE"
                    if counterfeit_ranked
                    else "NO_NEGATIVE_REFERENCE",
                ),
            )
        weighted_score, top, control = eligible[0]
        gates = {
            "score": weighted_score >= self.thresholds.counterfeit_alert_min_score,
            "coverage": top.similarity_coverage
            >= self.thresholds.counterfeit_alert_min_coverage,
            "quality": top.quality_score
            >= self.thresholds.counterfeit_alert_min_quality,
        }
        triggered = all(gates.values())
        strength = (
            "STRONG"
            if triggered and control.review_status == NegativeReviewStatus.VERIFIED
            else "WEAK"
            if triggered
            else "NONE"
        )
        delta = weighted_score - catalog_ranked[0].score if catalog_ranked else None
        competes = bool(
            triggered
            and strength == "STRONG"
            and (
                not catalog_ranked
                or weighted_score
                >= catalog_ranked[0].score - self.thresholds.same_artifact_min_margin
            )
        )
        reason_map = {
            "score": "NEGATIVE_REFERENCE_SCORE_BELOW_THRESHOLD",
            "coverage": "NEGATIVE_REFERENCE_COVERAGE_BELOW_THRESHOLD",
            "quality": "NEGATIVE_REFERENCE_QUALITY_BELOW_THRESHOLD",
        }
        reasons = tuple(
            reason_map[name] for name, passed in gates.items() if not passed
        )
        return CounterfeitSimilaritySignal(
            triggered=triggered,
            strength=strength,
            reference_id=top.artifact_id,
            score=top.score,
            weighted_score=weighted_score,
            evidence_weight=control.signal_weight,
            review_record_id=control.record_id,
            review_status=control.review_status.value,
            catalog_score_delta=delta,
            competes_with_top_catalog=competes,
            gates=gates,
            excluded_reference_count=excluded,
            reason_codes=reasons or ("NEGATIVE_REFERENCE_SIGNAL_GATES_PASSED",),
        )

    def _same_artifact_decision(
        self,
        catalog_ranked: Tuple[RetrievalHit, ...],
        counterfeit_signal: CounterfeitSimilaritySignal,
        exact_media_matches: Tuple[ExactMediaHashMatch, ...],
    ) -> SameArtifactDecision:
        if not catalog_ranked:
            calibration_required = self.calibration_record_sha256 is None
            exact_media_replay = bool(exact_media_matches)
            return SameArtifactDecision(
                accepted=False,
                status=(
                    "EXACT_MEDIA_REPLAY"
                    if exact_media_replay
                    else "CALIBRATION_REQUIRED"
                    if calibration_required
                    else "OPEN_SET_NO_MATCH"
                ),
                artifact_id=None,
                score=None,
                runner_up_margin=None,
                calibration_required=calibration_required,
                gates={
                    "score": False,
                    "margin": False,
                    "coverage": False,
                    "complementary_angles": False,
                    "quality": False,
                    "calibration_record": self.calibration_record_sha256 is not None,
                    "counterfeit_conflict_absent": not counterfeit_signal.competes_with_top_catalog,
                    "exact_media_replay_absent": not exact_media_matches,
                },
                reason_codes=tuple(
                    reason
                    for reason, included in (
                        ("NO_CATALOG_REFERENCES", True),
                        ("EXACT_MEDIA_REPLAY", exact_media_replay),
                        ("CALIBRATION_RECORD_REQUIRED", calibration_required),
                    )
                    if included
                ),
                audit_flags=(("EXACT_MEDIA_REPLAY",) if exact_media_matches else ()),
                interpretation="No catalog identity candidate was available.",
            )
        top = catalog_ranked[0]
        margin = (
            top.score - catalog_ranked[1].score if len(catalog_ranked) > 1 else None
        )
        gates = {
            "score": top.score >= self.thresholds.same_artifact_min_score,
            "margin": margin is None
            or margin >= self.thresholds.same_artifact_min_margin,
            "coverage": top.coverage >= self.thresholds.same_artifact_min_coverage,
            "complementary_angles": len(top.complementary_angles)
            >= self.thresholds.same_artifact_min_complementary_angles,
            "quality": top.quality_score >= self.thresholds.same_artifact_min_quality,
            "calibration_record": self.calibration_record_sha256 is not None,
            "counterfeit_conflict_absent": not counterfeit_signal.competes_with_top_catalog,
            "exact_media_replay_absent": not exact_media_matches,
        }
        reason_map = {
            "score": "SCORE_BELOW_SAME_ARTIFACT_THRESHOLD",
            "margin": "RUNNER_UP_MARGIN_BELOW_THRESHOLD",
            "coverage": "QUERY_VIEW_COVERAGE_BELOW_THRESHOLD",
            "complementary_angles": "INSUFFICIENT_COMPLEMENTARY_DECLARED_ANGLES",
            "quality": "IMAGE_QUALITY_BELOW_THRESHOLD",
            "calibration_record": "CALIBRATION_RECORD_REQUIRED",
            "counterfeit_conflict_absent": "KNOWN_COUNTERFEIT_REFERENCE_CONFLICT",
            "exact_media_replay_absent": "EXACT_MEDIA_REPLAY",
        }
        reasons = tuple(
            reason_map[name] for name, passed in gates.items() if not passed
        )
        accepted = all(gates.values())
        related_strength = (
            top.score >= self.thresholds.related_min_score
            and top.similarity_coverage >= self.thresholds.related_min_coverage
            and top.quality_score >= self.thresholds.related_min_quality
        )
        if not gates["exact_media_replay_absent"]:
            status = "EXACT_MEDIA_REPLAY"
        elif not gates["calibration_record"]:
            status = "CALIBRATION_REQUIRED"
        elif accepted:
            status = "KNOWN_ARTIFACT_CANDIDATE"
        elif (
            not gates["quality"]
            or not gates["coverage"]
            or not gates["complementary_angles"]
        ):
            status = "INSUFFICIENT_CAPTURE"
        elif related_strength:
            status = "RELATED_REFERENCES_ONLY"
        else:
            status = "OPEN_SET_NO_MATCH"
        return SameArtifactDecision(
            accepted=accepted,
            status=status,
            artifact_id=top.artifact_id if accepted else None,
            score=top.score,
            runner_up_margin=margin,
            calibration_required=self.calibration_record_sha256 is None,
            gates=gates,
            reason_codes=reasons or ("PASSED_ALL_IDENTITY_GATES",),
            audit_flags=(("EXACT_MEDIA_REPLAY",) if exact_media_matches else ()),
            interpretation=(
                "The query passed the configured visual identity gates for this catalog entry; "
                "this is not an authenticity conclusion."
                if accepted
                else (
                    "The uploaded bytes exactly reproduce media already registered in the "
                    "reference library. Identity acceptance is blocked to prevent replay "
                    "leakage; ranked related references remain available."
                    if exact_media_matches
                    else "The top catalog result remains a ranked similarity candidate, but at "
                    "least one open-set identity gate failed."
                )
            ),
        )

    def _related_decision(
        self, catalog_ranked: Tuple[RetrievalHit, ...]
    ) -> RelatedRetrievalDecision:
        qualifying = tuple(
            hit.artifact_id
            for hit in catalog_ranked
            if hit.score >= self.thresholds.related_min_score
            and hit.similarity_coverage >= self.thresholds.related_min_coverage
            and hit.quality_score >= self.thresholds.related_min_quality
        )
        if not catalog_ranked:
            gates = {"score": False, "coverage": False, "quality": False}
        else:
            top = catalog_ranked[0]
            gates = {
                "score": top.score >= self.thresholds.related_min_score,
                "coverage": top.similarity_coverage
                >= self.thresholds.related_min_coverage,
                "quality": top.quality_score >= self.thresholds.related_min_quality,
            }
        reason_map = {
            "score": "SCORE_BELOW_RELATED_THRESHOLD",
            "coverage": "RELATED_VIEW_COVERAGE_BELOW_THRESHOLD",
            "quality": "RELATED_IMAGE_QUALITY_BELOW_THRESHOLD",
        }
        reasons = tuple(
            reason_map[name] for name, passed in gates.items() if not passed
        )
        accepted = bool(qualifying)
        return RelatedRetrievalDecision(
            accepted=accepted,
            status="related_candidates"
            if accepted
            else "no_reliable_related_candidate",
            qualifying_artifact_ids=qualifying,
            top_candidate_gates=gates,
            reason_codes=reasons or ("RELATED_GATES_PASSED",),
            interpretation=(
                "Returned entries are visual-context candidates for an evidence-based comparison "
                "report, not identity or authenticity conclusions."
            ),
        )


def _canonical_complementary_angle(angle: Optional[str]) -> Optional[str]:
    """Return a controlled whole-object angle or ``None`` when not countable."""

    if angle is None:
        return None
    normalized = str(angle).strip().upper().replace("-", "_").replace(" ", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = _ANGLE_ALIASES.get(normalized, normalized)
    return normalized if normalized in _COMPLEMENTARY_VIEW_ANGLES else None


def _angles_are_compatible(
    query_angle: Optional[str], reference_angle: Optional[str]
) -> bool:
    query = _canonical_complementary_angle(query_angle)
    reference = _canonical_complementary_angle(reference_angle)
    return query is not None and query == reference


def _weighted_centroid(
    normalized_vectors: np.ndarray, weights: np.ndarray
) -> Optional[np.ndarray]:
    values = np.asarray(weights, dtype=np.float64)
    if values.shape != (normalized_vectors.shape[0],):
        raise ValueError("centroid weights do not match vector count")
    if float(values.sum()) <= 1e-12:
        values = np.ones(normalized_vectors.shape[0], dtype=np.float64)
    centroid = np.average(normalized_vectors.astype(np.float64), axis=0, weights=values)
    norm = float(np.linalg.norm(centroid))
    if norm <= 1e-12:
        return None
    return centroid / norm


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _required_run_text(run: Mapping[str, Any], name: str) -> str:
    value = _clean_optional_text(run.get(name))
    if value is None:
        raise EmbeddingRunUnavailable(f"embedding run {name} is missing")
    return value


def _validate_optional_sha256(value: Optional[str], *, label: str) -> Optional[str]:
    cleaned = _clean_optional_text(value)
    if cleaned is None:
        return None
    normalized = cleaned.lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{label} must be a 64-character hexadecimal SHA-256")
    return normalized


def _vector_sha256(vector: Sequence[float]) -> str:
    payload = json.dumps(
        [float(value) for value in vector], separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _maximum_bipartite_matches(valid_edges: np.ndarray) -> int:
    """Return maximum one-to-one query/reference coverage without SciPy."""

    matrix = np.asarray(valid_edges, dtype=bool)
    if matrix.ndim != 2:
        raise ValueError("coverage edge matrix must be two-dimensional")
    matched_query_by_reference: Dict[int, int] = {}

    def augment(query_position: int, visited_references: set[int]) -> bool:
        for reference_position in np.flatnonzero(matrix[query_position]):
            reference_index = int(reference_position)
            if reference_index in visited_references:
                continue
            visited_references.add(reference_index)
            previous_query = matched_query_by_reference.get(reference_index)
            if previous_query is None or augment(previous_query, visited_references):
                matched_query_by_reference[reference_index] = query_position
                return True
        return False

    match_count = 0
    for query_position in range(matrix.shape[0]):
        match_count += int(augment(query_position, set()))
    return match_count


__all__ = [
    "ArtifactReference",
    "ArtifactRetrievalEngine",
    "AutoCosineBackend",
    "BackendUnavailable",
    "AUTHENTICITY_NOT_ASSESSED",
    "COUNTERFEIT_SIGNAL_LIMITATION",
    "CallableLocalImageEmbeddingAdapter",
    "CounterfeitSimilaritySignal",
    "CuVSCosineBackend",
    "EmbeddedView",
    "EmbeddingRunUnavailable",
    "ExactMediaHashMatch",
    "FaissCosineBackend",
    "LocalImageEmbeddingAdapter",
    "MatchedViewScore",
    "NumpyCosineBackend",
    "NegativeReferenceControl",
    "NegativeReviewStatus",
    "QueryViewAudit",
    "ReferenceKind",
    "RelatedRetrievalDecision",
    "RetrievalHit",
    "RetrievalResult",
    "RetrievalThresholds",
    "SIMILARITY_LIMITATION",
    "SameArtifactDecision",
    "SearchBatch",
    "VectorSearchBackend",
    "create_cosine_backend",
    "encode_image_views",
    "embedded_views_from_verified_run",
]
