from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


REFERENCE_EXPLANATION_SCHEMA_VERSION = "relicscope-reference-explanation-v1"
AUTHENTICITY_NOT_ASSESSED = "NOT_ASSESSED"

_STANDARD_CAPTURE_ORDER = (
    "FRONT",
    "BACK",
    "LEFT_PROFILE",
    "RIGHT_PROFILE",
    "BASE",
)
_ANGLE_LABELS_ZH = {
    "FRONT": "正面",
    "BACK": "背面",
    "LEFT_PROFILE": "左侧",
    "RIGHT_PROFILE": "右侧",
    "TOP": "顶部",
    "BASE": "底部",
    "INTERIOR": "内部",
    "FRONT_LEFT_45": "左前 45 度",
    "FRONT_RIGHT_45": "右前 45 度",
    "BACK_LEFT_45": "左后 45 度",
    "BACK_RIGHT_45": "右后 45 度",
    "DETAIL": "局部细节",
    "MARK": "款识",
    "DAMAGE": "损伤区域",
}
_CATALOGUE_LABELS_ZH = {
    "culture_or_period": "年代或文化阶段",
    "maker_or_kiln": "制作者或窑口",
    "material": "材料",
    "technique": "工艺",
    "dimensions": "尺寸",
    "description": "目录描述",
}
_REASON_LABELS_ZH = {
    "SCORE_BELOW_SAME_ARTIFACT_THRESHOLD": "最高相似度未通过同件候选阈值",
    "RUNNER_UP_MARGIN_BELOW_THRESHOLD": "第一、第二候选差距不足",
    "QUERY_VIEW_COVERAGE_BELOW_THRESHOLD": "查询视角覆盖不足",
    "REFERENCE_VIEW_COVERAGE_BELOW_THRESHOLD": "参考视角覆盖不足",
    "IMAGE_QUALITY_BELOW_THRESHOLD": "采集质量不足",
    "CALIBRATION_RECORD_REQUIRED": "缺少与当前索引绑定的校准记录",
    "KNOWN_COUNTERFEIT_REFERENCE_CONFLICT": "负向参考相似信号与目录候选发生冲突",
    "INSUFFICIENT_COMPLEMENTARY_DECLARED_ANGLES": "互补的整器视角少于同件门槛",
    "EXACT_MEDIA_REPLAY": "上传文件与库内原图字节完全相同，不能作为独立复拍",
    "NO_CATALOG_REFERENCES": "当前索引没有可用目录参考",
    "PASSED_ALL_IDENTITY_GATES": "已通过当前同件候选判定门",
}
_STATUS_LABELS_ZH = {
    "KNOWN_ARTIFACT_CANDIDATE": "库内同件候选",
    "RELATED_REFERENCES_ONLY": "仅有相关参考",
    "INSUFFICIENT_CAPTURE": "采集信息不足",
    "OPEN_SET_NO_MATCH": "库外或无充分匹配",
    "CALIBRATION_REQUIRED": "需要完成校准",
    "EMBEDDING_UNAVAILABLE": "本地图像向量服务不可用",
    "EXACT_MEDIA_REPLAY": "检测到库内原图回放",
}


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_ready(item) for item in value]
    return value


@dataclass(frozen=True)
class ExplanationStatement:
    code: str
    text: str
    evidence_origin: str
    claim_scope: str
    candidate_id: Optional[str] = None
    citation_ids: Tuple[str, ...] = ()
    details: Tuple[Tuple[str, Any], ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "text": self.text,
            "evidence_origin": self.evidence_origin,
            "claim_scope": self.claim_scope,
            "candidate_id": self.candidate_id,
            "citation_ids": list(self.citation_ids),
            "details": {
                key: _json_ready(value) for key, value in self.details
            },
        }


@dataclass(frozen=True)
class SourceCitation:
    citation_id: str
    artifact_id: str
    reference_role: str
    display_name: Optional[str]
    source_type: Optional[str]
    institution: Optional[str]
    collection_name: Optional[str]
    accession_number: Optional[str]
    record_locator: Optional[str]
    retrieved_at: Optional[str]
    review_id: Optional[str]
    review_decision: Optional[str]
    reviewer_credential: Optional[str]
    reviewer_institution: Optional[str]
    reviewed_at: Optional[str]
    rights_holder: Optional[str]
    license_identifier: Optional[str]
    attribution_required: Optional[bool]
    attribution_text: Optional[str]
    source_status: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "artifact_id": self.artifact_id,
            "reference_role": self.reference_role,
            "display_name": self.display_name,
            "source_type": self.source_type,
            "institution": self.institution,
            "collection_name": self.collection_name,
            "accession_number": self.accession_number,
            "record_locator": self.record_locator,
            "retrieved_at": self.retrieved_at,
            "expert_review": {
                "review_id": self.review_id,
                "decision": self.review_decision,
                "reviewer_credential": self.reviewer_credential,
                "reviewer_institution": self.reviewer_institution,
                "reviewed_at": self.reviewed_at,
            },
            "rights": {
                "rights_holder": self.rights_holder,
                "license_identifier": self.license_identifier,
                "attribution_required": self.attribution_required,
                "attribution_text": self.attribution_text,
            },
            "source_status": self.source_status,
            "scope_note": (
                "此引用只说明参考记录的来源与审签状态；目录陈述不等于待测器物的实测事实。"
            ),
        }


@dataclass(frozen=True)
class RecaptureRecommendation:
    view_code: str
    view_label_zh: str
    priority: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "view_code": self.view_code,
            "view_label_zh": self.view_label_zh,
            "priority": self.priority,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DecisionBasis:
    status: str
    status_label_zh: str
    summary: str
    identity_candidate_accepted: bool
    accepted_artifact_id: Optional[str]
    related_accepted: bool
    related_candidate_ids: Tuple[str, ...]
    counterfeit_status: str
    counterfeit_triggered: bool
    reason_codes: Tuple[str, ...]
    metrics: Tuple[Tuple[str, Any], ...]
    authenticity_state: str = AUTHENTICITY_NOT_ASSESSED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "status_label_zh": self.status_label_zh,
            "summary": self.summary,
            "identity_candidate_accepted": self.identity_candidate_accepted,
            "accepted_artifact_id": self.accepted_artifact_id,
            "related_accepted": self.related_accepted,
            "related_candidate_ids": list(self.related_candidate_ids),
            "counterfeit_status": self.counterfeit_status,
            "counterfeit_triggered": self.counterfeit_triggered,
            "reason_codes": list(self.reason_codes),
            "metrics": dict(self.metrics),
            "authenticity_state": self.authenticity_state,
        }


@dataclass(frozen=True)
class ReferenceExplanation:
    shared_observations: Tuple[ExplanationStatement, ...]
    differences: Tuple[ExplanationStatement, ...]
    source_citations: Tuple[SourceCitation, ...]
    uncertainties: Tuple[ExplanationStatement, ...]
    recommended_recaptures: Tuple[RecaptureRecommendation, ...]
    decision_basis: DecisionBasis
    schema_version: str = REFERENCE_EXPLANATION_SCHEMA_VERSION

    def to_dict(self) -> Dict[str, Any]:
        """Return a stable JSON-ready DTO without adding unobserved visual facts."""

        return {
            "schema_version": self.schema_version,
            "shared_observations": [item.to_dict() for item in self.shared_observations],
            "differences": [item.to_dict() for item in self.differences],
            "source_citations": [item.to_dict() for item in self.source_citations],
            "uncertainties": [item.to_dict() for item in self.uncertainties],
            "recommended_recaptures": [
                item.to_dict() for item in self.recommended_recaptures
            ],
            "decision_basis": self.decision_basis.to_dict(),
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _state_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        return {"status": value}
    return _mapping(value)


def _mapping_sequence(value: Any) -> Tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _text(value: Any, *, maximum: int = 600) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if not normalized:
        return None
    if len(normalized) <= maximum:
        return normalized
    return normalized[: maximum - 1].rstrip() + "…"


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _rounded(value: Any) -> Optional[float]:
    number = _number(value)
    return round(number, 6) if number is not None else None


def _angle(value: Any) -> Optional[str]:
    text = _text(value, maximum=64)
    return text.upper() if text else None


def _candidate_id(hit: Mapping[str, Any]) -> Optional[str]:
    return _text(hit.get("artifact_id"), maximum=128)


def _citation_id(hit: Mapping[str, Any]) -> Optional[str]:
    artifact_id = _candidate_id(hit)
    metadata = _mapping(hit.get("metadata"))
    return _text(metadata.get("citation_id"), maximum=160) or (
        f"REFERENCE:{artifact_id}" if artifact_id else None
    )


def _source_citation(
    hit: Mapping[str, Any], *, reference_role: str
) -> Optional[SourceCitation]:
    artifact_id = _candidate_id(hit)
    citation_id = _citation_id(hit)
    if artifact_id is None or citation_id is None:
        return None
    metadata = _mapping(hit.get("metadata"))
    source = _mapping(metadata.get("source_citation") or metadata.get("source"))
    review = _mapping(metadata.get("expert_review"))
    rights = _mapping(metadata.get("rights"))
    source_fields = (
        source.get("source_type"),
        source.get("institution"),
        source.get("collection_name"),
        source.get("accession_number"),
        source.get("record_locator"),
    )
    return SourceCitation(
        citation_id=citation_id,
        artifact_id=artifact_id,
        reference_role=reference_role,
        display_name=_text(metadata.get("display_name")),
        source_type=_text(source.get("source_type"), maximum=128),
        institution=_text(source.get("institution")),
        collection_name=_text(source.get("collection_name")),
        accession_number=_text(source.get("accession_number"), maximum=160),
        record_locator=_text(source.get("record_locator")),
        retrieved_at=_text(source.get("retrieved_at"), maximum=96),
        review_id=_text(review.get("review_id"), maximum=160),
        review_decision=_text(review.get("decision"), maximum=80),
        reviewer_credential=_text(review.get("reviewer_credential")),
        reviewer_institution=_text(review.get("reviewer_institution")),
        reviewed_at=_text(review.get("reviewed_at"), maximum=96),
        rights_holder=_text(rights.get("rights_holder")),
        license_identifier=_text(rights.get("license_identifier"), maximum=160),
        attribution_required=(
            rights.get("attribution_required")
            if isinstance(rights.get("attribution_required"), bool)
            else None
        ),
        attribution_text=_text(rights.get("attribution_text")),
        source_status=(
            "SOURCE_AND_REVIEW_RECORDED"
            if any(_text(item) for item in source_fields) and _text(review.get("review_id"))
            else "SOURCE_METADATA_INCOMPLETE"
        ),
    )


def _catalogue(hit: Mapping[str, Any]) -> Mapping[str, Any]:
    metadata = _mapping(hit.get("metadata"))
    return _mapping(metadata.get("catalogue") or metadata.get("catalogue_metadata"))


def _counterfeit_parts(
    counterfeit: Any,
    counterfeit_hits: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Tuple[Mapping[str, Any], ...]]:
    outer = _state_mapping(counterfeit)
    nested_signal = _mapping(outer.get("signal"))
    signal = nested_signal if nested_signal else outer
    supplied_hits = _mapping_sequence(counterfeit_hits)
    nested_hits = _mapping_sequence(outer.get("candidates"))
    return outer, signal, supplied_hits or nested_hits


def _decision_summary(
    status: str,
    accepted_artifact_id: Optional[str],
    related_ids: Tuple[str, ...],
    counterfeit_triggered: bool,
) -> str:
    if status == "KNOWN_ARTIFACT_CANDIDATE":
        subject = accepted_artifact_id or "最高候选"
        summary = f"当前门控接受 {subject} 为库内同件候选。"
    elif status == "RELATED_REFERENCES_ONLY":
        summary = "当前只保留相关参考，未接受任何库内同件身份。"
    elif status == "INSUFFICIENT_CAPTURE":
        summary = "当前采集的视角覆盖或图像质量不足，暂不接受同件身份。"
    elif status == "OPEN_SET_NO_MATCH":
        summary = "当前参考库没有达到相关性门槛的候选，结果按库外查询处理。"
    elif status == "CALIBRATION_REQUIRED":
        summary = "当前索引尚未绑定合格校准记录，因此不开放同件身份接受。"
    elif status == "EMBEDDING_UNAVAILABLE":
        summary = "本地图像向量服务不可用，本次未完成目录相关性检索。"
    elif status == "EXACT_MEDIA_REPLAY":
        summary = "上传文件与库内原图字节完全相同；系统阻断同件接受并要求独立复拍。"
    elif related_ids:
        summary = "状态字段不完整；系统仅呈现已有的相关参考排序。"
    else:
        summary = "状态信息不足，系统未形成同件或相关参考判断。"
    if counterfeit_triggered:
        summary += " 同时触发了负向参考相似信号，必须人工交叉复核。"
    return summary


def _recapture_recommendations(
    query_views: Tuple[Mapping[str, Any], ...],
    status: str,
    gates: Mapping[str, Any],
    counterfeit_triggered: bool,
) -> Tuple[RecaptureRecommendation, ...]:
    captured_angles = {
        angle
        for angle in (_angle(item.get("angle")) for item in query_views)
        if angle is not None
    }
    recommendations: list[RecaptureRecommendation] = []
    recommendation_codes: set[str] = set()

    def add(view_code: str, priority: str, reason: str) -> None:
        if view_code in recommendation_codes:
            return
        recommendation_codes.add(view_code)
        recommendations.append(
            RecaptureRecommendation(
                view_code=view_code,
                view_label_zh=_ANGLE_LABELS_ZH.get(view_code, view_code),
                priority=priority,
                reason=reason,
            )
        )

    low_quality_angles = sorted(
        {
            angle
            for item in query_views
            if (angle := _angle(item.get("angle"))) is not None
            and (quality := _number(item.get("quality"))) is not None
            and quality < 0.55
        }
    )
    for angle in low_quality_angles:
        add(
            angle,
            "HIGH",
            "该视角的采集质量分低于补拍提示线；请固定器物、校正对焦和曝光后重拍。",
        )

    needs_coverage = (
        status in {"INSUFFICIENT_CAPTURE", "RELATED_REFERENCES_ONLY", "OPEN_SET_NO_MATCH"}
        or gates.get("coverage") is False
        or len(captured_angles) < 5
    )
    if needs_coverage:
        for angle in _STANDARD_CAPTURE_ORDER:
            if angle not in captured_angles:
                add(
                    angle,
                    "HIGH" if len(captured_angles) < 3 else "MEDIUM",
                    "补齐标准多视角覆盖，便于区分同件候选与仅风格相关的参考。",
                )

    if counterfeit_triggered:
        add("MARK", "HIGH", "记录完整款识区域，供负向参考的人工逐项复核。")
        add("DETAIL", "HIGH", "补拍胎釉、接缝或做旧痕迹等可见细节；具体部位由专家指定。")
        add("BASE", "HIGH", "补拍完整底部并保留尺度与色彩参照。")

    if status == "EXACT_MEDIA_REPLAY":
        add(
            "FRONT",
            "HIGH",
            "请现场独立复拍；不得复制、截图或重新编码参考库原图作为身份查询。",
        )

    return tuple(recommendations)


def build_reference_explanation(
    *,
    query_views: Any = None,
    catalog_hits: Any = None,
    same_artifact: Any = None,
    related: Any = None,
    counterfeit: Any = None,
    counterfeit_hits: Any = None,
) -> ReferenceExplanation:
    """Explain a reference search using only fields present in its audit envelope.

    Embedding similarity, capture labels, catalogue statements, and expert-review
    metadata remain separate evidence classes. In particular, catalogue period,
    kiln, material, technique, and dimensions are never restated as observations of
    the query object.
    """

    query_items = _mapping_sequence(query_views)
    catalog_items = _mapping_sequence(catalog_hits)
    same = _state_mapping(same_artifact)
    related_state = _state_mapping(related)
    counterfeit_outer, counterfeit_signal, negative_items = _counterfeit_parts(
        counterfeit, counterfeit_hits
    )

    raw_status = _text(same.get("status"), maximum=96)
    status = raw_status.upper() if raw_status else "UNKNOWN"
    same_accepted = same.get("accepted") is True
    accepted_artifact_id = (
        _text(same.get("artifact_id"), maximum=128) if same_accepted else None
    )
    related_accepted = related_state.get("accepted") is True
    related_ids = tuple(
        item
        for value in (related_state.get("qualifying_artifact_ids") or ())
        if (item := _text(value, maximum=128)) is not None
    ) if isinstance(related_state.get("qualifying_artifact_ids"), Sequence) and not isinstance(
        related_state.get("qualifying_artifact_ids"), (str, bytes, bytearray)
    ) else ()
    counterfeit_status = (
        _text(counterfeit_outer.get("status"), maximum=96)
        or ("TRIGGERED" if counterfeit_signal.get("triggered") is True else "NOT_TRIGGERED")
    )
    counterfeit_triggered = counterfeit_signal.get("triggered") is True or (
        counterfeit_status.upper() in {"CONFLICT_REVIEW", "STRONG_SIGNAL", "TRIGGERED"}
    )

    shared: list[ExplanationStatement] = []
    differences: list[ExplanationStatement] = []
    uncertainties: list[ExplanationStatement] = []

    visible_observations: list[str] = []
    for query_item in query_items:
        values = query_item.get("visible_observations")
        if not isinstance(values, Sequence) or isinstance(
            values, (str, bytes, bytearray)
        ):
            continue
        for value in values:
            observation = _text(value)
            if observation is not None and observation not in visible_observations:
                visible_observations.append(observation)
    if visible_observations:
        shared.append(
            ExplanationStatement(
                code="QUERY_VISIBLE_OBSERVATIONS",
                text=(
                    "本地图像观察记录了："
                    + "；".join(visible_observations[:8])
                    + "。这些内容仅限照片中可见现象。"
                ),
                evidence_origin="query_image_model_observation",
                claim_scope="QUERY_IMAGE_VISIBLE_ONLY",
                details=(("observations", tuple(visible_observations[:8])),),
            )
        )

    top = catalog_items[0] if catalog_items else {}
    top_id = _candidate_id(top)
    top_citation = _citation_id(top)
    top_score = _rounded(top.get("score"))
    top_coverage = _rounded(top.get("coverage"))
    top_quality = _rounded(top.get("quality_score"))
    if top_quality is None:
        top_quality = _rounded(top.get("quality"))
    runner_up_margin = _rounded(same.get("runner_up_margin"))

    if top_id is not None:
        metric_fragments = []
        metric_details: list[tuple[str, Any]] = []
        for label, key, value in (
            ("综合相似度", "score", top_score),
            ("视角覆盖", "coverage", top_coverage),
            ("质量分", "quality_score", top_quality),
            ("Top-2 间隔", "runner_up_margin", runner_up_margin),
        ):
            if value is not None:
                metric_fragments.append(f"{label} {value:.4f}")
                metric_details.append((key, value))
        metric_text = "、".join(metric_fragments) or "未提供可复核的数值指标"
        shared.append(
            ExplanationStatement(
                code="TOP_CANDIDATE_RETRIEVAL_METRICS",
                text=(
                    f"参考 {top_id} 在当前图像向量索引中排名第一：{metric_text}。"
                    "这些数值只描述本库内的检索关系。"
                ),
                evidence_origin="retrieval_metric",
                claim_scope="INDEX_RELATION_ONLY",
                candidate_id=top_id,
                citation_ids=(top_citation,) if top_citation else (),
                details=tuple(metric_details),
            )
        )

        matched_views = _mapping_sequence(top.get("matched_views"))
        labelled_pairs = []
        aligned_pairs = []
        mismatched_pairs = []
        for item in matched_views:
            query_angle = _angle(item.get("query_angle"))
            reference_angle = _angle(item.get("reference_angle"))
            if query_angle is None or reference_angle is None:
                continue
            pair = (query_angle, reference_angle)
            labelled_pairs.append(pair)
            if query_angle == reference_angle:
                aligned_pairs.append(pair)
            else:
                mismatched_pairs.append(pair)
        if labelled_pairs:
            shared.append(
                ExplanationStatement(
                    code="MATCHED_VIEW_LABEL_ALIGNMENT",
                    text=(
                        f"最高候选有 {len(labelled_pairs)} 组带视角标签的匹配，其中 "
                        f"{len(aligned_pairs)} 组使用相同视角标签。"
                        "这说明拍摄角度具有可比性，不代表器物特征已经逐项一致。"
                    ),
                    evidence_origin="capture_and_reference_labels",
                    claim_scope="VIEW_LABEL_COMPARABILITY_ONLY",
                    candidate_id=top_id,
                    citation_ids=(top_citation,) if top_citation else (),
                    details=(
                        ("labelled_pair_count", len(labelled_pairs)),
                        ("aligned_pair_count", len(aligned_pairs)),
                    ),
                )
            )
        if mismatched_pairs:
            pair_text = "、".join(
                f"{_ANGLE_LABELS_ZH.get(query, query)}→"
                f"{_ANGLE_LABELS_ZH.get(reference, reference)}"
                for query, reference in sorted(set(mismatched_pairs))
            )
            differences.append(
                ExplanationStatement(
                    code="CAPTURE_VIEW_LABEL_DIFFERENCE",
                    text=(
                        f"查询与参考存在视角标签不同的匹配：{pair_text}。"
                        "这属于采集角度差异，不能解释为器物本体差异。"
                    ),
                    evidence_origin="capture_and_reference_labels",
                    claim_scope="CAPTURE_DIFFERENCE_ONLY",
                    candidate_id=top_id,
                    citation_ids=(top_citation,) if top_citation else (),
                    details=(("mismatched_pair_count", len(mismatched_pairs)),),
                )
            )

    for hit in catalog_items[:3]:
        artifact_id = _candidate_id(hit)
        if artifact_id is None:
            continue
        citation_id = _citation_id(hit)
        catalogue = _catalogue(hit)
        recorded = tuple(
            (field, value)
            for field in _CATALOGUE_LABELS_ZH
            if (value := _text(catalogue.get(field))) is not None
        )
        if not recorded:
            continue
        catalogue_text = "；".join(
            f"{_CATALOGUE_LABELS_ZH[field]}：{value}" for field, value in recorded
        )
        shared.append(
            ExplanationStatement(
                code="CATALOGUE_STATEMENT_FOR_CONTEXT",
                text=(
                    f"参考 {artifact_id} 的来源目录陈述为：{catalogue_text}。"
                    "这些内容只属于参考记录，未由待测图片直接测得。"
                ),
                evidence_origin="catalogue_statement",
                claim_scope="REFERENCE_RECORD_ONLY",
                candidate_id=artifact_id,
                citation_ids=(citation_id,) if citation_id else (),
                details=recorded,
            )
        )
        differences.append(
            ExplanationStatement(
                code="CATALOGUE_FIELDS_NOT_DIRECTLY_COMPARED",
                text=(
                    f"待测输入没有 {artifact_id} 目录字段对应的直接测量值；"
                    "当前结果不能陈述年代、窑口、材料、工艺或尺寸与该记录一致或不同。"
                ),
                evidence_origin="measurement_absence",
                claim_scope="UNMEASURED",
                candidate_id=artifact_id,
                citation_ids=(citation_id,) if citation_id else (),
                details=(("unmeasured_fields", tuple(field for field, _ in recorded)),),
            )
        )

    if not catalog_items:
        shared.append(
            ExplanationStatement(
                code="NO_CATALOG_CANDIDATE",
                text="当前结果没有可呈现的目录候选，因此没有可比较的库内共同点。",
                evidence_origin="retrieval_state",
                claim_scope="INDEX_RELATION_ONLY",
            )
        )
        differences.append(
            ExplanationStatement(
                code="NO_REFERENCE_FOR_DIRECT_COMPARISON",
                text="没有目录候选时，系统不推测待测器物与任何年代、窑口、材料或工艺的差异。",
                evidence_origin="retrieval_state",
                claim_scope="UNMEASURED",
            )
        )

    for hit in negative_items[:3]:
        artifact_id = _candidate_id(hit)
        if artifact_id is None:
            continue
        citation_id = _citation_id(hit)
        profile = _mapping(_mapping(hit.get("metadata")).get("counterfeit_profile"))
        indicators_value = profile.get("known_indicators")
        indicators = tuple(
            item
            for value in indicators_value
            if (item := _text(value)) is not None
        ) if isinstance(indicators_value, Sequence) and not isinstance(
            indicators_value, (str, bytes, bytearray)
        ) else ()
        if indicators:
            shared.append(
                ExplanationStatement(
                    code="NEGATIVE_REFERENCE_RECORD_STATEMENT",
                    text=(
                        f"负向参考 {artifact_id} 的审签记录列出 {len(indicators)} 项已知指标。"
                        "当前相似检索没有独立确认待测图具备这些指标，需由专家逐项核对。"
                    ),
                    evidence_origin="negative_reference_record",
                    claim_scope="REFERENCE_RECORD_ONLY",
                    candidate_id=artifact_id,
                    citation_ids=(citation_id,) if citation_id else (),
                    details=(("recorded_indicators", indicators),),
                )
            )

    reasons_value = same.get("reason_codes")
    reason_codes = tuple(
        sorted(
            {
                item
                for value in reasons_value
                if (item := _text(value, maximum=128)) is not None
            }
        )
    ) if isinstance(reasons_value, Sequence) and not isinstance(
        reasons_value, (str, bytes, bytearray)
    ) else ()
    if reason_codes:
        uncertainties.append(
            ExplanationStatement(
                code="IDENTITY_GATE_REASONS",
                text="同件身份门控记录："
                + "；".join(_REASON_LABELS_ZH.get(code, code) for code in reason_codes)
                + "。",
                evidence_origin="decision_gate",
                claim_scope="DECISION_PROCESS_ONLY",
                details=(("reason_codes", reason_codes),),
            )
        )

    if status == "INSUFFICIENT_CAPTURE":
        uncertainties.append(
            ExplanationStatement(
                code="CAPTURE_INSUFFICIENT",
                text="视角覆盖或采集质量不足会放大排名波动；补拍前不应接受同件身份。",
                evidence_origin="decision_state",
                claim_scope="LIMITATION",
            )
        )
    elif status == "CALIBRATION_REQUIRED":
        uncertainties.append(
            ExplanationStatement(
                code="CALIBRATION_REQUIRED",
                text="当前阈值尚未与独立复拍和库外样本校准绑定；相关排序不能升级为同件身份。",
                evidence_origin="decision_state",
                claim_scope="LIMITATION",
            )
        )
    elif status == "OPEN_SET_NO_MATCH":
        uncertainties.append(
            ExplanationStatement(
                code="OPEN_SET_LIMITATION",
                text="未匹配只说明当前有限样本库没有合格候选，不说明器物不存在或属于某一年代、窑口。",
                evidence_origin="decision_state",
                claim_scope="LIMITATION",
            )
        )
    elif status == "RELATED_REFERENCES_ONLY":
        uncertainties.append(
            ExplanationStatement(
                code="RELATED_ONLY_LIMITATION",
                text="相关候选可作为比较材料，但同件身份门控未通过；风格相似不能替代实物身份。",
                evidence_origin="decision_state",
                claim_scope="LIMITATION",
            )
        )
    elif status == "EXACT_MEDIA_REPLAY":
        uncertainties.append(
            ExplanationStatement(
                code="EXACT_MEDIA_REPLAY_BLOCKED",
                text=(
                    "查询文件与受控库媒体的 SHA-256 完全相同，无法证明系统对新拍实物具有识别能力；"
                    "本次只保留相关排序，必须使用独立拍摄的新图重试。"
                ),
                evidence_origin="media_integrity_gate",
                claim_scope="LIMITATION",
            )
        )
    elif status == "UNKNOWN":
        uncertainties.append(
            ExplanationStatement(
                code="DECISION_STATE_UNKNOWN",
                text="输入未提供可识别的检索状态，系统保留未知并停止推断。",
                evidence_origin="input_state",
                claim_scope="LIMITATION",
            )
        )

    if counterfeit_triggered:
        uncertainties.append(
            ExplanationStatement(
                code="COUNTERFEIT_REFERENCE_SIGNAL",
                text=(
                    "当前查询触发负向参考相似信号；该信号用于交叉复核，"
                    "不能单独证明待测器物为假，也不能反向证明其为真。"
                ),
                evidence_origin="negative_reference_retrieval",
                claim_scope="CROSS_CHECK_SIGNAL_ONLY",
                candidate_id=_text(counterfeit_signal.get("reference_id"), maximum=128),
                details=tuple(
                    (key, value)
                    for key, value in (
                        ("strength", _text(counterfeit_signal.get("strength"), maximum=64)),
                        ("score", _rounded(counterfeit_signal.get("score"))),
                        ("review_status", _text(counterfeit_signal.get("review_status"), maximum=80)),
                    )
                    if value is not None
                ),
            )
        )
    elif counterfeit_status.upper() in {"NOT_RUN", "UNKNOWN"}:
        uncertainties.append(
            ExplanationStatement(
                code="COUNTERFEIT_CROSS_CHECK_NOT_RUN",
                text="负向参考交叉验证未运行或状态未知，报告不得据此描述真伪倾向。",
                evidence_origin="negative_reference_state",
                claim_scope="LIMITATION",
            )
        )

    uncertainties.append(
        ExplanationStatement(
            code="AUTHENTICITY_NOT_ASSESSED",
            text=(
                "本解释只组织图像向量检索、采集标签与有来源的目录记录；"
                "不构成真伪、年代、窑口、作者、价值、文物定级或法律鉴定结论。"
            ),
            evidence_origin="system_boundary",
            claim_scope="LIMITATION",
        )
    )

    citations: list[SourceCitation] = []
    citation_keys: set[tuple[str, str, str]] = set()
    for role, hits in (
        ("catalog_candidate", catalog_items),
        ("counterfeit_reference", negative_items),
    ):
        for hit in hits[:5]:
            citation = _source_citation(hit, reference_role=role)
            if citation is None:
                continue
            key = (citation.reference_role, citation.artifact_id, citation.citation_id)
            if key not in citation_keys:
                citation_keys.add(key)
                citations.append(citation)

    gates = _mapping(same.get("gates"))
    recaptures = _recapture_recommendations(
        query_items,
        status,
        gates,
        counterfeit_triggered,
    )
    decision = DecisionBasis(
        status=status,
        status_label_zh=_STATUS_LABELS_ZH.get(status, "状态未知"),
        summary=_decision_summary(
            status, accepted_artifact_id, related_ids, counterfeit_triggered
        ),
        identity_candidate_accepted=same_accepted,
        accepted_artifact_id=accepted_artifact_id,
        related_accepted=related_accepted,
        related_candidate_ids=related_ids,
        counterfeit_status=counterfeit_status.upper(),
        counterfeit_triggered=counterfeit_triggered,
        reason_codes=reason_codes,
        metrics=tuple(
            (key, value)
            for key, value in (
                ("query_view_count", len(query_items)),
                ("catalog_candidate_count", len(catalog_items)),
                ("top_candidate_id", top_id),
                ("top_score", top_score),
                ("top_coverage", top_coverage),
                ("top_quality_score", top_quality),
                ("runner_up_margin", runner_up_margin),
            )
            if value is not None
        ),
    )
    return ReferenceExplanation(
        shared_observations=tuple(shared),
        differences=tuple(differences),
        source_citations=tuple(citations),
        uncertainties=tuple(uncertainties),
        recommended_recaptures=recaptures,
        decision_basis=decision,
    )


def explain_reference_result(result: Any) -> ReferenceExplanation:
    """Build an explanation directly from a recognition result mapping."""

    payload = _mapping(result)
    return build_reference_explanation(
        query_views=payload.get("query_views"),
        catalog_hits=payload.get("catalog_hits"),
        same_artifact=payload.get("same_artifact"),
        related=payload.get("related"),
        counterfeit=payload.get("counterfeit_cross_check")
        or payload.get("counterfeit_signal"),
        counterfeit_hits=payload.get("counterfeit_hits"),
    )


__all__ = [
    "AUTHENTICITY_NOT_ASSESSED",
    "DecisionBasis",
    "ExplanationStatement",
    "REFERENCE_EXPLANATION_SCHEMA_VERSION",
    "RecaptureRecommendation",
    "ReferenceExplanation",
    "SourceCitation",
    "build_reference_explanation",
    "explain_reference_result",
]
