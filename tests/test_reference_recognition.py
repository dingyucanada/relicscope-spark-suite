from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from app.services.artifact_retrieval import RetrievalThresholds
from app.services.image_embedding import EmbeddingImage, vector_sha256
from app.services.reference_evaluation import (
    EVALUATION_RESULT_HASH_ALGORITHM,
    calculate_evaluation_result_sha256,
)
from app.services.reference_library import canonical_json
from app.services.reference_recognition import (
    REFERENCE_CALIBRATION_SCHEMA_VERSION,
    ReferenceCalibrationError,
    ReferenceRecognitionService,
    ReferenceVectorIndexBuilder,
    ReferenceVectorIndexError,
    load_reference_calibration,
    load_reference_vector_index,
)
from app.services.reference_quality import REFERENCE_QUALITY_ALGORITHM_ID


MODEL = "qwen3_vl_embedding_2b"
MODEL_SOURCE = "Qwen/Qwen3-VL-Embedding-2B"
MODEL_REVISION = "a" * 40
DIMENSION = 64
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _basis(first: float, second: float = 0.0, offset: int = 0) -> list[float]:
    vector = [0.0] * DIMENSION
    vector[offset] = first
    vector[offset + 1] = second
    norm = float(np.linalg.norm(vector))
    return [value / norm for value in vector]


def _passing_quality() -> dict[str, Any]:
    checks = {
        "resolution": True,
        "exposure": True,
        "clipping": True,
        "sharpness": True,
        "dynamic_range": True,
    }
    return {
        "quality_gate": {
            "passed": True,
            "checks": checks,
            "failed_checks": [],
        },
        "reference_quality": {
            "algorithm_id": REFERENCE_QUALITY_ALGORITHM_ID,
            "score": 1.0,
            "passed": True,
            "checks": checks,
            "failed_checks": [],
        },
    }


class FakeEmbeddingClient:
    model = MODEL
    model_source = MODEL_SOURCE
    model_revision = MODEL_REVISION
    expected_dimension = DIMENSION
    immutable_identity_configured = True

    def __init__(self, vectors: dict[bytes, list[float]]) -> None:
        self.vectors = vectors

    async def embed(self, images: list[EmbeddingImage] | tuple[EmbeddingImage, ...]):
        vectors = [self.vectors[item.content] for item in images]
        return {
            "available": True,
            "status": "SUCCESS",
            "model": self.model,
            "model_source": self.model_source,
            "model_revision": self.model_revision,
            "dimension": self.expected_dimension,
            "request_id": "fake-local-embedding-run",
            "input_hashes": [item.sha256 for item in images],
            "vectors": vectors,
            "output_hashes": [vector_sha256(item) for item in vectors],
        }


class FakeReferenceLibraryIndex:
    rows: list[dict[str, Any]] = []
    image_bytes: dict[str, bytes] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    library_metadata: dict[str, Any] = {}

    def __init__(self, index_path: Path | str) -> None:
        self.index_path = Path(index_path)

    def metadata(self, *, verify_integrity: bool = True) -> dict[str, Any]:
        return dict(self.library_metadata)

    def iter_images(self):
        yield from [dict(item) for item in self.rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        return dict(self.artifacts[artifact_id])

    def read_image_bytes(self, image_id: str) -> bytes:
        return self.image_bytes[image_id]


@pytest.fixture
def recognition_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.services import reference_recognition as module

    media = {
        "a-front": b"controlled-reference-a",
        "b-front": b"controlled-reference-b",
        "f-front": b"controlled-counterfeit-control",
    }
    rows = [
        {
            "artifact_id": "ref:A",
            "image_id": "a-front",
            "sha256": _digest(media["a-front"]),
            "mime_type": "image/png",
            "angle": "FRONT",
            "record_kind": "REFERENCE",
            "quality": _passing_quality(),
        },
        {
            "artifact_id": "ref:B",
            "image_id": "b-front",
            "sha256": _digest(media["b-front"]),
            "mime_type": "image/png",
            "angle": "FRONT",
            "record_kind": "REFERENCE",
            "quality": _passing_quality(),
        },
        {
            "artifact_id": "fake:C",
            "image_id": "f-front",
            "sha256": _digest(media["f-front"]),
            "mime_type": "image/png",
            "angle": "FRONT",
            "record_kind": "COUNTERFEIT",
            "quality": _passing_quality(),
        },
    ]

    def artifact(artifact_id: str, *, counterfeit: bool) -> dict[str, Any]:
        return {
            "artifact_id": artifact_id,
            "physical_object_id": "object:" + artifact_id,
            "display_name": "TEST/SYNTHETIC " + artifact_id,
            "ceramic_class": "PORCELAIN",
            "catalogue": {"description": "TEST/SYNTHETIC only"},
            "source": {
                "source_type": "INSTITUTIONAL_COLLECTION",
                "institution": "TEST/SYNTHETIC",
                "collection_name": "unit test",
                "accession_number": artifact_id,
                "record_locator": "unit-test://" + artifact_id,
                "retrieved_at": "2026-08-30T00:00:00Z",
            },
            "rights": {
                "rights_holder": "TEST/SYNTHETIC",
                "license_identifier": "TEST-LOCAL-DEMO",
                "attribution_required": True,
                "attribution_text": "TEST/SYNTHETIC only",
                "valid_until": "2030-01-01",
            },
            "expert_review": {
                "review_id": "review:" + artifact_id,
                "decision": "COUNTERFEIT" if counterfeit else "AUTHENTIC",
                "reviewer_credential": "TEST/SYNTHETIC",
                "reviewer_institution": "TEST/SYNTHETIC",
                "reviewed_at": "2026-08-30T00:00:00Z",
                "dispute_status": "NO_KNOWN_DISPUTE",
            },
            "counterfeit_profile": (
                {"known_indicators": ["TEST/SYNTHETIC control"]}
                if counterfeit
                else None
            ),
            "record_sha256": ("c" if counterfeit else "b") * 64,
        }

    FakeReferenceLibraryIndex.rows = rows
    FakeReferenceLibraryIndex.image_bytes = media
    FakeReferenceLibraryIndex.artifacts = {
        "ref:A": artifact("ref:A", counterfeit=False),
        "ref:B": artifact("ref:B", counterfeit=False),
        "fake:C": artifact("fake:C", counterfeit=True),
    }
    FakeReferenceLibraryIndex.library_metadata = {
        "library_id": "test:reference-library",
        "library_version": "test-v1@0123456789ab",
        "manifest_sha256": "1" * 64,
        "index_payload_sha256": "2" * 64,
        "data_classification": "TEST/SYNTHETIC",
        "reference_artifact_count": 2,
        "counterfeit_record_count": 1,
        "image_count": 3,
        "minimum_images_per_artifact": 1,
        "counterfeit_coverage": "DEMO_LIMITED",
        "reference_quality_algorithm_id": REFERENCE_QUALITY_ALGORITHM_ID,
    }
    monkeypatch.setattr(module, "ReferenceLibraryIndex", FakeReferenceLibraryIndex)
    metadata_path = tmp_path / "reference-library.sqlite3"
    metadata_path.write_bytes(b"immutable-test-metadata-index")
    vector_path = tmp_path / "reference-vectors.npz"
    vectors = {
        media["a-front"]: _basis(2**-0.5, 2**-0.5),
        media["b-front"]: _basis(1.0, 0.0, offset=2),
        media["f-front"]: _basis(1.0, 0.0, offset=4),
        b"independent-query-a": _basis(2**-0.5, 2**-0.5),
    }
    client = FakeEmbeddingClient(vectors)
    result = __import__("asyncio").run(
        ReferenceVectorIndexBuilder(
            metadata_path,
            vector_path,
            client,
            batch_size=2,
        ).build()
    )
    loaded = load_reference_vector_index(
        vector_path,
        metadata_path,
        expected_model=MODEL,
        expected_model_source=MODEL_SOURCE,
        expected_model_revision=MODEL_REVISION,
        expected_dimension=DIMENSION,
    )
    return {
        "metadata_path": metadata_path,
        "vector_path": vector_path,
        "client": client,
        "build_result": result,
        "loaded": loaded,
    }


def _calibration_payload(vector_metadata: dict[str, Any] | Any) -> dict[str, Any]:
    thresholds = RetrievalThresholds(
        policy_id="test-measured-calibration-v1",
        same_artifact_min_score=0.90,
        same_artifact_min_margin=0.10,
        same_artifact_min_coverage=0.50,
        same_artifact_min_quality=0.50,
        same_artifact_min_complementary_angles=1,
        related_min_score=0.60,
        related_min_coverage=0.20,
        related_min_quality=0.30,
        view_match_min_similarity=0.80,
        minimum_view_quality=0.25,
        counterfeit_alert_min_score=0.90,
        counterfeit_alert_min_coverage=0.50,
        counterfeit_alert_min_quality=0.50,
        view_score_weight=0.80,
    )
    payload = {
        "schema_version": REFERENCE_CALIBRATION_SCHEMA_VERSION,
        "calibration_status": "CALIBRATED",
        "created_at": "2026-08-30T00:00:00Z",
        "library_manifest_sha256": vector_metadata["manifest_sha256"],
        "embedding_index_payload_sha256": vector_metadata["index_payload_sha256"],
        "embedding_model_source": vector_metadata["model_source"],
        "embedding_model_revision": vector_metadata["model_revision"],
        "instruction_sha256": vector_metadata["instruction_sha256"],
        "reference_quality_algorithm_id": vector_metadata[
            "reference_quality_algorithm_id"
        ],
        "frozen_evaluation_manifest_sha256": "3" * 64,
        "independent_capture_batch_sha256": "4" * 64,
        "evaluation_result_sha256": "5" * 64,
        "evaluation_result_hash_algorithm": EVALUATION_RESULT_HASH_ALGORITHM,
        "evaluation_protocol": {
            "protocol_id": "test-held-out-reshoot-v1",
            "held_out_by_physical_object": True,
            "held_out_physical_object_count": 2,
            "independent_reshoots": True,
            "independent_reshoot_query_count": 2,
            "exact_media_reuse_excluded": True,
            "in_library_query_count": 2,
            "open_set_negative_count": 2,
        },
        "metrics": {
            "top1": 1.0,
            "top5": 1.0,
            "far": 0.0,
            "frr": 0.0,
            "open_set_rejection_rate": 1.0,
            "per_view_recall": {"FRONT": 1.0},
        },
        "thresholds": asdict(thresholds),
    }
    payload["evaluation_result_sha256"] = calculate_evaluation_result_sha256(payload)
    payload["calibration_record_sha256"] = hashlib.sha256(
        canonical_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


def test_vector_builder_hashes_the_persisted_float32_values(recognition_bundle):
    loaded = recognition_bundle["loaded"]
    build_result = recognition_bundle["build_result"]
    binding = loaded.metadata["bindings"][0]
    expected = np.asarray(_basis(2**-0.5, 2**-0.5), dtype=np.float32).tolist()

    assert binding["vector_sha256"] == vector_sha256(expected)
    assert build_result.archive_sha256 == loaded.archive_sha256
    assert build_result.vector_count == 3
    with np.load(recognition_bundle["vector_path"], allow_pickle=False) as archive:
        persisted_matrix = np.ascontiguousarray(archive["vectors"], dtype=np.float32)
    assert loaded.metadata["vector_matrix_sha256"] == hashlib.sha256(
        persisted_matrix.tobytes(order="C")
    ).hexdigest()
    assert loaded.metadata["reference_quality_algorithm_id"] == (
        REFERENCE_QUALITY_ALGORITHM_ID
    )
    catalog_reference = next(
        item for item in loaded.references if item.artifact_id == "ref:A"
    )
    assert catalog_reference.metadata["citation_id"] == (
        "REFERENCE:111111111111:ref:A"
    )
    assert catalog_reference.metadata["rights"]["license_identifier"] == (
        "TEST-LOCAL-DEMO"
    )
    assert catalog_reference.metadata["catalogue"]["description"] == (
        "TEST/SYNTHETIC only"
    )


def test_vector_loader_rejects_a_normalized_but_tampered_vector(
    recognition_bundle, tmp_path: Path
):
    source = recognition_bundle["vector_path"]
    destination = tmp_path / "tampered-vectors.npz"
    with np.load(source, allow_pickle=False) as archive:
        arrays = {name: np.array(archive[name], copy=True) for name in archive.files}
    arrays["vectors"][0] = np.asarray(_basis(1.0, 0.0, offset=8), dtype=np.float32)
    with destination.open("wb") as stream:
        np.savez_compressed(stream, **arrays)

    with pytest.raises(ReferenceVectorIndexError, match="matrix hash mismatch"):
        load_reference_vector_index(
            destination,
            recognition_bundle["metadata_path"],
            expected_model=MODEL,
            expected_model_source=MODEL_SOURCE,
            expected_model_revision=MODEL_REVISION,
            expected_dimension=DIMENSION,
        )


def test_vector_builder_refuses_symbolic_link_output(
    recognition_bundle, tmp_path: Path
):
    target = tmp_path / "existing-vectors.npz"
    target.write_bytes(b"must-not-be-replaced")
    linked_output = tmp_path / "linked-reference-vectors.npz"
    linked_output.symlink_to(target)

    with pytest.raises(ReferenceVectorIndexError, match="symbolic-link"):
        __import__("asyncio").run(
            ReferenceVectorIndexBuilder(
                recognition_bundle["metadata_path"],
                linked_output,
                recognition_bundle["client"],
                batch_size=2,
            ).build()
        )

    assert target.read_bytes() == b"must-not-be-replaced"


def test_missing_calibration_fails_closed_but_related_retrieval_remains_available(
    recognition_bundle, tmp_path: Path
):
    service = ReferenceRecognitionService(
        enabled=True,
        metadata_index_path=recognition_bundle["metadata_path"],
        vector_index_path=recognition_bundle["vector_path"],
        calibration_path=tmp_path / "missing-calibration.json",
        embedding_client=recognition_bundle["client"],
        backend="numpy",
        minimum_reference_artifacts=2,
        minimum_views_per_artifact=1,
        minimum_counterfeit_records=1,
        minimum_open_set_queries=2,
    )
    query = b"independent-query-a"
    result = __import__("asyncio").run(
        service.recognize(
            [EmbeddingImage(query, "image/png", _digest(query))],
            view_ids=["query-front"],
            qualities=[1.0],
            angles=["FRONT"],
        )
    )

    assert service.readiness == "CALIBRATION_REQUIRED"
    assert result["decision_status"] == "CALIBRATION_REQUIRED"
    assert result["same_artifact"]["accepted"] is False
    assert result["same_artifact"]["artifact_id"] is None
    assert result["related"]["accepted"] is True
    assert result["authenticity_state"] == "NOT_ASSESSED"


def test_runtime_rejects_reference_index_below_configured_policy(
    recognition_bundle, tmp_path: Path
):
    service = ReferenceRecognitionService(
        enabled=True,
        metadata_index_path=recognition_bundle["metadata_path"],
        vector_index_path=recognition_bundle["vector_path"],
        calibration_path=tmp_path / "missing-calibration.json",
        embedding_client=recognition_bundle["client"],
        backend="numpy",
        minimum_reference_artifacts=50,
        minimum_views_per_artifact=5,
        minimum_counterfeit_records=10,
    )

    assert service.readiness == "LIBRARY_POLICY_MISMATCH"
    assert service.engine is None
    assert service.summary()["minimum_reference_artifacts"] == 50


def test_frozen_calibration_enables_known_artifact_candidate(recognition_bundle, tmp_path: Path):
    calibration_path = tmp_path / "reference-calibration.json"
    payload = _calibration_payload(recognition_bundle["loaded"].metadata)
    calibration_path.write_text(json.dumps(payload), encoding="utf-8")
    service = ReferenceRecognitionService(
        enabled=True,
        metadata_index_path=recognition_bundle["metadata_path"],
        vector_index_path=recognition_bundle["vector_path"],
        calibration_path=calibration_path,
        embedding_client=recognition_bundle["client"],
        backend="numpy",
        minimum_reference_artifacts=2,
        minimum_views_per_artifact=1,
        minimum_counterfeit_records=1,
        minimum_open_set_queries=2,
    )
    query = b"independent-query-a"
    result = __import__("asyncio").run(
        service.recognize(
            [EmbeddingImage(query, "image/png", _digest(query))],
            view_ids=["query-front"],
            qualities=[1.0],
            angles=["FRONT"],
        )
    )

    assert service.readiness == "READY"
    assert result["decision_status"] == "KNOWN_ARTIFACT_CANDIDATE"
    assert result["same_artifact"]["accepted"] is True
    assert result["same_artifact"]["artifact_id"] == "ref:A"
    assert result["authenticity_state"] == "NOT_ASSESSED"
    assert result["reference_library"]["calibration_record_sha256"] == payload[
        "calibration_record_sha256"
    ]


def test_calibration_rejects_tampering_and_missing_frozen_metrics(recognition_bundle, tmp_path: Path):
    payload = _calibration_payload(recognition_bundle["loaded"].metadata)
    tampered = dict(payload)
    tampered["metrics"] = dict(payload["metrics"], top1=0.5)
    tampered_path = tmp_path / "tampered-calibration.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(ReferenceCalibrationError, match="hash mismatch"):
        load_reference_calibration(
            tampered_path,
            vector_index_metadata=recognition_bundle["loaded"].metadata,
        )

    incomplete = _calibration_payload(recognition_bundle["loaded"].metadata)
    incomplete["metrics"] = {"top1": 1.0, "top5": 1.0, "far": 0.0, "frr": 0.0}
    incomplete.pop("calibration_record_sha256")
    incomplete["evaluation_result_sha256"] = calculate_evaluation_result_sha256(
        incomplete
    )
    incomplete["calibration_record_sha256"] = hashlib.sha256(
        canonical_json(incomplete).encode("utf-8")
    ).hexdigest()
    incomplete_path = tmp_path / "incomplete-calibration.json"
    incomplete_path.write_text(json.dumps(incomplete), encoding="utf-8")

    with pytest.raises(ReferenceCalibrationError, match="open_set_rejection_rate"):
        load_reference_calibration(
            incomplete_path,
            vector_index_metadata=recognition_bundle["loaded"].metadata,
        )


def test_calibration_cli_seals_measured_input_and_template_stays_unavailable(
    recognition_bundle, tmp_path: Path
):
    unsigned = _calibration_payload(recognition_bundle["loaded"].metadata)
    unsigned.pop("calibration_record_sha256")
    unsigned_path = tmp_path / "unsigned.json"
    sealed_path = tmp_path / "sealed.json"
    template_path = tmp_path / "template.json"
    unsigned_path.write_text(json.dumps(unsigned), encoding="utf-8")
    script = PROJECT_ROOT / "scripts" / "seal-reference-calibration.py"

    sealed_run = subprocess.run(
        [sys.executable, str(script), str(unsigned_path), "--output", str(sealed_path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert sealed_run.returncode == 0, sealed_run.stderr or sealed_run.stdout
    sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
    thresholds, loaded = load_reference_calibration(
        sealed_path,
        vector_index_metadata=recognition_bundle["loaded"].metadata,
    )
    assert thresholds.policy_id == "test-measured-calibration-v1"
    assert loaded["calibration_record_sha256"] == sealed["calibration_record_sha256"]

    template_run = subprocess.run(
        [sys.executable, str(script), "--template", str(template_path)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert template_run.returncode == 0, template_run.stderr or template_run.stdout
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert template["calibration_status"] == "TEMPLATE_NOT_CALIBRATED"
    assert "calibration_record_sha256" not in template
