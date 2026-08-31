from __future__ import annotations

import json

from app.services.reference_explanation import (
    REFERENCE_EXPLANATION_SCHEMA_VERSION,
    build_reference_explanation,
    explain_reference_result,
)


def _query_views(*angles: str, quality: float = 0.92):
    return [
        {
            "view_id": f"query:{position}",
            "angle": angle,
            "quality": quality,
            "input_sha256": f"{position + 1:064x}",
        }
        for position, angle in enumerate(angles)
    ]


def _catalog_hit(
    artifact_id: str = "REF-001",
    *,
    score: float = 0.93,
    coverage: float = 0.8,
    quality: float = 0.91,
):
    return {
        "artifact_id": artifact_id,
        "score": score,
        "coverage": coverage,
        "quality_score": quality,
        "matched_views": [
            {
                "query_view_id": "query:0",
                "reference_view_id": f"{artifact_id}:front",
                "query_angle": "FRONT",
                "reference_angle": "FRONT",
                "cosine_similarity": score,
            },
            {
                "query_view_id": "query:1",
                "reference_view_id": f"{artifact_id}:back",
                "query_angle": "BACK",
                "reference_angle": "BACK",
                "cosine_similarity": score - 0.02,
            },
        ],
        "metadata": {
            "citation_id": f"REFERENCE:{artifact_id}",
            "display_name": "受控测试参考器",
            "catalogue": {
                "culture_or_period": "目录记录：某时期",
                "maker_or_kiln": "目录记录：某窑口",
                "material": "目录记录：瓷",
                "technique": "目录记录：釉下彩",
                "dimensions": "目录记录：高 20 cm",
                "description": "目录提供的描述文字",
            },
            "source_citation": {
                "source_type": "MUSEUM_COLLECTION",
                "institution": "测试馆藏机构",
                "collection_name": "测试馆藏",
                "accession_number": "ACC-001",
                "record_locator": "catalogue/ACC-001",
                "retrieved_at": "2026-08-01T00:00:00Z",
            },
            "expert_review": {
                "review_id": "REVIEW-001",
                "decision": "AUTHENTIC",
                "reviewer_credential": "测试审签资质",
                "reviewer_institution": "测试审核机构",
                "reviewed_at": "2026-08-02T00:00:00Z",
            },
            "rights": {
                "rights_holder": "测试权利人",
                "license_identifier": "LOCAL-DEMO-001",
                "attribution_required": True,
                "attribution_text": "测试馆藏机构 / ACC-001",
            },
        },
    }


def _same(status: str, *, accepted: bool = False):
    return {
        "accepted": accepted,
        "status": status,
        "artifact_id": "REF-001" if accepted else None,
        "runner_up_margin": 0.12,
        "gates": {
            "score": True,
            "margin": True,
            "coverage": True,
            "quality": True,
            "calibration_record": True,
            "counterfeit_conflict_absent": True,
        },
        "reason_codes": [
            "PASSED_ALL_IDENTITY_GATES"
            if accepted
            else "RUNNER_UP_MARGIN_BELOW_THRESHOLD"
        ],
    }


def test_known_item_explanation_keeps_metrics_catalogue_and_identity_separate():
    explanation = build_reference_explanation(
        query_views=_query_views(
            "FRONT", "BACK", "LEFT_PROFILE", "RIGHT_PROFILE", "BASE"
        ),
        catalog_hits=[_catalog_hit()],
        same_artifact=_same("KNOWN_ARTIFACT_CANDIDATE", accepted=True),
        related={
            "accepted": True,
            "qualifying_artifact_ids": ["REF-001"],
        },
        counterfeit={"status": "NO_SIGNAL", "signal": {"triggered": False}},
    ).to_dict()

    assert explanation["schema_version"] == REFERENCE_EXPLANATION_SCHEMA_VERSION
    assert explanation["decision_basis"] == {
        "status": "KNOWN_ARTIFACT_CANDIDATE",
        "status_label_zh": "库内同件候选",
        "summary": "当前门控接受 REF-001 为库内同件候选。",
        "identity_candidate_accepted": True,
        "accepted_artifact_id": "REF-001",
        "related_accepted": True,
        "related_candidate_ids": ["REF-001"],
        "counterfeit_status": "NO_SIGNAL",
        "counterfeit_triggered": False,
        "reason_codes": ["PASSED_ALL_IDENTITY_GATES"],
        "metrics": {
            "query_view_count": 5,
            "catalog_candidate_count": 1,
            "top_candidate_id": "REF-001",
            "top_score": 0.93,
            "top_coverage": 0.8,
            "top_quality_score": 0.91,
            "runner_up_margin": 0.12,
        },
        "authenticity_state": "NOT_ASSESSED",
    }
    statements = {
        item["code"]: item for item in explanation["shared_observations"]
    }
    assert statements["TOP_CANDIDATE_RETRIEVAL_METRICS"]["claim_scope"] == (
        "INDEX_RELATION_ONLY"
    )
    assert statements["MATCHED_VIEW_LABEL_ALIGNMENT"]["details"] == {
        "labelled_pair_count": 2,
        "aligned_pair_count": 2,
    }
    catalogue = statements["CATALOGUE_STATEMENT_FOR_CONTEXT"]
    assert catalogue["claim_scope"] == "REFERENCE_RECORD_ONLY"
    assert "未由待测图片直接测得" in catalogue["text"]
    assert explanation["differences"][0]["claim_scope"] == "UNMEASURED"
    assert explanation["source_citations"][0]["source_status"] == (
        "SOURCE_AND_REVIEW_RECORDED"
    )
    assert explanation["source_citations"][0]["rights"] == {
        "rights_holder": "测试权利人",
        "license_identifier": "LOCAL-DEMO-001",
        "attribution_required": True,
        "attribution_text": "测试馆藏机构 / ACC-001",
    }
    assert explanation["recommended_recaptures"] == []
    assert json.loads(json.dumps(explanation, ensure_ascii=False)) == explanation


def test_out_of_library_related_result_is_deterministic_and_explains_top2_margin():
    payload = {
        "query_views": _query_views("FRONT", "BACK"),
        "catalog_hits": [
            _catalog_hit("REF-001", score=0.78, coverage=0.4),
            _catalog_hit("REF-002", score=0.76, coverage=0.4),
        ],
        "same_artifact": {
            **_same("RELATED_REFERENCES_ONLY"),
            "runner_up_margin": 0.02,
        },
        "related": {
            "accepted": True,
            "qualifying_artifact_ids": ["REF-001", "REF-002"],
        },
        "counterfeit_signal": {"triggered": False},
    }

    first = explain_reference_result(payload).to_dict()
    second = explain_reference_result(payload).to_dict()

    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    assert first["decision_basis"]["identity_candidate_accepted"] is False
    assert first["decision_basis"]["related_candidate_ids"] == [
        "REF-001",
        "REF-002",
    ]
    assert first["decision_basis"]["metrics"]["runner_up_margin"] == 0.02
    assert "未接受任何库内同件身份" in first["decision_basis"]["summary"]
    assert any(
        item["code"] == "RELATED_ONLY_LIMITATION"
        for item in first["uncertainties"]
    )
    assert all(
        item["claim_scope"] == "UNMEASURED" for item in first["differences"]
    )


def test_query_visible_observations_are_preserved_without_becoming_catalogue_facts():
    query_views = _query_views("FRONT")
    query_views[0]["visible_observations"] = [
        "可见青色纹饰与白色地色",
        "底部不在本视角内",
    ]
    result = build_reference_explanation(
        query_views=query_views,
        catalog_hits=[_catalog_hit()],
        same_artifact=_same("RELATED_REFERENCES_ONLY"),
        related={"accepted": True, "qualifying_artifact_ids": ["REF-001"]},
        counterfeit={"status": "NO_SIGNAL", "signal": {"triggered": False}},
    ).to_dict()

    statement = next(
        item
        for item in result["shared_observations"]
        if item["code"] == "QUERY_VISIBLE_OBSERVATIONS"
    )
    assert statement["claim_scope"] == "QUERY_IMAGE_VISIBLE_ONLY"
    assert statement["candidate_id"] is None
    assert statement["details"]["observations"] == [
        "可见青色纹饰与白色地色",
        "底部不在本视角内",
    ]


def test_insufficient_capture_returns_specific_quality_and_missing_view_actions():
    same = _same("INSUFFICIENT_CAPTURE")
    same["gates"] = {**same["gates"], "coverage": False, "quality": False}
    same["reason_codes"] = [
        "QUERY_VIEW_COVERAGE_BELOW_THRESHOLD",
        "IMAGE_QUALITY_BELOW_THRESHOLD",
    ]

    result = build_reference_explanation(
        query_views=_query_views("FRONT", quality=0.31),
        catalog_hits=[_catalog_hit(coverage=0.2, quality=0.31)],
        same_artifact=same,
        related={"accepted": False, "qualifying_artifact_ids": []},
        counterfeit="NOT_RUN",
    ).to_dict()

    actions = {item["view_code"]: item for item in result["recommended_recaptures"]}
    assert actions["FRONT"]["priority"] == "HIGH"
    assert "质量分" in actions["FRONT"]["reason"]
    assert {"BACK", "LEFT_PROFILE", "RIGHT_PROFILE", "BASE"}.issubset(actions)
    assert result["decision_basis"]["status_label_zh"] == "采集信息不足"
    reason_text = next(
        item["text"]
        for item in result["uncertainties"]
        if item["code"] == "IDENTITY_GATE_REASONS"
    )
    assert "查询视角覆盖不足" in reason_text
    assert "采集质量不足" in reason_text


def test_no_match_and_unknown_inputs_are_readable_without_inventing_candidates():
    no_match = build_reference_explanation(
        query_views=_query_views("FRONT"),
        catalog_hits=[],
        same_artifact={
            "accepted": False,
            "status": "OPEN_SET_NO_MATCH",
            "reason_codes": ["NO_CATALOG_REFERENCES"],
        },
        related={"accepted": False, "qualifying_artifact_ids": []},
        counterfeit="NOT_RUN",
    ).to_dict()

    assert no_match["source_citations"] == []
    assert no_match["shared_observations"][0]["code"] == "NO_CATALOG_CANDIDATE"
    assert "不推测" in no_match["differences"][0]["text"]
    assert "有限样本库" in next(
        item["text"]
        for item in no_match["uncertainties"]
        if item["code"] == "OPEN_SET_LIMITATION"
    )

    unknown = build_reference_explanation(
        query_views="invalid",
        catalog_hits={"invalid": True},
        same_artifact=None,
        related=None,
        counterfeit=None,
    ).to_dict()
    assert unknown["decision_basis"]["status"] == "UNKNOWN"
    assert unknown["decision_basis"]["summary"].startswith("状态信息不足")
    assert any(
        item["code"] == "DECISION_STATE_UNKNOWN"
        for item in unknown["uncertainties"]
    )


def test_counterfeit_conflict_remains_a_cited_cross_check_signal_not_a_verdict():
    negative_hit = _catalog_hit("NEG-001", score=0.91)
    negative_hit["metadata"]["expert_review"]["decision"] = "COUNTERFEIT"
    negative_hit["metadata"]["counterfeit_profile"] = {
        "counterfeit_type": "FORGED_MARK",
        "claimed_identity": "测试声称身份",
        "known_indicators": ["审签记录中的指标甲", "审签记录中的指标乙"],
    }
    same = _same("RELATED_REFERENCES_ONLY")
    same["gates"] = {**same["gates"], "counterfeit_conflict_absent": False}
    same["reason_codes"] = ["KNOWN_COUNTERFEIT_REFERENCE_CONFLICT"]

    result = build_reference_explanation(
        query_views=_query_views("FRONT", "BACK"),
        catalog_hits=[_catalog_hit(score=0.90)],
        same_artifact=same,
        related={"accepted": True, "qualifying_artifact_ids": ["REF-001"]},
        counterfeit={
            "status": "CONFLICT_REVIEW",
            "signal": {
                "triggered": True,
                "strength": "STRONG",
                "reference_id": "NEG-001",
                "score": 0.91,
                "review_status": "verified",
            },
            "candidates": [negative_hit],
        },
    ).to_dict()

    assert result["decision_basis"]["counterfeit_triggered"] is True
    assert "必须人工交叉复核" in result["decision_basis"]["summary"]
    negative_statement = next(
        item
        for item in result["shared_observations"]
        if item["code"] == "NEGATIVE_REFERENCE_RECORD_STATEMENT"
    )
    assert negative_statement["claim_scope"] == "REFERENCE_RECORD_ONLY"
    assert "没有独立确认待测图具备" in negative_statement["text"]
    signal = next(
        item
        for item in result["uncertainties"]
        if item["code"] == "COUNTERFEIT_REFERENCE_SIGNAL"
    )
    assert signal["claim_scope"] == "CROSS_CHECK_SIGNAL_ONLY"
    assert "不能单独证明待测器物为假" in signal["text"]
    assert any(
        citation["reference_role"] == "counterfeit_reference"
        and citation["artifact_id"] == "NEG-001"
        for citation in result["source_citations"]
    )
    assert {"MARK", "DETAIL", "BASE"}.issubset(
        {item["view_code"] for item in result["recommended_recaptures"]}
    )


def test_nonfinite_or_unrecognized_values_are_not_rendered_as_facts():
    hit = _catalog_hit()
    hit["score"] = float("nan")
    hit["coverage"] = float("inf")
    hit["metadata"]["catalogue"] = {"unrecognized_field": "不得进入输出"}

    result = build_reference_explanation(
        query_views=[{"view_id": "q", "quality": float("nan"), "angle": None}],
        catalog_hits=[hit],
        same_artifact={"accepted": False, "status": "RELATED_REFERENCES_ONLY"},
        related={"accepted": True, "qualifying_artifact_ids": ["REF-001"]},
        counterfeit={"status": "NO_SIGNAL", "signal": {"triggered": False}},
    ).to_dict()

    serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)
    assert "不得进入输出" not in serialized
    assert "NaN" not in serialized
    assert "Infinity" not in serialized
    assert all(
        item["code"] != "CATALOGUE_STATEMENT_FOR_CONTEXT"
        for item in result["shared_observations"]
    )


def test_exact_reference_media_replay_is_explained_as_a_blocked_identity_input():
    same = _same("EXACT_MEDIA_REPLAY")
    same["reason_codes"] = ["EXACT_MEDIA_REPLAY"]
    same["gates"] = {**same["gates"], "exact_media_replay_absent": False}

    result = build_reference_explanation(
        query_views=_query_views(
            "FRONT", "BACK", "LEFT_PROFILE", "RIGHT_PROFILE", "BASE"
        ),
        catalog_hits=[_catalog_hit()],
        same_artifact=same,
        related={"accepted": True, "qualifying_artifact_ids": ["REF-001"]},
        counterfeit={"status": "NO_SIGNAL", "signal": {"triggered": False}},
    ).to_dict()

    assert result["decision_basis"]["status"] == "EXACT_MEDIA_REPLAY"
    assert result["decision_basis"]["identity_candidate_accepted"] is False
    assert "阻断同件接受" in result["decision_basis"]["summary"]
    assert any(
        item["code"] == "EXACT_MEDIA_REPLAY_BLOCKED"
        for item in result["uncertainties"]
    )
    assert any(
        item["view_code"] == "FRONT"
        and "独立复拍" in item["reason"]
        for item in result["recommended_recaptures"]
    )
