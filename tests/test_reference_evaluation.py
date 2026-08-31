from __future__ import annotations

import asyncio
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from PIL import Image

from app.services.artifact_retrieval import ArtifactReference, EmbeddedView
from app.services.image_embedding import REFERENCE_EMBEDDING_INSTRUCTION, vector_sha256
from app.services.reference_evaluation import (
    EVALUATION_RESULT_HASH_ALGORITHM,
    MINIMUM_OPEN_SET_QUERIES,
    MINIMUM_REFERENCE_PHYSICAL_OBJECTS,
    REFERENCE_EVALUATION_MANIFEST_SCHEMA_VERSION,
    ReferenceEvaluationError,
    ReferenceRecognitionEvaluator,
    calculate_evaluation_result_sha256,
)
from app.services.reference_quality import REFERENCE_QUALITY_ALGORITHM_ID


DIMENSION = 16
MODEL = "qwen3_vl_embedding_2b"
MODEL_SOURCE = "Qwen/Qwen3-VL-Embedding-2B"
MODEL_REVISION = "a" * 40
LIBRARY_ID = "test:reference-library"
MANIFEST_SHA = "1" * 64
INDEX_PAYLOAD_SHA = "2" * 64
ARCHIVE_SHA = "3" * 64
INSTRUCTION_SHA = hashlib.sha256(
    REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8")
).hexdigest()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ANGLES = ("FRONT", "BACK", "BASE")


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _png_bytes(marker: int) -> bytes:
    grid = np.indices((256, 256)).sum(axis=0) % 2
    array = np.empty((256, 256, 3), dtype=np.uint8)
    array[grid == 0] = (24, 42, 60)
    array[grid == 1] = (218, 232, 206)
    array[:8, :8] = (
        30 + marker % 180,
        30 + (marker // 180) % 180,
        30 + (marker * 37) % 180,
    )
    image = Image.fromarray(array, mode="RGB")
    stream = io.BytesIO()
    image.save(stream, format="PNG", compress_level=1)
    return stream.getvalue()


class FakeEmbeddingClient:
    model = MODEL
    model_source = MODEL_SOURCE
    model_revision = MODEL_REVISION
    expected_dimension = DIMENSION
    immutable_identity_configured = True

    def __init__(self, vectors: dict[str, list[float]], *, available: bool = True) -> None:
        self.vectors = vectors
        self.available = available

    async def embed(self, images):
        if not self.available:
            return {
                "available": False,
                "status": "EMBEDDING_UNAVAILABLE",
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
                "instruction_sha256": INSTRUCTION_SHA,
            }
        vectors = [self.vectors[image.sha256] for image in images]
        return {
            "available": True,
            "status": "SUCCESS",
            "model": self.model,
            "model_source": self.model_source,
            "model_revision": self.model_revision,
            "model_identity_verified": True,
            "request_id": "test-local-embedding-run",
            "instruction_sha256": INSTRUCTION_SHA,
            "dimension": self.expected_dimension,
            "input_hashes": [image.sha256 for image in images],
            "output_hashes": [vector_sha256(vector) for vector in vectors],
            "vectors": vectors,
        }


class FakeReferenceLibraryIndex:
    rows: list[dict[str, str]] = []
    metadata_payload: dict[str, Any] = {}

    def __init__(self, index_path: Path | str) -> None:
        self.index_path = Path(index_path)

    def metadata(self):
        return dict(self.metadata_payload)

    def iter_images(self):
        yield from [dict(row) for row in self.rows]


def _reference(
    artifact_id: str,
    physical_object_id: str,
    vector: list[float],
    image_hashes: tuple[str, ...],
) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=artifact_id,
        views=tuple(
            EmbeddedView(
                view_id=f"{artifact_id}:{angle.lower()}",
                vector=vector,
                quality=1.0,
                angle=angle,
                input_sha256=image_hash,
            )
            for angle, image_hash in zip(
                EVALUATION_ANGLES, image_hashes, strict=True
            )
        ),
        metadata={
            "physical_object_id": physical_object_id,
            "display_name": f"TEST/SYNTHETIC {artifact_id}",
        },
    )


@pytest.fixture
def evaluation_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.services import reference_evaluation as module

    rng = np.random.default_rng(20260830)
    reference_matrix = rng.normal(
        size=(MINIMUM_REFERENCE_PHYSICAL_OBJECTS, DIMENSION)
    )
    reference_matrix /= np.linalg.norm(reference_matrix, axis=1, keepdims=True)
    reference_hashes = [
        tuple(
            _digest(f"controlled-reference-{position:03d}-{angle}".encode())
            for angle in EVALUATION_ANGLES
        )
        for position in range(MINIMUM_REFERENCE_PHYSICAL_OBJECTS)
    ]
    references = tuple(
        _reference(
            f"ref:{position:03d}",
            f"physical:{position:03d}",
            reference_matrix[position].tolist(),
            reference_hashes[position],
        )
        for position in range(MINIMUM_REFERENCE_PHYSICAL_OBJECTS)
    )
    media: dict[str, bytes] = {}
    vectors: dict[str, list[float]] = {}
    queries: list[dict[str, Any]] = []
    for position in range(MINIMUM_REFERENCE_PHYSICAL_OBJECTS):
        query_images = []
        for angle_position, angle in enumerate(EVALUATION_ANGLES):
            relative = f"captures/reference-{position:03d}-{angle.lower()}.png"
            content = _png_bytes(position * len(EVALUATION_ANGLES) + angle_position)
            media[relative] = content
            noise = rng.normal(size=DIMENSION)
            query_vector = reference_matrix[position] + noise * 0.005
            query_vector /= np.linalg.norm(query_vector)
            vectors[_digest(content)] = query_vector.tolist()
            query_images.append(
                {
                    "path": relative,
                    "sha256": _digest(content),
                    "mime": "image/png",
                    "angle": angle,
                }
            )
        queries.append(
            {
                "query_id": f"query:reference:{position:03d}",
                "capture_batch_id": f"capture:independent:reference:{position:03d}",
                "expected_artifact_id": f"ref:{position:03d}",
                "images": query_images,
            }
        )
    for position in range(MINIMUM_OPEN_SET_QUERIES):
        open_vector = rng.normal(size=DIMENSION)
        open_vector /= np.linalg.norm(open_vector)
        query_images = []
        for angle_position, angle in enumerate(EVALUATION_ANGLES):
            marker = (
                MINIMUM_REFERENCE_PHYSICAL_OBJECTS * len(EVALUATION_ANGLES)
                + position * len(EVALUATION_ANGLES)
                + angle_position
            )
            relative = f"captures/open-set-{position:03d}-{angle.lower()}.png"
            content = _png_bytes(marker)
            media[relative] = content
            noise = rng.normal(size=DIMENSION)
            query_vector = open_vector + noise * 0.005
            query_vector /= np.linalg.norm(query_vector)
            vectors[_digest(content)] = query_vector.tolist()
            query_images.append(
                {
                    "path": relative,
                    "sha256": _digest(content),
                    "mime": "image/png",
                    "angle": angle,
                }
            )
        queries.append(
            {
                "query_id": f"query:open:{position:03d}",
                "capture_batch_id": f"capture:independent:open:{position:03d}",
                "expected_artifact_id": None,
                "images": query_images,
            }
        )
    for relative, content in media.items():
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    vector_metadata = {
        "library_id": LIBRARY_ID,
        "manifest_sha256": MANIFEST_SHA,
        "index_payload_sha256": INDEX_PAYLOAD_SHA,
        "model": MODEL,
        "model_source": MODEL_SOURCE,
        "model_revision": MODEL_REVISION,
        "dimension": DIMENSION,
        "instruction_sha256": INSTRUCTION_SHA,
        "reference_quality_algorithm_id": REFERENCE_QUALITY_ALGORITHM_ID,
    }
    loaded = SimpleNamespace(
        metadata=vector_metadata,
        references=references,
        archive_sha256=ARCHIVE_SHA,
    )
    FakeReferenceLibraryIndex.metadata_payload = {
        "library_id": LIBRARY_ID,
        "manifest_sha256": MANIFEST_SHA,
    }
    FakeReferenceLibraryIndex.rows = [
        {
            "image_id": f"ref-{position:03d}-{angle.lower()}",
            "sha256": reference_hashes[position][angle_position],
        }
        for position in range(MINIMUM_REFERENCE_PHYSICAL_OBJECTS)
        for angle_position, angle in enumerate(EVALUATION_ANGLES)
    ]
    monkeypatch.setattr(module, "ReferenceLibraryIndex", FakeReferenceLibraryIndex)
    monkeypatch.setattr(module, "load_reference_vector_index", lambda *args, **kwargs: loaded)
    manifest = {
        "schema_version": REFERENCE_EVALUATION_MANIFEST_SCHEMA_VERSION,
        "evaluation_id": "test:independent-reshoots",
        "version": "test-v1",
        "library_id": LIBRARY_ID,
        "library_manifest_sha256": MANIFEST_SHA,
        "embedding_index_payload_sha256": INDEX_PAYLOAD_SHA,
        "embedding_index_archive_sha256": ARCHIVE_SHA,
        "protocol": {
            "protocol_id": "test-independent-reshoot-v1",
            "independent_reshoots": True,
            "exact_media_reuse_excluded": True,
            "capture_batch_attestation": (
                "TEST/SYNTHETIC images were captured independently from reference media."
            ),
        },
        "queries": queries,
    }
    manifest_path = tmp_path / "evaluation-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    client = FakeEmbeddingClient(vectors)
    evaluator = ReferenceRecognitionEvaluator(
        metadata_index_path=tmp_path / "reference.sqlite3",
        vector_index_path=tmp_path / "vectors.npz",
        embedding_client=client,
        target_far=0.02,
        top_k=5,
    )
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "media": media,
        "vectors": vectors,
        "client": client,
        "evaluator": evaluator,
        "loaded": loaded,
    }


def test_frozen_evaluation_suggests_thresholds_and_is_sealable(
    evaluation_environment, tmp_path: Path
):
    payload = asyncio.run(
        evaluation_environment["evaluator"].evaluate(
            evaluation_environment["manifest_path"]
        )
    )

    assert payload["calibration_status"] == "CALIBRATED"
    assert "calibration_record_sha256" not in payload
    assert payload["metrics"] == {
        "top1": 1.0,
        "top5": 1.0,
        "far": 0.0,
        "frr": 0.0,
        "open_set_rejection_rate": 1.0,
        "per_view_recall": {"BACK": 1.0, "BASE": 1.0, "FRONT": 1.0},
    }
    details = payload["evaluation_details"]
    assert details["backend"] == "numpy-exact-cosine"
    assert details["threshold_selection"]["target_far"] == 0.02
    assert details["threshold_selection"]["target_far_met"] is True
    assert details["queries"][0]["decision"] == "CORRECT_IDENTITY_ACCEPT"
    assert details["queries"][MINIMUM_REFERENCE_PHYSICAL_OBJECTS]["decision"] == (
        "OPEN_SET_REJECTED"
    )
    assert details["queries"][0]["raw_top_k"][0]["artifact_id"] == "ref:000"
    assert details["queries"][0]["raw_complementary_angle_count"] == 3
    assert details["threshold_selection"]["minimum_complementary_angle_count"] == 3
    assert payload["thresholds"]["same_artifact_min_complementary_angles"] == 3
    assert payload["evaluation_result_hash_algorithm"] == EVALUATION_RESULT_HASH_ALGORITHM
    assert payload["evaluation_result_sha256"] == (
        calculate_evaluation_result_sha256(payload)
    )
    assert len(payload["independent_capture_batch_sha256"]) == 64

    for section, mutate in (
        ("metrics", lambda value: value.update({"top1": 0.5})),
        (
            "thresholds",
            lambda value: value.update({"same_artifact_min_score": 0.99}),
        ),
        ("evaluation_details", lambda value: value.update({"boundary": "tampered"})),
    ):
        tampered = json.loads(json.dumps(payload))
        mutate(tampered[section])
        assert calculate_evaluation_result_sha256(tampered) != payload[
            "evaluation_result_sha256"
        ]

    unsigned_path = tmp_path / "unsigned-calibration.json"
    sealed_path = tmp_path / "sealed-calibration.json"
    unsigned_path.write_text(json.dumps(payload), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "seal-reference-calibration.py"),
            str(unsigned_path),
            "--output",
            str(sealed_path),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr or run.stdout
    sealed = json.loads(sealed_path.read_text(encoding="utf-8"))
    assert len(sealed["calibration_record_sha256"]) == 64

    tampered = json.loads(json.dumps(payload))
    tampered["thresholds"]["same_artifact_min_score"] = 0.99
    tampered_path = tmp_path / "tampered-unsigned-calibration.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    rejected = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "seal-reference-calibration.py"),
            str(tampered_path),
            "--output",
            str(tmp_path / "must-not-seal.json"),
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "does not bind the complete measured result" in rejected.stdout


def test_calibration_metrics_apply_the_runtime_complementary_angle_gate(
    evaluation_environment,
) -> None:
    payload = json.loads(json.dumps(evaluation_environment["manifest"]))
    for query in payload["queries"]:
        query["images"] = query["images"][:1]
    evaluation_environment["manifest_path"].write_text(
        json.dumps(payload), encoding="utf-8"
    )

    result = asyncio.run(
        evaluation_environment["evaluator"].evaluate(
            evaluation_environment["manifest_path"]
        )
    )

    first = result["evaluation_details"]["queries"][0]
    assert result["thresholds"]["same_artifact_min_complementary_angles"] == 3
    assert first["raw_complementary_angle_count"] == 1
    assert first["accepted_candidate_id"] is None
    assert first["decision"] == "IN_LIBRARY_REJECTED"
    assert result["metrics"]["frr"] == 1.0


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update({"unexpected": True}), "fields are invalid"),
        (
            lambda payload: payload["queries"][0]["images"][0].update(
                {"path": "../a-front.png"}
            ),
            "unsafe path",
        ),
        (
            lambda payload: payload["queries"][0]["images"][0].update(
                {"mime": "image/jpeg"}
            ),
            "MIME does not match",
        ),
        (
            lambda payload: payload.update({"embedding_index_archive_sha256": "4" * 64}),
            "archive_sha256 binding mismatch",
        ),
    ],
)
def test_strict_manifest_rejects_untrusted_input(
    evaluation_environment,
    mutation,
    message: str,
):
    payload = json.loads(json.dumps(evaluation_environment["manifest"]))
    mutation(payload)
    evaluation_environment["manifest_path"].write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(ReferenceEvaluationError, match=message):
        asyncio.run(
            evaluation_environment["evaluator"].evaluate(
                evaluation_environment["manifest_path"]
            )
        )


def test_original_reference_hash_reuse_and_duplicate_query_media_fail_closed(
    evaluation_environment,
):
    from app.services import reference_evaluation as module

    first_hash = evaluation_environment["manifest"]["queries"][0]["images"][0][
        "sha256"
    ]
    original_references = evaluation_environment["loaded"].references
    FakeReferenceLibraryIndex.rows = [
        {
            "image_id": f"ref-{position:03d}-{view.angle.lower()}",
            "sha256": (
                first_hash
                if position == 0 and view_position == 0
                else view.input_sha256
            ),
        }
        for position in range(MINIMUM_REFERENCE_PHYSICAL_OBJECTS)
        for view_position, view in enumerate(original_references[position].views)
    ]
    reused_reference = _reference(
        "ref:000",
        "physical:000",
        list(original_references[0].views[0].vector),
        (
            first_hash,
            *(
                str(view.input_sha256)
                for view in original_references[0].views[1:]
            ),
        ),
    )
    loaded = SimpleNamespace(
        metadata=evaluation_environment["loaded"].metadata,
        references=(reused_reference, *original_references[1:]),
        archive_sha256=ARCHIVE_SHA,
    )
    module.load_reference_vector_index = lambda *args, **kwargs: loaded
    with pytest.raises(ReferenceEvaluationError, match="reuses indexed reference media"):
        asyncio.run(
            evaluation_environment["evaluator"].evaluate(
                evaluation_environment["manifest_path"]
            )
        )

    module.load_reference_vector_index = lambda *args, **kwargs: evaluation_environment[
        "loaded"
    ]
    FakeReferenceLibraryIndex.rows = [
        {
            "image_id": f"ref-{position:03d}-{view.angle.lower()}",
            "sha256": view.input_sha256,
        }
        for position in range(MINIMUM_REFERENCE_PHYSICAL_OBJECTS)
        for view in original_references[position].views
    ]
    payload = json.loads(json.dumps(evaluation_environment["manifest"]))
    duplicate = payload["queries"][0]["images"][0]
    payload["queries"][MINIMUM_REFERENCE_PHYSICAL_OBJECTS]["images"][0] = {
        **duplicate,
        "path": "captures/a-front-copy.png",
    }
    copy_path = evaluation_environment["manifest_path"].parent / "captures/a-front-copy.png"
    copy_path.write_bytes(
        evaluation_environment["media"]["captures/reference-000-front.png"]
    )
    evaluation_environment["manifest_path"].write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(ReferenceEvaluationError, match="duplicate evaluation image SHA-256"):
        asyncio.run(
            evaluation_environment["evaluator"].evaluate(
                evaluation_environment["manifest_path"]
            )
        )


def test_requires_both_in_library_and_open_set_queries(evaluation_environment):
    payload = json.loads(json.dumps(evaluation_environment["manifest"]))
    for query in payload["queries"][MINIMUM_REFERENCE_PHYSICAL_OBJECTS:]:
        query["expected_artifact_id"] = "ref:000"
    evaluation_environment["manifest_path"].write_text(
        json.dumps(payload), encoding="utf-8"
    )

    with pytest.raises(ReferenceEvaluationError, match="in-library and one open-set"):
        asyncio.run(
            evaluation_environment["evaluator"].evaluate(
                evaluation_environment["manifest_path"]
            )
        )


def test_calibration_requires_all_50_objects_and_20_open_set_queries(
    evaluation_environment,
):
    payload = json.loads(json.dumps(evaluation_environment["manifest"]))
    del payload["queries"][MINIMUM_REFERENCE_PHYSICAL_OBJECTS - 1]
    evaluation_environment["manifest_path"].write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(ReferenceEvaluationError, match="independent reshoot for every"):
        asyncio.run(
            evaluation_environment["evaluator"].evaluate(
                evaluation_environment["manifest_path"]
            )
        )

    payload = json.loads(json.dumps(evaluation_environment["manifest"]))
    payload["queries"].pop()
    evaluation_environment["manifest_path"].write_text(
        json.dumps(payload), encoding="utf-8"
    )
    with pytest.raises(ReferenceEvaluationError, match="at least 20 independent open-set"):
        asyncio.run(
            evaluation_environment["evaluator"].evaluate(
                evaluation_environment["manifest_path"]
            )
        )


def test_calibration_safety_floors_cannot_be_lowered(evaluation_environment):
    with pytest.raises(ValueError, match="reference physical objects cannot be below"):
        ReferenceRecognitionEvaluator(
            metadata_index_path="unused.sqlite3",
            vector_index_path="unused.npz",
            embedding_client=evaluation_environment["client"],
            minimum_reference_physical_objects=49,
        )
    with pytest.raises(ValueError, match="open-set queries cannot be below"):
        ReferenceRecognitionEvaluator(
            metadata_index_path="unused.sqlite3",
            vector_index_path="unused.npz",
            embedding_client=evaluation_environment["client"],
            minimum_open_set_queries=19,
        )


def test_embedding_unavailability_stops_evaluation(evaluation_environment):
    evaluator = ReferenceRecognitionEvaluator(
        metadata_index_path="unused.sqlite3",
        vector_index_path="unused.npz",
        embedding_client=FakeEmbeddingClient(
            evaluation_environment["vectors"], available=False
        ),
    )
    with pytest.raises(ReferenceEvaluationError, match="verified embedding unavailable"):
        asyncio.run(evaluator.evaluate(evaluation_environment["manifest_path"]))
