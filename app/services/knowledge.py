from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .embedding import EmbeddingProvider, EmbeddingRuntime, tokenize


MANIFEST_SCHEMA_VERSION = "relicscope-knowledge-manifest-v1"
RETRIEVAL_CONFIG_VERSION = "relicscope-hybrid-retrieval-v1"
DEMO_DATA_LEVEL = "DEMO/SYNTHETIC"
DEFAULT_DEMO_SPACE = "demo"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class KnowledgeError(RuntimeError):
    pass


class ManifestValidationError(KnowledgeError):
    def __init__(self, issues: Sequence[str]) -> None:
        self.issues = list(issues)
        super().__init__("invalid knowledge manifest: " + "; ".join(self.issues))


class KnowledgePolicyError(KnowledgeError):
    pass


@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    position: int
    text: str
    content_sha256: str
    location: Mapping[str, str]


@dataclass(frozen=True)
class KnowledgeEntry:
    source_id: str
    title: str
    publisher: str
    source_type: str
    published_at: str
    document_version: str
    ingested_at: str
    content_sha256: str
    license: Mapping[str, Any]
    scope: Mapping[str, Any]
    location: Mapping[str, str]
    review_status: Mapping[str, Any]
    text: str
    feature_vector: Tuple[float, ...]
    knowledge_space: str
    data_level: str
    chunks: Tuple[KnowledgeChunk, ...]


@dataclass(frozen=True)
class KnowledgeManifest:
    schema_version: str
    knowledge_base_id: str
    knowledge_space: str
    data_level: str
    version: str
    published_at: str
    retrieval_config_version: str
    vector_dimension: int
    content_set_sha256: str
    manifest_sha256: str
    entries: Tuple[KnowledgeEntry, ...]


_TOP_LEVEL_FIELDS = {
    "schema_version",
    "knowledge_base_id",
    "knowledge_space",
    "data_level",
    "version",
    "published_at",
    "retrieval_config_version",
    "vector_dimension",
    "content_set_sha256",
    "manifest_sha256",
    "entries",
}
_ENTRY_FIELDS = {
    "source_id",
    "title",
    "publisher",
    "source_type",
    "published_at",
    "document_version",
    "ingested_at",
    "content_sha256",
    "license",
    "scope",
    "location",
    "review_status",
    "text",
    "feature_vector",
    "knowledge_space",
    "data_level",
    "chunks",
}
_LICENSE_FIELDS = {
    "identifier",
    "statement",
    "allowed_purposes",
    "access_scopes",
    "valid_from",
    "valid_until",
    "attribution_required",
}
_SCOPE_FIELDS = {
    "artifact_types",
    "materials",
    "modalities",
    "crafts",
    "regions",
    "tags",
    "limitations",
}
_LOCATION_FIELDS = {"kind", "locator"}
_REVIEW_FIELDS = {"status", "reviewer", "reviewed_at", "note"}
_CHUNK_FIELDS = {"chunk_id", "position", "text", "content_sha256", "location"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._:@/-]{2,127}$")
_SOURCE_TYPES = {
    "synthetic_protocol",
    "synthetic_reference",
    "synthetic_record",
    "publication",
    "museum_catalog",
    "standard",
    "laboratory_report",
    "expert_record",
    "institutional_protocol",
    "local_document",
}
_REVIEW_STATES = {
    "NOT_REVIEWED",
    "REVIEWED_DEMO_ONLY",
    "EXPERT_REVIEWED",
    "INSTITUTIONAL_APPROVED",
    "REJECTED",
}
_DATA_LEVELS = {
    DEMO_DATA_LEVEL,
    "UNREVIEWED",
    "EXPERT_REVIEWED",
    "INSTITUTIONAL_APPROVED",
}


def _is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _is_date_or_unknown(value: Any) -> bool:
    if value == "UNKNOWN":
        return True
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _required_string(container: Mapping[str, Any], field: str, path: str, issues: List[str]) -> None:
    value = container.get(field)
    if not isinstance(value, str) or not value.strip():
        issues.append(f"{path}.{field} must be a non-empty string")


def _required_string_list(
    container: Mapping[str, Any], field: str, path: str, issues: List[str]
) -> None:
    value = container.get(field)
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        issues.append(f"{path}.{field} must be a non-empty string list")


def _check_exact_fields(
    container: Mapping[str, Any], allowed: set[str], path: str, issues: List[str]
) -> None:
    missing = sorted(allowed - set(container))
    unknown = sorted(set(container) - allowed)
    if missing:
        issues.append(f"{path} missing fields: {', '.join(missing)}")
    if unknown:
        issues.append(f"{path} unknown fields: {', '.join(unknown)}")


def _validate_location(value: Any, path: str, issues: List[str]) -> None:
    if not isinstance(value, dict):
        issues.append(f"{path} must be an object")
        return
    _check_exact_fields(value, _LOCATION_FIELDS, path, issues)
    _required_string(value, "kind", path, issues)
    _required_string(value, "locator", path, issues)


def _validate_entry(
    entry: Any,
    *,
    index: int,
    vector_dimension: int,
    manifest_space: str,
    manifest_data_level: str,
) -> List[str]:
    issues: List[str] = []
    path = f"entries[{index}]"
    if not isinstance(entry, dict):
        return [f"{path} must be an object"]
    _check_exact_fields(entry, _ENTRY_FIELDS, path, issues)
    for field in (
        "source_id",
        "title",
        "publisher",
        "source_type",
        "document_version",
        "content_sha256",
        "text",
        "knowledge_space",
        "data_level",
    ):
        _required_string(entry, field, path, issues)

    source_id = entry.get("source_id")
    if isinstance(source_id, str) and not _IDENTIFIER.fullmatch(source_id):
        issues.append(f"{path}.source_id has an invalid format")
    if entry.get("source_type") not in _SOURCE_TYPES:
        issues.append(f"{path}.source_type is not allowed by this schema")
    if not _is_date_or_unknown(entry.get("published_at")):
        issues.append(f"{path}.published_at must be ISO date or UNKNOWN")
    if not _is_iso_datetime(entry.get("ingested_at")):
        issues.append(f"{path}.ingested_at must be an ISO datetime")
    if entry.get("knowledge_space") != manifest_space:
        issues.append(f"{path}.knowledge_space differs from manifest space")
    if entry.get("data_level") != manifest_data_level:
        issues.append(f"{path}.data_level differs from manifest data level")
    if manifest_data_level == DEMO_DATA_LEVEL and entry.get("data_level") != DEMO_DATA_LEVEL:
        issues.append(f"{path} is an unlabelled built-in demo entry")

    text = entry.get("text")
    digest = entry.get("content_sha256")
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        issues.append(f"{path}.content_sha256 must be a lowercase SHA-256")
    elif isinstance(text, str) and sha256_text(text) != digest:
        issues.append(f"{path}.content_sha256 does not match text")

    vector = entry.get("feature_vector")
    if not isinstance(vector, list) or len(vector) != vector_dimension:
        issues.append(f"{path}.feature_vector must have {vector_dimension} values")
    elif any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in vector
    ):
        issues.append(f"{path}.feature_vector must contain finite numbers")

    license_policy = entry.get("license")
    if not isinstance(license_policy, dict):
        issues.append(f"{path}.license must be an object")
    else:
        _check_exact_fields(license_policy, _LICENSE_FIELDS, f"{path}.license", issues)
        _required_string(license_policy, "identifier", f"{path}.license", issues)
        _required_string(license_policy, "statement", f"{path}.license", issues)
        _required_string_list(license_policy, "allowed_purposes", f"{path}.license", issues)
        _required_string_list(license_policy, "access_scopes", f"{path}.license", issues)
        for field in ("valid_from", "valid_until"):
            if not _is_date_or_unknown(license_policy.get(field)):
                issues.append(f"{path}.license.{field} must be ISO date or UNKNOWN")
        if not isinstance(license_policy.get("attribution_required"), bool):
            issues.append(f"{path}.license.attribution_required must be boolean")

    scope = entry.get("scope")
    if not isinstance(scope, dict):
        issues.append(f"{path}.scope must be an object")
    else:
        _check_exact_fields(scope, _SCOPE_FIELDS, f"{path}.scope", issues)
        for field in _SCOPE_FIELDS:
            _required_string_list(scope, field, f"{path}.scope", issues)

    _validate_location(entry.get("location"), f"{path}.location", issues)

    review = entry.get("review_status")
    if not isinstance(review, dict):
        issues.append(f"{path}.review_status must be an object")
    else:
        _check_exact_fields(review, _REVIEW_FIELDS, f"{path}.review_status", issues)
        if review.get("status") not in _REVIEW_STATES:
            issues.append(f"{path}.review_status.status is invalid")
        for field in ("reviewer", "reviewed_at", "note"):
            _required_string(review, field, f"{path}.review_status", issues)
        if isinstance(review.get("reviewed_at"), str) and review.get(
            "reviewed_at"
        ) != "UNKNOWN" and not _is_iso_datetime(review.get("reviewed_at")):
            issues.append(
                f"{path}.review_status.reviewed_at must be an ISO datetime or UNKNOWN"
            )

    chunks = entry.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        issues.append(f"{path}.chunks must be a non-empty list")
    else:
        positions: List[int] = []
        chunk_ids: set[str] = set()
        for chunk_index, chunk in enumerate(chunks):
            chunk_path = f"{path}.chunks[{chunk_index}]"
            if not isinstance(chunk, dict):
                issues.append(f"{chunk_path} must be an object")
                continue
            _check_exact_fields(chunk, _CHUNK_FIELDS, chunk_path, issues)
            _required_string(chunk, "chunk_id", chunk_path, issues)
            _required_string(chunk, "text", chunk_path, issues)
            chunk_id = chunk.get("chunk_id")
            if isinstance(chunk_id, str):
                if chunk_id in chunk_ids:
                    issues.append(f"{chunk_path}.chunk_id is duplicated")
                chunk_ids.add(chunk_id)
            position = chunk.get("position")
            if not isinstance(position, int) or isinstance(position, bool) or position < 0:
                issues.append(f"{chunk_path}.position must be a non-negative integer")
            else:
                positions.append(position)
            chunk_text = chunk.get("text")
            chunk_digest = chunk.get("content_sha256")
            if not isinstance(chunk_digest, str) or not _HEX64.fullmatch(chunk_digest):
                issues.append(f"{chunk_path}.content_sha256 must be a lowercase SHA-256")
            elif isinstance(chunk_text, str) and sha256_text(chunk_text) != chunk_digest:
                issues.append(f"{chunk_path}.content_sha256 does not match text")
            _validate_location(chunk.get("location"), f"{chunk_path}.location", issues)
        if sorted(positions) != list(range(len(chunks))):
            issues.append(f"{path}.chunks positions must be contiguous from zero")
        if isinstance(text, str) and all(isinstance(item, dict) for item in chunks):
            joined = "\n".join(str(item.get("text", "")) for item in chunks)
            if joined != text:
                issues.append(f"{path}.text must equal chunks joined by newline")
    return issues


def validate_manifest(payload: Any) -> Dict[str, Any]:
    """Strictly validate and return a defensive copy of a sealed manifest."""

    issues: List[str] = []
    if not isinstance(payload, dict):
        raise ManifestValidationError(["manifest must be an object"])
    _check_exact_fields(payload, _TOP_LEVEL_FIELDS, "manifest", issues)
    for field in (
        "schema_version",
        "knowledge_base_id",
        "knowledge_space",
        "data_level",
        "version",
        "published_at",
        "retrieval_config_version",
        "content_set_sha256",
        "manifest_sha256",
    ):
        _required_string(payload, field, "manifest", issues)
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        issues.append("manifest.schema_version is unsupported")
    if payload.get("retrieval_config_version") != RETRIEVAL_CONFIG_VERSION:
        issues.append("manifest.retrieval_config_version is unsupported")
    if payload.get("knowledge_space") == DEFAULT_DEMO_SPACE and payload.get(
        "data_level"
    ) != DEMO_DATA_LEVEL:
        issues.append("demo knowledge space must use DEMO/SYNTHETIC data level")
    if payload.get("data_level") not in _DATA_LEVELS:
        issues.append("manifest.data_level is not an allowed source level")
    if not _is_iso_datetime(payload.get("published_at")):
        issues.append("manifest.published_at must be an ISO datetime")
    vector_dimension = payload.get("vector_dimension")
    if not isinstance(vector_dimension, int) or isinstance(vector_dimension, bool) or vector_dimension < 2:
        issues.append("manifest.vector_dimension must be an integer of at least two")
        vector_dimension = 0
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        issues.append("manifest.entries must be a non-empty list")
        entries = []
    source_ids: set[str] = set()
    for index, entry in enumerate(entries):
        issues.extend(
            _validate_entry(
                entry,
                index=index,
                vector_dimension=vector_dimension,
                manifest_space=str(payload.get("knowledge_space", "")),
                manifest_data_level=str(payload.get("data_level", "")),
            )
        )
        if isinstance(entry, dict) and isinstance(entry.get("source_id"), str):
            if entry["source_id"] in source_ids:
                issues.append(f"entries[{index}].source_id is duplicated")
            source_ids.add(entry["source_id"])

    expected_content_set_hash = sha256_json(entries)
    declared_content_set_hash = payload.get("content_set_sha256")
    if not isinstance(declared_content_set_hash, str) or not _HEX64.fullmatch(
        declared_content_set_hash
    ):
        issues.append("manifest.content_set_sha256 must be a lowercase SHA-256")
    elif declared_content_set_hash != expected_content_set_hash:
        issues.append("manifest.content_set_sha256 does not match entries")
    version = payload.get("version")
    if isinstance(version, str) and isinstance(declared_content_set_hash, str):
        if not version.endswith("@" + declared_content_set_hash[:12]):
            issues.append("manifest.version must end with the content hash prefix")

    expected_manifest_hash = sha256_json(
        {key: value for key, value in payload.items() if key != "manifest_sha256"}
    )
    declared_manifest_hash = payload.get("manifest_sha256")
    if not isinstance(declared_manifest_hash, str) or not _HEX64.fullmatch(
        declared_manifest_hash
    ):
        issues.append("manifest.manifest_sha256 must be a lowercase SHA-256")
    elif declared_manifest_hash != expected_manifest_hash:
        issues.append("manifest.manifest_sha256 does not match sealed manifest")
    if issues:
        raise ManifestValidationError(issues)
    return copy.deepcopy(payload)


def seal_manifest(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Seal a new content-addressed version; callers must still provide entry hashes."""

    sealed = copy.deepcopy(dict(payload))
    sealed.pop("manifest_sha256", None)
    entries = sealed.get("entries")
    if not isinstance(entries, list):
        raise ManifestValidationError(["manifest.entries must be a list before sealing"])
    content_hash = sha256_json(entries)
    sealed["content_set_sha256"] = content_hash
    base_version = str(sealed.get("version", "")).split("@", 1)[0]
    sealed["version"] = f"{base_version}@{content_hash[:12]}"
    sealed["manifest_sha256"] = sha256_json(sealed)
    return validate_manifest(sealed)


def _manifest_from_dict(payload: Dict[str, Any]) -> KnowledgeManifest:
    entries: List[KnowledgeEntry] = []
    for raw in payload["entries"]:
        chunks = tuple(
            KnowledgeChunk(
                chunk_id=chunk["chunk_id"],
                position=chunk["position"],
                text=chunk["text"],
                content_sha256=chunk["content_sha256"],
                location=copy.deepcopy(chunk["location"]),
            )
            for chunk in raw["chunks"]
        )
        entries.append(
            KnowledgeEntry(
                source_id=raw["source_id"],
                title=raw["title"],
                publisher=raw["publisher"],
                source_type=raw["source_type"],
                published_at=raw["published_at"],
                document_version=raw["document_version"],
                ingested_at=raw["ingested_at"],
                content_sha256=raw["content_sha256"],
                license=copy.deepcopy(raw["license"]),
                scope=copy.deepcopy(raw["scope"]),
                location=copy.deepcopy(raw["location"]),
                review_status=copy.deepcopy(raw["review_status"]),
                text=raw["text"],
                feature_vector=tuple(float(value) for value in raw["feature_vector"]),
                knowledge_space=raw["knowledge_space"],
                data_level=raw["data_level"],
                chunks=chunks,
            )
        )
    return KnowledgeManifest(
        schema_version=payload["schema_version"],
        knowledge_base_id=payload["knowledge_base_id"],
        knowledge_space=payload["knowledge_space"],
        data_level=payload["data_level"],
        version=payload["version"],
        published_at=payload["published_at"],
        retrieval_config_version=payload["retrieval_config_version"],
        vector_dimension=payload["vector_dimension"],
        content_set_sha256=payload["content_set_sha256"],
        manifest_sha256=payload["manifest_sha256"],
        entries=tuple(entries),
    )


def load_manifest(path: Path | str) -> KnowledgeManifest:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestValidationError([f"unable to load manifest: {type(exc).__name__}"]) from exc
    return _manifest_from_dict(validate_manifest(payload))


def _normalized_query_text(text: str) -> str:
    return " ".join(str(text).strip().lower().split())


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _visual_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    distance = math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(left, right)))
    return max(0.0, min(1.0, 1.0 - distance / math.sqrt(len(left))))


def _date_is_active(policy: Mapping[str, Any], on_date: date) -> bool:
    valid_from = policy.get("valid_from")
    valid_until = policy.get("valid_until")
    if valid_from != "UNKNOWN" and date.fromisoformat(str(valid_from)) > on_date:
        return False
    if valid_until != "UNKNOWN" and date.fromisoformat(str(valid_until)) < on_date:
        return False
    return True


_QUERY_TO_SCOPE = {
    "artifact_type": "artifact_types",
    "artifact_types": "artifact_types",
    "material": "materials",
    "materials": "materials",
    "modality": "modalities",
    "modalities": "modalities",
    "craft": "crafts",
    "crafts": "crafts",
    "region": "regions",
    "regions": "regions",
}


def _query_values(value: Any) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {_normalized_query_text(item) for item in values if isinstance(item, str) and item.strip()}


def _scope_compatible(entry: KnowledgeEntry, attributes: Mapping[str, Any]) -> bool:
    for query_key, scope_key in _QUERY_TO_SCOPE.items():
        if query_key not in attributes:
            continue
        requested = _query_values(attributes[query_key])
        allowed = {_normalized_query_text(value) for value in entry.scope[scope_key]}
        if requested and "*" not in allowed and requested.isdisjoint(allowed):
            return False
    return True


def _structured_score(entry: KnowledgeEntry, attributes: Mapping[str, Any]) -> float:
    if not attributes:
        return 0.0
    searchable_scope = {
        _normalized_query_text(value)
        for field in ("artifact_types", "materials", "modalities", "crafts", "regions", "tags")
        for value in entry.scope[field]
    }
    searchable_text = _normalized_query_text(entry.text + " " + entry.title)
    scores: List[float] = []
    for raw in attributes.values():
        for value in _query_values(raw):
            if value in searchable_scope:
                scores.append(1.0)
            elif value and value in searchable_text:
                scores.append(0.7)
            else:
                scores.append(0.0)
    return sum(scores) / len(scores) if scores else 0.0


def _lexical_scores(query_tokens: Sequence[str], documents: Sequence[Sequence[str]]) -> List[float]:
    if not query_tokens:
        return [0.0] * len(documents)
    document_count = max(len(documents), 1)
    document_frequency: Counter[str] = Counter()
    for tokens in documents:
        document_frequency.update(set(tokens))
    query_counter = Counter(query_tokens)
    query_weight = sum(
        count * (math.log((document_count + 1) / (document_frequency[token] + 1)) + 1.0)
        for token, count in query_counter.items()
    )
    scores: List[float] = []
    for tokens in documents:
        document_counter = Counter(tokens)
        overlap = 0.0
        for token, count in query_counter.items():
            idf = math.log((document_count + 1) / (document_frequency[token] + 1)) + 1.0
            overlap += min(count, document_counter.get(token, 0)) * idf
        scores.append(overlap / query_weight if query_weight else 0.0)
    return scores


class KnowledgeBase:
    """Immutable local knowledge version with deterministic hybrid retrieval."""

    def __init__(
        self,
        manifest: KnowledgeManifest,
        *,
        embedding_provider: Optional[EmbeddingProvider] = None,
        offline: bool = True,
    ) -> None:
        self.manifest = manifest
        self.offline = offline
        self.embedding_runtime = EmbeddingRuntime(embedding_provider, offline=offline)
        self._entries_by_id = {entry.source_id: entry for entry in manifest.entries}

    @classmethod
    def from_path(
        cls,
        path: Path | str,
        *,
        embedding_provider: Optional[EmbeddingProvider] = None,
        offline: bool = True,
    ) -> "KnowledgeBase":
        return cls(
            load_manifest(path), embedding_provider=embedding_provider, offline=offline
        )

    @property
    def version(self) -> str:
        return self.manifest.version

    @property
    def immutable_version_hash(self) -> str:
        return self.manifest.manifest_sha256

    def health(self) -> Dict[str, Any]:
        probe = self.embedding_runtime.embed(["relicscope health probe"])
        return {
            "status": "degraded" if probe.degraded else "ready",
            "knowledge_base_id": self.manifest.knowledge_base_id,
            "knowledge_version": self.manifest.version,
            "immutable_version_hash": self.manifest.manifest_sha256,
            "knowledge_space": self.manifest.knowledge_space,
            "data_level": self.manifest.data_level,
            "entry_count": len(self.manifest.entries),
            "offline": self.offline,
            "embedding": {
                "provider": probe.provider,
                "model": probe.model,
                "algorithm": probe.algorithm,
                "degraded": probe.degraded,
                "reason": probe.degraded_reason,
            },
        }

    def search(
        self,
        *,
        text: str = "",
        attributes: Optional[Mapping[str, Any]] = None,
        visual_feature_vector: Optional[Sequence[float]] = None,
        knowledge_spaces: Optional[Sequence[str]] = None,
        access_scope: str = "demo-public",
        purpose: str = "product_demo",
        minimum_score: float = 0.20,
        limit: int = 5,
        license_allowlist: Optional[Sequence[str]] = None,
        retrieved_at: Optional[str] = None,
        policy_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        attributes = dict(attributes or {})
        spaces = list(knowledge_spaces or [self.manifest.knowledge_space])
        if len(spaces) != 1:
            raise KnowledgePolicyError("cross-knowledge-space retrieval is forbidden")
        if spaces[0] != self.manifest.knowledge_space:
            raise KnowledgePolicyError("requested knowledge space is not loaded")
        if not isinstance(minimum_score, (int, float)) or not 0.0 <= float(minimum_score) <= 1.0:
            raise ValueError("minimum_score must be between zero and one")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if visual_feature_vector is not None:
            if len(visual_feature_vector) != self.manifest.vector_dimension:
                raise ValueError(
                    f"visual_feature_vector must have {self.manifest.vector_dimension} values"
                )
            if any(not math.isfinite(float(value)) for value in visual_feature_vector):
                raise ValueError("visual_feature_vector must contain finite values")

        normalized_query = {
            "text": _normalized_query_text(text),
            "attributes": attributes,
            "visual_feature_vector": (
                [round(float(value), 8) for value in visual_feature_vector]
                if visual_feature_vector is not None
                else None
            ),
            "knowledge_spaces": spaces,
            "access_scope": access_scope,
            "purpose": purpose,
            "minimum_score": round(float(minimum_score), 8),
            "limit": limit,
            "license_allowlist": sorted(license_allowlist) if license_allowlist else None,
        }
        query_hash = sha256_json(normalized_query)
        now_date = policy_date or datetime.now(timezone.utc).date()
        exclusions: List[Dict[str, str]] = []
        eligible: List[KnowledgeEntry] = []
        allowlist = set(license_allowlist or [])
        for entry in self.manifest.entries:
            policy = entry.license
            exclusion: Optional[str] = None
            if entry.knowledge_space != spaces[0]:
                exclusion = "KNOWLEDGE_SPACE_MISMATCH"
            elif access_scope not in policy["access_scopes"]:
                exclusion = "ACCESS_SCOPE_MISMATCH"
            elif purpose not in policy["allowed_purposes"]:
                exclusion = "PURPOSE_NOT_LICENSED"
            elif allowlist and policy["identifier"] not in allowlist:
                exclusion = "LICENSE_NOT_ALLOWLISTED"
            elif not _date_is_active(policy, now_date):
                exclusion = "LICENSE_NOT_ACTIVE"
            elif not _scope_compatible(entry, attributes):
                exclusion = "APPLICABILITY_SCOPE_MISMATCH"
            if exclusion:
                exclusions.append({"source_id": entry.source_id, "reason": exclusion})
            else:
                eligible.append(entry)

        document_texts = [entry.title + "\n" + entry.text for entry in eligible]
        embedding_batch = self.embedding_runtime.embed(
            [normalized_query["text"]] + document_texts
        )
        query_embedding = embedding_batch.vectors[0] if embedding_batch.vectors else []
        document_embeddings = embedding_batch.vectors[1:]
        tokenized_documents = [tokenize(value) for value in document_texts]
        lexical = _lexical_scores(tokenize(normalized_query["text"]), tokenized_documents)

        modality_weights: Dict[str, float] = {}
        if normalized_query["text"]:
            modality_weights["text"] = 0.45
        if attributes:
            modality_weights["structured"] = 0.25
        if visual_feature_vector is not None:
            modality_weights["visual"] = 0.30
        if not modality_weights:
            raise ValueError("at least one text, structured, or visual query input is required")
        weight_total = sum(modality_weights.values())
        modality_weights = {
            key: round(value / weight_total, 8) for key, value in modality_weights.items()
        }

        ranked: List[Tuple[float, KnowledgeEntry, Dict[str, float]]] = []
        for index, entry in enumerate(eligible):
            embedding_similarity = (
                _cosine(query_embedding, document_embeddings[index])
                if index < len(document_embeddings)
                else 0.0
            )
            text_score = 0.65 * lexical[index] + 0.35 * embedding_similarity
            component_scores = {
                "lexical": round(lexical[index], 6),
                "text_embedding": round(embedding_similarity, 6),
                "text": round(text_score, 6),
                "structured": round(_structured_score(entry, attributes), 6),
                "visual": round(
                    _visual_similarity(visual_feature_vector, entry.feature_vector)
                    if visual_feature_vector is not None
                    else 0.0,
                    6,
                ),
            }
            total = sum(
                modality_weights[mode] * component_scores[mode] for mode in modality_weights
            )
            ranked.append((round(total, 8), entry, component_scores))
        ranked.sort(key=lambda item: (-item[0], item[1].source_id))

        results: List[Dict[str, Any]] = []
        for score, entry, components in ranked:
            if score < float(minimum_score):
                exclusions.append({"source_id": entry.source_id, "reason": "BELOW_MINIMUM_SCORE"})
                continue
            chunk = entry.chunks[0]
            citation = {
                "source_id": entry.source_id,
                "chunk_id": chunk.chunk_id,
                "knowledge_base_version": self.manifest.version,
                "knowledge_space": entry.knowledge_space,
                "data_level": entry.data_level,
                "location": dict(chunk.location),
                "content_sha256": entry.content_sha256,
                "chunk_sha256": chunk.content_sha256,
            }
            matched_on = [
                mode
                for mode in ("text", "structured", "visual")
                if mode in modality_weights and components[mode] > 0
            ]
            results.append(
                {
                    "rank": len(results) + 1,
                    "source_id": entry.source_id,
                    "title": entry.title,
                    "publisher": entry.publisher,
                    "source_type": entry.source_type,
                    "published_at": entry.published_at,
                    "document_version": entry.document_version,
                    "score": round(score, 6),
                    "component_scores": components,
                    "matched_on": matched_on,
                    "match_explanation": (
                        "本地文本、结构化属性与视觉特征的加权检索相似性；"
                        "该相似性不代表身份、真伪、年代、窑口或作者结论。"
                    ),
                    "snippet": chunk.text,
                    "applicability_scope": copy.deepcopy(entry.scope),
                    "source_status": copy.deepcopy(entry.review_status),
                    "license": {
                        "identifier": entry.license["identifier"],
                        "statement": entry.license["statement"],
                    },
                    "knowledge_space": entry.knowledge_space,
                    "data_level": entry.data_level,
                    "citation": citation,
                }
            )
            if len(results) >= limit:
                break

        citation_whitelist = [result["citation"] for result in results]
        stable_candidate_payload = [
            {
                "source_id": result["source_id"],
                "chunk_id": result["citation"]["chunk_id"],
                "score": result["score"],
                "content_sha256": result["citation"]["content_sha256"],
            }
            for result in results
        ]
        candidate_set_hash = sha256_json(stable_candidate_payload)
        timestamp = retrieved_at or utc_now()
        if not _is_iso_datetime(timestamp):
            raise ValueError("retrieved_at must be an ISO datetime")
        status = "LOCAL_KNOWLEDGE_INSUFFICIENT" if not results else "RESULTS_AVAILABLE"
        snapshot: Dict[str, Any] = {
            "status": status,
            "degraded": embedding_batch.degraded,
            "degraded_reasons": (
                [embedding_batch.degraded_reason] if embedding_batch.degraded_reason else []
            ),
            "offline": self.offline,
            "data_boundary": "LOCAL_ONLY" if self.offline else "APPROVED_ENDPOINT_ONLY",
            "knowledge_base_id": self.manifest.knowledge_base_id,
            "knowledge_base_version": self.manifest.version,
            "immutable_version_hash": self.manifest.manifest_sha256,
            "knowledge_space": self.manifest.knowledge_space,
            "data_level": self.manifest.data_level,
            "retrieved_at": timestamp,
            "query": normalized_query,
            "query_hash": query_hash,
            "retrieval_config_version": self.manifest.retrieval_config_version,
            "algorithm": {
                "fusion": RETRIEVAL_CONFIG_VERSION,
                "lexical": "relicscope-local-token-overlap-v1",
                "embedding_provider": embedding_batch.provider,
                "embedding_model": embedding_batch.model,
                "embedding_algorithm": embedding_batch.algorithm,
                "visual": "relicscope-normalized-feature-distance-v1",
                "weights": modality_weights,
            },
            "candidate_set_hash": candidate_set_hash,
            "result_count": len(results),
            "results": results,
            "citation_whitelist": citation_whitelist,
            "citation_whitelist_hash": sha256_json(citation_whitelist),
            "exclusions": exclusions,
            "abstention": (
                {
                    "code": "LOCAL_KNOWLEDGE_INSUFFICIENT",
                    "message": "没有达到当前门槛且满足许可、权限与适用范围的本地资料。",
                }
                if not results
                else None
            ),
            "interpretation_boundary": (
                "DEMO/SYNTHETIC 本地检索候选，仅用于验证检索与引用流程；"
                "非真实馆藏记录、专家结论或鉴定结论。"
            ),
        }
        snapshot["snapshot_hash"] = sha256_json(snapshot)
        return snapshot

    def validate_citations(
        self, citations: Sequence[Mapping[str, Any]], snapshot: Mapping[str, Any]
    ) -> Dict[str, Any]:
        if snapshot.get("knowledge_base_version") != self.manifest.version:
            raise KnowledgePolicyError("citation snapshot belongs to a different knowledge version")
        expected_snapshot = dict(snapshot)
        declared_snapshot_hash = expected_snapshot.pop("snapshot_hash", None)
        if declared_snapshot_hash != sha256_json(expected_snapshot):
            raise KnowledgePolicyError("citation snapshot hash is invalid")
        whitelist = snapshot.get("citation_whitelist")
        if not isinstance(whitelist, list):
            raise KnowledgePolicyError("citation snapshot has no whitelist")
        if snapshot.get("citation_whitelist_hash") != sha256_json(whitelist):
            raise KnowledgePolicyError("citation whitelist hash is invalid")
        allowed = {canonical_json(item): item for item in whitelist if isinstance(item, dict)}
        accepted: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for citation in citations:
            candidate = dict(citation)
            if canonical_json(candidate) in allowed:
                accepted.append(copy.deepcopy(candidate))
            else:
                rejected.append(
                    {
                        "source_id": str(candidate.get("source_id", "UNKNOWN")),
                        "reason": "CITATION_NOT_IN_RETRIEVAL_WHITELIST",
                    }
                )
        return {
            "all_valid": not rejected and bool(accepted),
            "accepted": accepted,
            "rejected": rejected,
            "knowledge_base_version": self.manifest.version,
            "query_hash": snapshot.get("query_hash"),
            "snapshot_hash": declared_snapshot_hash,
            "citation_whitelist_hash": snapshot.get("citation_whitelist_hash"),
        }

    @staticmethod
    def audit_payload(snapshot: Mapping[str, Any]) -> Dict[str, Any]:
        """Return the bounded payload to store in the session hash audit chain."""

        return {
            "query_hash": snapshot["query_hash"],
            "snapshot_hash": snapshot["snapshot_hash"],
            "candidate_set_hash": snapshot["candidate_set_hash"],
            "knowledge_base_version": snapshot["knowledge_base_version"],
            "immutable_version_hash": snapshot["immutable_version_hash"],
            "retrieval_config_version": snapshot["retrieval_config_version"],
            "algorithm": copy.deepcopy(snapshot["algorithm"]),
            "degraded": snapshot["degraded"],
            "degraded_reasons": list(snapshot["degraded_reasons"]),
            "citations": copy.deepcopy(snapshot["citation_whitelist"]),
            "citation_whitelist_hash": snapshot["citation_whitelist_hash"],
            "result_count": snapshot["result_count"],
            "knowledge_space": snapshot["knowledge_space"],
            "data_level": snapshot["data_level"],
        }


__all__ = [
    "DEFAULT_DEMO_SPACE",
    "DEMO_DATA_LEVEL",
    "KnowledgeBase",
    "KnowledgeChunk",
    "KnowledgeEntry",
    "KnowledgeError",
    "KnowledgeManifest",
    "KnowledgePolicyError",
    "MANIFEST_SCHEMA_VERSION",
    "ManifestValidationError",
    "RETRIEVAL_CONFIG_VERSION",
    "canonical_json",
    "load_manifest",
    "seal_manifest",
    "sha256_json",
    "sha256_text",
    "validate_manifest",
]
