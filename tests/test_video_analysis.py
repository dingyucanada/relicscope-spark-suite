from __future__ import annotations

import base64
import io
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image


def _new_session(client) -> str:
    response = client.post("/api/sessions", json={"artifact_name": "视频测试青花瓷"})
    assert response.status_code == 201, response.text
    return response.json()["session"]["id"]


def _fake_mp4(payload_size: int = 1024) -> bytes:
    header = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2"
    return header + b"v" * max(payload_size - len(header), 0)


def _frame(seed: int) -> tuple[str, bytes]:
    generator = np.random.default_rng(seed)
    array = generator.integers(25, 231, size=(320, 320, 3), dtype=np.uint8)
    # Add a stable ceramic-like blue band while retaining different viewpoints.
    array[80:240, (seed * 17) % 120 : ((seed * 17) % 120) + 80, 2] = 230
    image = Image.fromarray(array)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    return base64.b64encode(raw).decode("ascii"), raw


def _solid_frame(level: int) -> str:
    image = Image.new("RGB", (320, 320), color=(level, level, level))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _register_video(
    client,
    session_id: str,
    *,
    video_bytes: bytes | None = None,
    duration_ms: int = 4000,
):
    return client.post(
        f"/api/sessions/{session_id}/videos/register",
        files={"file": ("rotation.mp4", video_bytes or _fake_mp4(), "video/mp4")},
        data={
            "modality": "RGB_VIDEO",
            "region_id": "R1",
            "duration_ms": str(duration_ms),
            "capture_note": "环绕器物一周",
        },
    )


def test_video_upload_frame_analysis_evidence_integrity_and_report(api_client):
    session_id = _new_session(api_client)
    registered = _register_video(api_client, session_id)
    assert registered.status_code == 201, registered.text
    state = registered.json()["session"]
    video = state["videos"][-1]
    assert video["status"] == "REGISTERED"
    assert len(video["sha256"]) == 64
    assert state["raw_files"][-1]["media_kind"] == "VIDEO"

    frames = []
    frame_hashes = []
    for timestamp, seed in zip((0, 1200, 2600, 4000), (11, 22, 33, 44)):
        encoded, raw = _frame(seed)
        frames.append(
            {
                "timestamp_ms": timestamp,
                "mime_type": "image/png",
                "image_base64": encoded,
            }
        )
        frame_hashes.append(raw)
    analyzed = api_client.post(
        f"/api/sessions/{session_id}/videos/{video['id']}/analyze",
        json={
            "duration_ms": 4000,
            "sampling_strategy": "uniform-browser-v1",
            "frames": frames,
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    envelope = analyzed.json()
    result = envelope["session"]["video_analyses"][-1]
    assert len(result["frames"]) == 4
    assert 2 <= len(result["representative_frame_ids"]) <= 3
    assert result["visible_observations"]
    assert result["next_best_observation"]["risk_class"]
    assert "不判断真伪" in result["conclusion_boundary"]
    assert len(envelope["session"]["raw_files"]) == 5
    assert envelope["integrity"]["valid"] is True
    assert envelope["integrity"]["raw_files"]["checked_count"] == 5

    stored = api_client.app.state.service.store.list_raw_files(session_id)
    assert len(stored) == 5
    frame_records = [
        item for item in stored if item["metadata"].get("media_kind") == "VIDEO_FRAME"
    ]
    assert len(frame_records) == 4
    assert all(Path(item["path"]).is_file() for item in frame_records)
    assert all(
        item["metadata"]["parent_file_id"] == video["file_id"] for item in frame_records
    )

    graph = envelope["session"]["evidence_graph"]
    video_node = f"raw:{session_id}:{video['file_id']}"
    frame_nodes = {
        f"observation:{session_id}:{item['id']}" for item in result["frames"]
    }
    assert all(
        any(
            edge["source"] == node
            and edge["target"] == video_node
            and edge["relation"] == "derived_from"
            for edge in graph["edges"]
        )
        for node in frame_nodes
    )
    audit = api_client.get(f"/api/sessions/{session_id}/audit").json()
    assert [item["event_type"] for item in audit["events"]][-2:] == [
        "VIDEO_REGISTERED",
        "VIDEO_FRAMES_ANALYZED",
    ]

    report_response = api_client.post(f"/api/sessions/{session_id}/report")
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()["session"]["last_report"]
    assert report["media_summary"]["registered_video_count"] == 1
    assert report["media_summary"]["sampled_frame_count"] == 4
    assert report["video_analyses"][0]["frames"][0]["sha256"]
    html = api_client.get(f"/api/sessions/{session_id}/report.html")
    assert "视频多帧观察" in html.text
    assert "下一项最佳观察" in html.text


def test_duplicate_frames_are_retained_but_suppressed_from_admission(api_client):
    session_id = _new_session(api_client)
    video = _register_video(api_client, session_id).json()["session"]["videos"][-1]
    encoded_a, _ = _frame(7)
    encoded_b, _ = _frame(8)
    encoded_c, _ = _frame(9)
    response = api_client.post(
        f"/api/sessions/{session_id}/videos/{video['id']}/analyze",
        json={
            "duration_ms": 4000,
            "frames": [
                {
                    "timestamp_ms": 0,
                    "mime_type": "image/png",
                    "image_base64": encoded_a,
                },
                {
                    "timestamp_ms": 1000,
                    "mime_type": "image/png",
                    "image_base64": encoded_a,
                },
                {
                    "timestamp_ms": 2500,
                    "mime_type": "image/png",
                    "image_base64": encoded_b,
                },
                {
                    "timestamp_ms": 4000,
                    "mime_type": "image/png",
                    "image_base64": encoded_c,
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()["session"]["video_analyses"][-1]
    assert result["sampling_summary"]["duplicate_suppressed_count"] == 1
    duplicate = next(
        item
        for item in result["frames"]
        if item["admission_status"] == "DUPLICATE_SUPPRESSED"
    )
    assert duplicate["duplicate_of"]
    assert duplicate["sha256"]
    assert len(response.json()["session"]["raw_files"]) == 5


def test_native_video_model_run_is_bound_to_original_video_and_report(api_client):
    session_id = _new_session(api_client)
    demo_video = (
        Path(__file__).parents[1] / "demo_media" / "synthetic_orbit.mp4"
    ).read_bytes()
    registered = _register_video(
        api_client,
        session_id,
        video_bytes=demo_video,
        duration_ms=3000,
    )
    video = registered.json()["session"]["videos"][-1]

    response = api_client.post(
        f"/api/sessions/{session_id}/videos/{video['id']}/native-analyze"
    )
    assert response.status_code == 200, response.text
    envelope = response.json()
    analysis = envelope["session"]["native_video_analyses"][-1]
    run = envelope["session"]["model_runs"][-1]
    assert analysis["analysis_kind"] == "NATIVE_VIDEO_MODEL"
    assert analysis["result"]["temporal_observations"]
    assert run["role"] == "native_video_multimodal_observation"
    assert run["mode"] == "local_vllm"
    assert run["input_hash"] == video["sha256"]
    assert run["model_identity_verified"] is True
    assert run["provider_request_id"] == "video-test-request"
    assert run["token_usage"]["total_tokens"] == 90
    assert analysis["media_validation"]["actual_duration_ms"] == 3000
    assert analysis["media_validation"]["duration_source"] == "SERVER_PARSED_ISO_BMFF"
    assert analysis["media_validation"]["codec"] == "H264"
    assert analysis["media_validation"]["width"] == 768
    assert analysis["media_validation"]["height"] == 768
    assert envelope["integrity"]["valid"] is True

    graph = envelope["session"]["evidence_graph"]
    assert any(
        edge["target"] == f"raw:{session_id}:{video['file_id']}"
        and edge["relation"] == "analyzes"
        for edge in graph["edges"]
    )
    audit = api_client.get(f"/api/sessions/{session_id}/audit").json()
    assert audit["events"][-1]["event_type"] == "NATIVE_VIDEO_ANALYZED"

    report_response = api_client.post(f"/api/sessions/{session_id}/report")
    report = report_response.json()["session"]["last_report"]
    assert report["media_summary"]["native_video_analysis_count"] == 1
    assert report["native_video_analyses"][0]["model"] == "test-vision-model"
    html = api_client.get(f"/api/sessions/{session_id}/report.html")
    assert html.status_code == 200
    assert "原生视频模型观察" in html.text
    assert "环绕视角覆盖器身与底足" in html.text


def test_failed_native_model_run_does_not_create_an_observation(api_client):
    session_id = _new_session(api_client)
    demo_video = (
        Path(__file__).parents[1] / "demo_media" / "synthetic_orbit.mp4"
    ).read_bytes()
    registered = _register_video(
        api_client,
        session_id,
        video_bytes=demo_video,
        duration_ms=3000,
    )
    video = registered.json()["session"]["videos"][-1]

    async def unavailable_video_model(_video_data_url, _metadata):
        return {
            "available": False,
            "mode": "deterministic_fallback",
            "role": "native_video",
            "model": "test-vision-model",
            "configured_model": "test-vision-model",
            "model_identity_verified": False,
            "request_id": None,
            "usage": {},
            "finish_reason": None,
            "prompt_hash": "7" * 64,
            "latency_ms": 1,
            "output_hash": None,
            "output": None,
            "error": "MODEL_UNAVAILABLE",
        }

    api_client.app.state.service.vision_client.video_observe = unavailable_video_model
    response = api_client.post(
        f"/api/sessions/{session_id}/videos/{video['id']}/native-analyze"
    )
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    analysis = session["native_video_analyses"][-1]
    assert analysis["status"] == "DEGRADED"
    assert analysis["source_category"] == "MODEL_RUN_FAILURE"

    graph = session["evidence_graph"]
    run_id = session["model_runs"][-1]["run_id"]
    model_node_id = f"model-run:{session_id}:{run_id}"
    model_node = next(node for node in graph["nodes"] if node["id"] == model_node_id)
    assert model_node["status"] == "rejected"
    assert not any(
        node.get("label") == "原生视频跨视角观察" for node in graph["nodes"]
    )
    assert not any(
        edge.get("relation") == "produced_by"
        and edge.get("target") == model_node_id
        for edge in graph["edges"]
    )


def test_native_video_rejects_declared_duration_that_disagrees_with_mp4(api_client):
    session_id = _new_session(api_client)
    demo_video = (
        Path(__file__).parents[1] / "demo_media" / "synthetic_orbit.mp4"
    ).read_bytes()
    registered = _register_video(
        api_client,
        session_id,
        video_bytes=demo_video,
        duration_ms=9000,
    )
    video = registered.json()["session"]["videos"][-1]
    response = api_client.post(
        f"/api/sessions/{session_id}/videos/{video['id']}/native-analyze"
    )
    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]


def test_all_rejected_frames_trigger_reacquisition_without_model_admission(api_client):
    session_id = _new_session(api_client)
    video = _register_video(api_client, session_id).json()["session"]["videos"][-1]
    response = api_client.post(
        f"/api/sessions/{session_id}/videos/{video['id']}/analyze",
        json={
            "duration_ms": 4000,
            "frames": [
                {
                    "timestamp_ms": timestamp,
                    "mime_type": "image/png",
                    "image_base64": _solid_frame(level),
                }
                for timestamp, level in zip((0, 2000, 4000), (0, 3, 6))
            ],
        },
    )
    assert response.status_code == 200, response.text
    session = response.json()["session"]
    result = session["video_analyses"][-1]
    assert result["quality"]["passed"] is False
    assert result["representative_frame_ids"] == []
    assert result["sampling_summary"]["usable_frame_count"] == 0
    assert all(frame["model_run_id"] is None for frame in result["frames"])
    assert session["model_runs"] == []
    assert result["next_best_observation"]["id"] == "OBS-RGB-QUALITY"
    assert "0 帧通过逐帧准入" in result["visible_observations"][0]


def test_invalid_or_oversized_video_does_not_mutate_session(api_client, app_settings):
    session_id = _new_session(api_client)
    before = api_client.get(f"/api/sessions/{session_id}").json()["session"]["version"]
    invalid = _register_video(api_client, session_id, video_bytes=b"not-a-video")
    assert invalid.status_code == 400
    after = api_client.get(f"/api/sessions/{session_id}").json()["session"]
    assert after["version"] == before
    assert after["raw_files"] == []

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.knowledge import KnowledgeBase

    limited = replace(app_settings, max_video_bytes=64)
    knowledge = KnowledgeBase.from_path(limited.knowledge_manifest_path, offline=True)
    application = create_app(limited, knowledge=knowledge)
    with TestClient(application) as client:
        second_id = _new_session(client)
        oversized = _register_video(client, second_id, video_bytes=_fake_mp4(256))
        assert oversized.status_code == 400
        state = client.get(f"/api/sessions/{second_id}").json()["session"]
        assert state["raw_files"] == []
        upload_dir = limited.upload_dir / second_id
        assert not upload_dir.exists() or not list(upload_dir.iterdir())


def test_image_analysis_has_guidance_and_conservative_comparison(api_client):
    session_id = _new_session(api_client)
    encoded, _ = _frame(51)
    analysis_ids = []
    for filename in ("baseline.png", "followup.png"):
        response = api_client.post(
            f"/api/sessions/{session_id}/images/analyze",
            json={
                "filename": filename,
                "mime_type": "image/png",
                "image_base64": encoded,
                "modality": "RGB",
                "region_id": "R1",
            },
        )
        assert response.status_code == 200, response.text
        analysis = response.json()["session"]["image_analyses"][-1]
        assert analysis["visible_observations"]
        assert analysis["acquisition_guidance"]
        assert analysis["next_best_observation"]
        analysis_ids.append(analysis["id"])

    compared = api_client.post(
        f"/api/sessions/{session_id}/images/compare",
        json={
            "baseline_analysis_id": analysis_ids[0],
            "comparison_analysis_id": analysis_ids[1],
        },
    )
    assert compared.status_code == 200, compared.text
    comparison = compared.json()["session"]["image_comparisons"][-1]
    assert comparison["status"] == "STABLE_WITHIN_CAPTURE_TOLERANCE"
    assert "不解释为劣化" in comparison["conclusion_boundary"]
