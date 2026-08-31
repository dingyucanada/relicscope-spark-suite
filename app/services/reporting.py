from __future__ import annotations

import hashlib
import html
from copy import deepcopy
from typing import Any, Dict, Iterable
from uuid import uuid4

from ..store import canonical_json, utc_now


DEMO_DISCLAIMER = (
    "DEMO/SYNTHETIC：本报告包含演示参考资料或仪器回放数据，"
    "仅用于验证 RelicScope 工作流；这是非真实鉴定结论，不可用于交易或法律用途。"
)
SCIENTIFIC_BOUNDARY = (
    "输出仅描述科学测量、证据状态与送检声明的一致性；"
    "不构成真伪裁决、确定断代、作者归属、价格、文物定级或法律意见。"
)


def _raw_file_summary(raw_files: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    result = []
    for item in raw_files:
        metadata = item.get("metadata", {})
        result.append(
            {
                "id": item["id"],
                "filename": item["filename"],
                "mime_type": item["mime_type"],
                "sha256": item["sha256"],
                "modality": metadata.get("modality"),
                "region_id": metadata.get("region_id"),
                "view_code": metadata.get("view_code"),
                "byte_length": metadata.get("byte_length"),
                "source_category": metadata.get("source_category", "USER_UPLOAD"),
                "media_kind": metadata.get("media_kind", "IMAGE"),
                "video_id": metadata.get("video_id"),
                "parent_file_id": metadata.get("parent_file_id"),
                "frame_id": metadata.get("frame_id"),
                "timestamp_ms": metadata.get("timestamp_ms"),
                "derivation_method": metadata.get("derivation_method"),
            }
        )
    return result


def build_report(
    state: Dict[str, Any],
    audit_details: Dict[str, Any],
    raw_files: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    reports = state.get("reports", [])
    video_analyses = deepcopy(state.get("video_analyses", []))
    report = {
        "report_id": f"RPT-{uuid4().hex[:16].upper()}",
        "report_version": len(reports) + 1,
        "covered_session_version": int(state.get("version", 1)),
        "generated_at": utc_now(),
        "artifact": deepcopy(state["artifact"]),
        "claim": deepcopy(state["claim"]),
        "protocol": deepcopy(state["protocol"]),
        "claim_consistency": state.get("claim_consistency", "EVIDENCE_INSUFFICIENT"),
        "authenticity_state": state.get("authenticity_state", "NOT_ASSESSED"),
        "uncertainty": state.get("uncertainty"),
        "status": state.get("status"),
        "raw_files": _raw_file_summary(raw_files),
        "image_analyses": deepcopy(state.get("image_analyses", [])),
        "image_comparisons": deepcopy(state.get("image_comparisons", [])),
        "reference_recognitions": deepcopy(
            state.get("reference_recognitions", [])
        ),
        "latest_reference_recognition": deepcopy(
            state.get("latest_reference_recognition")
        ),
        "videos": deepcopy(state.get("videos", [])),
        "video_analyses": video_analyses,
        "native_video_analyses": deepcopy(state.get("native_video_analyses", [])),
        "media_summary": {
            "image_analysis_count": len(state.get("image_analyses", [])),
            "image_comparison_count": len(state.get("image_comparisons", [])),
            "reference_recognition_count": len(
                state.get("reference_recognitions", [])
            ),
            "registered_video_count": len(state.get("videos", [])),
            "video_analysis_count": len(video_analyses),
            "native_video_analysis_count": len(state.get("native_video_analyses", [])),
            "sampled_frame_count": sum(
                len(item.get("frames", [])) for item in video_analyses
            ),
            "admitted_frame_count": sum(
                sum(
                    frame.get("admission_status") == "ACCEPTED"
                    for frame in item.get("frames", [])
                )
                for item in video_analyses
            ),
            "boundary": (
                "媒体分析描述可见信息、采集质量和候选复核区；"
                "不能替代材料科学检测或专家审查。"
            ),
        },
        "next_best_observations": deepcopy(state.get("next_best_observations", [])),
        "model_runs": deepcopy(state.get("model_runs", [])),
        "knowledge": {
            "version": state.get("knowledge_version"),
            "searches": deepcopy(state.get("knowledge_searches", [])),
        },
        "risk_budgets": deepcopy(state.get("risk_budgets", {})),
        "plan_history": deepcopy(state.get("plan_history", [])),
        "executions": deepcopy(state.get("executions", [])),
        "evidence_graph": deepcopy(state.get("evidence_graph", {})),
        "audit": audit_details,
        "conclusion_boundary": SCIENTIFIC_BOUNDARY,
        "disclaimer": DEMO_DISCLAIMER
        if state.get("demo_data")
        else SCIENTIFIC_BOUNDARY,
        "limitations": [
            "内置知识条目和仪器数据为演示内容，尚未经过机构专家审核。",
            "哈希链可发现记录变化，但不等同于可信时间戳或机构数字签章。",
            "模型观察只描述图像可见信息，不能替代材料检测与专家复核。",
            "目录同件候选与负向参考相似信号均不构成真伪、年代或价值结论。",
            "浏览器抽取的视频帧保留字节、时间戳、父视频与 SHA-256；抽帧仍不等于仪器采集。",
        ],
        "next_steps": [
            "由文物保护与材料分析专家复核冲突证据及适用范围。",
            "如需进一步降低不确定性，转入经批准的外部实验室协议。",
        ],
        "source_category": "DEMO/SYNTHETIC" if state.get("demo_data") else "MIXED",
    }
    integrity_payload = deepcopy(report)
    report["integrity"] = {
        "algorithm": "SHA-256",
        "canonicalization": "sorted compact JSON UTF-8",
        "report_sha256": hashlib.sha256(
            canonical_json(integrity_payload).encode("utf-8")
        ).hexdigest(),
        "latest_audit_hash": audit_details.get("latest_hash"),
        "boundary": "完整性摘要不能证明输入事实真实性或机构签章有效性。",
    }
    return report


def rehash_report(report: Dict[str, Any]) -> Dict[str, Any]:
    payload = deepcopy(report)
    previous_integrity = payload.pop("integrity", {})
    report["integrity"] = {
        **previous_integrity,
        "report_sha256": hashlib.sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest(),
    }
    return report


def report_to_html(report: Dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    file_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item['filename'])}</td><td>{esc(item.get('modality'))}</td>"
            f"<td>{esc(item.get('region_id'))}</td><td><code>{esc(item['sha256'])}</code></td>"
            "</tr>"
            for item in report["raw_files"]
        )
        or '<tr><td colspan="4">无原始文件</td></tr>'
    )
    execution_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item['action']['label'])}</td>"
            f"<td>{esc(item['quality_gate']['passed'])}</td>"
            f"<td>{esc(item['uncertainty_before'])} → {esc(item['uncertainty_after'])}</td>"
            f"<td>{esc(item['result'].get('source_category'))}</td>"
            "</tr>"
            for item in report["executions"]
        )
        or '<tr><td colspan="4">尚无检测动作</td></tr>'
    )
    image_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('id'))}</td>"
            f"<td>{esc(item.get('modality'))} / {esc(item.get('region_id'))}</td>"
            f"<td>{esc(item.get('quality', {}).get('passed'))}</td>"
            f"<td>{esc(', '.join(item.get('quality', {}).get('failed_checks', [])) or '—')}</td>"
            f"<td><code>{esc(item.get('fingerprint', {}).get('id'))}</code></td>"
            "</tr>"
            for item in report.get("image_analyses", [])
        )
        or '<tr><td colspan="5">尚无图像质量结果</td></tr>'
    )
    video_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('id'))}</td>"
            f"<td>{esc(item.get('duration_ms'))} ms</td>"
            f"<td>{esc(item.get('sampling_summary', {}).get('usable_frame_count'))} / {esc(item.get('sampling_summary', {}).get('requested_frame_count'))}</td>"
            f"<td>{esc(item.get('sampling_summary', {}).get('temporal_span_ratio'))}</td>"
            f"<td>{esc(item.get('sampling_summary', {}).get('viewpoint_diversity_score'))}</td>"
            f"<td>{esc(item.get('quality', {}).get('passed'))}</td>"
            "</tr>"
            for item in report.get("video_analyses", [])
        )
        or '<tr><td colspan="6">尚无视频分析</td></tr>'
    )
    native_video_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('id'))}</td>"
            f"<td>{esc(item.get('model'))}</td>"
            f"<td>{esc(item.get('status'))}</td>"
            f"<td>{esc(item.get('media_validation', {}).get('codec'))} · "
            f"{esc(item.get('media_validation', {}).get('width'))}×"
            f"{esc(item.get('media_validation', {}).get('height'))} · "
            f"{esc(item.get('media_validation', {}).get('actual_duration_ms'))} ms</td>"
            f"<td>{esc('；'.join((item.get('result') or {}).get('observations', [])) or '—')}</td>"
            f"<td>{esc('；'.join((item.get('result') or {}).get('temporal_observations', [])) or '—')}</td>"
            f"<td>{esc('；'.join((item.get('result') or {}).get('limitations', [])) or '—')}</td>"
            "</tr>"
            for item in report.get("native_video_analyses", [])
        )
        or '<tr><td colspan="7">尚无原生视频模型观察</td></tr>'
    )
    comparison_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('id'))}</td>"
            f"<td>{esc(item.get('baseline_analysis_id'))}</td>"
            f"<td>{esc(item.get('comparison_analysis_id'))}</td>"
            f"<td>{esc(item.get('status'))}</td>"
            f"<td>{esc(item.get('metrics', {}).get('feature_distance'))}</td>"
            "</tr>"
            for item in report.get("image_comparisons", [])
        )
        or '<tr><td colspan="5">尚无图像变化候选比较</td></tr>'
    )
    recommendation_rows = (
        "".join(
            "<li>"
            f"<strong>{esc(item.get('label'))}</strong> — {esc(item.get('reason'))} · "
            f"{esc(item.get('risk_class'))}"
            "</li>"
            for item in report.get("next_best_observations", [])
        )
        or "<li>尚无下一观察建议</li>"
    )
    model_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('run_id'))}</td>"
            f"<td>{esc(item.get('node_id'))}</td>"
            f"<td>{esc(item.get('role'))}</td>"
            f"<td>{esc(item.get('model'))}</td>"
            f"<td>{esc(item.get('status'))} / {esc(item.get('mode'))}</td>"
            f"<td><code>{esc(item.get('input_hash'))}</code><br><code>{esc(item.get('output_hash'))}</code></td>"
            "</tr>"
            for item in report.get("model_runs", [])
        )
        or '<tr><td colspan="6">尚无模型运行</td></tr>'
    )
    risk_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(region_id)}</td><td>{esc(channel)}</td>"
            f"<td>{esc(values.get('used'))}</td><td>{esc(values.get('reserved'))}</td>"
            f"<td>{esc(values.get('limit'))} {esc(values.get('unit'))}</td>"
            "</tr>"
            for region_id, channels in report.get("risk_budgets", {}).items()
            for channel, values in channels.items()
        )
        or '<tr><td colspan="5">尚无风险预算</td></tr>'
    )
    graph = report.get("evidence_graph", {})
    graph_nodes = graph.get("nodes", [])
    graph_edges = graph.get("edges", [])
    relation_counts: Dict[str, int] = {}
    for edge in graph_edges:
        relation = str(edge.get("relation", "unknown"))
        relation_counts[relation] = relation_counts.get(relation, 0) + 1
    relation_rows = (
        "".join(
            f"<li><code>{esc(relation)}</code>：{esc(count)}</li>"
            for relation, count in sorted(relation_counts.items())
        )
        or "<li>尚无证据关系</li>"
    )
    audit = report.get("audit", {})
    audit_status = "完整" if audit.get("valid") else "验证失败"
    assistant_summary = report.get("assistant_summary", {})
    summary_citations = assistant_summary.get("citation_ids", [])
    references = []
    for search in report["knowledge"]["searches"]:
        references.extend(search.get("results", []))
    reference_rows = (
        "".join(
            "<li>"
            f"<strong>{esc(item.get('title') or item.get('label'))}</strong> — "
            f"{esc(item.get('citation', {}).get('location', {}).get('locator'))} · "
            f"{esc(item.get('data_level') or item.get('source_category'))}"
            "</li>"
            for item in references
        )
        or "<li>尚无知识引用</li>"
    )
    recognition = report.get("latest_reference_recognition") or {}
    same_artifact = recognition.get("same_artifact", {})
    catalog_hits = recognition.get("catalog_hits", [])
    counterfeit_cross_check = recognition.get("counterfeit_cross_check", {})
    reference_library = recognition.get("reference_library", {})
    related_report = recognition.get("related_report", {})
    decision_basis = related_report.get("decision_basis", {})
    recognition_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('artifact_id'))}</td>"
            f"<td>{esc(item.get('metadata', {}).get('display_name') or '—')}</td>"
            f"<td>{esc(round(float(item.get('score', 0)), 4))}</td>"
            f"<td>{esc(round(float(item.get('coverage', 0)), 4))}</td>"
            f"<td>{esc(len(item.get('matched_views', [])))}</td>"
            "</tr>"
            for item in catalog_hits[:5]
        )
        or '<tr><td colspan="5">尚无目录检索候选</td></tr>'
    )
    counterfeit_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('artifact_id'))}</td>"
            f"<td>{esc(item.get('metadata', {}).get('display_name') or '—')}</td>"
            f"<td>{esc(round(float(item.get('score', 0)), 4))}</td>"
            f"<td>{esc(item.get('metadata', {}).get('expert_review', {}).get('review_id') or '—')}</td>"
            f"<td>{esc(item.get('metadata', {}).get('expert_review', {}).get('dispute_status') or '—')}</td>"
            "</tr>"
            for item in counterfeit_cross_check.get("candidates", [])[:5]
        )
        or '<tr><td colspan="5">未列出负向参考候选</td></tr>'
    )
    shared_rows = (
        "".join(
            f"<li>{esc(item.get('text'))} <small>[{esc(item.get('claim_scope'))}]</small></li>"
            for item in related_report.get("shared_observations", [])[:8]
        )
        or "<li>尚无可复核的相关性观察</li>"
    )
    difference_rows = (
        "".join(
            f"<li>{esc(item.get('text'))} <small>[{esc(item.get('claim_scope'))}]</small></li>"
            for item in related_report.get("differences", [])[:6]
        )
        or "<li>尚无直接测得的差异</li>"
    )
    uncertainty_rows = (
        "".join(
            f"<li>{esc(item.get('text'))}</li>"
            for item in related_report.get("uncertainties", [])[:8]
        )
        or "<li>尚无结构化不确定性说明</li>"
    )
    recapture_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('view_label_zh') or item.get('view_code'))}</td>"
            f"<td>{esc(item.get('priority'))}</td>"
            f"<td>{esc(item.get('reason'))}</td>"
            "</tr>"
            for item in related_report.get("recommended_recaptures", [])[:8]
        )
        or '<tr><td colspan="3">当前没有额外补拍建议</td></tr>'
    )
    source_rows = (
        "".join(
            "<tr>"
            f"<td>{esc(item.get('citation_id'))}</td>"
            f"<td>{esc(item.get('institution') or '—')}</td>"
            f"<td>{esc(item.get('accession_number') or '—')}</td>"
            f"<td>{esc(item.get('record_locator') or '—')}</td>"
            f"<td>{esc(item.get('expert_review', {}).get('review_id') or '—')}</td>"
            f"<td>{esc(item.get('rights', {}).get('license_identifier') or '—')}</td>"
            "</tr>"
            for item in related_report.get("source_citations", [])[:10]
        )
        or '<tr><td colspan="6">尚无可列出的来源引用</td></tr>'
    )
    recognition_block = (
        f"""
<h2>本地参考目录识别</h2>
<p><strong>运行：</strong>{esc(recognition.get('recognition_run_id'))}；
<strong>状态：</strong>{esc(recognition.get('decision_status'))}；
<strong>真伪状态：</strong>{esc(report.get('authenticity_state', 'NOT_ASSESSED'))}</p>
<p><strong>同件候选：</strong>{esc(same_artifact.get('artifact_id') or '未接受')}；
检索相似度：{esc(same_artifact.get('score'))}；
Top-2 间隔：{esc(same_artifact.get('runner_up_margin'))}</p>
<p><strong>目录：</strong>{esc(reference_library.get('library_id'))} /
{esc(reference_library.get('library_version'))}；校准：
{esc(reference_library.get('calibration_record_sha256') or '未就绪')}</p>
<table><tr><th>目录 ID</th><th>名称</th><th>检索相似度</th>
<th>视角覆盖</th><th>匹配视角数</th></tr>{recognition_rows}</table>
<h3>负向参考交叉验证</h3><p><strong>状态：</strong>
{esc(counterfeit_cross_check.get('status', 'NOT_RUN'))}；
{esc(counterfeit_cross_check.get('interpretation') or '负向参考交叉验证未运行。')}</p>
<table><tr><th>记录 ID</th><th>名称</th><th>检索相似度</th>
<th>审核记录</th><th>争议状态</th></tr>{counterfeit_rows}</table>
<h3>相关性解释与边界</h3>
<p><strong>决策依据：</strong>{esc(decision_basis.get('summary') or '尚未生成结构化解释')}</p>
<h4>观察依据</h4><ul>{shared_rows}</ul>
<h4>差异与未测项</h4><ul>{difference_rows}</ul>
<h4>不确定性</h4><ul>{uncertainty_rows}</ul>
<h4>建议补拍</h4><table><tr><th>视角</th><th>优先级</th><th>原因</th></tr>{recapture_rows}</table>
<h4>参考来源</h4><table><tr><th>引用 ID</th><th>机构</th><th>馆藏号</th>
<th>记录位置</th><th>审核记录</th><th>许可</th></tr>{source_rows}</table>
<p><strong>结果快照：</strong><code>
{esc(recognition.get('result_snapshot_sha256'))}</code></p>
<p>{esc(recognition.get('limitation') or reference_library.get('boundary') or SCIENTIFIC_BOUNDARY)}</p>
        """
        if recognition
        else "<h2>本地参考目录识别</h2><p>尚未运行目录识别；真伪状态：NOT_ASSESSED。</p>"
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{esc(report["report_id"])}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;color:#172433;margin:40px;line-height:1.65}}
h1,h2{{color:#163b5b}} .warning{{border:2px solid #c76837;background:#fff3e8;padding:16px;font-weight:700}}
.meta{{color:#526477}} table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd5dd;padding:8px;text-align:left}}
code{{font-size:11px;word-break:break-all}} .hash{{word-break:break-all;background:#eef3f6;padding:12px}}
</style></head><body>
<div class="warning">{esc(report["disclaimer"])}</div>
<h1>RelicScope AI 科学证据报告</h1>
<p class="meta">报告 {esc(report["report_id"])} · 会话版本 {esc(report["covered_session_version"])} · 协议 {esc(report.get("protocol", {}).get("id") or report.get("protocol", {}).get("name"))} / {esc(report.get("protocol", {}).get("version"))} · {esc(report["generated_at"])}</p>
<p><strong>数据来源：</strong>{esc(report.get("source_category"))}</p>
<h2>器物与送检声明</h2><p>{esc(report["artifact"]["name"])}</p><pre>{esc(canonical_json(report["claim"]))}</pre>
<h2>科学状态</h2><p>声明一致性：<strong>{esc(report["claim_consistency"])}</strong>；不确定度：{esc(report["uncertainty"])}</p>
<p>真伪状态：<strong>{esc(report.get("authenticity_state", "NOT_ASSESSED"))}</strong></p>
<p>{esc(report["conclusion_boundary"])}</p>
<h2>原始文件</h2><table><tr><th>文件</th><th>模态</th><th>区域</th><th>SHA-256</th></tr>{file_rows}</table>
<h2>图像质量与视觉指纹</h2><table><tr><th>分析</th><th>模态 / 区域</th><th>质量通过</th><th>失败项</th><th>指纹</th></tr>{image_rows}</table>
<h2>视频多帧观察</h2><table><tr><th>分析</th><th>时长</th><th>可用 / 抽样帧</th><th>时间覆盖</th><th>视角多样性</th><th>质量通过</th></tr>{video_rows}</table>
<h2>原生视频模型观察</h2><table><tr><th>分析</th><th>模型</th><th>状态</th><th>服务端媒体校验</th><th>可见观察</th><th>跨视角观察</th><th>限制</th></tr>{native_video_rows}</table>
<p>{esc(report.get("media_summary", {}).get("boundary"))}</p>
<h2>同区域图像变化候选比较</h2><table><tr><th>比较</th><th>基线</th><th>复拍</th><th>状态</th><th>特征距离</th></tr>{comparison_rows}</table>
{recognition_block}
<h2>模型运行追溯</h2><table><tr><th>运行</th><th>节点</th><th>角色</th><th>模型</th><th>状态</th><th>输入 / 输出哈希</th></tr>{model_rows}</table>
<h2>主动检测记录</h2><table><tr><th>动作</th><th>质量通过</th><th>不确定度</th><th>来源</th></tr>{execution_rows}</table>
<h2>区域风险账本</h2><table><tr><th>区域</th><th>通道</th><th>实耗</th><th>预留</th><th>上限</th></tr>{risk_rows}</table>
<h2>本地知识引用</h2><ul>{reference_rows}</ul>
<h2>证据图摘要</h2><p>节点：{esc(len(graph_nodes))}；关系：{esc(len(graph_edges))}</p><ul>{relation_rows}</ul>
<h2>审计链验证</h2><p><strong>{esc(audit_status)}</strong>；事件数：{esc(audit.get("event_count"))}；失败位置：{esc(audit.get("failure_seq") or "—")}；说明：{esc(audit.get("reason") or "—")}</p>
<h2>保守摘要</h2><p>{esc(assistant_summary.get("summary") or "未生成模型摘要；请以结构化证据区块为准。")}</p>
<p><strong>摘要绑定引用：</strong>{esc(", ".join(summary_citations) if summary_citations else "无；摘要未使用本地知识陈述")}</p>
<h2>限制与下一步</h2><ul>{"".join(f"<li>{esc(item)}</li>" for item in report["limitations"])}</ul>
<h3>下一项最佳观察</h3><ul>{recommendation_rows}</ul>
<ul>{"".join(f"<li>{esc(item)}</li>" for item in report["next_steps"])}</ul>
<h2>完整性</h2><div class="hash">SHA-256：{esc(report["integrity"]["report_sha256"])}<br>审计链：{esc(report["integrity"]["latest_audit_hash"])}</div>
<div class="warning">{esc(report["disclaimer"])}</div>
</body></html>"""
