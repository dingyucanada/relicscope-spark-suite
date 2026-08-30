from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import math
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .config import Settings
from .schemas import (
    CreateSessionRequest,
    DemoScenarioRequest,
    ExecuteActionRequest,
    ImageAnalyzeRequest,
    ImageCompareRequest,
    KnowledgeSearchRequest,
    VideoFramesAnalyzeRequest,
)
from .services.active_sensing import (
    ActionAlreadySettled,
    ActionExecutionInProgress,
    DEFAULT_ACTIONS,
    claim_action_for_execution,
    conservative_adapter_failure_result,
    default_budgets,
    plan_and_reserve,
    settle_and_gate,
)
from .services.evidence import (
    add_edge,
    add_node,
    build_initial_graph,
    evidence_graph_sha256,
)
from .services.image_analysis import analyze_image, decode_image
from .services.instruments import ReplayInstrumentAdapter
from .services.knowledge import KnowledgeBase, KnowledgePolicyError
from .services.reporting import build_report, rehash_report, report_to_html
from .services.runtime import runtime_snapshot
from .services.video_analysis import (
    VIDEO_EXTENSIONS,
    detect_video_container,
    dhash_distance,
    inspect_mp4_bytes,
    next_best_observations,
    summarize_frames,
    validate_video_mime,
)
from .services.vlm import (
    OpenAICompatibleClient,
    report_citation_ids,
    validate_reasoner_output,
)
from .store import SessionStore, canonical_json, utc_now


PROTOCOL = {
    "id": "P01-ACTIVE-SENSING-DEMO-V1",
    "version": "1.0.0",
    "name": "古陶瓷主动科学检测演示协议",
    "stop_uncertainty": 0.50,
    "minimum_information_gain": 0.25,
    "minimum_utility": 0.10,
    "status": "DEMO/NOT-VALIDATED-FOR-REAL-ARTIFACTS",
}


def _claim_label(claim: Dict[str, Any]) -> str:
    return f"送检声明：{claim['period']} · {claim['kiln']} · {claim['material']}"


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _safe_payload_hash(value: Any) -> str:
    """Hash even a malformed, non-JSON adapter response without retaining it."""

    try:
        return _sha256_json(value)
    except (TypeError, ValueError):
        return hashlib.sha256(repr(value).encode("utf-8", errors="replace")).hexdigest()


def _model_runtime_trace(result: Dict[str, Any]) -> Dict[str, Any]:
    """Select non-secret, server-returned facts for the durable evidence record."""

    return {
        "configured_model": result.get("configured_model"),
        "model_identity_verified": result.get("model_identity_verified"),
        "provider_request_id": result.get("request_id"),
        "token_usage": result.get("usage", {}),
        "finish_reason": result.get("finish_reason"),
        "model_profile": result.get("model_profile"),
        "model_source": result.get("model_source"),
        "model_revision": result.get("model_revision"),
        "deployment_git_commit": result.get("deployment_git_commit"),
    }


class RelicScopeService:
    def __init__(
        self,
        settings: Settings,
        store: SessionStore,
        knowledge: KnowledgeBase,
        *,
        vision_client: Optional[OpenAICompatibleClient] = None,
        reasoner_client: Optional[OpenAICompatibleClient] = None,
        instrument_adapter: Optional[ReplayInstrumentAdapter] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.knowledge = knowledge
        self.vision_client = vision_client or OpenAICompatibleClient(
            settings.vision_base_url,
            settings.vision_api_key,
            settings.vision_model,
            settings.model_timeout_seconds,
            model_profile=settings.model_profile,
            model_source=settings.vision_model_source,
            model_revision=settings.vision_model_revision,
            deployment_git_commit=settings.deployment_git_commit,
        )
        self.reasoner_client = reasoner_client or OpenAICompatibleClient(
            settings.reasoner_base_url,
            settings.reasoner_api_key,
            settings.reasoner_model,
            settings.model_timeout_seconds,
            model_profile=settings.model_profile,
            model_source=(
                settings.vision_model_source
                if settings.runtime_mode == "single-spark"
                else settings.reasoner_model
            ),
            model_revision=(
                settings.vision_model_revision
                if settings.runtime_mode == "single-spark"
                else "unknown"
            ),
            deployment_git_commit=settings.deployment_git_commit,
        )
        self.model_semaphore = asyncio.Semaphore(settings.model_max_concurrency)
        self.instrument_adapter = instrument_adapter or ReplayInstrumentAdapter()

    @staticmethod
    def provenance_summary(state: Dict[str, Any]) -> Dict[str, Any]:
        raw_categories = sorted(
            {
                str(item.get("source_category", "UNKNOWN"))
                for item in state.get("raw_files", [])
            }
        )
        instrument_categories = sorted(
            {
                str(item.get("result", {}).get("source_category", "UNKNOWN"))
                for item in state.get("executions", [])
            }
        )
        knowledge_levels = sorted(
            {
                str(result.get("data_level", "UNKNOWN"))
                for search in state.get("knowledge_searches", [])
                for result in search.get("results", [])
            }
        )
        model_modes = sorted(
            {str(item.get("mode", "UNKNOWN")) for item in state.get("model_runs", [])}
        )
        contains_user_upload = "USER_UPLOAD" in raw_categories
        contains_demo = bool(state.get("demo_data")) or any(
            value == "DEMO/SYNTHETIC"
            for value in instrument_categories + knowledge_levels
        )
        contains_real_instrument = any(
            not bool(item.get("result", {}).get("demo_data", True))
            and item.get("result", {}).get("source_category") == "REAL_INSTRUMENT"
            for item in state.get("executions", [])
        )
        overall = (
            "MIXED/DEMO-SYNTHETIC"
            if contains_user_upload and contains_demo
            else "DEMO/SYNTHETIC"
            if contains_demo
            else "USER_OR_INSTRUMENT_DATA/UNVERIFIED"
        )
        return {
            "overall": overall,
            "display_badge": (
                "DEMO/SYNTHETIC · 非真实鉴定结论"
                if contains_demo
                else "USER/INSTRUMENT DATA · 尚未经专家审核"
            ),
            "workflow_use": (
                "WORKFLOW_VALIDATION_ONLY"
                if contains_demo
                else "SCIENTIFIC_REVIEW_REQUIRED"
            ),
            "contains_demo_synthetic": contains_demo,
            "contains_user_upload": contains_user_upload,
            "contains_real_instrument_data": contains_real_instrument,
            "raw_input_categories": raw_categories,
            "instrument_result_categories": instrument_categories,
            "knowledge_data_levels": knowledge_levels,
            "model_execution_modes": model_modes,
            "boundary": (
                "用户上传不等同于真实仪器采集；回放、演示知识和派生结果"
                "必须保留各自来源标识。"
            ),
        }

    def integrity_manifest(
        self,
        session_id: str,
        *,
        state: Optional[Dict[str, Any]] = None,
        audit: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        current = state or self.store.get_session(session_id)
        verification = audit or self.store.verify_audit_chain_details(session_id)
        graph_hash = evidence_graph_sha256(current.get("evidence_graph", {}))
        state_files = {
            str(item.get("id")): item for item in current.get("raw_files", [])
        }
        upload_root = self.settings.upload_dir.resolve()
        raw_file_checks = []
        for record in self.store.list_raw_files(session_id):
            file_id = str(record["id"])
            state_record = state_files.get(file_id, {})
            stored_path = Path(record["path"]).resolve()
            reasons = []
            try:
                stored_path.relative_to(upload_root)
            except ValueError:
                reasons.append("PATH_OUTSIDE_UPLOAD_ROOT")
            actual_hash = None
            if not reasons:
                if not stored_path.is_file():
                    reasons.append("FILE_MISSING")
                else:
                    digest = hashlib.sha256()
                    with stored_path.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                    actual_hash = digest.hexdigest()
            expected_hash = str(state_record.get("sha256") or "")
            catalog_hash = str(record.get("sha256") or "")
            if not expected_hash or expected_hash != catalog_hash:
                reasons.append("STATE_AND_FILE_CATALOG_HASH_MISMATCH")
            if actual_hash is not None and actual_hash != expected_hash:
                reasons.append("FILE_BYTES_HASH_MISMATCH")
            raw_file_checks.append(
                {
                    "file_id": file_id,
                    "expected_sha256": expected_hash or None,
                    "actual_sha256": actual_hash,
                    "valid": not reasons,
                    "reasons": reasons,
                }
            )
        checked_file_ids = {item["file_id"] for item in raw_file_checks}
        missing_catalog_ids = sorted(set(state_files) - checked_file_ids)
        for file_id in missing_catalog_ids:
            raw_file_checks.append(
                {
                    "file_id": file_id,
                    "expected_sha256": state_files[file_id].get("sha256"),
                    "actual_sha256": None,
                    "valid": False,
                    "reasons": ["FILE_CATALOG_RECORD_MISSING"],
                }
            )
        raw_files_valid = all(item["valid"] for item in raw_file_checks)
        evidence_payload = {
            "session_id": session_id,
            "session_version": int(current.get("version", 0)),
            "claim_consistency": current.get("claim_consistency"),
            "uncertainty": current.get("uncertainty"),
            "evidence_graph_sha256": graph_hash,
            "raw_file_sha256": sorted(
                str(item.get("sha256"))
                for item in current.get("raw_files", [])
                if item.get("sha256")
            ),
            "knowledge_snapshot_sha256": sorted(
                str(item.get("snapshot_hash"))
                for item in current.get("knowledge_searches", [])
                if item.get("snapshot_hash")
            ),
            "instrument_result_sha256": sorted(
                str(item.get("result", {}).get("result_hash"))
                for item in current.get("executions", [])
                if item.get("result", {}).get("result_hash")
            ),
            "report_sha256": sorted(
                str(item.get("integrity", {}).get("report_sha256"))
                for item in current.get("reports", [])
                if item.get("integrity", {}).get("report_sha256")
            ),
        }
        evidence_bundle_hash = _sha256_json(evidence_payload)
        binding_payload = {
            "session_id": session_id,
            "session_state_sha256": verification.get("session_state_sha256"),
            "audit_tail_sha256": verification.get("latest_hash"),
            "evidence_bundle_sha256": evidence_bundle_hash,
        }
        state_bound = bool(
            verification.get("state_integrity_bound")
            and verification.get("state_integrity_valid")
        )
        verification_valid = bool(verification.get("valid") and state_bound)
        return {
            "valid": bool(verification_valid and raw_files_valid),
            "verification_strength": verification.get(
                "verification_strength", "AUDIT_CHAIN_ONLY_LEGACY"
            ),
            "algorithm": "SHA-256",
            "canonicalization": "sorted compact JSON UTF-8",
            "session_version": evidence_payload["session_version"],
            "audit_event_count": verification.get("event_count", 0),
            "audit_tail_sha256": verification.get("latest_hash"),
            "session_state_sha256": verification.get("session_state_sha256"),
            "evidence_graph_sha256": graph_hash,
            "evidence_bundle_sha256": evidence_bundle_hash,
            "binding_sha256": _sha256_json(binding_payload),
            "raw_files": {
                "valid": raw_files_valid,
                "checked_count": len(raw_file_checks),
                "items": raw_file_checks,
            },
            "failure_reason": (
                None
                if verification_valid and raw_files_valid
                else verification.get("reason")
                if not verification_valid
                else "raw file integrity failure"
            ),
            "boundary": (
                "完整性哈希可发现记录变化；它不证明输入事实真实，"
                "也不等同于机构数字签章或可信时间戳。"
            ),
        }

    def envelope(self, session_id: str) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        audit = self.store.verify_audit_chain_details(session_id)
        return {
            "session": state,
            "audit_verified": audit["valid"],
            "audit_event_count": audit["event_count"],
            "integrity": self.integrity_manifest(session_id, state=state, audit=audit),
            "data_provenance": self.provenance_summary(state),
        }

    def create_session(self, request: CreateSessionRequest) -> Dict[str, Any]:
        session_id = f"RS-{uuid4().hex[:16].upper()}"
        claim = request.claim.model_dump()
        graph = build_initial_graph(
            session_id, request.artifact_name, _claim_label(claim)
        )
        state: Dict[str, Any] = {
            "id": session_id,
            "artifact": {
                "name": request.artifact_name,
                "operator": request.operator,
                "institution": request.institution,
                "artifact_type": "陶瓷",
            },
            "claim": claim,
            "regions": [{"id": "R1", "label": "R1 蓝色纹饰区"}],
            "protocol": deepcopy(PROTOCOL),
            "status": "ready",
            "next_step": "上传器物图像或规划下一检测",
            "uncertainty": 0.85,
            "claim_consistency": "EVIDENCE_INSUFFICIENT",
            "demo_data": True,
            "source_category": "DEMO/SYNTHETIC",
            "disclaimer": "DEMO/SYNTHETIC；仅验证科学工作流，不构成真实鉴定结论。",
            "risk_budgets": default_budgets(),
            "candidate_actions": deepcopy(DEFAULT_ACTIONS),
            "retry_blocked": [],
            "current_action_id": None,
            "current_action_run_id": None,
            "current_action_status": None,
            "execution_claim": None,
            "plan_history": [],
            "executions": [],
            "raw_files": [],
            "image_analyses": [],
            "image_comparisons": [],
            "videos": [],
            "video_analyses": [],
            "native_video_analyses": [],
            "next_best_observations": [],
            "model_runs": [],
            "knowledge_version": self.knowledge.version,
            "knowledge_searches": [],
            "reports": [],
            "evidence_graph": graph,
            "runtime": {
                "mode": self.settings.runtime_mode,
                "gateway_node": self.settings.node_id,
                "compute_node": self.settings.compute_node_id,
                "topology": "APPLICATION_LEVEL_INDEPENDENT_SERVICES",
                "offline": self.settings.offline_mode,
                "model_profile": self.settings.model_profile,
                "model_source": self.settings.vision_model_source,
                "model_revision": self.settings.vision_model_revision,
                "served_model": self.settings.vision_model,
                "deployment_git_commit": self.settings.deployment_git_commit,
            },
        }
        self.store.create_session(state)
        return self.envelope(session_id)

    @staticmethod
    def _require_region(state: Dict[str, Any], region_id: str) -> None:
        if region_id not in {item["id"] for item in state.get("regions", [])}:
            raise ValueError(f"unknown region: {region_id}")

    def _knowledge_snapshot(
        self,
        *,
        state: Dict[str, Any],
        text: str,
        limit: int,
        space: str,
        visual_feature_vector: Optional[list[float]] = None,
    ) -> Dict[str, Any]:
        declared_material = state["claim"].get("material", "瓷")
        normalized_material = "瓷" if "瓷" in declared_material else declared_material
        attributes = {
            "artifact_type": state["artifact"].get("artifact_type", "陶瓷"),
            "material": normalized_material,
        }
        if visual_feature_vector is not None:
            attributes["modality"] = "RGB"
        try:
            return self.knowledge.search(
                text=text,
                attributes=attributes,
                visual_feature_vector=visual_feature_vector,
                knowledge_spaces=[space],
                access_scope="demo-public",
                purpose="product_demo",
                minimum_score=0.08 if visual_feature_vector is not None else 0.20,
                limit=limit,
            )
        except KnowledgePolicyError as exc:
            raise ValueError(str(exc)) from exc

    @staticmethod
    def _image_interpretation(
        analysis: Dict[str, Any], vision_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        metrics = analysis["metrics"]
        quality = analysis["quality_gate"]
        visible_observations = list(
            vision_result.get("output", {}).get("observations", [])
            if vision_result.get("available")
            else []
        )
        if not visible_observations:
            visible_observations = [
                (
                    f"已对 {metrics['width']}×{metrics['height']} 像素输入计算曝光、"
                    "清晰度、边缘分布与视觉指纹。"
                ),
                (
                    f"蓝色像素候选比例为 {metrics['blue_ratio']:.1%}；"
                    "该数值属于图像统计，不能解释为颜料或元素成分。"
                ),
            ]
        guidance = []
        guidance_by_check = {
            "resolution": "提高拍摄分辨率并确保器物主体完整入框。",
            "exposure": "使用稳定漫射光并锁定曝光。",
            "clipping": "降低高光反射或补足暗部，避免像素剪切。",
            "sharpness": "使用三脚架、定时快门或更快快门重新拍摄。",
            "dynamic_range": "调整光照角度或曝光，使釉面与纹饰层次可分辨。",
        }
        for failed in quality.get("failed_checks", []):
            if failed in guidance_by_check:
                guidance.append(guidance_by_check[failed])
        if not guidance:
            guidance.append("保留原始文件，并补拍口沿、腹部、底足与款识的同尺度视角。")
        recommendations = []
        if not quality["passed"]:
            recommendations.append(
                {
                    "id": "OBS-RGB-QUALITY",
                    "priority": 1,
                    "label": "先补拍质量合格的可见光图像",
                    "reason": "当前输入未通过全部图像质量门控。",
                    "modality": "RGB",
                    "risk_class": "NON_CONTACT",
                }
            )
        recommendations.extend(
            [
                {
                    "id": "OBS-MULTI-VIEW",
                    "priority": 2,
                    "label": "采集环绕视频与关键视角",
                    "reason": "单张图像无法覆盖器形、底足、口沿和款识。",
                    "modality": "RGB_VIDEO",
                    "risk_class": "NON_CONTACT",
                },
                {
                    "id": "OBS-UV-NIR",
                    "priority": 3,
                    "label": "复核 UV / NIR 表面响应",
                    "reason": "以受控非接触成像复核可见光候选区域。",
                    "modality": "UV_NIR",
                    "risk_class": "NON_CONTACT_CONTROLLED_LIGHT",
                },
                {
                    "id": "OBS-MATERIAL-SCIENCE",
                    "priority": 4,
                    "label": "由专家审批材料科学检测",
                    "reason": "图像不能给出元素、分子结构、内部结构或热释光信息。",
                    "modality": "SCIENTIFIC_INSTRUMENT",
                    "risk_class": "EXPERT_APPROVAL_REQUIRED",
                },
            ]
        )
        return {
            "visible_observations": visible_observations,
            "acquisition_guidance": guidance,
            "next_best_observations": recommendations,
            "next_best_observation": recommendations[0],
            "conclusion_boundary": (
                "仅描述图像可见信息和采集质量；不据此判断真伪、年代、"
                "窑口、材料成分、价值或法律状态。"
            ),
        }

    async def analyze_image(
        self, session_id: str, request: ImageAnalyzeRequest
    ) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        self._require_region(state, request.region_id)
        if len(request.image_base64) > (self.settings.max_upload_bytes * 4 // 3 + 16):
            raise ValueError("image exceeds configured upload limit")
        try:
            raw_bytes = base64.b64decode(request.image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("invalid base64 image payload") from exc
        if len(raw_bytes) > self.settings.max_upload_bytes:
            raise ValueError("image exceeds configured upload limit")
        decoded = decode_image(raw_bytes)
        if decoded.detected_mime == "application/octet-stream":
            raise ValueError(f"unsupported image format: {decoded.detected_format}")
        if decoded.detected_mime != request.mime_type:
            raise ValueError(
                f"declared MIME {request.mime_type} does not match {decoded.detected_mime}"
            )

        file_id = f"FILE-{uuid4().hex[:16].upper()}"
        safe_name = Path(request.filename).name.replace("\x00", "") or "artifact-image"
        extension = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "TIFF": ".tiff"}[
            decoded.detected_format
        ]
        session_dir = self.settings.upload_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        stored_path = session_dir / f"{file_id}{extension}"
        stored_path.write_bytes(raw_bytes)

        analysis = analyze_image(decoded.image, decoded.sha256)
        analysis_id = f"IMG-{uuid4().hex[:16].upper()}"
        model_run_id = f"MRUN-{uuid4().hex[:16].upper()}"
        model_started = utc_now()
        async with self.model_semaphore:
            vision_result = await self.vision_client.vision_observe(
                f"data:{decoded.detected_mime};base64,{request.image_base64}",
                {
                    "session_id": session_id,
                    "file_id": file_id,
                    "region_id": request.region_id,
                    "quality_gate": analysis["quality_gate"],
                },
            )
        model_completed = utc_now()
        model_run = {
            "run_id": model_run_id,
            "role": "multimodal_observation",
            "node_id": (
                self.settings.compute_node_id
                if vision_result.get("available")
                else self.settings.node_id
            ),
            "model": vision_result.get("model", self.settings.vision_model),
            "template_hash": vision_result.get("prompt_hash"),
            "started_at": model_started,
            "completed_at": model_completed,
            "latency_ms": vision_result.get("latency_ms", 0),
            "status": "SUCCESS" if vision_result.get("available") else "DEGRADED",
            "mode": vision_result.get("mode", "deterministic_fallback"),
            "input_refs": [file_id, request.region_id],
            "input_hash": decoded.sha256,
            "output_hash": vision_result.get("output_hash"),
            "output_ref": analysis_id if vision_result.get("available") else None,
            "error_category": vision_result.get("error"),
            "output": vision_result.get("output"),
            **_model_runtime_trace(vision_result),
        }
        knowledge_snapshot = self._knowledge_snapshot(
            state=state,
            text=(
                f"{state['claim']['material']} {state['claim']['kiln']} "
                "可见纹饰、釉面与材料响应参考"
            ),
            limit=3,
            space="demo",
            visual_feature_vector=analysis["fingerprint"]["feature_vector"],
        )
        interpretation = self._image_interpretation(analysis, vision_result)
        analysis_record = {
            "id": analysis_id,
            "file_id": file_id,
            "region_id": request.region_id,
            "modality": request.modality,
            "source_category": "DERIVED_MEASUREMENT",
            "quality": analysis["quality_gate"],
            "metrics": analysis["metrics"],
            "salient_regions": analysis["salient_regions"],
            "fingerprint": analysis["fingerprint"],
            "model_observation": vision_result,
            "knowledge_snapshot_hash": knowledge_snapshot["snapshot_hash"],
            **interpretation,
        }
        file_summary = {
            "id": file_id,
            "filename": safe_name,
            "mime_type": decoded.detected_mime,
            "sha256": decoded.sha256,
            "byte_length": len(raw_bytes),
            "modality": request.modality,
            "region_id": request.region_id,
            "received_at": utc_now(),
            "source_category": "USER_UPLOAD",
            "media_kind": "IMAGE",
        }
        file_record = {
            "id": file_id,
            "filename": safe_name,
            "mime_type": decoded.detected_mime,
            "sha256": decoded.sha256,
            "path": str(stored_path),
            "metadata": file_summary,
            "created_at": file_summary["received_at"],
        }

        def updater(current: Dict[str, Any]):
            self._require_region(current, request.region_id)
            current.setdefault("raw_files", []).append(file_summary)
            current.setdefault("image_analyses", []).append(analysis_record)
            current.setdefault("model_runs", []).append(model_run)
            current.setdefault("knowledge_searches", []).append(knowledge_snapshot)
            current["next_best_observations"] = deepcopy(
                interpretation["next_best_observations"]
            )
            current["next_step"] = "复核本地知识引用或规划下一检测"
            if vision_result.get("output", {}).get("ood_risk") == "HIGH":
                current["claim_consistency"] = "ESCALATE"
                current["status"] = "review_required"

            graph = current["evidence_graph"]
            raw_node = f"raw:{session_id}:{file_id}"
            observation_node = f"observation:{session_id}:{analysis_id}"
            model_node = f"model-run:{session_id}:{model_run_id}"
            add_node(
                graph,
                raw_node,
                safe_name,
                "raw",
                "accepted",
                {"sha256": decoded.sha256, "data_level": "RAW_CAPTURE"},
            )
            add_edge(
                graph,
                raw_node,
                f"region:{session_id}:{request.region_id}",
                "measured_at",
            )
            add_node(
                graph,
                observation_node,
                "RGB 图像质量与视觉指纹",
                "observation",
                "accepted" if analysis["quality_gate"]["passed"] else "rejected",
                {
                    "quality_gate": analysis["quality_gate"],
                    "fingerprint_id": analysis["fingerprint"]["id"],
                    "data_level": "DERIVED_MEASUREMENT",
                },
            )
            add_edge(graph, observation_node, raw_node, "derived_from")
            add_node(
                graph,
                model_node,
                "本地多模态模型观察",
                "model_run",
                "success" if vision_result.get("available") else "degraded",
                {key: value for key, value in model_run.items() if key != "output"},
            )
            add_edge(graph, model_node, raw_node, "analyzes")
            if vision_result.get("available"):
                add_edge(graph, observation_node, model_node, "produced_by")
            for item in knowledge_snapshot.get("results", []):
                reference_node = f"reference:{item['source_id']}"
                add_node(
                    graph,
                    reference_node,
                    item["title"],
                    "reference",
                    "demo",
                    {
                        "citation": item["citation"],
                        "score": item["score"],
                        "data_level": item["data_level"],
                    },
                )
                add_edge(graph, observation_node, reference_node, "cites")
            return current, {
                "file_id": file_id,
                "sha256": decoded.sha256,
                "byte_length": len(raw_bytes),
                "mime_type": decoded.detected_mime,
                "modality": request.modality,
                "region_id": request.region_id,
                "quality_gate": analysis["quality_gate"],
                "model_run": {
                    key: value for key, value in model_run.items() if key != "output"
                },
                "knowledge": self.knowledge.audit_payload(knowledge_snapshot),
            }

        try:
            self.store.atomic_register_raw_file(session_id, file_record, updater)
        except Exception:
            stored_path.unlink(missing_ok=True)
            raise
        return self.envelope(session_id)

    def compare_images(
        self, session_id: str, request: ImageCompareRequest
    ) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        analyses = {item["id"]: item for item in state.get("image_analyses", [])}
        baseline = analyses.get(request.baseline_analysis_id)
        comparison = analyses.get(request.comparison_analysis_id)
        if baseline is None or comparison is None:
            raise ValueError("unknown image analysis identifier")
        if baseline.get("region_id") != comparison.get("region_id"):
            raise ValueError("image comparison requires the same region")
        if baseline.get("modality") != comparison.get("modality"):
            raise ValueError("image comparison requires the same modality")

        baseline_quality = bool(baseline.get("quality", {}).get("passed"))
        comparison_quality = bool(comparison.get("quality", {}).get("passed"))
        distance = dhash_distance(
            baseline["fingerprint"]["dhash"], comparison["fingerprint"]["dhash"]
        )
        dhash_distance_normalized = distance / 64.0
        first_vector = baseline["fingerprint"]["feature_vector"]
        second_vector = comparison["fingerprint"]["feature_vector"]
        feature_distance = math.sqrt(
            sum(
                (first - second) ** 2
                for first, second in zip(first_vector, second_vector)
            )
            / max(len(first_vector), 1)
        )
        reasons = []
        if not baseline_quality or not comparison_quality:
            status = "NOT_COMPARABLE"
            reasons.append("至少一个输入未通过图像质量门控")
        elif dhash_distance_normalized > 0.35:
            status = "NOT_COMPARABLE"
            reasons.append("构图或视角差异过大，无法进行保守的同角度比较")
        elif dhash_distance_normalized <= 0.08 and feature_distance <= 0.10:
            status = "STABLE_WITHIN_CAPTURE_TOLERANCE"
            reasons.append("视觉指纹差异处于演示阈值内")
        else:
            status = "VISIBLE_CHANGE_CANDIDATE"
            reasons.append("跨次采集出现需要复核的可见差异候选")

        comparison_id = f"CMP-{uuid4().hex[:16].upper()}"
        record = {
            "id": comparison_id,
            "baseline_analysis_id": baseline["id"],
            "comparison_analysis_id": comparison["id"],
            "region_id": baseline["region_id"],
            "modality": baseline["modality"],
            "status": status,
            "metrics": {
                "dhash_distance_bits": distance,
                "dhash_distance_normalized": round(dhash_distance_normalized, 4),
                "feature_distance": round(feature_distance, 4),
                "brightness_delta": round(
                    float(comparison["metrics"]["brightness_mean"])
                    - float(baseline["metrics"]["brightness_mean"]),
                    2,
                ),
                "sharpness_delta": round(
                    float(comparison["metrics"]["sharpness_score"])
                    - float(baseline["metrics"]["sharpness_score"]),
                    2,
                ),
            },
            "reasons": reasons,
            "next_best_observation": {
                "label": "按同机位、同焦距、同光照和同色卡条件复拍",
                "reason": "先控制采集差异，再由专家判断可见变化候选是否值得升级检测。",
                "risk_class": "NON_CONTACT",
            },
            "conclusion_boundary": (
                "该比较只反映两次图像采集的可见差异；不解释为劣化、修复、"
                "真伪、年代或材料变化。"
            ),
            "source_category": "DERIVED_MEASUREMENT",
            "created_at": utc_now(),
        }

        def updater(current: Dict[str, Any]):
            current.setdefault("image_comparisons", []).append(record)
            graph = current["evidence_graph"]
            node_id = f"observation:{session_id}:{comparison_id}"
            add_node(
                graph,
                node_id,
                "同区域图像变化候选比较",
                "observation",
                status.lower(),
                {
                    "status": status,
                    "dhash_distance_normalized": round(dhash_distance_normalized, 4),
                    "feature_distance": round(feature_distance, 4),
                    "data_level": "DERIVED_MEASUREMENT",
                },
            )
            add_edge(
                graph,
                node_id,
                f"observation:{session_id}:{baseline['id']}",
                "derived_from",
            )
            add_edge(
                graph,
                node_id,
                f"observation:{session_id}:{comparison['id']}",
                "derived_from",
            )
            return current, {
                "comparison_id": comparison_id,
                "baseline_analysis_id": baseline["id"],
                "comparison_analysis_id": comparison["id"],
                "status": status,
                "metrics": record["metrics"],
            }

        self.store.atomic_update(session_id, "IMAGE_ANALYSES_COMPARED", updater)
        return self.envelope(session_id)

    async def register_video(
        self,
        session_id: str,
        upload: Any,
        *,
        modality: str,
        region_id: str,
        duration_ms: Optional[int],
        capture_note: str,
    ) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        self._require_region(state, region_id)
        if modality not in {"RGB_VIDEO", "UV_VIDEO", "NIR_VIDEO"}:
            raise ValueError("unsupported video modality")
        if duration_ms is not None and not 1 <= duration_ms <= 86_400_000:
            raise ValueError("video duration must be between 1 ms and 24 hours")
        capture_note = " ".join((capture_note or "").split())
        if len(capture_note) > 240:
            raise ValueError("capture note exceeds 240 characters")

        video_id = f"VID-{uuid4().hex[:16].upper()}"
        file_id = f"FILE-{uuid4().hex[:16].upper()}"
        safe_name = Path(getattr(upload, "filename", "") or "artifact-video").name
        safe_name = safe_name.replace("\x00", "")[:180] or "artifact-video"
        declared_mime = str(getattr(upload, "content_type", "") or "")
        session_dir = self.settings.upload_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = session_dir / f".{file_id}.uploading"
        stored_path: Optional[Path] = None
        digest = hashlib.sha256()
        byte_length = 0
        prefix = bytearray()
        try:
            with temporary_path.open("xb") as handle:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    byte_length += len(chunk)
                    if byte_length > self.settings.max_video_bytes:
                        raise ValueError("video exceeds configured upload limit")
                    if len(prefix) < 4096:
                        prefix.extend(chunk[: 4096 - len(prefix)])
                    digest.update(chunk)
                    handle.write(chunk)
            if byte_length == 0:
                raise ValueError("empty video")
            detected_container = detect_video_container(bytes(prefix))
            normalized_mime = validate_video_mime(declared_mime, detected_container)
            stored_path = (
                session_dir / f"{file_id}{VIDEO_EXTENSIONS[detected_container]}"
            )
            temporary_path.replace(stored_path)
            video_record = {
                "id": video_id,
                "file_id": file_id,
                "filename": safe_name,
                "mime_type": normalized_mime,
                "container": detected_container,
                "sha256": digest.hexdigest(),
                "byte_length": byte_length,
                "modality": modality,
                "region_id": region_id,
                "duration_ms": duration_ms,
                "duration_source": "CLIENT_DECLARED_UNVERIFIED",
                "capture_note": capture_note,
                "status": "REGISTERED",
                "frame_extraction": "CLIENT_SIDE_BROWSER",
                "source_category": "USER_UPLOAD",
                "registered_at": utc_now(),
            }
            file_summary = {
                "id": file_id,
                "filename": safe_name,
                "mime_type": normalized_mime,
                "sha256": digest.hexdigest(),
                "byte_length": byte_length,
                "modality": modality,
                "region_id": region_id,
                "received_at": video_record["registered_at"],
                "source_category": "USER_UPLOAD",
                "media_kind": "VIDEO",
                "video_id": video_id,
                "duration_ms": duration_ms,
            }
            file_record = {
                "id": file_id,
                "filename": safe_name,
                "mime_type": normalized_mime,
                "sha256": digest.hexdigest(),
                "path": str(stored_path),
                "metadata": file_summary,
                "created_at": video_record["registered_at"],
            }

            def updater(current: Dict[str, Any]):
                self._require_region(current, region_id)
                current.setdefault("raw_files", []).append(file_summary)
                current.setdefault("videos", []).append(video_record)
                current["next_step"] = "在浏览器抽取代表帧并运行多帧分析"
                graph = current["evidence_graph"]
                raw_node = f"raw:{session_id}:{file_id}"
                add_node(
                    graph,
                    raw_node,
                    safe_name,
                    "raw",
                    "accepted",
                    {
                        "sha256": digest.hexdigest(),
                        "data_level": "RAW_CAPTURE",
                        "media_kind": "VIDEO",
                        "container": detected_container,
                    },
                )
                add_edge(
                    graph, raw_node, f"region:{session_id}:{region_id}", "measured_at"
                )
                return current, {
                    "video_id": video_id,
                    "file_id": file_id,
                    "sha256": digest.hexdigest(),
                    "byte_length": byte_length,
                    "mime_type": normalized_mime,
                    "container": detected_container,
                    "modality": modality,
                    "region_id": region_id,
                    "duration_ms": duration_ms,
                    "frame_extraction": "CLIENT_SIDE_BROWSER",
                }

            self.store.atomic_register_raw_file(
                session_id,
                file_record,
                updater,
                event_type="VIDEO_REGISTERED",
            )
        except Exception:
            temporary_path.unlink(missing_ok=True)
            if stored_path is not None:
                stored_path.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
        return self.envelope(session_id)

    async def analyze_video_frames(
        self,
        session_id: str,
        video_id: str,
        request: VideoFramesAnalyzeRequest,
    ) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        video = next(
            (item for item in state.get("videos", []) if item.get("id") == video_id),
            None,
        )
        if video is None:
            raise ValueError("unknown video identifier")
        self._require_region(state, video["region_id"])
        if len(request.frames) > self.settings.max_video_frames:
            raise ValueError("video frame count exceeds configured limit")
        registered_duration = video.get("duration_ms")
        if registered_duration is not None:
            tolerance = max(1000, int(registered_duration * 0.05))
            if abs(int(registered_duration) - request.duration_ms) > tolerance:
                raise ValueError(
                    "analyzed duration does not match registered video metadata"
                )

        analysis_id = f"VAN-{uuid4().hex[:16].upper()}"
        frame_directory = self.settings.upload_dir / session_id / "frames" / video_id
        frame_directory.mkdir(parents=True, exist_ok=True)
        frame_entries: List[Dict[str, Any]] = []
        created_paths: List[Path] = []
        frame_file_records: List[Dict[str, Any]] = []
        try:
            for input_frame in request.frames:
                encoded = input_frame.image_base64
                maximum_encoded = self.settings.max_frame_bytes * 4 // 3 + 16
                if len(encoded) > maximum_encoded:
                    raise ValueError("video frame exceeds configured frame limit")
                try:
                    raw_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("invalid base64 video frame payload") from exc
                if len(raw_bytes) > self.settings.max_frame_bytes:
                    raise ValueError("video frame exceeds configured frame limit")
                decoded = decode_image(raw_bytes)
                if decoded.detected_mime == "application/octet-stream":
                    raise ValueError("unsupported video frame image format")
                if decoded.detected_mime != input_frame.mime_type:
                    raise ValueError(
                        f"declared frame MIME {input_frame.mime_type} does not match "
                        f"{decoded.detected_mime}"
                    )
                frame_id = f"FRM-{uuid4().hex[:16].upper()}"
                frame_file_id = f"FILE-{uuid4().hex[:16].upper()}"
                extension = {
                    "JPEG": ".jpg",
                    "PNG": ".png",
                    "WEBP": ".webp",
                    "TIFF": ".tiff",
                }[decoded.detected_format]
                stored_path = frame_directory / f"{frame_file_id}{extension}"
                stored_path.write_bytes(raw_bytes)
                created_paths.append(stored_path)
                derived_at = utc_now()
                file_summary = {
                    "id": frame_file_id,
                    "filename": f"{video_id}-{input_frame.timestamp_ms}ms{extension}",
                    "mime_type": decoded.detected_mime,
                    "sha256": decoded.sha256,
                    "byte_length": len(raw_bytes),
                    "modality": video["modality"],
                    "region_id": video["region_id"],
                    "received_at": derived_at,
                    "source_category": "DERIVED_FRAME",
                    "media_kind": "VIDEO_FRAME",
                    "video_id": video_id,
                    "parent_file_id": video["file_id"],
                    "frame_id": frame_id,
                    "timestamp_ms": input_frame.timestamp_ms,
                    "derivation_method": request.sampling_strategy,
                }
                frame_file_records.append(
                    {
                        "id": frame_file_id,
                        "filename": file_summary["filename"],
                        "mime_type": decoded.detected_mime,
                        "sha256": decoded.sha256,
                        "path": str(stored_path),
                        "metadata": file_summary,
                        "created_at": derived_at,
                    }
                )
                frame_entries.append(
                    {
                        "id": frame_id,
                        "file_id": frame_file_id,
                        "timestamp_ms": input_frame.timestamp_ms,
                        "sha256": decoded.sha256,
                        "mime_type": decoded.detected_mime,
                        "byte_length": len(raw_bytes),
                        "analysis": analyze_image(decoded.image, decoded.sha256),
                        "_image_base64": encoded,
                    }
                )

            summarized = summarize_frames(
                frame_entries,
                duration_ms=request.duration_ms,
            )
            selected = {
                item["id"]: item
                for item in summarized["frames"]
                if item.get("selected")
            }

            async def observe_frame(frame: Dict[str, Any]):
                async with self.model_semaphore:
                    result = await self.vision_client.vision_observe(
                        f"data:{frame['mime_type']};base64,{frame['_image_base64']}",
                        {
                            "session_id": session_id,
                            "video_id": video_id,
                            "frame_id": frame["id"],
                            "timestamp_ms": frame["timestamp_ms"],
                            "region_id": video["region_id"],
                            "quality_gate": frame["analysis"]["quality_gate"],
                            "boundary": "visible observation only",
                        },
                    )
                return frame, result

            observed = await asyncio.gather(
                *(observe_frame(frame) for frame in selected.values())
            )
            model_runs = []
            visible_observations = [
                (
                    f"已将原始视频哈希与 {len(frame_entries)} 个浏览器抽样帧绑定，"
                    f"其中 {summarized['summary']['usable_frame_count']} 帧通过逐帧准入。"
                ),
                (
                    "跨帧指标描述拍摄覆盖、画面质量与视觉一致性；"
                    "不等同于材料成分、内部结构或年代证据。"
                ),
            ]
            frame_model_run_ids: Dict[str, str] = {}
            any_high_ood = False
            for frame, vision_result in observed:
                run_id = f"MRUN-{uuid4().hex[:16].upper()}"
                frame_model_run_ids[frame["id"]] = run_id
                output = vision_result.get("output", {})
                for observation in output.get("observations", []):
                    if observation not in visible_observations:
                        visible_observations.append(observation)
                any_high_ood = any_high_ood or output.get("ood_risk") == "HIGH"
                model_runs.append(
                    {
                        "run_id": run_id,
                        "role": "video_frame_multimodal_observation",
                        "node_id": (
                            self.settings.compute_node_id
                            if vision_result.get("available")
                            else self.settings.node_id
                        ),
                        "model": vision_result.get("model", self.settings.vision_model),
                        "template_hash": vision_result.get("prompt_hash"),
                        "started_at": utc_now(),
                        "completed_at": utc_now(),
                        "latency_ms": vision_result.get("latency_ms", 0),
                        "status": "SUCCESS"
                        if vision_result.get("available")
                        else "DEGRADED",
                        "mode": vision_result.get("mode", "deterministic_fallback"),
                        "input_refs": [video["file_id"], frame["file_id"], frame["id"]],
                        "input_hash": frame["sha256"],
                        "output_hash": vision_result.get("output_hash"),
                        "output_ref": analysis_id
                        if vision_result.get("available")
                        else None,
                        "error_category": vision_result.get("error"),
                        "output": output if vision_result.get("available") else None,
                        **_model_runtime_trace(vision_result),
                    }
                )

            feature_source = list(selected.values()) or summarized["frames"]
            feature_vectors = [
                item["analysis"]["fingerprint"]["feature_vector"]
                for item in feature_source
            ]
            aggregate_vector = [
                sum(values) / len(values) for values in zip(*feature_vectors)
            ]
            knowledge_snapshot = self._knowledge_snapshot(
                state=state,
                text=(
                    f"{state['claim']['material']} {state['claim']['kiln']} "
                    "环绕视频可见纹饰、器形、釉面与底足观察参考"
                ),
                limit=3,
                space="demo",
                visual_feature_vector=aggregate_vector,
            )
            recommendations = next_best_observations(summarized)
            persistent_frames = []
            for frame in summarized["frames"]:
                image_result = frame["analysis"]
                persistent_frames.append(
                    {
                        "id": frame["id"],
                        "file_id": frame["file_id"],
                        "timestamp_ms": frame["timestamp_ms"],
                        "sha256": frame["sha256"],
                        "mime_type": frame["mime_type"],
                        "byte_length": frame["byte_length"],
                        "quality": image_result["quality_gate"],
                        "metrics": image_result["metrics"],
                        "salient_regions": image_result["salient_regions"],
                        "fingerprint": image_result["fingerprint"],
                        "quality_score": frame["quality_score"],
                        "duplicate_of": frame["duplicate_of"],
                        "duplicate_distance": frame["duplicate_distance"],
                        "selected": frame["selected"],
                        "admission_status": frame["admission_status"],
                        "model_run_id": frame_model_run_ids.get(frame["id"]),
                        "derivation_method": request.sampling_strategy,
                        "source_category": "DERIVED_FRAME",
                    }
                )
            review_regions = [
                {
                    "frame_id": frame["id"],
                    "timestamp_ms": frame["timestamp_ms"],
                    "regions": frame["analysis"]["salient_regions"],
                    "label": "高边缘响应候选复核区",
                    "boundary": "算法显著区域，不代表裂纹、修复或材料异常。",
                }
                for frame in summarized["frames"]
                if frame["id"] in summarized["representative_frame_ids"]
            ]
            video_analysis = {
                "id": analysis_id,
                "video_id": video_id,
                "file_id": video["file_id"],
                "region_id": video["region_id"],
                "modality": video["modality"],
                "duration_ms": request.duration_ms,
                "sampling_strategy": request.sampling_strategy,
                "quality": summarized["quality_gate"],
                "sampling_summary": summarized["summary"],
                "representative_frame_ids": summarized["representative_frame_ids"],
                "frames": persistent_frames,
                "visible_observations": visible_observations,
                "review_regions": review_regions,
                "next_best_observations": recommendations,
                "next_best_observation": recommendations[0],
                "knowledge_snapshot_hash": knowledge_snapshot["snapshot_hash"],
                "limitations": [
                    "抽帧由浏览器媒体引擎完成；服务端保留每帧字节、时间戳、父视频和哈希以供复核。",
                    "跨帧稳定性衡量采集一致性，不表示器物状态长期稳定。",
                    "图像与视频不能给出元素、分子结构、内部结构、热释光或真伪结论。",
                ],
                "conclusion_boundary": (
                    "只输出可见观察、采集质量、候选复核区和下一步建议；"
                    "不判断真伪、年代、窑口、成分、价值或法律状态。"
                ),
                "source_category": "DERIVED_MEASUREMENT",
                "created_at": utc_now(),
            }

            frame_summaries = [item["metadata"] for item in frame_file_records]

            def updater(current: Dict[str, Any]):
                current_video = next(
                    (
                        item
                        for item in current.get("videos", [])
                        if item.get("id") == video_id
                    ),
                    None,
                )
                if current_video is None:
                    raise ValueError("video disappeared before analysis commit")
                current_video["status"] = "ANALYZED"
                current_video["duration_ms"] = request.duration_ms
                current_video["latest_analysis_id"] = analysis_id
                current.setdefault("raw_files", []).extend(frame_summaries)
                current.setdefault("video_analyses", []).append(video_analysis)
                current.setdefault("model_runs", []).extend(model_runs)
                current.setdefault("knowledge_searches", []).append(knowledge_snapshot)
                current["next_best_observations"] = deepcopy(recommendations)
                current["next_step"] = recommendations[0]["label"]
                if any_high_ood:
                    current["claim_consistency"] = "ESCALATE"
                    current["status"] = "review_required"

                graph = current["evidence_graph"]
                raw_video_node = f"raw:{session_id}:{video['file_id']}"
                analysis_node = f"observation:{session_id}:{analysis_id}"
                add_node(
                    graph,
                    analysis_node,
                    "视频多帧结构化观察",
                    "observation",
                    "accepted" if summarized["quality_gate"]["passed"] else "review",
                    {
                        "quality_gate": summarized["quality_gate"],
                        "sampling_summary": summarized["summary"],
                        "data_level": "DERIVED_MEASUREMENT",
                    },
                )
                add_edge(graph, analysis_node, raw_video_node, "derived_from")
                for frame in persistent_frames:
                    frame_node = f"observation:{session_id}:{frame['id']}"
                    add_node(
                        graph,
                        frame_node,
                        f"视频帧 {frame['timestamp_ms']} ms",
                        "observation",
                        frame["admission_status"].lower(),
                        {
                            "sha256": frame["sha256"],
                            "timestamp_ms": frame["timestamp_ms"],
                            "parent_video_id": video_id,
                            "file_id": frame["file_id"],
                            "derivation_method": request.sampling_strategy,
                            "quality_gate": frame["quality"],
                            "duplicate_of": frame["duplicate_of"],
                            "data_level": "DERIVED_FRAME",
                        },
                    )
                    add_edge(graph, frame_node, raw_video_node, "derived_from")
                    add_edge(graph, analysis_node, frame_node, "derived_from")
                for model_run in model_runs:
                    frame_id = model_run["input_refs"][-1]
                    model_node = f"model-run:{session_id}:{model_run['run_id']}"
                    add_node(
                        graph,
                        model_node,
                        "视频代表帧本地多模态观察",
                        "model_run",
                        "success" if model_run["status"] == "SUCCESS" else "degraded",
                        {
                            key: value
                            for key, value in model_run.items()
                            if key != "output"
                        },
                    )
                    add_edge(
                        graph,
                        model_node,
                        f"observation:{session_id}:{frame_id}",
                        "analyzes",
                    )
                    if model_run["status"] == "SUCCESS":
                        add_edge(graph, analysis_node, model_node, "produced_by")
                for item in knowledge_snapshot.get("results", []):
                    reference_node = f"reference:{item['source_id']}"
                    add_node(
                        graph,
                        reference_node,
                        item["title"],
                        "reference",
                        "demo",
                        {
                            "citation": item["citation"],
                            "score": item["score"],
                            "data_level": item["data_level"],
                        },
                    )
                    add_edge(graph, analysis_node, reference_node, "cites")
                for recommendation in recommendations:
                    action_node = (
                        f"action:{session_id}:{analysis_id}:{recommendation['id']}"
                    )
                    add_node(
                        graph,
                        action_node,
                        recommendation["label"],
                        "action",
                        "recommended",
                        recommendation,
                    )
                    add_edge(
                        graph,
                        action_node,
                        f"region:{session_id}:{video['region_id']}",
                        "targets",
                    )
                return current, {
                    "video_id": video_id,
                    "analysis_id": analysis_id,
                    "video_sha256": video["sha256"],
                    "sampling_strategy": request.sampling_strategy,
                    "frame_hashes": [
                        {
                            "frame_id": item["id"],
                            "file_id": item["file_id"],
                            "timestamp_ms": item["timestamp_ms"],
                            "sha256": item["sha256"],
                            "admission_status": item["admission_status"],
                        }
                        for item in persistent_frames
                    ],
                    "quality_gate": summarized["quality_gate"],
                    "sampling_summary": summarized["summary"],
                    "representative_frame_ids": summarized["representative_frame_ids"],
                    "model_runs": [
                        {key: value for key, value in item.items() if key != "output"}
                        for item in model_runs
                    ],
                    "knowledge": self.knowledge.audit_payload(knowledge_snapshot),
                    "conclusion_boundary": video_analysis["conclusion_boundary"],
                }

            self.store.atomic_register_raw_files(
                session_id,
                frame_file_records,
                updater,
                event_type="VIDEO_FRAMES_ANALYZED",
            )
        except Exception:
            for path in created_paths:
                path.unlink(missing_ok=True)
            raise
        return self.envelope(session_id)

    async def analyze_native_video(
        self, session_id: str, video_id: str
    ) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        video = next(
            (item for item in state.get("videos", []) if item.get("id") == video_id),
            None,
        )
        if video is None:
            raise ValueError("unknown video identifier")
        if int(video.get("byte_length", 0)) > self.settings.max_native_video_bytes:
            raise ValueError(
                "video exceeds native model limit; use representative-frame analysis"
            )
        declared_duration_ms = video.get("duration_ms")
        if declared_duration_ms is None:
            raise ValueError("native model analysis requires a declared video duration")
        if int(declared_duration_ms) > self.settings.max_native_video_duration_ms:
            raise ValueError(
                "video exceeds native model duration limit; use representative-frame analysis"
            )
        file_record = next(
            (
                item
                for item in self.store.list_raw_files(session_id)
                if item.get("id") == video.get("file_id")
            ),
            None,
        )
        if file_record is None:
            raise ValueError("registered video file is unavailable")
        stored_path = Path(file_record["path"]).resolve()
        upload_root = self.settings.upload_dir.resolve()
        try:
            stored_path.relative_to(upload_root)
        except ValueError as exc:
            raise ValueError(
                "registered video path crossed the upload boundary"
            ) from exc
        raw_bytes = stored_path.read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != video["sha256"]:
            raise ValueError("registered video bytes failed integrity verification")
        if video.get("mime_type") != "video/mp4" or video.get("container") != "ISO-BMFF":
            raise ValueError("native model analysis currently requires an H.264 MP4 file")
        media_probe = inspect_mp4_bytes(
            raw_bytes,
            max_duration_ms=self.settings.max_native_video_duration_ms,
        )
        actual_duration_ms = int(media_probe["duration_ms"])
        duration_tolerance_ms = max(500, round(actual_duration_ms * 0.05))
        if abs(int(declared_duration_ms) - actual_duration_ms) > duration_tolerance_ms:
            raise ValueError(
                "declared video duration does not match the server-parsed MP4 duration"
            )

        started_at = utc_now()
        async with self.model_semaphore:
            result = await self.vision_client.video_observe(
                f"data:{video['mime_type']};base64,{base64.b64encode(raw_bytes).decode('ascii')}",
                {
                    "session_id": session_id,
                    "video_id": video_id,
                    "region_id": video["region_id"],
                    "duration_ms": actual_duration_ms,
                    "duration_source": "SERVER_PARSED_ISO_BMFF",
                    "codec": media_probe["codec"],
                    "width": media_probe["width"],
                    "height": media_probe["height"],
                    "sha256": video["sha256"],
                    "analysis_mode": "native_video",
                },
            )
        completed_at = utc_now()
        analysis_id = f"NVAN-{uuid4().hex[:16].upper()}"
        run_id = f"MRUN-{uuid4().hex[:16].upper()}"
        model_run = {
            "run_id": run_id,
            "role": "native_video_multimodal_observation",
            "node_id": self.settings.node_id,
            "model": result.get("model", self.settings.vision_model),
            "template_hash": result.get("prompt_hash"),
            "started_at": started_at,
            "completed_at": completed_at,
            "latency_ms": result.get("latency_ms", 0),
            "status": "SUCCESS" if result.get("available") else "DEGRADED",
            "mode": result.get("mode", "deterministic_fallback"),
            "input_refs": [video["file_id"], video_id],
            "input_hash": video["sha256"],
            "output_hash": result.get("output_hash"),
            "output_ref": analysis_id if result.get("available") else None,
            "error_category": result.get("error"),
            "output": result.get("output"),
            **_model_runtime_trace(result),
        }
        analysis = {
            "id": analysis_id,
            "video_id": video_id,
            "file_id": video["file_id"],
            "analysis_kind": "NATIVE_VIDEO_MODEL",
            "model": model_run["model"],
            "status": model_run["status"],
            "result": result.get("output"),
            "source_category": (
                "MODEL_DERIVED_OBSERVATION"
                if result.get("available")
                else "MODEL_RUN_FAILURE"
            ),
            "media_validation": {
                "container_signature": video.get("container"),
                "input_sha256": video["sha256"],
                "declared_duration_ms": declared_duration_ms,
                "actual_duration_ms": actual_duration_ms,
                "duration_source": "SERVER_PARSED_ISO_BMFF",
                "duration_tolerance_ms": duration_tolerance_ms,
                "codec": media_probe["codec"],
                "width": media_probe["width"],
                "height": media_probe["height"],
                "frame_metadata": media_probe["frame_metadata"],
                "container_metadata": media_probe["container_metadata"],
                "model_endpoint_decode": (
                    "ACCEPTED" if result.get("available") else "REJECTED"
                ),
            },
            "conclusion_boundary": (
                "原生视频模型仅描述跨视角可见信息；不构成真伪、年代、"
                "窑口、材料成分、价值或法律结论。"
            ),
            "created_at": completed_at,
        }

        def updater(current: Dict[str, Any]):
            current.setdefault("native_video_analyses", []).append(analysis)
            current.setdefault("model_runs", []).append(model_run)
            current["next_step"] = (
                "比较原生视频与代表帧观察，并由专家复核"
                if result.get("available")
                else "原生视频模型运行失败；改用代表帧路径或检查模型服务"
            )
            graph = current["evidence_graph"]
            observation_node = f"observation:{session_id}:{analysis_id}"
            model_node = f"model-run:{session_id}:{run_id}"
            add_node(
                graph,
                model_node,
                f"原生视频模型运行 · {model_run['model']}",
                "model-run",
                "accepted" if result.get("available") else "rejected",
                {
                    "model": model_run["model"],
                    "input_sha256": video["sha256"],
                    "output_sha256": model_run["output_hash"],
                    "request_id": model_run["provider_request_id"],
                },
            )
            add_edge(
                graph,
                model_node,
                f"raw:{session_id}:{video['file_id']}",
                "analyzes",
            )
            if result.get("available"):
                add_node(
                    graph,
                    observation_node,
                    "原生视频跨视角观察",
                    "observation",
                    "accepted",
                    {"data_level": "MODEL_DERIVED_OBSERVATION"},
                )
                add_edge(graph, observation_node, model_node, "produced_by")
            return current, {
                "analysis_id": analysis_id,
                "video_id": video_id,
                "model_run_id": run_id,
                "status": model_run["status"],
                "model": model_run["model"],
                "output_hash": model_run["output_hash"],
            }

        self.store.atomic_update(session_id, "NATIVE_VIDEO_ANALYZED", updater)
        return self.envelope(session_id)

    def search_knowledge(
        self, session_id: str, request: KnowledgeSearchRequest
    ) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        self._require_region(state, request.region_id)
        snapshot = self._knowledge_snapshot(
            state=state,
            text=request.query,
            limit=request.limit,
            space=request.space,
        )

        def updater(current: Dict[str, Any]):
            current.setdefault("knowledge_searches", []).append(snapshot)
            graph = current["evidence_graph"]
            query_node = (
                f"observation:{session_id}:knowledge:{snapshot['query_hash'][:12]}"
            )
            add_node(
                graph,
                query_node,
                "本地知识检索",
                "observation",
                "available" if snapshot["results"] else "insufficient",
                {
                    "query_hash": snapshot["query_hash"],
                    "snapshot_hash": snapshot["snapshot_hash"],
                    "data_boundary": snapshot["data_boundary"],
                },
            )
            add_edge(
                graph,
                query_node,
                f"region:{session_id}:{request.region_id}",
                "analyzes",
            )
            for item in snapshot["results"]:
                reference_node = f"reference:{item['source_id']}"
                add_node(
                    graph,
                    reference_node,
                    item["title"],
                    "reference",
                    "demo",
                    {"citation": item["citation"], "score": item["score"]},
                )
                add_edge(graph, query_node, reference_node, "cites")
            return current, self.knowledge.audit_payload(snapshot)

        self.store.atomic_update(session_id, "LOCAL_KNOWLEDGE_RETRIEVED", updater)
        return self.envelope(session_id)

    def plan(self, session_id: str) -> Dict[str, Any]:
        self.store.atomic_update(
            session_id, "ACTION_PLANNED_AND_RESERVED", plan_and_reserve
        )
        return self.envelope(session_id)

    @staticmethod
    def _execution_for_run(
        state: Dict[str, Any], action_run_id: str
    ) -> Optional[Dict[str, Any]]:
        return next(
            (
                item
                for item in state.get("executions", [])
                if item.get("action_run_id") == action_run_id
            ),
            None,
        )

    def _wait_for_settled_execution(
        self, session_id: str, action_run_id: str
    ) -> Dict[str, Any]:
        """Let a duplicate request return the winner's persisted result."""

        wait_seconds = min(
            60.0, max(1.0, float(self.settings.model_timeout_seconds) + 1.0)
        )
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            state = self.store.get_session(session_id)
            if self._execution_for_run(state, action_run_id) is not None:
                return self.envelope(session_id)
            current_run = state.get("current_action_run_id")
            if current_run != action_run_id:
                raise ValueError(
                    "action execution ended without a persisted settlement"
                )
            time.sleep(0.01)
        raise ValueError("action execution is still in progress")

    @staticmethod
    def _validate_adapter_result(
        result: Any, action: Dict[str, Any], action_run_id: str
    ) -> Dict[str, Any]:
        if not isinstance(result, dict):
            raise TypeError("adapter result must be an object")
        normalized = deepcopy(result)
        if (
            "action_run_id" in normalized
            and normalized["action_run_id"] != action_run_id
        ):
            raise ValueError("adapter returned an unexpected action_run_id")
        normalized["action_run_id"] = action_run_id
        expected_identity = {
            "action_id": action["id"],
            "region_id": action["region_id"],
            "modality": action["modality"],
        }
        for field, expected in expected_identity.items():
            if normalized.get(field) != expected:
                raise ValueError(f"adapter returned an unexpected {field}")
        for field in ("actual_load", "telemetry_valid", "quality_metrics", "device"):
            if not isinstance(normalized.get(field), dict):
                raise TypeError(f"adapter result {field} must be an object")
        for channel in action.get("predicted_load", {}):
            telemetry_value = normalized["telemetry_valid"].get(channel)
            if not isinstance(telemetry_value, bool):
                raise TypeError(f"adapter telemetry_valid.{channel} must be a boolean")
            if telemetry_value:
                actual_value = normalized["actual_load"].get(channel)
                if isinstance(actual_value, bool) or not isinstance(
                    actual_value, (int, float)
                ):
                    raise TypeError(f"adapter actual_load.{channel} must be numeric")
                if not math.isfinite(float(actual_value)) or float(actual_value) < 0.0:
                    raise ValueError(
                        f"adapter actual_load.{channel} must be finite and non-negative"
                    )
        if not isinstance(normalized.get("finding"), str):
            raise TypeError("adapter result finding must be a string")
        if normalized.get("evidence_status") not in {
            "support",
            "conflict",
            "uncertain",
            "escalate",
        }:
            raise ValueError("adapter returned an invalid evidence_status")
        if not isinstance(normalized.get("demo_data"), bool):
            raise TypeError("adapter result demo_data must be a boolean")
        for field in ("source_category", "protocol"):
            if not isinstance(normalized.get(field), str) or not normalized[field]:
                raise TypeError(f"adapter result {field} must be a non-empty string")
        return normalized

    def _execute_adapter_fail_closed(
        self,
        action: Dict[str, Any],
        action_run_id: str,
        replay_profile: str,
    ) -> Dict[str, Any]:
        raw_result: Any = None
        try:
            raw_result = self.instrument_adapter.execute(action, replay_profile)
            result = self._validate_adapter_result(raw_result, action, action_run_id)
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                error_category = "ADAPTER_TIMEOUT"
            elif raw_result is not None:
                error_category = "ADAPTER_CONTRACT_VIOLATION"
            else:
                error_category = "ADAPTER_EXCEPTION"
            result = conservative_adapter_failure_result(
                action,
                action_run_id,
                error_category=error_category,
                adapter_error_type=type(exc).__name__,
                adapter_response_hash=(
                    _safe_payload_hash(raw_result) if raw_result is not None else None
                ),
            )

        result["execution_id"] = f"EXEC-{uuid4().hex[:16].upper()}"
        result["result_hash"] = _sha256_json(result)
        return result

    def execute(self, session_id: str, request: ExecuteActionRequest) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        requested_run = request.action_run_id or state.get("current_action_run_id")
        if not requested_run:
            raise ValueError("no action is reserved")
        if self._execution_for_run(state, requested_run) is not None:
            return self.envelope(session_id)

        def claim_updater(current: Dict[str, Any]):
            return claim_action_for_execution(current, requested_run)

        try:
            claimed_state = self.store.atomic_update(
                session_id, "ACTION_EXECUTION_CLAIMED", claim_updater
            )
        except ActionAlreadySettled:
            return self.envelope(session_id)
        except ActionExecutionInProgress:
            return self._wait_for_settled_execution(session_id, requested_run)

        action_id = claimed_state.get("current_action_id")
        action = next(
            item
            for item in claimed_state["candidate_actions"]
            if item["id"] == action_id
        )
        result = self._execute_adapter_fail_closed(
            action, requested_run, request.replay_profile or ""
        )

        def updater(current: Dict[str, Any]):
            return settle_and_gate(current, result)

        self.store.atomic_update(session_id, "ACTION_EXECUTED_AND_SETTLED", updater)
        return self.envelope(session_id)

    async def run_p01_demo(self, request: DemoScenarioRequest) -> Dict[str, Any]:
        """Run the deterministic P01 story through the same public service methods."""

        scenario_run_id = f"SCN-{uuid4().hex[:16].upper()}"
        timeline: List[Dict[str, Any]] = []
        created = self.create_session(
            CreateSessionRequest(
                artifact_name=request.artifact_name,
                operator=request.operator,
                institution=request.institution,
            )
        )
        session_id = created["session"]["id"]
        timeline.append(
            {
                "step": 1,
                "event": "SESSION_CREATED",
                "status": "COMPLETED",
                "session_version": created["session"]["version"],
                "source_category": "DEMO/SYNTHETIC",
            }
        )

        first_plan = self.plan(session_id)["session"]
        if first_plan.get("current_action_id") != "A2":
            raise RuntimeError(
                "P01 demo invariant failed: Raman was not selected first"
            )
        xrf = next(item for item in first_plan["last_plan"] if item["id"] == "A3")
        timeline.append(
            {
                "step": 2,
                "event": "RAMAN_SELECTED_XRF_BLOCKED",
                "status": "COMPLETED",
                "selected_action": first_plan["current_action_id"],
                "action_run_id": first_plan["current_action_run_id"],
                "xrf_decision": xrf["decision"],
                "xrf_reasons": xrf["reasons"],
                "source_category": "DEMO/SYNTHETIC",
            }
        )

        first_run_id = first_plan["current_action_run_id"]
        first_execution = self.execute(
            session_id,
            ExecuteActionRequest(
                action_run_id=first_run_id,
                replay_profile="raman_low_snr",
            ),
        )["session"]
        raman = first_execution["executions"][-1]
        timeline.append(
            {
                "step": 3,
                "event": "RAMAN_QUALITY_FAILED_RISK_SETTLED",
                "status": "COMPLETED",
                "quality_passed": raman["quality_gate"]["passed"],
                "failed_checks": raman["quality_gate"]["failed_checks"],
                "risk_settlement": raman["settlement"],
                "uncertainty": first_execution["uncertainty"],
                "source_category": raman["result"]["source_category"],
            }
        )

        second_plan = self.plan(session_id)["session"]
        if second_plan.get("current_action_id") != "A1":
            raise RuntimeError("P01 demo invariant failed: HSI was not selected second")
        timeline.append(
            {
                "step": 4,
                "event": "HSI_SELECTED_AFTER_REPLAN",
                "status": "COMPLETED",
                "selected_action": second_plan["current_action_id"],
                "action_run_id": second_plan["current_action_run_id"],
                "raman_retry_blocked": "R1:RAMAN:STANDARD"
                in second_plan["retry_blocked"],
                "source_category": "DEMO/SYNTHETIC",
            }
        )

        second_run_id = second_plan["current_action_run_id"]
        second_execution = self.execute(
            session_id,
            ExecuteActionRequest(
                action_run_id=second_run_id,
                replay_profile="hsi_material_anomaly",
            ),
        )["session"]
        hsi = second_execution["executions"][-1]
        timeline.append(
            {
                "step": 5,
                "event": "HSI_ADMITTED_UNCERTAINTY_REDUCED",
                "status": "COMPLETED",
                "quality_passed": hsi["quality_gate"]["passed"],
                "uncertainty_before": hsi["uncertainty_before"],
                "uncertainty_after": hsi["uncertainty_after"],
                "claim_consistency": second_execution["claim_consistency"],
                "source_category": hsi["result"]["source_category"],
            }
        )

        if request.include_report:
            report_envelope = await self.generate_report(
                session_id, deterministic_only=request.deterministic_only
            )
            report = report_envelope["session"]["last_report"]
            timeline.append(
                {
                    "step": 6,
                    "event": "REPORT_GENERATED",
                    "status": "COMPLETED",
                    "report_id": report["report_id"],
                    "report_sha256": report["integrity"]["report_sha256"],
                    "reasoner_mode": report_envelope["session"]["model_runs"][-1][
                        "mode"
                    ],
                    "source_category": report["source_category"],
                }
            )

        final = self.envelope(session_id)
        timeline.append(
            {
                "step": len(timeline) + 1,
                "event": "INTEGRITY_VERIFIED",
                "status": "COMPLETED" if final["integrity"]["valid"] else "FAILED",
                "binding_sha256": final["integrity"]["binding_sha256"],
                "audit_event_count": final["audit_event_count"],
                "source_category": final["data_provenance"]["overall"],
            }
        )
        return {
            "scenario": {
                "id": "P01-RAMAN-FAIL-HSI-RECOVERY",
                "run_id": scenario_run_id,
                "status": "COMPLETED",
                "deterministic_only": request.deterministic_only,
                "data_classification": "DEMO/SYNTHETIC",
                "disclaimer": "一键播演使用回放检测数据；非真实鉴定结论。",
                "capabilities_exercised": [
                    "risk_budget",
                    "quality_gate",
                    "failure_suppression",
                    "replanning",
                    "evidence_graph",
                    "state_bound_audit_chain",
                    "structured_report" if request.include_report else "report_skipped",
                ],
            },
            "timeline": timeline,
            **final,
        }

    async def generate_report(
        self, session_id: str, *, deterministic_only: bool = False
    ) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        audit = self.store.verify_audit_chain_details(session_id)
        raw_files = self.store.list_raw_files(session_id)
        report = build_report(state, audit, raw_files)
        model_run_id = f"MRUN-{uuid4().hex[:16].upper()}"
        started_at = utc_now()
        if deterministic_only:
            reasoner_result = {
                "available": False,
                "mode": "deterministic_scenario",
                "role": "reasoner",
                "model": "deterministic-report-template-v1",
                "prompt_hash": _sha256_json(
                    {"template": "deterministic-report-template-v1"}
                ),
                "latency_ms": 0,
                "error": "OptionalReasonerBypassedForDeterministicScenario",
            }
        else:
            async with self.model_semaphore:
                reasoner_result = await self.reasoner_client.summarize_report(report)
        if reasoner_result.get("available"):
            try:
                reasoner_result["output"] = validate_reasoner_output(
                    reasoner_result.get("output", {}), report_citation_ids(report)
                )
            except ValueError:
                reasoner_result = {
                    key: value
                    for key, value in reasoner_result.items()
                    if key not in {"output", "output_hash"}
                }
                reasoner_result.update(
                    {
                        "available": False,
                        "mode": "deterministic_fallback",
                        "error": "ReasonerCitationOrBoundaryViolation",
                    }
                )
        completed_at = utc_now()
        if reasoner_result.get("available"):
            report["assistant_summary"] = reasoner_result.get("output")
        else:
            deterministic_scenario = (
                reasoner_result.get("mode") == "deterministic_scenario"
            )
            report["assistant_summary"] = {
                "summary": "证据包已按确定性模板生成；本地推理模型未参与摘要。",
                "limitations": [
                    (
                        "为保证一键演示可离线复演，本次主动跳过可选推理模型。"
                        if deterministic_scenario
                        else "推理服务未配置或不可用。"
                    )
                ],
                "next_steps": report["next_steps"],
                "citation_ids": [],
            }
        model_run = {
            "run_id": model_run_id,
            "role": "evidence_report_summary",
            "node_id": self.settings.node_id,
            "model": reasoner_result.get("model", self.settings.reasoner_model),
            "template_hash": reasoner_result.get("prompt_hash"),
            "started_at": started_at,
            "completed_at": completed_at,
            "latency_ms": reasoner_result.get("latency_ms", 0),
            "status": "SUCCESS" if reasoner_result.get("available") else "DEGRADED",
            "mode": reasoner_result.get("mode", "deterministic_fallback"),
            "input_refs": [report["report_id"]],
            "input_hash": report["integrity"]["report_sha256"],
            "output_hash": reasoner_result.get("output_hash"),
            "output_ref": report["report_id"],
            "error_category": reasoner_result.get("error"),
            "citation_ids": reasoner_result.get("output", {}).get("citation_ids", []),
            **_model_runtime_trace(reasoner_result),
        }
        report.setdefault("model_runs", []).append(model_run)
        rehash_report(report)

        def updater(current: Dict[str, Any]):
            current.setdefault("reports", []).append(report)
            current["last_report"] = report
            current.setdefault("model_runs", []).append(model_run)
            current["next_step"] = "专家复核或外部实验室升级"
            graph = current["evidence_graph"]
            report_node = f"report:{session_id}:{report['report_id']}"
            add_node(
                graph,
                report_node,
                f"科学证据报告 v{report['report_version']}",
                "report",
                current.get("claim_consistency", "EVIDENCE_INSUFFICIENT"),
                {
                    "sha256": report["integrity"]["report_sha256"],
                    "source_category": report["source_category"],
                },
            )
            add_edge(graph, report_node, f"claim:{session_id}", "summarizes")
            return current, {
                "report_id": report["report_id"],
                "report_version": report["report_version"],
                "covered_session_version": report["covered_session_version"],
                "report_sha256": report["integrity"]["report_sha256"],
                "reasoner_run": model_run,
            }

        self.store.atomic_update(session_id, "REPORT_GENERATED", updater)
        return self.envelope(session_id)

    def get_report(self, session_id: str) -> Dict[str, Any]:
        state = self.store.get_session(session_id)
        report = state.get("last_report")
        if report is None:
            raise ValueError("no report has been generated")
        return report

    def get_report_html(self, session_id: str) -> str:
        return report_to_html(self.get_report(session_id))

    async def health(self) -> Dict[str, Any]:
        vision, reasoner = await asyncio.gather(
            self.vision_client.health("spark-a-vision"),
            self.reasoner_client.health("spark-b-reasoner"),
        )
        knowledge = self.knowledge.health()
        dual_node = self.settings.runtime_mode == "dual-node"
        single_spark = self.settings.runtime_mode == "single-spark"
        actual_compute_node = (
            self.settings.compute_node_id if dual_node else self.settings.node_id
        )
        components = [
            {
                "name": "gateway-store",
                "status": "online",
                "detail": "SQLite transaction store and audit chain ready",
                "node_id": self.settings.node_id,
                "role": "knowledge-evidence-gateway",
                "version": self.settings.service_version,
            },
            {
                "name": "local-knowledge",
                "status": (
                    "online"
                    if knowledge["status"] in {"ready", "degraded"}
                    else "unavailable"
                ),
                "detail": f"{knowledge['entry_count']} entries · {knowledge['data_level']}",
                "node_id": self.settings.node_id,
                "role": "local-knowledge",
                "version": knowledge["knowledge_version"],
            },
            {
                **vision,
                "name": "spark-vision" if single_spark else "spark-a-vision",
                "node_id": actual_compute_node,
                "role": "multimodal-compute",
                "required": dual_node or single_spark,
            },
            {
                **reasoner,
                "name": "spark-report-model" if single_spark else "spark-b-reasoner",
                "node_id": self.settings.node_id,
                "role": (
                    "shared-multimodal-report-model"
                    if single_spark
                    else "optional-report-reasoner"
                ),
                "required": single_spark,
            },
            {
                "name": "instrument-adapter",
                "status": "demo",
                "detail": "Replay adapter ready; no real instrument connected",
                "node_id": self.settings.node_id,
                "role": "instrument-control-plane",
                "version": "replay-v1",
                "required": False,
            },
        ]
        capabilities = [
            {
                "id": "session-and-evidence-store",
                "name": "会话、证据与事务存储",
                "status": "online",
                "execution_mode": "LOCAL_DETERMINISTIC",
                "node_id": self.settings.node_id,
                "required": True,
                "data_classification": "SESSION_DATA",
            },
            {
                "id": "state-bound-audit-chain",
                "name": "状态绑定审计链",
                "status": "online",
                "execution_mode": "SHA-256_CANONICAL_JSON",
                "node_id": self.settings.node_id,
                "required": True,
                "data_classification": "INTEGRITY_METADATA",
            },
            {
                "id": "local-knowledge-retrieval",
                "name": "本地知识检索",
                "status": "online"
                if knowledge["status"] in {"ready", "degraded"}
                else "unavailable",
                "execution_mode": (
                    "DETERMINISTIC_FALLBACK"
                    if knowledge.get("embedding", {}).get("degraded")
                    else "LOCAL_EMBEDDING"
                ),
                "node_id": self.settings.node_id,
                "required": True,
                "data_classification": knowledge["data_level"],
                "degraded_reason": knowledge.get("embedding", {}).get("reason"),
            },
            {
                "id": "multimodal-model-observation",
                "name": "多模态模型观察",
                "status": vision["status"],
                "execution_mode": (
                    "LOCAL_MODEL"
                    if vision["status"] == "online"
                    else "DETERMINISTIC_IMAGE_ONLY"
                ),
                "node_id": actual_compute_node,
                "required": dual_node or single_spark,
                "model": vision.get("model"),
                "data_classification": "USER_UPLOAD/UNVERIFIED",
                "degraded_reason": (
                    None if vision["status"] == "online" else vision.get("detail")
                ),
            },
            {
                "id": "p01-active-sensing",
                "name": "P01 主动科学检测",
                "status": "demo",
                "execution_mode": "DETERMINISTIC_RULES_WITH_REPLAY_ADAPTER",
                "node_id": self.settings.node_id,
                "required": True,
                "data_classification": "DEMO/SYNTHETIC",
            },
            {
                "id": "optional-report-reasoner",
                "name": "共享模型报告摘要" if single_spark else "可选报告推理",
                "status": reasoner["status"],
                "execution_mode": (
                    "LOCAL_MODEL"
                    if reasoner["status"] == "online"
                    else "DETERMINISTIC_REPORT_TEMPLATE"
                ),
                "node_id": self.settings.node_id,
                "required": single_spark,
                "model": reasoner.get("model"),
                "data_classification": "DERIVED_SUMMARY",
                "degraded_reason": (
                    None if reasoner["status"] == "online" else reasoner.get("detail")
                ),
            },
        ]
        node_map: Dict[str, Dict[str, Any]] = {}
        for capability in capabilities:
            node = node_map.setdefault(
                capability["node_id"],
                {
                    "node_id": capability["node_id"],
                    "roles": [],
                    "capability_ids": [],
                    "core_ready": True,
                    "degraded_capabilities": [],
                },
            )
            role = (
                "multimodal-compute"
                if capability["id"] == "multimodal-model-observation"
                else "knowledge-evidence-gateway"
            )
            if role not in node["roles"]:
                node["roles"].append(role)
            node["capability_ids"].append(capability["id"])
            if capability["status"] in {"degraded", "disabled", "unavailable"}:
                node["degraded_capabilities"].append(capability["id"])
                if capability["required"]:
                    node["core_ready"] = False
        nodes = []
        for node in node_map.values():
            node["status"] = (
                "online"
                if node["core_ready"] and not node["degraded_capabilities"]
                else "degraded"
                if node["core_ready"]
                else "unavailable"
            )
            nodes.append(node)
        nodes.sort(key=lambda item: item["node_id"])
        degraded = any(
            item["status"] in {"degraded", "disabled"} and item.get("required", True)
            for item in components
        )
        compute_runtime = runtime_snapshot(
            node_id=self.settings.node_id,
            runtime_mode=self.settings.runtime_mode,
        )
        compute_runtime.update(
            {
                "model_endpoint_status": vision["status"],
                "model_profile": self.settings.model_profile,
                "model_source": self.settings.vision_model_source,
                "model_revision": self.settings.vision_model_revision,
                "deployment_git_commit": self.settings.deployment_git_commit,
                "configured_model": vision.get("model"),
                "served_models": vision.get("served_models", []),
                "model_identity_verified": vision.get("model_identity_verified", False),
                "endpoint_identity_ready": bool(
                    vision["status"] == "online"
                    and vision.get("model_identity_verified", False)
                ),
            }
        )
        return {
            "status": "degraded" if degraded else "online",
            "mode": self.settings.runtime_mode,
            "offline": self.settings.offline_mode,
            "demo_data": True,
            "disclaimer": "DEMO/SYNTHETIC；非真实鉴定结论。",
            "topology": {
                "type": "APPLICATION_LEVEL_INDEPENDENT_SERVICES",
                "tensor_parallel": False,
                "gateway_node": self.settings.node_id,
                "compute_node": actual_compute_node,
                "configured_compute_node": self.settings.compute_node_id,
                "dual_node_active": dual_node,
                "physical_node_count": 2 if dual_node else 1,
                "colocated_services": single_spark,
            },
            "operational_profile": (
                "DUAL_NODE_LOCAL_AI"
                if dual_node
                else "SINGLE_SPARK_LOCAL_AI"
                if single_spark
                else "SINGLE_NODE_DEGRADED"
                if self.settings.runtime_mode == "single-degraded"
                else "LOCAL_DEVELOPMENT"
            ),
            "nodes": nodes,
            "capabilities": capabilities,
            "data_boundary": {
                "mode": (
                    "LOCAL_INTERNAL_NETWORK_CONFIGURED"
                    if single_spark and self.settings.offline_mode
                    else "APPLICATION_LEVEL_LOCAL_ENDPOINT_POLICY"
                    if self.settings.offline_mode
                    else "APPROVED_PRIVATE_ENDPOINTS_ONLY"
                ),
                "public_fallback_allowed": False,
                "private_endpoint_enforcement": self.settings.require_private_endpoints,
                "network_enforcement": (
                    "COMPOSE_INTERNAL_REQUIRES_HOST_ATTESTATION"
                    if single_spark
                    else "NOT_ATTESTED_BY_APPLICATION"
                ),
                "raw_artifact_data_egress": (
                    "BLOCKED_WHEN_INTERNAL_NETWORK_ATTESTATION_PASSES"
                    if single_spark
                    else "NOT_ATTESTED_AT_APPLICATION_LAYER"
                ),
            },
            "knowledge_version": self.knowledge.version,
            "compute_runtime": compute_runtime,
            "components": components,
            "checked_at": utc_now(),
        }
