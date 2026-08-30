from __future__ import annotations

import json
from dataclasses import replace

import pytest


def test_public_model_endpoint_is_rejected(app_settings):
    unsafe = replace(
        app_settings,
        vision_base_url="https://api.example.com/v1",
        vision_api_key="local-private-service-key",
        require_private_endpoints=True,
    )
    with pytest.raises(ValueError, match="local-data boundary"):
        unsafe.validate_runtime()


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1:8000", "http://10.20.0.2:8000/v1", "http://spark-a:8000/v1"],
)
def test_private_model_endpoints_are_accepted(app_settings, url):
    replace(
        app_settings,
        vision_base_url=url,
        vision_api_key="local-private-service-key",
    ).validate_runtime()


@pytest.mark.parametrize(
    ("endpoint_field", "credential_field", "credential_name"),
    [
        ("vision_base_url", "vision_api_key", "VISION_API_KEY"),
        ("embedding_base_url", "embedding_api_key", "EMBEDDING_API_KEY"),
        ("reasoner_base_url", "reasoner_api_key", "REASONER_API_KEY"),
    ],
)
def test_configured_private_model_endpoints_require_api_keys(
    app_settings, endpoint_field, credential_field, credential_name
):
    configured = replace(
        app_settings,
        **{
            endpoint_field: "http://spark-a:8000/v1",
            credential_field: "",
        },
    )

    with pytest.raises(ValueError, match=credential_name):
        configured.validate_runtime()


def test_oversized_declared_request_is_rejected(api_client):
    response = api_client.post(
        "/api/sessions",
        content=b"{}",
        headers={"content-type": "application/json", "content-length": "999999999"},
    )
    assert response.status_code == 413, response.text
    assert response.headers["cache-control"] == "no-store"


def test_actual_streamed_request_size_is_enforced_without_content_length(app_settings):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.knowledge import KnowledgeBase

    limited = replace(app_settings, max_upload_bytes=64, max_request_bytes=128)
    knowledge = KnowledgeBase.from_path(limited.knowledge_manifest_path, offline=True)
    application = create_app(limited, knowledge=knowledge)

    def body_chunks():
        yield b'{"artifact_name":"'
        yield b"x" * 256
        yield b'"}'

    with TestClient(application) as client:
        response = client.post(
            "/api/sessions",
            content=body_chunks(),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413, response.text
    assert response.json()["error"] == "REQUEST_TOO_LARGE"


def test_video_upload_limit_forwards_chunks_without_request_prebuffer():
    import asyncio

    from app.main import RequestBodyLimitMiddleware

    upstream_calls = 0
    app_saw_upstream_calls = []
    incoming = [
        {"type": "http.request", "body": b"a" * 32, "more_body": True},
        {"type": "http.request", "body": b"b" * 32, "more_body": False},
    ]
    outgoing = []

    async def upstream_receive():
        nonlocal upstream_calls
        upstream_calls += 1
        return incoming.pop(0)

    async def downstream_send(message):
        outgoing.append(message)

    async def streaming_app(scope, receive, send):
        first = await receive()
        app_saw_upstream_calls.append(upstream_calls)
        second = await receive()
        assert first["body"] == b"a" * 32
        assert second["body"] == b"b" * 32
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(
        streaming_app,
        maximum_body=32,
        video_upload_maximum_body=128,
        frame_batch_maximum_body=96,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/sessions/RS-X/videos/register",
        "headers": [],
    }
    asyncio.run(middleware(scope, upstream_receive, downstream_send))

    # The first chunk reached the application after exactly one upstream read.
    # The previous buffering implementation consumed both chunks first.
    assert app_saw_upstream_calls == [1]
    assert outgoing[0]["status"] == 204


class HealthStub:
    def __init__(self, status: str) -> None:
        self.status = status

    async def health(self, name: str):
        return {
            "name": name,
            "status": self.status,
            "detail": "test health state",
            "model": "test-model",
        }


@pytest.mark.parametrize(("vision_status", "expected_status"), [("online", 200), ("degraded", 503)])
def test_dual_node_readiness_requires_multimodal_compute(
    app_settings, vision_status, expected_status
):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.knowledge import KnowledgeBase

    dual = replace(app_settings, runtime_mode="dual-node")
    knowledge = KnowledgeBase.from_path(dual.knowledge_manifest_path, offline=True)
    application = create_app(dual, knowledge=knowledge)
    application.state.service.vision_client = HealthStub(vision_status)

    with TestClient(application) as client:
        response = client.get("/health/ready")

    assert response.status_code == expected_status
    if expected_status == 503:
        assert response.json()["status"] == "not_ready"
        assert "spark-a-vision" in response.json()["required_unavailable"]


def test_request_validation_does_not_echo_rejected_input(api_client):
    secret_input = "private-input-marker-" + "x" * 140
    response = api_client.post(
        "/api/sessions",
        json={"artifact_name": secret_input},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "REQUEST_VALIDATION_ERROR"
    assert secret_input not in response.text


def test_operation_errors_redact_secret_like_values(api_client, monkeypatch):
    secret = "never-return-this-token"

    def fail_safely(_):
        raise ValueError(f"API_KEY={secret}")

    monkeypatch.setattr(api_client.app.state.service, "create_session", fail_safely)
    response = api_client.post("/api/sessions", json={})

    assert response.status_code == 400
    assert secret not in response.text
    assert "REDACTED" in response.text


def test_health_and_audit_do_not_leak_model_keys(api_client):
    service = api_client.app.state.service
    secret = "never-log-this-secret"
    service.vision_client.api_key = secret
    service.reasoner_client.api_key = secret
    body = api_client.get("/api/health").json()
    assert secret not in json.dumps(body, ensure_ascii=False)
