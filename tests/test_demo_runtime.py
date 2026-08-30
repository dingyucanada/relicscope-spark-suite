from __future__ import annotations

import base64
import io
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.services.evidence import evidence_graph_sha256
from app.services.knowledge import KnowledgeBase


class _OnlineModel:
    def __init__(self, model: str) -> None:
        self.model = model

    async def health(self, name: str):
        return {
            "name": name,
            "status": "online",
            "detail": "private local endpoint ready",
            "model": self.model,
            "configured_model": self.model,
            "served_models": [self.model],
            "model_identity_verified": True,
            "request_id": "health-request",
            "latency_ms": 1,
        }


def _small_png_payload() -> str:
    image = Image.new("RGB", (64, 64), color=(38, 76, 142))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_health_exposes_nodes_capabilities_and_offline_boundary(api_client):
    response = api_client.get("/api/health")
    assert response.status_code == 200
    health = response.json()

    assert health["operational_profile"] == "LOCAL_DEVELOPMENT"
    assert health["topology"]["dual_node_active"] is False
    assert health["topology"]["tensor_parallel"] is False
    assert health["data_boundary"]["public_fallback_allowed"] is False
    assert (
        health["data_boundary"]["raw_artifact_data_egress"]
        == "NOT_ATTESTED_AT_APPLICATION_LAYER"
    )
    assert health["nodes"]

    capabilities = {item["id"]: item for item in health["capabilities"]}
    assert capabilities["state-bound-audit-chain"]["status"] == "online"
    assert capabilities["p01-active-sensing"]["data_classification"] == "DEMO/SYNTHETIC"
    assert capabilities["multimodal-model-observation"]["model"] == "test-vision-model"


def test_unconfigured_models_degrade_visibly_without_public_fallback(
    app_settings,
):
    knowledge = KnowledgeBase.from_path(
        app_settings.knowledge_manifest_path, offline=True
    )
    application = create_app(app_settings, knowledge=knowledge)
    with TestClient(application) as client:
        health = client.get("/api/health").json()

    capabilities = {item["id"]: item for item in health["capabilities"]}
    vision = capabilities["multimodal-model-observation"]
    reasoner = capabilities["optional-report-reasoner"]
    assert vision["status"] == "disabled"
    assert vision["execution_mode"] == "DETERMINISTIC_IMAGE_ONLY"
    assert reasoner["status"] == "disabled"
    assert reasoner["execution_mode"] == "DETERMINISTIC_REPORT_TEMPLATE"
    assert health["data_boundary"]["mode"] == "APPLICATION_LEVEL_LOCAL_ENDPOINT_POLICY"
    assert health["data_boundary"]["public_fallback_allowed"] is False


def test_dual_node_health_reports_distinct_actual_roles(app_settings):
    settings = replace(app_settings, runtime_mode="dual-node")
    knowledge = KnowledgeBase.from_path(settings.knowledge_manifest_path, offline=True)
    application = create_app(settings, knowledge=knowledge)
    application.state.service.vision_client = _OnlineModel("vision-on-spark-a")
    application.state.service.reasoner_client = _OnlineModel("reasoner-on-spark-b")

    with TestClient(application) as client:
        health = client.get("/api/health").json()
        readiness = client.get("/health/ready")

    assert readiness.status_code == 200
    assert health["operational_profile"] == "DUAL_NODE_LOCAL_AI"
    assert health["topology"]["dual_node_active"] is True
    assert health["topology"]["gateway_node"] == "spark-b"
    assert health["topology"]["compute_node"] == "spark-a"
    nodes = {item["node_id"]: item for item in health["nodes"]}
    assert set(nodes) == {"spark-a", "spark-b"}
    assert "multimodal-compute" in nodes["spark-a"]["roles"]
    assert "knowledge-evidence-gateway" in nodes["spark-b"]["roles"]
    assert nodes["spark-a"]["core_ready"] is True


def test_single_spark_health_reports_one_physical_gpu_system(app_settings):
    settings = replace(
        app_settings,
        runtime_mode="single-spark",
        node_id="spark-single",
        compute_node_id="spark-single",
        model_profile="qwen3-vl",
        vision_model_source="Qwen/Qwen3-VL-30B-A3B-Instruct",
        vision_model_revision="a" * 40,
        deployment_git_commit="b" * 40,
    )
    knowledge = KnowledgeBase.from_path(settings.knowledge_manifest_path, offline=True)
    application = create_app(settings, knowledge=knowledge)
    application.state.service.vision_client = _OnlineModel("qwen3_vl_30b_a3b")
    application.state.service.reasoner_client = _OnlineModel("qwen3_vl_30b_a3b")

    with TestClient(application) as client:
        health = client.get("/api/health").json()
        readiness = client.get("/health/ready")

    assert readiness.status_code == 200
    assert health["status"] == "online"
    assert health["operational_profile"] == "SINGLE_SPARK_LOCAL_AI"
    assert health["topology"]["physical_node_count"] == 1
    assert health["topology"]["colocated_services"] is True
    assert health["compute_runtime"]["endpoint_identity_ready"] is True
    assert "real_model_execution_ready" not in health["compute_runtime"]
    assert health["compute_runtime"]["model_profile"] == "qwen3-vl"
    assert health["compute_runtime"]["model_revision"] == "a" * 40
    assert health["compute_runtime"]["deployment_git_commit"] == "b" * 40
    assert health["data_boundary"]["mode"] == "LOCAL_INTERNAL_NETWORK_CONFIGURED"
    assert (
        health["data_boundary"]["network_enforcement"]
        == "COMPOSE_INTERNAL_REQUIRES_HOST_ATTESTATION"
    )
    assert [item["node_id"] for item in health["nodes"]] == ["spark-single"]


def test_one_click_p01_demo_runs_backend_story_and_labels_all_demo_data(api_client):
    response = api_client.post("/api/demo/scenarios/p01")
    assert response.status_code == 201, response.text
    payload = response.json()

    assert payload["scenario"]["status"] == "COMPLETED"
    assert payload["scenario"]["data_classification"] == "DEMO/SYNTHETIC"
    assert "非真实鉴定结论" in payload["scenario"]["disclaimer"]
    assert payload["scenario"]["deterministic_only"] is True

    event_names = [item["event"] for item in payload["timeline"]]
    assert event_names == [
        "SESSION_CREATED",
        "RAMAN_SELECTED_XRF_BLOCKED",
        "RAMAN_QUALITY_FAILED_RISK_SETTLED",
        "HSI_SELECTED_AFTER_REPLAN",
        "HSI_ADMITTED_UNCERTAINTY_REDUCED",
        "REPORT_GENERATED",
        "INTEGRITY_VERIFIED",
    ]
    assert payload["timeline"][1]["xrf_decision"] == "BLOCKED"
    assert payload["timeline"][2]["quality_passed"] is False
    assert payload["timeline"][4]["quality_passed"] is True
    assert payload["timeline"][4]["uncertainty_after"] == 0.48
    assert payload["timeline"][5]["reasoner_mode"] == "deterministic_scenario"

    state = payload["session"]
    assert state["uncertainty"] == 0.48
    assert state["claim_consistency"] == "REVIEW_REQUIRED"
    assert len(state["executions"]) == 2
    assert all(item["result"]["demo_data"] for item in state["executions"])
    assert payload["data_provenance"]["contains_demo_synthetic"] is True
    assert payload["data_provenance"]["contains_real_instrument_data"] is False
    assert payload["integrity"]["valid"] is True
    assert (
        payload["integrity"]["verification_strength"] == "AUDIT_CHAIN_AND_SESSION_STATE"
    )
    assert len(payload["integrity"]["binding_sha256"]) == 64


def test_integrity_endpoint_binds_graph_state_and_audit_chain(api_client):
    created = api_client.post("/api/sessions", json={}).json()
    session_id = created["session"]["id"]
    response = api_client.get(f"/api/sessions/{session_id}/integrity")
    assert response.status_code == 200
    payload = response.json()
    integrity = payload["integrity"]

    assert integrity["valid"] is True
    assert len(integrity["audit_tail_sha256"]) == 64
    assert len(integrity["session_state_sha256"]) == 64
    assert len(integrity["evidence_graph_sha256"]) == 64
    assert len(integrity["evidence_bundle_sha256"]) == 64
    assert len(integrity["binding_sha256"]) == 64
    assert integrity["evidence_graph_sha256"] == evidence_graph_sha256(
        created["session"]["evidence_graph"]
    )
    assert payload["data_provenance"]["display_badge"].startswith("DEMO/SYNTHETIC")


def test_integrity_endpoint_detects_tampered_uploaded_file_bytes(api_client):
    created = api_client.post("/api/sessions", json={}).json()
    session_id = created["session"]["id"]
    analyzed = api_client.post(
        f"/api/sessions/{session_id}/images/analyze",
        json={
            "filename": "artifact.png",
            "mime_type": "image/png",
            "image_base64": _small_png_payload(),
            "modality": "RGB",
            "region_id": "R1",
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["integrity"]["raw_files"]["valid"] is True

    service = api_client.app.state.service
    stored_file = service.store.list_raw_files(session_id)[0]
    Path(stored_file["path"]).write_bytes(b"tampered-after-ingest")

    response = api_client.get(f"/api/sessions/{session_id}/integrity")
    assert response.status_code == 200
    integrity = response.json()["integrity"]
    assert integrity["valid"] is False
    assert integrity["raw_files"]["valid"] is False
    assert integrity["failure_reason"] == "raw file integrity failure"
    assert integrity["raw_files"]["items"][0]["reasons"] == ["FILE_BYTES_HASH_MISMATCH"]
