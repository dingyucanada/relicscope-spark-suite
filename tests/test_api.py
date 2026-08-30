from __future__ import annotations

import base64
import io
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image


def _image_payload():
    size = 320
    y, x = np.indices((size, size))
    array = np.stack(
        [45 + (x % 180), 50 + (y % 150), 90 + ((x + y) % 155)], axis=2
    ).astype(np.uint8)
    image = Image.fromarray(array)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _new_session(client):
    response = client.post(
        "/api/sessions",
        json={
            "artifact_name": "疑似清代青花瓷碗",
            "operator": "测试员",
            "institution": "RelicScope Demo Lab",
            "claim": {
                "period": "清代",
                "kiln": "景德镇窑",
                "material": "青花瓷",
                "provenance_note": "来源待核验",
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["session"]["id"]


def test_health_and_session_validation(api_client):
    health = api_client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["topology"]["tensor_parallel"] is False
    assert body["demo_data"] is True
    assert body["knowledge_version"].startswith("relicscope-demo-ceramics-v1")

    invalid = api_client.post(
        "/api/sessions",
        json={"artifact_name": "   ", "operator": "x", "institution": "x"},
    )
    assert invalid.status_code == 422

    session_id = _new_session(api_client)
    envelope = api_client.get(f"/api/sessions/{session_id}").json()
    assert envelope["session"]["version"] == 1
    assert envelope["audit_verified"] is True
    assert envelope["session"]["source_category"] == "DEMO/SYNTHETIC"


def test_image_knowledge_active_sensing_and_report_flow(api_client):
    session_id = _new_session(api_client)
    image_response = api_client.post(
        f"/api/sessions/{session_id}/images/analyze",
        json={
            "filename": "../../artifact.png",
            "mime_type": "image/png",
            "image_base64": _image_payload(),
            "modality": "RGB",
            "region_id": "R1",
        },
    )
    assert image_response.status_code == 200, image_response.text
    image_state = image_response.json()["session"]
    assert image_state["raw_files"][0]["filename"] == "artifact.png"
    assert len(image_state["raw_files"][0]["sha256"]) == 64
    assert image_state["model_runs"][0]["status"] == "SUCCESS"
    assert image_state["knowledge_searches"][0]["data_boundary"] == "LOCAL_ONLY"
    graph = image_state["evidence_graph"]
    assert any(node["type"] == "raw" for node in graph["nodes"])
    assert any(node["type"] == "model_run" for node in graph["nodes"])
    assert any(node["type"] == "reference" for node in graph["nodes"])
    assert any(edge["relation"] == "derived_from" for edge in graph["edges"])
    assert any(edge["relation"] == "cites" for edge in graph["edges"])

    search = api_client.post(
        f"/api/sessions/{session_id}/knowledge/search",
        json={"query": "青花陶瓷 RGB 可见特征", "limit": 3, "space": "demo"},
    )
    assert search.status_code == 200, search.text
    latest_search = search.json()["session"]["knowledge_searches"][-1]
    assert latest_search["result_count"] >= 1
    assert latest_search["results"][0]["data_level"] == "DEMO/SYNTHETIC"
    assert latest_search["results"][0]["citation"]["content_sha256"]

    first_plan = api_client.post(f"/api/sessions/{session_id}/plan")
    assert first_plan.status_code == 200
    first_state = first_plan.json()["session"]
    assert first_state["current_action_id"] == "A2"
    first_run = first_state["current_action_run_id"]

    first_execution = api_client.post(
        f"/api/sessions/{session_id}/execute",
        json={"action_run_id": first_run, "replay_profile": "raman_low_snr"},
    )
    assert first_execution.status_code == 200
    assert first_execution.json()["session"]["uncertainty"] == 0.85
    assert any(
        edge["relation"] == "not_admitted"
        for edge in first_execution.json()["session"]["evidence_graph"]["edges"]
    )
    version_after_execution = first_execution.json()["session"]["version"]

    replay = api_client.post(
        f"/api/sessions/{session_id}/execute",
        json={"action_run_id": first_run, "replay_profile": "raman_low_snr"},
    )
    assert replay.status_code == 200
    assert replay.json()["session"]["version"] == version_after_execution

    second_plan = api_client.post(f"/api/sessions/{session_id}/plan").json()["session"]
    assert second_plan["current_action_id"] == "A1"
    second_execution = api_client.post(
        f"/api/sessions/{session_id}/execute",
        json={
            "action_run_id": second_plan["current_action_run_id"],
            "replay_profile": "hsi_material_anomaly",
        },
    )
    assert second_execution.status_code == 200
    assert second_execution.json()["session"]["uncertainty"] == 0.48

    report_response = api_client.post(f"/api/sessions/{session_id}/report")
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()["session"]["last_report"]
    assert report["claim_consistency"] == "REVIEW_REQUIRED"
    assert report["source_category"] == "DEMO/SYNTHETIC"
    assert "非真实鉴定结论" in report["disclaimer"]
    assert len(report["integrity"]["report_sha256"]) == 64
    assert report["assistant_summary"]["summary"]

    downloaded_json = api_client.get(f"/api/sessions/{session_id}/report.json")
    downloaded_html = api_client.get(f"/api/sessions/{session_id}/report.html")
    assert downloaded_json.status_code == 200
    assert downloaded_html.status_code == 200
    assert "DEMO/SYNTHETIC" in downloaded_html.text
    assert "SHA-256" in downloaded_html.text

    evidence = api_client.get(f"/api/sessions/{session_id}/evidence").json()
    audit = api_client.get(f"/api/sessions/{session_id}/audit").json()
    assert any(edge["relation"] == "conflicts_with" for edge in evidence["graph"]["edges"])
    assert audit["verification"]["valid"] is True
    assert audit["verification"]["event_count"] >= 8


def test_invalid_order_mime_region_and_knowledge_space_are_non_mutating(api_client):
    session_id = _new_session(api_client)
    before = api_client.get(f"/api/sessions/{session_id}").json()["session"]["version"]
    execute = api_client.post(f"/api/sessions/{session_id}/execute", json={})
    assert execute.status_code == 400

    bad_region = api_client.post(
        f"/api/sessions/{session_id}/images/analyze",
        json={
            "filename": "artifact.jpg",
            "mime_type": "image/jpeg",
            "image_base64": _image_payload(),
            "region_id": "UNKNOWN",
        },
    )
    assert bad_region.status_code == 400
    mismatch = api_client.post(
        f"/api/sessions/{session_id}/images/analyze",
        json={
            "filename": "artifact.jpg",
            "mime_type": "image/jpeg",
            "image_base64": _image_payload(),
            "region_id": "R1",
        },
    )
    assert mismatch.status_code == 400
    cross_space = api_client.post(
        f"/api/sessions/{session_id}/knowledge/search",
        json={"query": "test", "space": "formal"},
    )
    assert cross_space.status_code == 400
    after = api_client.get(f"/api/sessions/{session_id}").json()["session"]["version"]
    assert after == before


def test_concurrent_plans_reserve_budget_once(api_client):
    session_id = _new_session(api_client)
    service = api_client.app.state.service

    def attempt_plan():
        try:
            service.plan(session_id)
            return "reserved"
        except ValueError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt_plan(), range(2)))
    assert sorted(outcomes) == ["rejected", "reserved"]
    state = service.envelope(session_id)["session"]
    assert state["risk_budgets"]["R1"]["photochemical"]["reserved"] == 0.20
    assert state["current_action_run_id"].startswith("RUN-")


def test_model_failures_are_recorded_and_report_falls_back(api_client):
    from app.services.vlm import OpenAICompatibleClient

    session_id = _new_session(api_client)
    service = api_client.app.state.service
    service.vision_client = OpenAICompatibleClient("", "", "disabled-vision")
    image = api_client.post(
        f"/api/sessions/{session_id}/images/analyze",
        json={
            "filename": "artifact.png",
            "mime_type": "image/png",
            "image_base64": _image_payload(),
            "region_id": "R1",
        },
    )
    assert image.status_code == 200
    run = image.json()["session"]["model_runs"][-1]
    assert run["status"] == "DEGRADED"
    assert run["error_category"] == "NotConfigured"
    assert run["node_id"] == service.settings.node_id

    service.reasoner_client = OpenAICompatibleClient("", "", "disabled-reasoner")
    response = api_client.post(f"/api/sessions/{session_id}/report")
    assert response.status_code == 200
    report = response.json()["session"]["last_report"]
    assert "未参与摘要" in report["assistant_summary"]["summary"]
    assert report["model_runs"][-1]["status"] == "DEGRADED"
