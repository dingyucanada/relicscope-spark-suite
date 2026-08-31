from __future__ import annotations

import hashlib

import httpx
import pytest

from app.services import image_embedding as module
from app.services.image_embedding import (
    EmbeddingImage,
    LocalImageEmbeddingClient,
    REFERENCE_EMBEDDING_INSTRUCTION,
    vector_sha256,
)


def _client(**overrides) -> LocalImageEmbeddingClient:
    values = {
        "base_url": "http://reference-embedding:8010/v1",
        "api_key": "k" * 32,
        "model": "qwen3-vl-embedding-2b",
        "model_source": "Qwen/Qwen3-VL-Embedding-2B",
        "model_revision": "a" * 40,
        "expected_dimension": 64,
        "timeout_seconds": 2,
    }
    values.update(overrides)
    return LocalImageEmbeddingClient(**values)


@pytest.mark.asyncio
async def test_private_embedding_client_verifies_identity_order_and_vector_hash(
    monkeypatch,
):
    content = b"verified-image-bytes"
    digest = hashlib.sha256(content).hexdigest()
    vector = [1.0] + [0.0] * 63

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {'k' * 32}"
        if request.url.path.endswith("/health"):
            return httpx.Response(
                200,
                json={
                    "ready": True,
                    "model": "qwen3-vl-embedding-2b",
                    "model_source": "Qwen/Qwen3-VL-Embedding-2B",
                    "model_revision": "a" * 40,
                    "dimension": 64,
                },
                headers={"x-request-id": "health-1"},
            )
        return httpx.Response(
            200,
            json={
                "request_id": "embed-1",
                "model": "qwen3-vl-embedding-2b",
                "model_source": "Qwen/Qwen3-VL-Embedding-2B",
                "model_revision": "a" * 40,
                "dimension": 64,
                "instruction_sha256": hashlib.sha256(
                    REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8")
                ).hexdigest(),
                "data": [
                    {
                        "index": 0,
                        "input_sha256": digest,
                        "embedding": vector,
                        "output_sha256": vector_sha256(vector),
                    }
                ],
            },
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    client = _client()

    health = await client.health()
    result = await client.embed(
        [EmbeddingImage(content=content, mime_type="image/jpeg", sha256=digest)]
    )

    assert health["status"] == "online"
    assert health["model_identity_verified"] is True
    assert result["available"] is True
    assert result["request_id"] == "embed-1"
    assert result["input_hashes"] == [digest]
    assert result["output_hashes"] == [vector_sha256(vector)]
    assert result["vectors"] == [vector]


@pytest.mark.asyncio
async def test_embedding_client_fails_closed_on_missing_revision_or_bad_binding(
    monkeypatch,
):
    unpinned = _client(model_revision="unknown")
    unavailable = await unpinned.embed(
        [
            EmbeddingImage(
                content=b"x",
                mime_type="image/jpeg",
                sha256=hashlib.sha256(b"x").hexdigest(),
            )
        ]
    )
    assert unavailable["status"] == "EMBEDDING_UNAVAILABLE"

    vector = [1.0] + [0.0] * 63

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "request_id": "embed-bad",
                "model": "wrong-model",
                "model_source": "Qwen/Qwen3-VL-Embedding-2B",
                "model_revision": "a" * 40,
                "dimension": 64,
                "instruction_sha256": hashlib.sha256(
                    REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8")
                ).hexdigest(),
                "data": [],
            },
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(
            transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    content = b"bound-image"
    result = await _client().embed(
        [
            EmbeddingImage(
                content=content,
                mime_type="image/jpeg",
                sha256=hashlib.sha256(content).hexdigest(),
            )
        ]
    )
    assert result["available"] is False
    assert result["status"] == "EMBEDDING_UNAVAILABLE"
    assert "vectors" not in result
    assert vector_sha256(vector)


def test_embedding_client_blocks_public_endpoints_and_wrong_input_hash():
    with pytest.raises(ValueError, match="public"):
        _client(base_url="https://public.example.com/v1")

    client = _client()
    with pytest.raises(ValueError, match="SHA-256"):
        import asyncio

        asyncio.run(
            client.embed(
                [
                    EmbeddingImage(
                        content=b"content",
                        mime_type="image/jpeg",
                        sha256="0" * 64,
                    )
                ]
            )
        )
