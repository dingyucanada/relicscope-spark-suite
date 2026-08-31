from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import numpy as np
import pytest

from app.services.artifact_retrieval import (
    ArtifactReference,
    ArtifactRetrievalEngine,
    CallableLocalImageEmbeddingAdapter,
    CuVSCosineBackend,
    EmbeddedView,
    EmbeddingRunUnavailable,
    FaissCosineBackend,
    NegativeReferenceControl,
    NegativeReviewStatus,
    NumpyCosineBackend,
    ReferenceKind,
    RetrievalThresholds,
    encode_image_views,
    embedded_views_from_verified_run,
)


CALIBRATION_SHA256 = "c" * 64


def _provider_vector_sha256(vector) -> str:
    payload = json.dumps(
        [float(value) for value in vector], separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _view(
    view_id: str,
    vector,
    *,
    quality: float = 1.0,
    angle: str | None = None,
    input_sha256: str | None = None,
) -> EmbeddedView:
    return EmbeddedView(
        view_id,
        vector,
        quality=quality,
        angle=angle,
        input_sha256=input_sha256,
    )


def _policy(**overrides) -> RetrievalThresholds:
    values = {
        "policy_id": "test-calibrated-policy",
        "same_artifact_min_score": 0.88,
        "same_artifact_min_margin": 0.05,
        "same_artifact_min_coverage": 0.60,
        "same_artifact_min_quality": 0.60,
        "same_artifact_min_complementary_angles": 1,
        "related_min_score": 0.60,
        "related_min_coverage": 0.20,
        "related_min_quality": 0.30,
        "view_match_min_similarity": 0.80,
        "minimum_view_quality": 0.20,
        "counterfeit_alert_min_score": 0.82,
        "counterfeit_alert_min_coverage": 0.34,
        "counterfeit_alert_min_quality": 0.50,
        "view_score_weight": 0.80,
    }
    values.update(overrides)
    return RetrievalThresholds(**values)


def _catalog_references() -> list[ArtifactReference]:
    return [
        ArtifactReference(
            "A-001",
            [
                _view("a-front", [1.0, 0.0, 0.0], angle="front"),
                _view("a-side", [0.98, 0.20, 0.0], angle="LEFT_PROFILE"),
                _view("a-base", [0.96, 0.0, 0.28], angle="base"),
            ],
            metadata={"title": "catalog A"},
        ),
        ArtifactReference(
            "B-002",
            [
                _view("b-front", [0.0, 1.0, 0.0], angle="front"),
                _view("b-side", [0.15, 0.98, 0.0], angle="LEFT_PROFILE"),
            ],
        ),
        ArtifactReference(
            "C-003",
            [_view("c-front", [0.0, 0.0, 1.0], angle="front")],
        ),
    ]


def test_numpy_backend_returns_exact_cosine_in_descending_order():
    backend = NumpyCosineBackend()
    backend.build(np.asarray([[3.0, 0.0], [1.0, 1.0], [0.0, 4.0]]))

    result = backend.search(np.asarray([[1.0, 0.0]]), top_k=3)

    assert result.indices.tolist() == [[0, 1, 2]]
    assert result.scores[0].tolist() == pytest.approx([1.0, 2**-0.5, 0.0])


def test_optional_backend_adapters_honor_the_same_cosine_contract():
    class FakeFaissIndex:
        def __init__(self, dimension):
            self.dimension = dimension
            self.vectors = None
            self.ntotal = 0

        def add(self, vectors):
            self.vectors = np.asarray(vectors)
            self.ntotal = len(vectors)

        def search(self, queries, limit):
            similarities = np.asarray(queries) @ self.vectors.T
            indices = np.argsort(-similarities, axis=1)[:, :limit]
            return np.take_along_axis(similarities, indices, axis=1), indices

    class FakeFaiss:
        IndexFlatIP = FakeFaissIndex

    class FakeCuPy:
        @staticmethod
        def asarray(value):
            return np.asarray(value)

        @staticmethod
        def asnumpy(value):
            return np.asarray(value)

    class FakeBruteForce:
        @staticmethod
        def build(dataset, metric):
            assert metric == "cosine"
            return np.asarray(dataset)

        @staticmethod
        def search(index, queries, limit):
            similarities = np.asarray(queries) @ index.T
            indices = np.argsort(-similarities, axis=1)[:, :limit]
            cosine_distances = 1.0 - np.take_along_axis(similarities, indices, axis=1)
            return cosine_distances, indices

    vectors = np.asarray([[1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    query = np.asarray([[1.0, 0.0]])
    expected = NumpyCosineBackend()
    expected.build(vectors)
    expected_result = expected.search(query, 3)

    backends = [
        FaissCosineBackend(module=FakeFaiss),
        CuVSCosineBackend(
            brute_force_module=FakeBruteForce,
            cupy_module=FakeCuPy,
        ),
    ]
    for backend in backends:
        backend.build(vectors)
        result = backend.search(query, 3)
        assert result.indices.tolist() == expected_result.indices.tolist()
        assert np.allclose(result.scores, expected_result.scores)


def test_multi_view_query_recognizes_catalog_entry_and_exposes_score_breakdown():
    engine = ArtifactRetrievalEngine(
        _catalog_references(),
        thresholds=_policy(),
        backend="numpy",
        embedding_space_id="test-image-model-v1",
        reference_library_id="demo-50-v1",
        calibration_record_sha256=CALIBRATION_SHA256,
    )
    query = [
        _view("q-front", [0.999, 0.02, 0.0], quality=0.95, angle="front"),
        _view("q-side", [0.97, 0.24, 0.0], quality=0.90, angle="LEFT_PROFILE"),
        _view("q-base", [0.95, 0.01, 0.30], quality=0.85, angle="base"),
    ]

    result = engine.retrieve(query, top_k=2)

    assert result.backend == "numpy-exact-cosine"
    assert [hit.artifact_id for hit in result.catalog_hits] == ["A-001", "B-002"]
    assert result.same_artifact.accepted is True
    assert result.same_artifact.status == "KNOWN_ARTIFACT_CANDIDATE"
    assert result.same_artifact.artifact_id == "A-001"
    assert result.same_artifact.calibration_required is False
    assert result.same_artifact.gates["calibration_record"] is True
    assert result.same_artifact.runner_up_margin > 0.5
    assert result.open_set_rejected is False
    top = result.catalog_hits[0]
    assert top.score > 0.99
    assert top.coverage == 1.0
    assert top.component_weights == {"best_view": 0.8, "centroid": 0.2}
    assert [item.reference_view_id for item in top.matched_views] == [
        "a-front",
        "a-side",
        "a-base",
    ]
    assert all(item.passes_view_match for item in top.matched_views)

    payload = result.to_dict()
    assert payload["policy_id"] == "test-calibrated-policy"
    assert payload["reference_library_id"] == "demo-50-v1"
    assert payload["authenticity_state"] == "NOT_ASSESSED"
    assert payload["catalog_hits"][0]["matched_views"][0]["cosine_similarity"] > 0.99
    assert "not an authenticity" in payload["limitation"]
    json.dumps(payload)


def test_related_decision_remains_available_when_same_artifact_gate_rejects():
    engine = ArtifactRetrievalEngine(
        _catalog_references(),
        thresholds=_policy(
            same_artifact_min_score=0.93,
            related_min_score=0.68,
            view_match_min_similarity=0.65,
        ),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )

    result = engine.retrieve([_view("q", [0.78, 0.63, 0.0], quality=0.9)], top_k=3)

    assert result.open_set_rejected is True
    assert result.same_artifact.accepted is False
    assert result.same_artifact.status == "INSUFFICIENT_CAPTURE"
    assert result.related.accepted is True
    assert "SCORE_BELOW_SAME_ARTIFACT_THRESHOLD" in result.same_artifact.reason_codes
    assert result.related.accepted is True
    assert result.related.status == "related_candidates"
    assert result.related.qualifying_artifact_ids[0] == "A-001"
    assert result.catalog_hits[0].artifact_id == "A-001"


def test_open_set_margin_rejects_ambiguous_identity_but_keeps_ranked_hits():
    references = [
        ArtifactReference("A", [_view("a", [1.0, 0.0], angle="FRONT")]),
        ArtifactReference("B", [_view("b", [0.999, 0.045], angle="FRONT")]),
    ]
    engine = ArtifactRetrievalEngine(
        references,
        thresholds=_policy(
            same_artifact_min_score=0.90,
            same_artifact_min_margin=0.04,
            related_min_score=0.50,
        ),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )

    result = engine.retrieve([_view("q", [1.0, 0.0], angle="FRONT")], top_k=1)

    assert len(result.catalog_hits) == 1
    assert result.catalog_hits[0].artifact_id == "A"
    assert result.same_artifact.accepted is False
    assert result.same_artifact.status == "RELATED_REFERENCES_ONLY"
    assert result.same_artifact.runner_up_margin < 0.01
    assert result.same_artifact.gates["margin"] is False
    assert "RUNNER_UP_MARGIN_BELOW_THRESHOLD" in result.same_artifact.reason_codes


def test_coverage_and_quality_are_independent_auditable_gates():
    references = [
        ArtifactReference("A", [_view("a", [1.0, 0.0, 0.0], angle="FRONT")]),
        ArtifactReference("B", [_view("b", [0.0, 1.0, 0.0], angle="BACK")]),
    ]
    coverage_engine = ArtifactRetrievalEngine(
        references,
        thresholds=_policy(
            same_artifact_min_score=-0.50,
            same_artifact_min_coverage=0.80,
            same_artifact_min_quality=0.10,
            related_min_score=-0.80,
            related_min_coverage=0.10,
            related_min_quality=0.05,
            view_match_min_similarity=0.90,
        ),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )
    query = [
        _view("match", [1.0, 0.0, 0.0], angle="FRONT"),
        _view("miss-1", [0.0, 1.0, 0.0], angle="BACK"),
        _view("miss-2", [0.0, 0.0, 1.0], angle="BASE"),
    ]

    coverage_result = coverage_engine.retrieve(query)

    assert coverage_result.catalog_hits[0].coverage == pytest.approx(1 / 3)
    assert coverage_result.same_artifact.gates["coverage"] is False
    assert (
        "QUERY_VIEW_COVERAGE_BELOW_THRESHOLD"
        in coverage_result.same_artifact.reason_codes
    )

    quality_engine = ArtifactRetrievalEngine(
        references,
        thresholds=_policy(
            same_artifact_min_quality=0.80,
            related_min_quality=0.10,
        ),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )
    quality_result = quality_engine.retrieve(
        [
            _view(
                "low-quality",
                [1.0, 0.0, 0.0],
                quality=0.30,
                angle="FRONT",
            )
        ]
    )

    assert quality_result.catalog_hits[0].quality_score == pytest.approx(0.30)
    assert quality_result.same_artifact.status == "INSUFFICIENT_CAPTURE"
    assert quality_result.same_artifact.gates["quality"] is False
    assert "IMAGE_QUALITY_BELOW_THRESHOLD" in quality_result.same_artifact.reason_codes


def test_counterfeit_similarity_is_a_separate_signal_and_blocks_ambiguous_identity():
    references = [
        ArtifactReference("CATALOG-A", [_view("catalog", [1.0, 0.02], angle="FRONT")]),
        ArtifactReference("CATALOG-B", [_view("other", [0.0, 1.0], angle="FRONT")]),
        ArtifactReference(
            "KNOWN-FAKE-A",
            [_view("counterfeit", [1.0, 0.0], angle="FRONT")],
            kind=ReferenceKind.KNOWN_COUNTERFEIT,
            negative_control=NegativeReferenceControl(
                record_id="NEG-REVIEW-001",
                review_status=NegativeReviewStatus.VERIFIED,
                admissible_for_signal=True,
            ),
            metadata={"provenance": "expert-reviewed training fixture"},
        ),
    ]
    engine = ArtifactRetrievalEngine(
        references,
        thresholds=_policy(),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )

    result = engine.retrieve([_view("q", [1.0, 0.0], angle="FRONT")])

    assert result.counterfeit_signal.triggered is True
    assert result.counterfeit_signal.strength == "STRONG"
    assert result.counterfeit_signal.reference_id == "KNOWN-FAKE-A"
    assert result.counterfeit_signal.review_record_id == "NEG-REVIEW-001"
    assert result.counterfeit_signal.weighted_score == pytest.approx(
        result.counterfeit_signal.score
    )
    assert result.counterfeit_signal.competes_with_top_catalog is True
    assert result.same_artifact.accepted is False
    assert result.same_artifact.status == "RELATED_REFERENCES_ONLY"
    assert result.same_artifact.gates["counterfeit_conflict_absent"] is False
    assert "KNOWN_COUNTERFEIT_REFERENCE_CONFLICT" in result.same_artifact.reason_codes
    assert (
        result.counterfeit_hits[0].metadata["provenance"].startswith("expert-reviewed")
    )
    assert "does not by itself establish" in result.counterfeit_signal.interpretation
    assert "not an authenticity" in result.limitation


def test_local_encoder_adapter_supports_query_images_without_online_binding():
    vectors_by_image = {
        "front-photo": [1.0, 0.0],
        "side-photo": [0.98, 0.20],
    }
    adapter = CallableLocalImageEmbeddingAdapter(
        name="local-openclip-adapter",
        model_id="openclip-local-revision-123",
        encoder=lambda images: [vectors_by_image[image] for image in images],
    )
    reference_views = encode_image_views(
        ["front-photo", "side-photo"],
        encoder=adapter,
        view_ids=["front", "side"],
        qualities=[0.9, 0.8],
        angles=["front", "left_profile"],
    )
    engine = ArtifactRetrievalEngine(
        [
            ArtifactReference("A", reference_views),
            ArtifactReference("B", [_view("b", [0.0, 1.0])]),
        ],
        thresholds=_policy(),
        backend="numpy",
        embedding_space_id=adapter.model_id,
        calibration_record_sha256=CALIBRATION_SHA256,
    )

    result = engine.retrieve_images(
        ["front-photo", "side-photo"],
        encoder=adapter,
        qualities=[0.95, 0.95],
        angles=["front", "left_profile"],
    )

    assert result.same_artifact.artifact_id == "A"
    assert result.embedding_space_id == adapter.model_id
    assert reference_views[0].metadata == {
        "encoder": "local-openclip-adapter",
        "model_id": adapter.model_id,
    }


def test_verified_async_embedding_result_bridge_rechecks_hash_and_identity_bindings():
    vector = [1.0, 0.0]
    run = {
        "available": True,
        "status": "SUCCESS",
        "model_identity_verified": True,
        "model": "qwen-embedding-test",
        "model_source": "local/qwen-embedding",
        "model_revision": "d" * 40,
        "request_id": "embedding-request-1",
        "instruction_sha256": "e" * 64,
        "dimension": 2,
        "input_hashes": ["f" * 64],
        "output_hashes": [_provider_vector_sha256(vector)],
        "vectors": [vector],
    }

    views = embedded_views_from_verified_run(
        run,
        view_ids=["query-front"],
        qualities=[0.9],
        angles=["FRONT"],
    )

    assert views[0].input_sha256 == "f" * 64
    assert views[0].metadata["output_sha256"] == _provider_vector_sha256(vector)
    assert views[0].metadata["model_revision"] == "d" * 40

    invalid = dict(run, output_hashes=["0" * 64])
    with pytest.raises(EmbeddingRunUnavailable, match="output SHA-256"):
        embedded_views_from_verified_run(invalid, view_ids=["query-front"])


def test_networked_encoder_and_malformed_vectors_fail_closed():
    class NetworkedEncoder:
        name = "remote"
        model_id = "remote-model"
        networked = True

        def encode(self, images):
            return [[1.0, 0.0] for _ in images]

    with pytest.raises(ValueError, match="networked"):
        encode_image_views(["photo"], encoder=NetworkedEncoder())

    with pytest.raises(ValueError, match="zero-length"):
        ArtifactRetrievalEngine(
            [ArtifactReference("bad", [_view("zero", [0.0, 0.0])])],
            backend="numpy",
        )

    engine = ArtifactRetrievalEngine(
        [ArtifactReference("ok", [_view("ref", [1.0, 0.0])])], backend="numpy"
    )
    with pytest.raises(ValueError, match="dimensions"):
        engine.retrieve([_view("wrong", [1.0, 0.0, 0.0])])
    with pytest.raises(ValueError, match="non-finite"):
        engine.retrieve([_view("nan", [float("nan"), 1.0])])

    with pytest.raises(ValueError, match="not an identity embedding"):
        ArtifactRetrievalEngine(
            [ArtifactReference("ok", [_view("ref", [1.0, 0.0])])],
            backend="numpy",
            embedding_space_id="relicscope-visual-fingerprint-v1",
        )
    with pytest.raises(ValueError, match="quality metadata"):
        ArtifactRetrievalEngine(
            [ArtifactReference("bad-8d", [_view("ref", [1.0] * 8)])],
            backend="numpy",
        )


def test_duplicate_angle_like_queries_cannot_fake_distinct_reference_coverage():
    reference = ArtifactReference(
        "A",
        [
            _view("front", [1.0, 0.0, 0.0], angle="FRONT"),
            _view("side", [0.99, 0.10, 0.0], angle="LEFT_PROFILE"),
            _view("base", [0.99, 0.0, 0.10], angle="BASE"),
        ],
    )
    engine = ArtifactRetrievalEngine(
        [reference],
        thresholds=_policy(same_artifact_min_coverage=0.60),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )

    result = engine.retrieve(
        [
            _view("front-1", [1.0, 0.0, 0.0], angle="FRONT"),
            _view("front-2", [1.0, 0.0, 0.0], angle="front"),
            _view("front-3", [1.0, 0.0, 0.0], angle="FRONT"),
        ]
    )

    top = result.catalog_hits[0]
    assert top.query_view_coverage == 1.0
    assert top.distinct_reference_coverage == pytest.approx(1 / 3)
    assert top.coverage == pytest.approx(1 / 3)
    assert top.similarity_coverage == 1.0
    assert top.complementary_angles == ("FRONT",)
    assert result.same_artifact.accepted is False
    assert result.same_artifact.status == "INSUFFICIENT_CAPTURE"


def test_unspecified_views_never_fake_identity_coverage_but_remain_related():
    reference = ArtifactReference(
        "A",
        [
            _view("front", [1.0, 0.0, 0.0], angle="FRONT"),
            _view("left", [0.0, 1.0, 0.0], angle="LEFT_PROFILE"),
            _view("base", [0.0, 0.0, 1.0], angle="BASE"),
        ],
    )
    result = ArtifactRetrievalEngine(
        [reference],
        thresholds=_policy(),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    ).retrieve(
        [
            _view("unknown-1", [1.0, 0.0, 0.0], angle=None),
            _view("unknown-2", [0.0, 1.0, 0.0], angle="UNSPECIFIED"),
            _view("detail", [0.0, 0.0, 1.0], angle="DETAIL"),
        ]
    )

    assert result.catalog_hits[0].similarity_coverage == 1.0
    assert result.catalog_hits[0].coverage == 0.0
    assert result.catalog_hits[0].complementary_angles == ()
    assert result.same_artifact.accepted is False
    assert "INSUFFICIENT_COMPLEMENTARY_DECLARED_ANGLES" in (
        result.same_artifact.reason_codes
    )
    assert result.related.accepted is True


def test_formal_identity_defaults_to_three_complementary_declared_angles():
    thresholds_payload = asdict(RetrievalThresholds())
    assert thresholds_payload["same_artifact_min_complementary_angles"] == 3

    engine = ArtifactRetrievalEngine(
        _catalog_references(),
        thresholds=RetrievalThresholds(),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )
    two_views = engine.retrieve(
        [
            _view("front", [1.0, 0.0, 0.0], angle="FRONT"),
            _view("side", [0.98, 0.20, 0.0], angle="LEFT"),
        ]
    )

    assert two_views.catalog_hits[0].coverage >= 0.60
    assert two_views.same_artifact.gates["coverage"] is True
    assert two_views.same_artifact.gates["complementary_angles"] is False
    assert two_views.same_artifact.accepted is False

    three_views = engine.retrieve(
        [
            _view("front", [1.0, 0.0, 0.0], angle="FRONT"),
            _view("side", [0.98, 0.20, 0.0], angle="LEFT"),
            _view("base", [0.96, 0.0, 0.28], angle="BASE"),
        ]
    )
    assert three_views.catalog_hits[0].complementary_angles == (
        "BASE",
        "FRONT",
        "LEFT_PROFILE",
    )
    assert three_views.same_artifact.accepted is True


def test_one_photo_does_not_claim_full_multi_view_reference_coverage():
    reference = ArtifactReference(
        "A",
        [
            _view("front", [1.0, 0.0, 0.0], angle="FRONT"),
            _view("back", [0.95, 0.05, 0.0], angle="BACK"),
            _view("left", [0.90, 0.10, 0.0], angle="LEFT_PROFILE"),
            _view("right", [0.85, 0.15, 0.0], angle="RIGHT_PROFILE"),
            _view("base", [0.80, 0.20, 0.0], angle="BASE"),
        ],
    )
    engine = ArtifactRetrievalEngine(
        [reference],
        thresholds=_policy(same_artifact_min_coverage=0.60),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    )

    result = engine.retrieve([_view("query-front", [1.0, 0.0, 0.0], angle="FRONT")])

    assert result.catalog_hits[0].query_view_coverage == 1.0
    assert result.catalog_hits[0].distinct_reference_coverage == pytest.approx(0.2)
    assert result.catalog_hits[0].coverage == pytest.approx(0.2)
    assert result.same_artifact.accepted is False


def test_threshold_profile_rejects_incoherent_identity_policy():
    with pytest.raises(ValueError, match="score threshold"):
        RetrievalThresholds(
            same_artifact_min_score=0.50,
            related_min_score=0.70,
        )


def test_missing_calibration_cannot_accept_an_identity_candidate():
    engine = ArtifactRetrievalEngine(
        [
            ArtifactReference("A", [_view("a", [1.0, 0.0])]),
            ArtifactReference("B", [_view("b", [0.0, 1.0])]),
        ],
        thresholds=_policy(),
        backend="numpy",
    )

    result = engine.retrieve([_view("q", [1.0, 0.0])])

    assert result.same_artifact.accepted is False
    assert result.same_artifact.status == "CALIBRATION_REQUIRED"
    assert result.same_artifact.calibration_required is True
    assert result.calibration_required is True
    assert result.same_artifact.gates["calibration_record"] is False
    assert "CALIBRATION_RECORD_REQUIRED" in result.same_artifact.reason_codes


def test_provenance_hashes_and_exact_catalog_media_replay_are_explicit():
    media_hash = "a" * 64
    references = [
        ArtifactReference(
            "A",
            [_view("front", [1.0, 0.0], input_sha256=media_hash)],
        ),
        ArtifactReference("B", [_view("front", [0.0, 1.0])]),
    ]
    engine = ArtifactRetrievalEngine(
        references,
        thresholds=_policy(),
        backend="numpy",
        reference_library_id="catalog-v7",
        catalog_manifest_sha256="b" * 64,
        calibration_record_sha256="c" * 64,
        embedding_space_id="ceramic-openclip-r4",
        embedding_model_source="local/open_clip",
        embedding_model_revision="immutable-revision-456",
    )

    result = engine.retrieve([_view("uploaded", [1.0, 0.0], input_sha256=media_hash)])

    assert len(result.index_sha256) == 64
    assert result.catalog_manifest_sha256 == "b" * 64
    assert result.calibration_record_sha256 == "c" * 64
    assert result.embedding_model_source == "local/open_clip"
    assert result.embedding_model_revision == "immutable-revision-456"
    assert len(result.query_views[0].embedding_sha256) == 64
    assert result.query_views[0].input_sha256 == media_hash
    assert result.exact_media_hash_matches[0].artifact_id == "A"
    assert result.same_artifact.accepted is False
    assert result.same_artifact.status == "EXACT_MEDIA_REPLAY"
    assert "EXACT_MEDIA_REPLAY" in result.same_artifact.reason_codes
    assert result.same_artifact.gates["exact_media_replay_absent"] is False
    assert result.same_artifact.audit_flags == ("EXACT_MEDIA_REPLAY",)
    assert result.related.accepted is True

    reordered = ArtifactRetrievalEngine(
        list(reversed(references)),
        thresholds=_policy(),
        backend="numpy",
        reference_library_id="catalog-v7",
        embedding_space_id="ceramic-openclip-r4",
    )
    assert reordered._index_sha256 == result.index_sha256


def test_replay_of_any_library_media_blocks_identity_without_hiding_related_hits():
    replay_hash = "d" * 64
    references = [
        ArtifactReference(
            "CATALOG-A",
            [_view("catalog-front", [1.0, 0.0], angle="FRONT")],
        ),
        ArtifactReference(
            "NEGATIVE-A",
            [
                _view(
                    "negative-original",
                    [0.0, 1.0],
                    angle="FRONT",
                    input_sha256=replay_hash,
                )
            ],
            kind=ReferenceKind.KNOWN_COUNTERFEIT,
            negative_control=NegativeReferenceControl(
                record_id="NEG-DISPUTED-REPLAY",
                review_status=NegativeReviewStatus.DISPUTED,
                admissible_for_signal=False,
            ),
        ),
    ]
    result = ArtifactRetrievalEngine(
        references,
        thresholds=_policy(),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    ).retrieve(
        [
            _view(
                "uploaded",
                [1.0, 0.0],
                angle="FRONT",
                input_sha256=replay_hash,
            )
        ]
    )

    assert result.exact_media_hash_matches[0].artifact_id == "NEGATIVE-A"
    assert result.same_artifact.status == "EXACT_MEDIA_REPLAY"
    assert result.same_artifact.artifact_id is None
    assert result.related.qualifying_artifact_ids == ("CATALOG-A",)


def test_negative_reference_requires_control_and_respects_admissibility_weight():
    with pytest.raises(ValueError, match="negative_control"):
        ArtifactReference(
            "unreviewed",
            [_view("negative", [1.0, 0.0])],
            kind=ReferenceKind.KNOWN_COUNTERFEIT,
        )

    references = [
        ArtifactReference("catalog", [_view("catalog", [0.0, 1.0])]),
        ArtifactReference(
            "provisional-negative",
            [_view("negative", [1.0, 0.0])],
            kind=ReferenceKind.KNOWN_COUNTERFEIT,
            negative_control=NegativeReferenceControl(
                record_id="NEG-PROVISIONAL",
                review_status=NegativeReviewStatus.PROVISIONAL,
                admissible_for_signal=True,
                signal_weight=0.50,
            ),
        ),
        ArtifactReference(
            "excluded-negative",
            [_view("negative", [1.0, 0.0])],
            kind=ReferenceKind.KNOWN_COUNTERFEIT,
            negative_control=NegativeReferenceControl(
                record_id="NEG-DISPUTED",
                review_status=NegativeReviewStatus.DISPUTED,
                admissible_for_signal=False,
            ),
        ),
    ]
    result = ArtifactRetrievalEngine(
        references, thresholds=_policy(), backend="numpy"
    ).retrieve([_view("q", [1.0, 0.0])])

    assert result.counterfeit_signal.triggered is False
    assert result.counterfeit_signal.score == pytest.approx(1.0)
    assert result.counterfeit_signal.weighted_score == pytest.approx(0.5)
    assert result.counterfeit_signal.gates["score"] is False
    assert result.counterfeit_signal.excluded_reference_count == 1


def test_provisional_negative_hit_is_weak_and_cannot_block_identity_by_itself():
    references = [
        ArtifactReference("catalog", [_view("catalog", [1.0, 0.0], angle="FRONT")]),
        ArtifactReference(
            "provisional-negative",
            [_view("negative", [1.0, 0.0], angle="FRONT")],
            kind=ReferenceKind.KNOWN_COUNTERFEIT,
            negative_control=NegativeReferenceControl(
                record_id="NEG-PROVISIONAL-STRONG-SIMILARITY",
                review_status=NegativeReviewStatus.PROVISIONAL,
                admissible_for_signal=True,
                signal_weight=1.0,
            ),
        ),
    ]
    result = ArtifactRetrievalEngine(
        references,
        thresholds=_policy(),
        backend="numpy",
        calibration_record_sha256=CALIBRATION_SHA256,
    ).retrieve([_view("q", [1.0, 0.0], angle="FRONT")])

    assert result.counterfeit_signal.triggered is True
    assert result.counterfeit_signal.strength == "WEAK"
    assert result.counterfeit_signal.competes_with_top_catalog is False
    assert result.same_artifact.status == "KNOWN_ARTIFACT_CANDIDATE"
