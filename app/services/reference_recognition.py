from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np

from .artifact_retrieval import (
    ArtifactReference,
    ArtifactRetrievalEngine,
    EmbeddedView,
    NegativeReferenceControl,
    NegativeReviewStatus,
    ReferenceKind,
    RetrievalThresholds,
)
from .image_embedding import (
    EmbeddingImage,
    LocalImageEmbeddingClient,
    REFERENCE_EMBEDDING_INSTRUCTION,
    vector_sha256,
)
from .reference_library import (
    ReferenceIndexValidationError,
    ReferenceLibraryIndex,
    canonical_json,
    sha256_file,
)
from .reference_explanation import explain_reference_result
from .reference_quality import (
    REFERENCE_QUALITY_ALGORITHM_ID,
    ReferenceQualityError,
    assess_reference_quality,
)


REFERENCE_VECTOR_INDEX_SCHEMA_VERSION = "relicscope-reference-vector-index-v1"
REFERENCE_CALIBRATION_SCHEMA_VERSION = "relicscope-reference-calibration-v1"
REFERENCE_RECOGNITION_RESULT_SCHEMA_VERSION = "relicscope-reference-recognition-v1"
REFERENCE_EVALUATION_RESULT_HASH_ALGORITHM = (
    "sha256-canonical-json-excluding-evaluation-and-calibration-hashes-v1"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_VECTOR_ARCHIVE_FIELDS = {
    "metadata_json",
    "vectors",
    "artifact_ids",
    "image_ids",
    "image_sha256s",
    "angles",
    "qualities",
    "record_kinds",
}


class ReferenceRecognitionError(RuntimeError):
    pass


class ReferenceVectorIndexError(ReferenceRecognitionError):
    pass


class ReferenceCalibrationError(ReferenceRecognitionError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _instruction_sha256() -> str:
    return _sha256_bytes(REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8"))


def _reference_quality(row: Mapping[str, Any]) -> float:
    quality = row.get("quality", {})
    if not isinstance(quality, Mapping):
        raise ReferenceVectorIndexError("reference image quality is unavailable")
    try:
        assessment = assess_reference_quality(quality.get("quality_gate", {}))
    except ReferenceQualityError as exc:
        raise ReferenceVectorIndexError(
            "reference image quality envelope is invalid"
        ) from exc
    if quality.get("reference_quality") != assessment.to_dict():
        raise ReferenceVectorIndexError(
            "reference image quality score is not bound to the versioned algorithm"
        )
    return assessment.score


def _public_artifact_metadata(
    record: Mapping[str, Any],
    *,
    library_id: str,
    library_version: str,
    manifest_sha256: str,
) -> Dict[str, Any]:
    source = record.get("source") or {}
    rights = record.get("rights") or {}
    review = record.get("expert_review") or {}
    counterfeit = record.get("counterfeit_profile") or {}
    artifact_id = str(record.get("artifact_id"))
    return {
        "citation_id": f"REFERENCE:{manifest_sha256[:12]}:{artifact_id}",
        "display_name": record.get("display_name"),
        "physical_object_id": record.get("physical_object_id"),
        "ceramic_class": record.get("ceramic_class"),
        "catalogue": record.get("catalogue_metadata") or record.get("catalogue") or {},
        "source_citation": {
            "source_type": source.get("source_type"),
            "institution": source.get("institution"),
            "collection_name": source.get("collection_name"),
            "accession_number": source.get("accession_number"),
            "record_locator": source.get("record_locator"),
            "retrieved_at": source.get("retrieved_at"),
        },
        "rights": {
            "rights_holder": rights.get("rights_holder"),
            "license_identifier": rights.get("license_identifier"),
            "attribution_required": rights.get("attribution_required"),
            "attribution_text": rights.get("attribution_text"),
            "valid_until": rights.get("valid_until"),
        },
        "expert_review": {
            "review_id": review.get("review_id"),
            "decision": review.get("decision"),
            "reviewer_credential": review.get("reviewer_credential"),
            "reviewer_institution": review.get("reviewer_institution"),
            "reviewed_at": review.get("reviewed_at"),
            "dispute_status": review.get("dispute_status"),
        },
        "counterfeit_profile": (
            {
                "counterfeit_type": counterfeit.get("counterfeit_type"),
                "claimed_identity": counterfeit.get("claimed_identity"),
                "comparison_artifact_ids": counterfeit.get(
                    "comparison_artifact_ids"
                ),
                "known_indicators": counterfeit.get("known_indicators"),
                "evidence_sha256": counterfeit.get("evidence_sha256"),
            }
            if counterfeit
            else None
        ),
        "record_sha256": record.get("record_sha256"),
        "library_binding": {
            "library_id": library_id,
            "library_version": library_version,
            "manifest_sha256": manifest_sha256,
        },
    }


def _calibration_payload_hash(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("calibration_record_sha256", None)
    return _sha256_json(unsigned)


def _evaluation_result_payload_hash(payload: Mapping[str, Any]) -> str:
    unsigned = {
        str(key): value
        for key, value in payload.items()
        if key not in {"evaluation_result_sha256", "calibration_record_sha256"}
    }
    return _sha256_json(unsigned)


def _calibration_positive_count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReferenceCalibrationError(f"calibration {label} must be positive")
    return value


def _calibration_rate(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceCalibrationError(f"calibration {label} must be numeric")
    rate = float(value)
    if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
        raise ReferenceCalibrationError(
            f"calibration {label} must be finite and between 0 and 1"
        )
    return rate


@dataclass(frozen=True)
class ReferenceVectorBuildResult:
    output_path: Path
    library_id: str
    library_version: str
    vector_count: int
    artifact_count: int
    dimension: int
    index_payload_sha256: str
    archive_sha256: str
    model_source: str
    model_revision: str

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["output_path"] = str(self.output_path)
        result["status"] = "BUILT"
        return result


class ReferenceVectorIndexBuilder:
    """Precompute an immutable, model-bound image index from controlled media."""

    def __init__(
        self,
        metadata_index_path: Path | str,
        output_path: Path | str,
        embedding_client: LocalImageEmbeddingClient,
        *,
        batch_size: int = 4,
    ) -> None:
        self.metadata_index_path = Path(metadata_index_path)
        self.output_path = Path(output_path)
        self.embedding_client = embedding_client
        self.batch_size = int(batch_size)
        if not 1 <= self.batch_size <= 8:
            raise ValueError("embedding build batch size must be between 1 and 8")

    async def build(self) -> ReferenceVectorBuildResult:
        if not self.embedding_client.immutable_identity_configured:
            raise ReferenceVectorIndexError(
                "an immutable local embedding model identity is required"
            )
        metadata_index = ReferenceLibraryIndex(self.metadata_index_path)
        metadata = metadata_index.metadata()
        if (
            metadata.get("reference_quality_algorithm_id")
            != REFERENCE_QUALITY_ALGORITHM_ID
        ):
            raise ReferenceVectorIndexError(
                "reference metadata quality algorithm is unsupported"
            )
        rows = list(metadata_index.iter_images())
        if not rows:
            raise ReferenceVectorIndexError("reference metadata index contains no images")

        artifact_ids = sorted({str(row["artifact_id"]) for row in rows})
        artifacts = {
            artifact_id: _public_artifact_metadata(
                metadata_index.get_artifact(artifact_id),
                library_id=str(metadata["library_id"]),
                library_version=str(metadata["library_version"]),
                manifest_sha256=str(metadata["manifest_sha256"]),
            )
            for artifact_id in artifact_ids
        }
        vectors: list[list[float]] = []
        bindings: list[Dict[str, Any]] = []
        for offset in range(0, len(rows), self.batch_size):
            batch = rows[offset : offset + self.batch_size]
            inputs: list[EmbeddingImage] = []
            for row in batch:
                content = metadata_index.read_image_bytes(str(row["image_id"]))
                actual_sha = _sha256_bytes(content)
                if actual_sha != str(row["sha256"]):
                    raise ReferenceVectorIndexError(
                        f"reference image hash changed: {row['image_id']}"
                    )
                inputs.append(
                    EmbeddingImage(
                        content=content,
                        mime_type=str(row["mime_type"]),
                        sha256=actual_sha,
                    )
                )
            response = await self.embedding_client.embed(inputs)
            if not response.get("available"):
                raise ReferenceVectorIndexError(
                    "local reference embedding failed: "
                    + str(response.get("error", "EMBEDDING_UNAVAILABLE"))
                )
            if response.get("input_hashes") != [item.sha256 for item in inputs]:
                raise ReferenceVectorIndexError("embedding input order was not preserved")
            for row, vector, output_sha in zip(
                batch, response["vectors"], response["output_hashes"], strict=True
            ):
                if vector_sha256(vector) != output_sha:
                    raise ReferenceVectorIndexError("embedding vector hash mismatch")
                values = np.asarray(vector, dtype=np.float32).tolist()
                if len(values) != self.embedding_client.expected_dimension:
                    raise ReferenceVectorIndexError("embedding dimension mismatch")
                vectors.append(values)
                bindings.append(
                    {
                        "position": len(bindings),
                        "artifact_id": str(row["artifact_id"]),
                        "image_id": str(row["image_id"]),
                        "image_sha256": str(row["sha256"]),
                        "angle": str(row["angle"]),
                        "quality": _reference_quality(row),
                        "record_kind": str(row["record_kind"]),
                        "provider_output_sha256": output_sha,
                        "vector_sha256": vector_sha256(values),
                    }
                )

        matrix = np.ascontiguousarray(vectors, dtype=np.float32)
        if matrix.ndim != 2 or matrix.shape[1] != self.embedding_client.expected_dimension:
            raise ReferenceVectorIndexError("invalid embedding matrix")
        vector_matrix_sha256 = _sha256_bytes(matrix.tobytes(order="C"))
        manifest = {
            "schema_version": REFERENCE_VECTOR_INDEX_SCHEMA_VERSION,
            "created_at": _utc_now(),
            "library_id": metadata["library_id"],
            "library_version": metadata["library_version"],
            "manifest_sha256": metadata["manifest_sha256"],
            "metadata_index_payload_sha256": metadata["index_payload_sha256"],
            "metadata_index_file_sha256": sha256_file(self.metadata_index_path),
            "data_classification": metadata["data_classification"],
            "reference_artifact_count": metadata["reference_artifact_count"],
            "counterfeit_record_count": metadata["counterfeit_record_count"],
            "vector_count": len(bindings),
            "dimension": int(matrix.shape[1]),
            "model": self.embedding_client.model,
            "model_source": self.embedding_client.model_source,
            "model_revision": self.embedding_client.model_revision,
            "instruction_sha256": _instruction_sha256(),
            "reference_quality_algorithm_id": REFERENCE_QUALITY_ALGORITHM_ID,
            "vector_matrix_sha256": vector_matrix_sha256,
            "bindings_sha256": _sha256_json(bindings),
            "artifacts_sha256": _sha256_json(artifacts),
            "bindings": bindings,
            "artifacts": artifacts,
        }
        manifest["index_payload_sha256"] = _sha256_json(manifest)
        if self.output_path.is_symlink():
            raise ReferenceVectorIndexError("refusing to replace a symbolic-link vector index")
        destination = self.output_path.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                np.savez_compressed(
                    stream,
                    metadata_json=np.frombuffer(
                        canonical_json(manifest).encode("utf-8"), dtype=np.uint8
                    ),
                    vectors=matrix,
                    artifact_ids=np.asarray(
                        [item["artifact_id"] for item in bindings], dtype="U128"
                    ),
                    image_ids=np.asarray(
                        [item["image_id"] for item in bindings], dtype="U128"
                    ),
                    image_sha256s=np.asarray(
                        [item["image_sha256"] for item in bindings], dtype="U64"
                    ),
                    angles=np.asarray(
                        [item["angle"] for item in bindings], dtype="U32"
                    ),
                    qualities=np.asarray(
                        [item["quality"] for item in bindings], dtype=np.float32
                    ),
                    record_kinds=np.asarray(
                        [item["record_kind"] for item in bindings], dtype="U16"
                    ),
                )
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return ReferenceVectorBuildResult(
            output_path=destination,
            library_id=str(metadata["library_id"]),
            library_version=str(metadata["library_version"]),
            vector_count=len(bindings),
            artifact_count=len(artifact_ids),
            dimension=int(matrix.shape[1]),
            index_payload_sha256=str(manifest["index_payload_sha256"]),
            archive_sha256=sha256_file(destination),
            model_source=self.embedding_client.model_source,
            model_revision=self.embedding_client.model_revision,
        )


@dataclass(frozen=True)
class LoadedReferenceVectorIndex:
    metadata: Mapping[str, Any]
    references: Sequence[ArtifactReference]
    archive_sha256: str


def load_reference_vector_index(
    vector_index_path: Path | str,
    metadata_index_path: Path | str,
    *,
    expected_model: str,
    expected_model_source: str,
    expected_model_revision: str,
    expected_dimension: int,
) -> LoadedReferenceVectorIndex:
    path = Path(vector_index_path)
    if path.is_symlink() or not path.is_file():
        raise ReferenceVectorIndexError("reference vector index is unavailable")
    if path.stat().st_size > 2 * 1024 * 1024 * 1024:
        raise ReferenceVectorIndexError("reference vector index exceeds the safe size limit")
    metadata_index = ReferenceLibraryIndex(metadata_index_path)
    catalog_metadata = metadata_index.metadata()
    if (
        catalog_metadata.get("reference_quality_algorithm_id")
        != REFERENCE_QUALITY_ALGORITHM_ID
    ):
        raise ReferenceVectorIndexError(
            "reference metadata quality algorithm is unsupported"
        )
    try:
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != _VECTOR_ARCHIVE_FIELDS:
                raise ReferenceVectorIndexError("reference vector archive fields are invalid")
            metadata_raw = archive["metadata_json"]
            if metadata_raw.dtype != np.uint8 or metadata_raw.size > 64 * 1024 * 1024:
                raise ReferenceVectorIndexError("reference vector metadata is invalid")
            metadata = json.loads(metadata_raw.tobytes().decode("utf-8"))
            vectors = np.asarray(archive["vectors"], dtype=np.float32)
            artifact_ids = np.asarray(archive["artifact_ids"])
            image_ids = np.asarray(archive["image_ids"])
            image_sha256s = np.asarray(archive["image_sha256s"])
            angles = np.asarray(archive["angles"])
            qualities = np.asarray(archive["qualities"], dtype=np.float32)
            record_kinds = np.asarray(archive["record_kinds"])
    except ReferenceVectorIndexError:
        raise
    except Exception as exc:
        raise ReferenceVectorIndexError("unable to read reference vector index") from exc

    if not isinstance(metadata, dict):
        raise ReferenceVectorIndexError("reference vector metadata must be an object")
    stored_payload_hash = metadata.get("index_payload_sha256")
    unsigned = dict(metadata)
    unsigned.pop("index_payload_sha256", None)
    if stored_payload_hash != _sha256_json(unsigned):
        raise ReferenceVectorIndexError("reference vector metadata hash mismatch")
    if metadata.get("schema_version") != REFERENCE_VECTOR_INDEX_SCHEMA_VERSION:
        raise ReferenceVectorIndexError("unsupported reference vector index schema")
    bindings = metadata.get("bindings")
    artifacts = metadata.get("artifacts")
    if not isinstance(bindings, list) or not isinstance(artifacts, dict):
        raise ReferenceVectorIndexError("reference vector bindings are invalid")
    count = len(bindings)
    arrays = (artifact_ids, image_ids, image_sha256s, angles, qualities, record_kinds)
    if (
        vectors.ndim != 2
        or vectors.shape != (count, int(expected_dimension))
        or any(array.ndim != 1 or len(array) != count for array in arrays)
        or count < 1
    ):
        raise ReferenceVectorIndexError("reference vector archive dimensions are invalid")
    if not np.all(np.isfinite(vectors)) or not np.all(np.isfinite(qualities)):
        raise ReferenceVectorIndexError("reference vector archive contains non-finite values")
    if np.any(qualities < 0.0) or np.any(qualities > 1.0):
        raise ReferenceVectorIndexError("reference quality scores must be between zero and one")
    norms = np.linalg.norm(vectors, axis=1)
    if np.any(norms < 0.995) or np.any(norms > 1.005):
        raise ReferenceVectorIndexError("reference vectors are not normalized")
    identity_checks = {
        "model": expected_model,
        "model_source": expected_model_source,
        "model_revision": expected_model_revision.lower(),
        "dimension": int(expected_dimension),
        "instruction_sha256": _instruction_sha256(),
        "reference_quality_algorithm_id": REFERENCE_QUALITY_ALGORITHM_ID,
        "manifest_sha256": catalog_metadata["manifest_sha256"],
        "metadata_index_payload_sha256": catalog_metadata["index_payload_sha256"],
        "metadata_index_file_sha256": sha256_file(metadata_index_path),
    }
    for key, expected in identity_checks.items():
        if metadata.get(key) != expected:
            raise ReferenceVectorIndexError(f"reference vector {key} binding mismatch")
    if metadata.get("vector_matrix_sha256") != _sha256_bytes(
        np.ascontiguousarray(vectors).tobytes(order="C")
    ):
        raise ReferenceVectorIndexError("reference vector matrix hash mismatch")
    if metadata.get("bindings_sha256") != _sha256_json(bindings):
        raise ReferenceVectorIndexError("reference vector binding hash mismatch")
    if metadata.get("artifacts_sha256") != _sha256_json(artifacts):
        raise ReferenceVectorIndexError("reference artifact metadata hash mismatch")

    grouped: Dict[tuple[str, str], list[EmbeddedView]] = {}
    for position, binding in enumerate(bindings):
        if not isinstance(binding, dict) or binding.get("position") != position:
            raise ReferenceVectorIndexError("reference vector position binding is invalid")
        expected_row = {
            "artifact_id": str(artifact_ids[position]),
            "image_id": str(image_ids[position]),
            "image_sha256": str(image_sha256s[position]),
            "angle": str(angles[position]),
            "record_kind": str(record_kinds[position]),
        }
        if any(binding.get(key) != value for key, value in expected_row.items()):
            raise ReferenceVectorIndexError("reference vector row binding mismatch")
        if binding.get("vector_sha256") != vector_sha256(vectors[position].tolist()):
            raise ReferenceVectorIndexError("reference vector row hash mismatch")
        quality = float(qualities[position])
        if not math.isclose(quality, float(binding.get("quality")), abs_tol=1e-6):
            raise ReferenceVectorIndexError("reference quality binding mismatch")
        kind = str(record_kinds[position])
        if kind not in {"REFERENCE", "COUNTERFEIT"}:
            raise ReferenceVectorIndexError("reference record kind is invalid")
        key = (kind, str(artifact_ids[position]))
        grouped.setdefault(key, []).append(
            EmbeddedView(
                view_id=str(image_ids[position]),
                vector=vectors[position].tolist(),
                quality=quality,
                angle=str(angles[position]),
                input_sha256=str(image_sha256s[position]),
            )
        )

    references: list[ArtifactReference] = []
    for (record_kind, artifact_id), views in sorted(grouped.items()):
        artifact_metadata = artifacts.get(artifact_id)
        if not isinstance(artifact_metadata, dict):
            raise ReferenceVectorIndexError("reference artifact metadata is missing")
        if record_kind == "COUNTERFEIT":
            review = artifact_metadata.get("expert_review") or {}
            dispute = str(review.get("dispute_status", ""))
            review_status = (
                NegativeReviewStatus.VERIFIED
                if dispute in {"NO_KNOWN_DISPUTE", "RESOLVED"}
                else NegativeReviewStatus.DISPUTED
            )
            control = NegativeReferenceControl(
                record_id=str(review.get("review_id") or artifact_id),
                review_status=review_status,
                admissible_for_signal=review_status == NegativeReviewStatus.VERIFIED,
                signal_weight=1.0 if review_status == NegativeReviewStatus.VERIFIED else 0.0,
            )
            references.append(
                ArtifactReference(
                    artifact_id=artifact_id,
                    views=views,
                    kind=ReferenceKind.KNOWN_COUNTERFEIT,
                    negative_control=control,
                    metadata=artifact_metadata,
                )
            )
        else:
            references.append(
                ArtifactReference(
                    artifact_id=artifact_id,
                    views=views,
                    kind=ReferenceKind.CATALOG_ARTIFACT,
                    metadata=artifact_metadata,
                )
            )
    if len(references) != len(artifacts):
        raise ReferenceVectorIndexError("reference artifact/vector coverage is incomplete")
    return LoadedReferenceVectorIndex(
        metadata=metadata,
        references=tuple(references),
        archive_sha256=sha256_file(path),
    )


def load_reference_calibration(
    path: Path | str,
    *,
    vector_index_metadata: Mapping[str, Any],
) -> tuple[RetrievalThresholds, Dict[str, Any]]:
    calibration_path = Path(path)
    if calibration_path.is_symlink() or not calibration_path.is_file():
        raise ReferenceCalibrationError("frozen calibration record is unavailable")
    try:
        payload = json.loads(calibration_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReferenceCalibrationError("unable to read calibration record") from exc
    if not isinstance(payload, dict):
        raise ReferenceCalibrationError("calibration record must be an object")
    if payload.get("schema_version") != REFERENCE_CALIBRATION_SCHEMA_VERSION:
        raise ReferenceCalibrationError("unsupported calibration schema")
    record_hash = payload.get("calibration_record_sha256")
    if not isinstance(record_hash, str) or not _SHA256.fullmatch(record_hash):
        raise ReferenceCalibrationError("calibration record hash is invalid")
    if record_hash != _calibration_payload_hash(payload):
        raise ReferenceCalibrationError("calibration record hash mismatch")
    if (
        payload.get("evaluation_result_hash_algorithm")
        != REFERENCE_EVALUATION_RESULT_HASH_ALGORITHM
    ):
        raise ReferenceCalibrationError(
            "calibration evaluation result hash algorithm is unsupported"
        )
    if payload.get("evaluation_result_sha256") != _evaluation_result_payload_hash(
        payload
    ):
        raise ReferenceCalibrationError(
            "calibration evaluation result hash mismatch"
        )
    if payload.get("calibration_status") != "CALIBRATED":
        raise ReferenceCalibrationError("calibration record is not marked CALIBRATED")
    bindings = {
        "library_manifest_sha256": vector_index_metadata.get("manifest_sha256"),
        "embedding_index_payload_sha256": vector_index_metadata.get(
            "index_payload_sha256"
        ),
        "embedding_model_source": vector_index_metadata.get("model_source"),
        "embedding_model_revision": vector_index_metadata.get("model_revision"),
        "instruction_sha256": vector_index_metadata.get("instruction_sha256"),
        "reference_quality_algorithm_id": vector_index_metadata.get(
            "reference_quality_algorithm_id"
        ),
    }
    for key, expected in bindings.items():
        if payload.get(key) != expected:
            raise ReferenceCalibrationError(f"calibration {key} binding mismatch")
    for key in (
        "frozen_evaluation_manifest_sha256",
        "independent_capture_batch_sha256",
        "evaluation_result_sha256",
    ):
        value = payload.get(key)
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ReferenceCalibrationError(f"calibration {key} is invalid")
    protocol = payload.get("evaluation_protocol")
    if not isinstance(protocol, dict):
        raise ReferenceCalibrationError("calibration evaluation protocol is missing")
    if not isinstance(protocol.get("protocol_id"), str) or not protocol[
        "protocol_id"
    ].strip():
        raise ReferenceCalibrationError("calibration protocol_id is missing")
    required_truths = (
        "held_out_by_physical_object",
        "independent_reshoots",
        "exact_media_reuse_excluded",
    )
    if any(protocol.get(key) is not True for key in required_truths):
        raise ReferenceCalibrationError("calibration protocol does not prevent leakage")
    for key in (
        "held_out_physical_object_count",
        "independent_reshoot_query_count",
        "open_set_negative_count",
        "in_library_query_count",
    ):
        _calibration_positive_count(protocol.get(key), f"protocol {key}")

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise ReferenceCalibrationError("calibration measured metrics are missing")
    measured = {
        key: _calibration_rate(metrics.get(key), f"metric {key}")
        for key in (
            "top1",
            "top5",
            "far",
            "frr",
            "open_set_rejection_rate",
        )
    }
    if measured["top5"] < measured["top1"]:
        raise ReferenceCalibrationError("calibration Top-5 cannot be below Top-1")
    if not math.isclose(
        measured["open_set_rejection_rate"],
        1.0 - measured["far"],
        abs_tol=1e-6,
    ):
        raise ReferenceCalibrationError(
            "calibration open-set rejection and FAR are inconsistent"
        )
    per_view = metrics.get("per_view_recall")
    if not isinstance(per_view, dict) or not per_view:
        raise ReferenceCalibrationError("calibration per-view recall is missing")
    for angle, value in per_view.items():
        if not isinstance(angle, str) or not angle.strip():
            raise ReferenceCalibrationError("calibration per-view angle is invalid")
        _calibration_rate(value, f"per-view recall {angle}")

    thresholds_payload = payload.get("thresholds")
    if not isinstance(thresholds_payload, dict):
        raise ReferenceCalibrationError("calibration thresholds are missing")
    threshold_fields = set(RetrievalThresholds.__dataclass_fields__)
    if set(thresholds_payload) != threshold_fields:
        raise ReferenceCalibrationError("calibration threshold fields are incomplete")
    try:
        thresholds = RetrievalThresholds(**thresholds_payload)
    except (TypeError, ValueError) as exc:
        raise ReferenceCalibrationError("calibration thresholds are invalid") from exc
    return thresholds, payload


class ReferenceRecognitionService:
    """Runtime bridge from uploaded bytes to calibrated local reference retrieval."""

    def __init__(
        self,
        *,
        enabled: bool,
        metadata_index_path: Path | str,
        vector_index_path: Path | str,
        calibration_path: Path | str,
        embedding_client: LocalImageEmbeddingClient,
        backend: str = "numpy",
        minimum_reference_artifacts: int = 50,
        minimum_views_per_artifact: int = 5,
        minimum_counterfeit_records: int = 10,
        minimum_open_set_queries: int = 20,
    ) -> None:
        self.enabled = bool(enabled)
        self.metadata_index_path = Path(metadata_index_path)
        self.vector_index_path = Path(vector_index_path)
        self.calibration_path = Path(calibration_path)
        self.embedding_client = embedding_client
        self.backend = backend
        self.minimum_reference_artifacts = int(minimum_reference_artifacts)
        self.minimum_views_per_artifact = int(minimum_views_per_artifact)
        self.minimum_counterfeit_records = int(minimum_counterfeit_records)
        self.minimum_open_set_queries = int(minimum_open_set_queries)
        if min(
            self.minimum_reference_artifacts,
            self.minimum_views_per_artifact,
            self.minimum_open_set_queries,
        ) < 1 or self.minimum_counterfeit_records < 0:
            raise ValueError("reference-library runtime policy values are invalid")
        self.engine: Optional[ArtifactRetrievalEngine] = None
        self.vector_metadata: Optional[Mapping[str, Any]] = None
        self.calibration: Optional[Mapping[str, Any]] = None
        self.archive_sha256: Optional[str] = None
        self.readiness = "DISABLED" if not enabled else "NOT_LOADED"
        self.detail = "reference recognition is disabled"
        self.refresh()

    def refresh(self) -> Dict[str, Any]:
        self.engine = None
        self.vector_metadata = None
        self.calibration = None
        self.archive_sha256 = None
        if not self.enabled:
            self.readiness = "DISABLED"
            self.detail = "reference recognition is disabled"
            return self.summary()
        if not self.metadata_index_path.is_file():
            self.readiness = "METADATA_INDEX_MISSING"
            self.detail = "controlled reference metadata index has not been imported"
            return self.summary()
        try:
            metadata = ReferenceLibraryIndex(self.metadata_index_path).metadata()
        except ReferenceIndexValidationError:
            self.readiness = "METADATA_INDEX_INVALID"
            self.detail = "controlled reference metadata index failed integrity checks"
            return self.summary()
        policy_failures = []
        if int(metadata["reference_artifact_count"]) < self.minimum_reference_artifacts:
            policy_failures.append("reference artifact count")
        if int(metadata["minimum_images_per_artifact"]) < self.minimum_views_per_artifact:
            policy_failures.append("views per artifact")
        if int(metadata["counterfeit_record_count"]) < self.minimum_counterfeit_records:
            policy_failures.append("counterfeit record count")
        if policy_failures:
            self.readiness = "LIBRARY_POLICY_MISMATCH"
            self.detail = "controlled reference library is below runtime policy: " + ", ".join(
                policy_failures
            )
            return self.summary()
        if not self.vector_index_path.is_file():
            self.readiness = "VECTOR_INDEX_MISSING"
            self.detail = "reference embeddings have not been built"
            return self.summary()
        try:
            loaded = load_reference_vector_index(
                self.vector_index_path,
                self.metadata_index_path,
                expected_model=self.embedding_client.model,
                expected_model_source=self.embedding_client.model_source,
                expected_model_revision=self.embedding_client.model_revision,
                expected_dimension=self.embedding_client.expected_dimension,
            )
            self.vector_metadata = loaded.metadata
            self.archive_sha256 = loaded.archive_sha256
            calibration_sha: Optional[str] = None
            try:
                thresholds, calibration = load_reference_calibration(
                    self.calibration_path,
                    vector_index_metadata=loaded.metadata,
                )
                protocol = calibration["evaluation_protocol"]
                if (
                    int(protocol["held_out_physical_object_count"])
                    < self.minimum_reference_artifacts
                    or int(protocol["in_library_query_count"])
                    < self.minimum_reference_artifacts
                    or int(protocol["open_set_negative_count"])
                    < self.minimum_open_set_queries
                ):
                    raise ReferenceCalibrationError(
                        "calibration evaluation batch is below runtime policy"
                    )
                calibration_sha = str(calibration["calibration_record_sha256"])
                self.calibration = calibration
                self.readiness = "READY"
                self.detail = "reference library, embeddings and frozen calibration are ready"
            except ReferenceCalibrationError:
                thresholds = RetrievalThresholds()
                self.readiness = "CALIBRATION_REQUIRED"
                self.detail = "retrieval is available but identity acceptance is disabled"
            self.engine = ArtifactRetrievalEngine(
                loaded.references,
                thresholds=thresholds,
                backend=self.backend,
                embedding_space_id=(
                    f"{loaded.metadata['model_source']}@{loaded.metadata['model_revision']}"
                ),
                reference_library_id=str(loaded.metadata["library_id"]),
                catalog_manifest_sha256=str(loaded.metadata["manifest_sha256"]),
                calibration_record_sha256=calibration_sha,
                embedding_model_source=str(loaded.metadata["model_source"]),
                embedding_model_revision=str(loaded.metadata["model_revision"]),
            )
        except (ReferenceRecognitionError, ReferenceIndexValidationError, ValueError):
            self.readiness = "VECTOR_INDEX_INVALID"
            self.detail = "reference vector index failed identity or integrity checks"
        return self.summary()

    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, Any] = {
            "reference_artifact_count": 0,
            "counterfeit_record_count": 0,
            "reference_image_count": 0,
            "counterfeit_image_count": 0,
            "total_image_count": 0,
        }
        library: Dict[str, Any] = {}
        vector = self.vector_metadata or {}
        if vector:
            bindings = vector.get("bindings", [])
            reference_image_count = sum(
                1
                for item in bindings
                if isinstance(item, Mapping) and item.get("record_kind") == "REFERENCE"
            )
            counterfeit_image_count = sum(
                1
                for item in bindings
                if isinstance(item, Mapping)
                and item.get("record_kind") == "COUNTERFEIT"
            )
            counts = {
                "reference_artifact_count": vector.get(
                    "reference_artifact_count", 0
                ),
                "counterfeit_record_count": vector.get(
                    "counterfeit_record_count", 0
                ),
                "reference_image_count": reference_image_count,
                "counterfeit_image_count": counterfeit_image_count,
                "total_image_count": len(bindings),
            }
            library = {
                "library_id": vector.get("library_id"),
                "library_version": vector.get("library_version"),
                "manifest_sha256": vector.get("manifest_sha256"),
                "counterfeit_coverage": "DEMO_LIMITED",
            }
        elif self.metadata_index_path.is_file():
            try:
                metadata_index = ReferenceLibraryIndex(self.metadata_index_path)
                metadata = metadata_index.metadata()
                image_rows = list(metadata_index.iter_images())
                reference_image_count = sum(
                    1 for item in image_rows if item.get("record_kind") == "REFERENCE"
                )
                counterfeit_image_count = sum(
                    1
                    for item in image_rows
                    if item.get("record_kind") == "COUNTERFEIT"
                )
                counts = {
                    "reference_artifact_count": metadata["reference_artifact_count"],
                    "counterfeit_record_count": metadata["counterfeit_record_count"],
                    "reference_image_count": reference_image_count,
                    "counterfeit_image_count": counterfeit_image_count,
                    "total_image_count": metadata["image_count"],
                }
                library = {
                    "library_id": metadata["library_id"],
                    "library_version": metadata["library_version"],
                    "manifest_sha256": metadata["manifest_sha256"],
                    "counterfeit_coverage": metadata["counterfeit_coverage"],
                }
            except ReferenceIndexValidationError:
                pass
        calibration_hash = (
            self.calibration.get("calibration_record_sha256")
            if self.calibration
            else None
        )
        return {
            "enabled": self.enabled,
            "readiness": self.readiness,
            "detail": self.detail,
            **library,
            **counts,
            "embedding_index_payload_sha256": vector.get("index_payload_sha256"),
            "embedding_index_archive_sha256": self.archive_sha256,
            "embedding_model": self.embedding_client.model,
            "embedding_model_source": self.embedding_client.model_source,
            "embedding_model_revision": self.embedding_client.model_revision,
            "embedding_dimension": self.embedding_client.expected_dimension,
            "reference_quality_algorithm_id": vector.get(
                "reference_quality_algorithm_id"
            ),
            "minimum_reference_artifacts": self.minimum_reference_artifacts,
            "minimum_views_per_artifact": self.minimum_views_per_artifact,
            "minimum_reference_images": (
                self.minimum_reference_artifacts * self.minimum_views_per_artifact
            ),
            "minimum_counterfeit_records": self.minimum_counterfeit_records,
            "minimum_counterfeit_record_count": self.minimum_counterfeit_records,
            "minimum_open_set_queries": self.minimum_open_set_queries,
            "calibration_record_sha256": calibration_hash,
            "policy_id": self.engine.thresholds.policy_id if self.engine else None,
            "authenticity_state": "NOT_ASSESSED",
            "validation_scope": "DEMO_HELD_OUT_MEASUREMENT_NOT_CERTIFIED",
            "accuracy_claim_status": "NOT_CERTIFIED",
            "boundary": (
                "目录检索与负向参考交叉验证不构成真伪、年代、窑口、价值或法律结论。"
            ),
        }

    async def recognize(
        self,
        query_images: Sequence[EmbeddingImage],
        *,
        view_ids: Sequence[str],
        qualities: Sequence[float],
        angles: Sequence[Optional[str]],
        top_k: int = 5,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return self._blocked_result("EMBEDDING_UNAVAILABLE", "reference library disabled")
        if self.engine is None:
            status = (
                "CALIBRATION_REQUIRED"
                if self.readiness == "CALIBRATION_REQUIRED"
                else "EMBEDDING_UNAVAILABLE"
            )
            return self._blocked_result(status, self.detail)
        if not (
            len(query_images) == len(view_ids) == len(qualities) == len(angles)
            and 1 <= len(query_images) <= 5
        ):
            raise ValueError("query image, view, quality and angle counts must match")
        engine = self.engine
        library_summary = self.summary()
        embedding = await self.embedding_client.embed(query_images)
        if not embedding.get("available"):
            return self._blocked_result(
                "EMBEDDING_UNAVAILABLE",
                "local image embedding could not be verified",
                embedding_run={
                    key: value for key, value in embedding.items() if key != "vectors"
                },
            )
        query_views = tuple(
            EmbeddedView(
                view_id=view_ids[index],
                vector=embedding["vectors"][index],
                quality=float(qualities[index]),
                angle=angles[index],
                input_sha256=query_images[index].sha256,
            )
            for index in range(len(query_images))
        )
        result = engine.retrieve(query_views, top_k=top_k).to_dict()
        decision_status = str(result["same_artifact"]["status"])
        if (
            not result["same_artifact"]["accepted"]
            and not result["related"]["accepted"]
            and decision_status
            not in {
                "INSUFFICIENT_CAPTURE",
                "CALIBRATION_REQUIRED",
                "EXACT_MEDIA_REPLAY",
            }
        ):
            decision_status = "OPEN_SET_NO_MATCH"
            result["same_artifact"]["status"] = decision_status
        cross = result["counterfeit_signal"]
        if cross["triggered"] and not result["same_artifact"]["gates"].get(
            "counterfeit_conflict_absent", True
        ):
            cross_status = "CONFLICT_REVIEW"
        elif cross["triggered"]:
            cross_status = "STRONG_SIGNAL"
        elif result.get("counterfeit_hits"):
            top_score = float(result["counterfeit_hits"][0]["score"])
            cross_status = "WEAK_SIGNAL" if top_score >= 0.50 else "NO_SIGNAL"
        else:
            cross_status = "NOT_RUN"
        result.update(
            {
                "schema_version": REFERENCE_RECOGNITION_RESULT_SCHEMA_VERSION,
                "run_status": "COMPLETED",
                "decision_status": decision_status,
                "counterfeit_cross_check": {
                    "status": cross_status,
                    "signal": cross,
                    "candidates": result.get("counterfeit_hits", []),
                    "interpretation": (
                        "负向参考交叉验证未运行。"
                        if cross_status == "NOT_RUN"
                        else "与经审核负向参考案例相似，需专家交叉复核；不构成假货结论。"
                        if cross["triggered"]
                        else "未触发当前负向参考阈值；这不证明器物为真。"
                    ),
                },
                "embedding_run": {
                    key: value for key, value in embedding.items() if key != "vectors"
                },
                "reference_library": library_summary,
                "query_views": [
                    {
                        "view_id": view.view_id,
                        "angle": view.angle,
                        "quality": view.quality,
                        "input_sha256": view.input_sha256,
                    }
                    for view in query_views
                ],
                "authenticity_state": "NOT_ASSESSED",
                "result_snapshot_sha256": None,
            }
        )
        result["related_report"] = explain_reference_result(result).to_dict()
        snapshot = dict(result)
        snapshot.pop("result_snapshot_sha256", None)
        result["result_snapshot_sha256"] = _sha256_json(snapshot)
        return result

    def _blocked_result(
        self,
        status: str,
        detail: str,
        *,
        embedding_run: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        result = {
            "schema_version": REFERENCE_RECOGNITION_RESULT_SCHEMA_VERSION,
            "run_status": "BLOCKED",
            "decision_status": status,
            "detail": detail,
            "same_artifact": {
                "accepted": False,
                "status": status,
                "artifact_id": None,
                "score": None,
                "runner_up_margin": None,
                "calibration_required": status == "CALIBRATION_REQUIRED",
                "gates": {
                    "score": False,
                    "margin": False,
                    "coverage": False,
                    "complementary_angles": False,
                    "quality": False,
                    "calibration_record": False,
                    "counterfeit_conflict_absent": True,
                    "exact_media_replay_absent": True,
                },
                "reason_codes": [status],
                "audit_flags": [],
                "interpretation": detail,
            },
            "related": {
                "accepted": False,
                "status": "not_run",
                "qualifying_artifact_ids": [],
                "top_candidate_gates": {
                    "score": False,
                    "coverage": False,
                    "quality": False,
                },
                "reason_codes": [status],
                "interpretation": "Reference retrieval was not completed.",
            },
            "catalog_hits": [],
            "counterfeit_hits": [],
            "counterfeit_signal": {
                "triggered": False,
                "strength": "NONE",
                "reference_id": None,
                "score": None,
                "weighted_score": None,
                "evidence_weight": None,
                "review_record_id": None,
                "review_status": None,
                "catalog_score_delta": None,
                "competes_with_top_catalog": False,
                "gates": {"score": False, "coverage": False, "quality": False},
                "excluded_reference_count": 0,
                "reason_codes": ["CROSS_CHECK_NOT_RUN"],
            },
            "counterfeit_cross_check": {
                "status": "NOT_RUN",
                "candidates": [],
                "interpretation": "负向参考交叉验证未运行。",
            },
            "embedding_run": dict(embedding_run or {}),
            "reference_library": self.summary(),
            "query_views": [],
            "query_view_count": 0,
            "backend": None,
            "open_set_rejected": True,
            "calibration_required": status == "CALIBRATION_REQUIRED",
            "authenticity_state": "NOT_ASSESSED",
            "limitation": (
                "正式目录识别已停止；系统未生成身份、真伪、年代、窑口或价值结论。"
            ),
            "result_snapshot_sha256": None,
        }
        result["related_report"] = explain_reference_result(result).to_dict()
        snapshot = dict(result)
        snapshot.pop("result_snapshot_sha256", None)
        result["result_snapshot_sha256"] = _sha256_json(snapshot)
        return result


__all__ = [
    "LoadedReferenceVectorIndex",
    "REFERENCE_CALIBRATION_SCHEMA_VERSION",
    "REFERENCE_RECOGNITION_RESULT_SCHEMA_VERSION",
    "REFERENCE_VECTOR_INDEX_SCHEMA_VERSION",
    "ReferenceCalibrationError",
    "ReferenceRecognitionError",
    "ReferenceRecognitionService",
    "ReferenceVectorBuildResult",
    "ReferenceVectorIndexBuilder",
    "ReferenceVectorIndexError",
    "load_reference_calibration",
    "load_reference_vector_index",
]
