from __future__ import annotations

import pytest
from httpx import Headers

from app.services.vlm import (
    OpenAICompatibleClient,
    report_citation_ids,
    validate_reasoner_output,
    validate_vision_output,
)


def test_nemotron_candidate_disables_thinking_and_audio_for_json_video_contract():
    nemotron = OpenAICompatibleClient(
        "http://vision:8000/v1", "key", "nemotron_3_nano_omni"
    )
    assert nemotron._model_request_options(video=True) == {
        "top_k": 1,
        "chat_template_kwargs": {"enable_thinking": False},
        "mm_processor_kwargs": {"use_audio_in_video": False},
    }
    qwen = OpenAICompatibleClient("http://vision:8000/v1", "key", "qwen3_vl_30b_a3b")
    assert qwen._model_request_options(video=True) == {}


def test_completion_identity_requires_exact_model_and_request_id():
    body = {"model": "qwen3_vl_30b_a3b", "id": "chatcmpl-123"}
    assert OpenAICompatibleClient._completion_identity(
        body, Headers(), "qwen3_vl_30b_a3b"
    ) == ("qwen3_vl_30b_a3b", "chatcmpl-123")
    with pytest.raises(ValueError, match="model identity"):
        OpenAICompatibleClient._completion_identity(
            {"id": "chatcmpl-123"}, Headers(), "qwen3_vl_30b_a3b"
        )
    with pytest.raises(ValueError, match="request identifier"):
        OpenAICompatibleClient._completion_identity(
            {"model": "qwen3_vl_30b_a3b"}, Headers(), "qwen3_vl_30b_a3b"
        )


def test_compliant_vision_output_is_accepted():
    value = {
        "observations": ["可见蓝色纹饰与白色釉面"],
        "suggested_regions": [{"label": "口沿", "reason": "反光变化明显"}],
        "limitations": ["单幅 RGB 图像不能代表材料成分"],
        "ood_risk": "MEDIUM",
    }
    assert validate_vision_output(value) == value


@pytest.mark.parametrize(
    "text",
    [
        "这是一件真品",
        "综合判断为赝品",
        "此物为仿品",
        "可以确定年代为清代",
        "作者为张大千",
        "此画出自张大千",
        "该器物为明代器物",
        "市场价格为二十万元",
        "This object is authentic.",
        "The material is genuine.",
        "This appears to be a counterfeit.",
        "It is likely an imitation.",
        "This is a reproduction.",
        "The object dates to the 18th century.",
        "This object is from the Ming dynasty.",
        "The work is attributed to a named artist.",
        "It was painted by a named artist.",
        "The painting is by Pablo Picasso.",
        "The artist is Zhang Daqian.",
        "器形倾向于康熙时期。",
        "约为十八世纪中期制作。",
        "这是一件大开门的老货。",
        "釉光老气，应当到代。",
        "底足符合景德镇烧造特征。",
        "具有很好的收藏价值和升值空间。",
        "行家会认为这件很开门，包老保真。",
        "市场价格约为人民币二十万元。",
        "This could be from the late 18th century.",
        "The glaze appears consistent with the Qianlong era.",
        "This may be late Qing.",
        "It is probably Jingdezhen ware.",
        "It is likely from Jingdezhen.",
        "This is a museum-quality collectible.",
        "The auction estimate is USD 25,000.",
    ],
)
def test_vision_verdicts_are_rejected(text):
    with pytest.raises(ValueError, match="conclusion boundary"):
        validate_vision_output(
            {
                "observations": [text],
                "suggested_regions": [],
                "limitations": [],
                "ood_risk": "LOW",
            }
        )


def test_reasoner_schema_and_verdict_boundary():
    valid = {
        "summary": "材料响应与送检声明存在待复核差异。",
        "limitations": ["演示数据"],
        "next_steps": ["专家复核"],
        "citation_ids": [],
    }
    assert validate_reasoner_output(valid) == valid
    with pytest.raises(ValueError, match="missing required fields"):
        validate_reasoner_output(
            {key: value for key, value in valid.items() if key != "citation_ids"}
        )
    with pytest.raises(ValueError):
        validate_reasoner_output({**valid, "summary": "结论为赝品"})


def test_guardrail_does_not_reject_scientific_abstention_language():
    value = {
        "summary": "现有材料响应不足以支持送检声明，状态为证据不足。",
        "limitations": ["演示数据；需要专家复核"],
        "next_steps": ["补充合格光谱数据"],
        "citation_ids": [],
    }
    assert validate_reasoner_output(value) == value


@pytest.mark.parametrize(
    "text",
    [
        "底部款识逐字转录：「大清乾隆年制」。",
        "Mark transcription: 'Da Qing Qianlong Nian Zhi'.",
    ],
)
def test_guardrail_preserves_literal_mark_transcription_without_interpretation(text):
    value = {
        "observations": [text],
        "suggested_regions": [],
        "limitations": ["款识文字仅作逐字记录，不据此形成结论。"],
        "ood_risk": "LOW",
    }
    assert validate_vision_output(value) == value


def test_literal_mark_exception_does_not_hide_interpretation_suffix():
    with pytest.raises(ValueError, match="conclusion boundary"):
        validate_vision_output(
            {
                "observations": [
                    "底部款识逐字转录：「大清乾隆年制」；因此器物属于乾隆时期。"
                ],
                "suggested_regions": [],
                "limitations": [],
                "ood_risk": "LOW",
            }
        )


def test_reasoner_citations_must_be_bound_to_report_results():
    value = {
        "summary": "本地参考仅用于说明检测适用范围。",
        "limitations": ["演示资料"],
        "next_steps": ["专家复核"],
        "citation_ids": ["KB-DEMO-001"],
    }
    assert validate_reasoner_output(value, {"KB-DEMO-001"}) == value
    with pytest.raises(ValueError, match="unbound local citation"):
        validate_reasoner_output(value, {"KB-DEMO-002"})


def test_report_citation_allowlist_uses_only_returned_source_ids():
    report = {
        "knowledge": {
            "searches": [
                {
                    "results": [
                        {"source_id": "KB-DEMO-001"},
                        {"source_id": "KB-DEMO-002"},
                        {"title": "missing identifier"},
                    ]
                }
            ]
        }
    }
    assert report_citation_ids(report) == {"KB-DEMO-001", "KB-DEMO-002"}


def test_report_reference_citations_use_the_rendered_latest_run_only():
    older = {
        "catalog_hits": [{"metadata": {"citation_id": "REFERENCE:OLD"}}],
        "counterfeit_hits": [],
    }
    latest = {
        "catalog_hits": [{"metadata": {"citation_id": "REFERENCE:LATEST"}}],
        "counterfeit_hits": [
            {"metadata": {"citation_id": "REFERENCE:LATEST-NEGATIVE"}}
        ],
    }
    report = {
        "knowledge": {"searches": []},
        "reference_recognitions": [older, latest],
        "latest_reference_recognition": latest,
    }

    assert report_citation_ids(report) == {
        "REFERENCE:LATEST",
        "REFERENCE:LATEST-NEGATIVE",
    }
