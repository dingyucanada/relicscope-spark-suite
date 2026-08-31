from __future__ import annotations

import base64
import io

import numpy as np
from PIL import Image


def _image_payload(marker: int = 0) -> str:
    y, x = np.indices((320, 320))
    pixels = np.stack(
        [50 + (x % 180), 45 + (y % 160), 80 + ((x + y) % 170)], axis=2
    ).astype(np.uint8)
    pixels[:8, :8] = (marker % 256, (marker * 3) % 256, (marker * 7) % 256)
    stream = io.BytesIO()
    Image.fromarray(pixels).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def _new_session(client) -> str:
    response = client.post(
        "/api/sessions",
        json={"artifact_name": "目录检索测试器物"},
    )
    assert response.status_code == 201
    return response.json()["session"]["id"]


class _EmbeddingHealthStub:
    async def health(self):
        return {
            "name": "reference_image_embedding",
            "status": "online",
            "detail": "test stub",
            "model": "test-reference-embedding",
        }


class _RecognitionStub:
    def __init__(self) -> None:
        self.embedding_client = _EmbeddingHealthStub()

    def summary(self):
        return {
            "enabled": True,
            "readiness": "READY",
            "library_id": "demo-50",
            "library_version": "v1@test",
            "manifest_sha256": "a" * 64,
            "reference_artifact_count": 50,
            "reference_image_count": 250,
            "counterfeit_record_count": 10,
            "calibration_record_sha256": "b" * 64,
            "authenticity_state": "NOT_ASSESSED",
            "boundary": "test boundary",
        }

    def refresh(self):
        return self.summary()

    async def recognize(
        self, query_images, *, view_ids, qualities, angles, top_k
    ):
        assert len(query_images) == len(view_ids) == len(qualities) == len(angles) == 1
        assert top_k == 5
        return {
            "run_status": "COMPLETED",
            "decision_status": "KNOWN_ARTIFACT_CANDIDATE",
            "same_artifact": {
                "accepted": True,
                "status": "KNOWN_ARTIFACT_CANDIDATE",
                "artifact_id": "ART-001",
                "score": 0.94,
                "runner_up_margin": 0.17,
                "gates": {"counterfeit_conflict_absent": True},
                "reason_codes": [],
            },
            "related": {
                "accepted": True,
                "status": "related_candidates",
                "qualifying_artifact_ids": ["ART-001"],
            },
            "catalog_hits": [
                {
                    "artifact_id": "ART-001",
                    "score": 0.94,
                    "coverage": 0.6,
                    "matched_views": [],
                    "metadata": {
                        "display_name": "馆藏测试器",
                        "record_sha256": "c" * 64,
                    },
                }
            ],
            "counterfeit_hits": [
                {
                    "artifact_id": "NEG-001",
                    "score": 0.51,
                    "coverage": 0.2,
                    "matched_views": [],
                    "metadata": {
                        "display_name": "经审核负向对照",
                        "record_sha256": "9" * 64,
                        "citation_id": "REFERENCE:aaaaaaaaaaaa:NEG-001",
                    },
                }
            ],
            "counterfeit_cross_check": {
                "status": "WEAK_SIGNAL",
                "candidates": [
                    {
                        "artifact_id": "NEG-001",
                        "score": 0.51,
                        "coverage": 0.2,
                        "matched_views": [],
                        "metadata": {
                            "display_name": "经审核负向对照",
                            "record_sha256": "9" * 64,
                            "citation_id": "REFERENCE:aaaaaaaaaaaa:NEG-001",
                        },
                    }
                ],
                "interpretation": "未触发当前负向参考阈值；这不证明器物为真。",
            },
            "embedding_run": {
                "available": True,
                "model": "test-reference-embedding",
                "model_source": "test/source",
                "model_revision": "d" * 40,
                "model_identity_verified": True,
                "request_id": "embedding-request-test",
                "instruction_sha256": "e" * 64,
                "input_hashes": [query_images[0].sha256],
                "output_hashes": ["f" * 64],
                "latency_ms": 1,
            },
            "reference_library": self.summary(),
            "authenticity_state": "NOT_ASSESSED",
            "limitation": "目录检索不构成真伪结论。",
            "result_snapshot_sha256": "0" * 64,
        }


def test_reference_library_summary_is_explicitly_disabled_by_default(api_client):
    response = api_client.get("/api/reference-library/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["readiness"] == "DISABLED"
    assert body["authenticity_state"] == "NOT_ASSESSED"


def test_recognition_route_persists_audited_image_only_result(api_client):
    api_client.app.state.service.reference_recognition = _RecognitionStub()
    session_id = _new_session(api_client)
    image = api_client.post(
        f"/api/sessions/{session_id}/images/analyze",
        json={
            "filename": "front.png",
            "mime_type": "image/png",
            "image_base64": _image_payload(),
            "modality": "RGB",
            "region_id": "R1",
            "view_code": "FRONT",
        },
    )
    assert image.status_code == 200, image.text
    analysis_id = image.json()["session"]["image_analyses"][0]["id"]

    response = api_client.post(
        f"/api/sessions/{session_id}/recognition",
        json={"image_analysis_ids": [analysis_id], "top_k": 5},
    )

    assert response.status_code == 200, response.text
    state = response.json()["session"]
    recognition = state["latest_reference_recognition"]
    assert recognition["decision_status"] == "KNOWN_ARTIFACT_CANDIDATE"
    assert recognition["schema_version"] == "relicscope-reference-recognition-v1"
    assert recognition["same_artifact"]["artifact_id"] == "ART-001"
    assert recognition["view_codes"] == ["FRONT"]
    assert recognition["quality_algorithm_id"] == (
        "relicscope-reference-image-quality-v1"
    )
    assert recognition["quality_scores"] == [0.8]
    assert recognition["authenticity_state"] == "NOT_ASSESSED"
    assert recognition["related_report"]["decision_basis"]["status"] == (
        "KNOWN_ARTIFACT_CANDIDATE"
    )
    assert recognition["related_report"]["decision_basis"][
        "authenticity_state"
    ] == "NOT_ASSESSED"
    assert recognition["related_report"]["shared_observations"]
    assert len(recognition["result_snapshot_sha256"]) == 64
    assert state["authenticity_state"] == "NOT_ASSESSED"
    assert "vectors" not in recognition["embedding_run"]
    assert state["model_runs"][-1]["role"] == "reference_image_embedding"
    assert any(
        node["id"].startswith("reference:catalog:")
        and node["id"].endswith(":ART-001")
        for node in state["evidence_graph"]["nodes"]
    )
    assert any(
        edge["relation"] == "cross_checks"
        for edge in state["evidence_graph"]["edges"]
    )
    assert any(
        node["type"] == "interpretation"
        for node in state["evidence_graph"]["nodes"]
    )
    reference_model_nodes = [
        node
        for node in state["evidence_graph"]["nodes"]
        if node["type"] == "model_run"
        and node["label"] == "本地参考图像嵌入与检索"
    ]
    assert len(reference_model_nodes) == 1
    assert any(
        edge["source"].startswith("observation:")
        and edge["target"] == reference_model_nodes[0]["id"]
        and edge["relation"] == "produced_by"
        for edge in state["evidence_graph"]["edges"]
    )

    audit = api_client.get(f"/api/sessions/{session_id}/audit").json()
    assert audit["verification"]["valid"] is True
    assert audit["events"][-1]["event_type"] == "REFERENCE_RECOGNITION_COMPLETED"

    report_response = api_client.post(f"/api/sessions/{session_id}/report")
    assert report_response.status_code == 200
    report = report_response.json()["session"]["last_report"]
    assert report["authenticity_state"] == "NOT_ASSESSED"
    assert report["latest_reference_recognition"]["decision_status"] == (
        "KNOWN_ARTIFACT_CANDIDATE"
    )
    report_html = api_client.get(f"/api/sessions/{session_id}/report.html").text
    assert "本地参考目录识别" in report_html
    assert "真伪状态" in report_html
    assert "NOT_ASSESSED" in report_html
    assert "相关性解释与边界" in report_html
    assert "观察依据" in report_html


def test_recognition_rejects_duplicate_and_unknown_analysis_ids(api_client):
    session_id = _new_session(api_client)
    duplicate = api_client.post(
        f"/api/sessions/{session_id}/recognition",
        json={"image_analysis_ids": ["IMG-X", "IMG-X"]},
    )
    assert duplicate.status_code == 422

    unknown = api_client.post(
        f"/api/sessions/{session_id}/recognition",
        json={"image_analysis_ids": ["IMG-X"]},
    )
    assert unknown.status_code == 400


def test_disabled_reference_service_preserves_blocked_query_count_and_explanation(
    api_client,
):
    session_id = _new_session(api_client)
    image = api_client.post(
        f"/api/sessions/{session_id}/images/analyze",
        json={
            "filename": "blocked-front.png",
            "mime_type": "image/png",
            "image_base64": _image_payload(22),
            "modality": "RGB",
            "region_id": "R1",
            "view_code": "FRONT",
        },
    )
    assert image.status_code == 200, image.text
    analysis_id = image.json()["session"]["image_analyses"][-1]["id"]

    response = api_client.post(
        f"/api/sessions/{session_id}/recognition",
        json={"image_analysis_ids": [analysis_id], "top_k": 5},
    )

    assert response.status_code == 200, response.text
    recognition = response.json()["session"]["latest_reference_recognition"]
    assert recognition["decision_status"] == "EMBEDDING_UNAVAILABLE"
    assert recognition["query_view_count"] == 1
    assert len(recognition["query_views"]) == 1
    assert recognition["related_report"]["decision_basis"]["status"] == (
        "EMBEDDING_UNAVAILABLE"
    )


def test_recognition_rejects_duplicate_media_and_declared_viewpoints(api_client):
    session_id = _new_session(api_client)

    def analyze(marker: int, view_code: str) -> str:
        response = api_client.post(
            f"/api/sessions/{session_id}/images/analyze",
            json={
                "filename": f"capture-{marker}.png",
                "mime_type": "image/png",
                "image_base64": _image_payload(marker),
                "modality": "RGB",
                "region_id": "R1",
                "view_code": view_code,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["session"]["image_analyses"][-1]["id"]

    repeated_bytes_a = analyze(11, "FRONT")
    repeated_bytes_b = analyze(11, "BACK")
    duplicate_media = api_client.post(
        f"/api/sessions/{session_id}/recognition",
        json={"image_analysis_ids": [repeated_bytes_a, repeated_bytes_b]},
    )
    assert duplicate_media.status_code == 400
    assert "distinct uploaded image bytes" in duplicate_media.json()["detail"]

    duplicate_view_a = analyze(12, "LEFT_PROFILE")
    duplicate_view_b = analyze(13, "LEFT_PROFILE")
    duplicate_view = api_client.post(
        f"/api/sessions/{session_id}/recognition",
        json={"image_analysis_ids": [duplicate_view_a, duplicate_view_b]},
    )
    assert duplicate_view.status_code == 400
    assert "distinct declared viewpoints" in duplicate_view.json()["detail"]
