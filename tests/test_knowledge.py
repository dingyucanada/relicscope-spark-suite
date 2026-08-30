from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import pytest

from app.services.embedding import (
    EmbeddingUnavailable,
    OpenAICompatibleEmbeddingProvider,
)
from app.services.knowledge import (
    DEMO_DATA_LEVEL,
    KnowledgeBase,
    KnowledgePolicyError,
    ManifestValidationError,
    load_manifest,
    seal_manifest,
    sha256_text,
    validate_manifest,
)


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "knowledge_manifest.json"
FIXED_TIME = "2026-08-28T08:00:00Z"
FIXED_DATE = date(2026, 8, 28)


def raw_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_is_sealed_complete_and_demo_isolated() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    assert manifest.version.endswith("@" + manifest.content_set_sha256[:12])
    assert len(manifest.manifest_sha256) == 64
    assert len(manifest.entries) == 6
    assert {entry.knowledge_space for entry in manifest.entries} == {"demo"}
    assert {entry.data_level for entry in manifest.entries} == {DEMO_DATA_LEVEL}
    for entry in manifest.entries:
        assert entry.content_sha256 == sha256_text(entry.text)
        assert entry.title and entry.publisher and entry.source_type
        assert entry.published_at and entry.document_version and entry.ingested_at
        assert entry.license["identifier"] == "RelicScope-Demo-Only-1.0"
        assert entry.scope["limitations"]
        assert entry.location["locator"]
        assert entry.review_status["status"] == "REVIEWED_DEMO_ONLY"
        assert entry.chunks and entry.chunks[0].content_sha256 == entry.content_sha256


@pytest.mark.parametrize(
    "mutator, expected_issue",
    [
        (lambda value: value["entries"][0].pop("publisher"), "publisher"),
        (lambda value: value["entries"][0].update(text="tampered"), "content_sha256"),
        (
            lambda value: value["entries"][0].update(data_level="UNLABELLED"),
            "data_level",
        ),
        (
            lambda value: value["entries"][0]["chunks"][0].update(position=3),
            "positions",
        ),
    ],
)
def test_strict_manifest_validation_rejects_missing_tampered_or_unlabelled_data(
    mutator, expected_issue: str
) -> None:
    payload = raw_manifest()
    mutator(payload)

    with pytest.raises(ManifestValidationError) as error:
        validate_manifest(payload)

    assert expected_issue in str(error.value)


def test_sealing_a_revision_creates_a_new_content_addressed_version() -> None:
    original = raw_manifest()
    revised = copy.deepcopy(original)
    revised.pop("content_set_sha256")
    revised.pop("manifest_sha256")
    revised["entries"][0]["text"] += " 新版本演示补充。"
    revised["entries"][0]["chunks"][0]["text"] = revised["entries"][0]["text"]
    new_hash = sha256_text(revised["entries"][0]["text"])
    revised["entries"][0]["content_sha256"] = new_hash
    revised["entries"][0]["chunks"][0]["content_sha256"] = new_hash

    sealed = seal_manifest(revised)

    assert sealed["version"] != original["version"]
    assert sealed["manifest_sha256"] != original["manifest_sha256"]
    assert validate_manifest(original)["version"] == original["version"]


def test_same_query_and_version_return_a_reproducible_candidate_set() -> None:
    first_base = KnowledgeBase.from_path(MANIFEST_PATH)
    second_base = KnowledgeBase.from_path(MANIFEST_PATH)
    query = {
        "text": "Raman 低信噪 质量门控",
        "attributes": {"artifact_type": "陶瓷", "modality": "Raman"},
        "retrieved_at": FIXED_TIME,
        "policy_date": FIXED_DATE,
    }

    first = first_base.search(**query)
    second = second_base.search(**query)

    assert first["query_hash"] == second["query_hash"]
    assert first["candidate_set_hash"] == second["candidate_set_hash"]
    assert first["snapshot_hash"] == second["snapshot_hash"]
    assert first["results"] == second["results"]
    assert first["results"][0]["source_id"] == "demo:ceramic:raman-quality:v1"
    assert first["results"][0]["citation"]["location"]["locator"].endswith("段落-1")


def test_hybrid_retrieval_explains_text_structure_and_visual_scores() -> None:
    knowledge = KnowledgeBase.from_path(MANIFEST_PATH)

    snapshot = knowledge.search(
        text="高光谱材料差异",
        attributes={"artifact_type": "陶瓷", "material": "釉", "modality": "HSI"},
        visual_feature_vector=[0.61, 0.79, 0.58, 0.24, 0.44, 0.33, 0.76, 0.84],
        retrieved_at=FIXED_TIME,
        policy_date=FIXED_DATE,
    )

    result = snapshot["results"][0]
    assert result["source_id"] == "demo:ceramic:hsi-observation:v1"
    assert set(result["matched_on"]) == {"text", "structured", "visual"}
    assert result["component_scores"]["visual"] == 1.0
    assert result["data_level"] == DEMO_DATA_LEVEL
    assert "不代表" in result["match_explanation"]
    assert snapshot["data_boundary"] == "LOCAL_ONLY"


class NetworkSpyProvider:
    networked = True
    algorithm = "test-network-provider-v1"
    model = "test-network-model"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        raise AssertionError("offline policy should prevent this call")


class FailingLocalProvider:
    networked = False
    algorithm = "test-local-provider-v1"
    model = "test-local-model"

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        raise EmbeddingUnavailable("fixture outage")


def test_offline_mode_never_calls_a_networked_provider_and_reports_degraded() -> None:
    provider = NetworkSpyProvider()
    knowledge = KnowledgeBase.from_path(
        MANIFEST_PATH, embedding_provider=provider, offline=True
    )

    snapshot = knowledge.search(
        text="引用哈希",
        retrieved_at=FIXED_TIME,
        policy_date=FIXED_DATE,
    )

    assert provider.calls == 0
    assert snapshot["degraded"] is True
    assert snapshot["degraded_reasons"] == ["OFFLINE_POLICY_BLOCKED_NETWORK_PROVIDER"]
    assert snapshot["algorithm"]["embedding_model"] == "deterministic-local-no-network"


def test_provider_outage_fails_closed_to_deterministic_local_index() -> None:
    provider = FailingLocalProvider()
    knowledge = KnowledgeBase.from_path(
        MANIFEST_PATH, embedding_provider=provider, offline=False
    )

    snapshot = knowledge.search(
        text="证据链 引用",
        retrieved_at=FIXED_TIME,
        policy_date=FIXED_DATE,
    )

    assert provider.calls == 1
    assert snapshot["degraded"] is True
    assert snapshot["degraded_reasons"][0].startswith("EMBEDDING_PROVIDER_UNAVAILABLE")
    assert snapshot["results"]


def test_space_access_license_scope_and_minimum_score_are_hard_filters() -> None:
    knowledge = KnowledgeBase.from_path(MANIFEST_PATH)

    with pytest.raises(KnowledgePolicyError, match="cross-knowledge-space"):
        knowledge.search(text="Raman", knowledge_spaces=["demo", "verified"])

    cases = [
        {"access_scope": "restricted"},
        {"license_allowlist": ["Some-Other-License"]},
        {"attributes": {"material": "青铜"}},
        {"minimum_score": 1.0},
    ]
    for overrides in cases:
        snapshot = knowledge.search(
            text="Raman",
            retrieved_at=FIXED_TIME,
            policy_date=FIXED_DATE,
            **overrides,
        )
        assert snapshot["status"] == "LOCAL_KNOWLEDGE_INSUFFICIENT"
        assert snapshot["results"] == []
        assert snapshot["abstention"]["code"] == "LOCAL_KNOWLEDGE_INSUFFICIENT"
        assert all(set(item) == {"source_id", "reason"} for item in snapshot["exclusions"])


def test_actual_retrieval_citations_pass_and_model_invented_citations_fail() -> None:
    knowledge = KnowledgeBase.from_path(MANIFEST_PATH)
    snapshot = knowledge.search(
        text="XRF 电离风险预算",
        attributes={"modality": "XRF"},
        retrieved_at=FIXED_TIME,
        policy_date=FIXED_DATE,
    )
    real = snapshot["results"][0]["citation"]
    invented = copy.deepcopy(real)
    invented["source_id"] = "model:invented:source:v1"
    altered_location = copy.deepcopy(real)
    altered_location["location"]["locator"] = "不存在的位置"

    validation = knowledge.validate_citations(
        [real, invented, altered_location], snapshot
    )

    assert validation["all_valid"] is False
    assert validation["accepted"] == [real]
    assert len(validation["rejected"]) == 2
    assert {item["reason"] for item in validation["rejected"]} == {
        "CITATION_NOT_IN_RETRIEVAL_WHITELIST"
    }


def test_query_snapshot_tampering_is_rejected_before_citation_validation() -> None:
    knowledge = KnowledgeBase.from_path(MANIFEST_PATH)
    snapshot = knowledge.search(
        text="UV 荧光",
        retrieved_at=FIXED_TIME,
        policy_date=FIXED_DATE,
    )
    snapshot["results"][0]["snippet"] = "tampered"

    with pytest.raises(KnowledgePolicyError, match="snapshot hash"):
        knowledge.validate_citations(snapshot["citation_whitelist"], snapshot)


def test_audit_payload_contains_version_algorithm_hashes_and_actual_references() -> None:
    knowledge = KnowledgeBase.from_path(MANIFEST_PATH)
    snapshot = knowledge.search(
        text="证据图 引用 哈希",
        retrieved_at=FIXED_TIME,
        policy_date=FIXED_DATE,
    )

    payload = knowledge.audit_payload(snapshot)

    assert payload["query_hash"] == snapshot["query_hash"]
    assert payload["snapshot_hash"] == snapshot["snapshot_hash"]
    assert payload["knowledge_base_version"] == knowledge.version
    assert payload["immutable_version_hash"] == knowledge.immutable_version_hash
    assert payload["retrieval_config_version"]
    assert payload["algorithm"]["embedding_algorithm"]
    assert payload["citations"] == snapshot["citation_whitelist"]
    assert payload["data_level"] == DEMO_DATA_LEVEL


def test_openai_compatible_provider_requires_explicit_local_network_opt_in() -> None:
    with pytest.raises(ValueError, match="public embedding endpoints"):
        OpenAICompatibleEmbeddingProvider(
            base_url="https://example.com/v1", model="example-model"
        )

    provider = OpenAICompatibleEmbeddingProvider(
        base_url="http://spark-a:8000/v1", model="local-model"
    )
    with pytest.raises(EmbeddingUnavailable, match="disabled by policy"):
        provider.embed(["must stay local"])
