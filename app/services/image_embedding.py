from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Sequence
from urllib.parse import urlparse

import httpx


REFERENCE_EMBEDDING_INSTRUCTION = (
    "Represent this porcelain photograph for instance-level retrieval. Preserve "
    "shape, painted layout, marks, foot, rim, glaze and local distinguishing details; "
    "do not infer authenticity, age or market value."
)
_IMMUTABLE_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.IGNORECASE)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)


class ImageEmbeddingError(RuntimeError):
    """Raised when a local image-embedding response cannot be trusted."""


@dataclass(frozen=True)
class EmbeddingImage:
    content: bytes
    mime_type: str
    sha256: str


def _private_or_local_url(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "host.docker.internal"} or hostname.endswith(
        ".local"
    ):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return "." not in hostname
    return bool(address.is_private or address.is_loopback or address.is_link_local)


def _normalized(vector: Sequence[float]) -> list[float]:
    values = [float(value) for value in vector]
    if not values or not all(math.isfinite(value) for value in values):
        raise ImageEmbeddingError("embedding vector is empty or non-finite")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        raise ImageEmbeddingError("embedding vector has zero norm")
    if not 0.995 <= norm <= 1.005:
        raise ImageEmbeddingError("embedding vector is not unit-normalized")
    return values


def vector_sha256(vector: Sequence[float]) -> str:
    payload = json.dumps(
        [float(value) for value in vector], separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LocalImageEmbeddingClient:
    """Strict client for the private Qwen multimodal embedding sidecar."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        model_source: str,
        model_revision: str,
        expected_dimension: int = 2048,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.model_source = model_source
        self.model_revision = model_revision.lower()
        self.expected_dimension = int(expected_dimension)
        self.timeout_seconds = float(timeout_seconds)
        if self.base_url and not _private_or_local_url(self.base_url):
            raise ValueError("public image-embedding endpoints are blocked")
        if self.expected_dimension < 64 or self.expected_dimension > 8192:
            raise ValueError("reference embedding dimension is outside the safe range")

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    @property
    def immutable_identity_configured(self) -> bool:
        return bool(
            self.configured
            and self.model
            and self.model_source
            and _IMMUTABLE_REVISION.fullmatch(self.model_revision)
        )

    def _endpoint(self, suffix: str) -> str:
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/{suffix.lstrip('/')}"
        return f"{self.base_url}/v1/{suffix.lstrip('/')}"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def health(self) -> Dict[str, Any]:
        if not self.immutable_identity_configured:
            return {
                "name": "reference_image_embedding",
                "status": "disabled" if not self.configured else "degraded",
                "detail": "endpoint or immutable model identity is not configured",
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
            }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 8.0)) as client:
                response = await client.get(
                    self._endpoint("health"), headers=self._headers()
                )
                response.raise_for_status()
                body = response.json()
            identity_valid = (
                body.get("model") == self.model
                and body.get("model_source") == self.model_source
                and str(body.get("model_revision", "")).lower()
                == self.model_revision
                and body.get("dimension") == self.expected_dimension
                and body.get("ready") is True
            )
            return {
                "name": "reference_image_embedding",
                "status": "online" if identity_valid else "degraded",
                "detail": (
                    "private multimodal embedding endpoint ready; identity verified"
                    if identity_valid
                    else "embedding endpoint responded but identity was not verified"
                ),
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
                "dimension": self.expected_dimension,
                "model_identity_verified": identity_valid,
                "request_id": response.headers.get("x-request-id"),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            return {
                "name": "reference_image_embedding",
                "status": "degraded",
                "detail": f"endpoint unavailable: {type(exc).__name__}",
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
                "dimension": self.expected_dimension,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }

    async def embed(self, images: Sequence[EmbeddingImage]) -> Dict[str, Any]:
        if not self.immutable_identity_configured:
            return {
                "available": False,
                "status": "EMBEDDING_UNAVAILABLE",
                "error": "ImmutableEndpointIdentityNotConfigured",
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
                "instruction_sha256": hashlib.sha256(
                    REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8")
                ).hexdigest(),
            }
        if not 1 <= len(images) <= 8:
            raise ValueError("image embedding batch must contain between 1 and 8 images")
        inputs = []
        expected_hashes = []
        for image in images:
            actual_hash = hashlib.sha256(image.content).hexdigest()
            if actual_hash != image.sha256.lower() or _SHA256.fullmatch(actual_hash) is None:
                raise ValueError("image bytes do not match the declared SHA-256")
            if image.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise ValueError("unsupported image MIME for reference embedding")
            inputs.append(
                {
                    "mime_type": image.mime_type,
                    "image_base64": base64.b64encode(image.content).decode("ascii"),
                    "sha256": actual_hash,
                }
            )
            expected_hashes.append(actual_hash)
        payload = {
            "model": self.model,
            "instruction": REFERENCE_EMBEDDING_INSTRUCTION,
            "inputs": inputs,
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    self._endpoint("image-embeddings"),
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            if body.get("model") != self.model:
                raise ImageEmbeddingError("embedding response model identity mismatch")
            if body.get("model_source") != self.model_source:
                raise ImageEmbeddingError("embedding response model source mismatch")
            if str(body.get("model_revision", "")).lower() != self.model_revision:
                raise ImageEmbeddingError("embedding response model revision mismatch")
            request_id = body.get("request_id") or response.headers.get("x-request-id")
            if not isinstance(request_id, str) or not request_id.strip():
                raise ImageEmbeddingError("embedding response lacks a request identifier")
            if body.get("dimension") != self.expected_dimension:
                raise ImageEmbeddingError("embedding response dimension mismatch")
            instruction_hash = hashlib.sha256(
                REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8")
            ).hexdigest()
            if body.get("instruction_sha256") != instruction_hash:
                raise ImageEmbeddingError("embedding response instruction binding mismatch")
            data = body.get("data")
            if not isinstance(data, list) or len(data) != len(images):
                raise ImageEmbeddingError("embedding response item count mismatch")
            ordered = sorted(data, key=lambda item: item.get("index", -1))
            vectors = []
            output_hashes = []
            for index, item in enumerate(ordered):
                if item.get("index") != index or item.get("input_sha256") != expected_hashes[index]:
                    raise ImageEmbeddingError("embedding response input binding mismatch")
                vector = _normalized(item.get("embedding", []))
                if len(vector) != self.expected_dimension:
                    raise ImageEmbeddingError("embedding vector dimension mismatch")
                output_hash = vector_sha256(vector)
                if item.get("output_sha256") != output_hash:
                    raise ImageEmbeddingError("embedding vector hash mismatch")
                vectors.append(vector)
                output_hashes.append(output_hash)
            return {
                "available": True,
                "status": "SUCCESS",
                "mode": "local_multimodal_embedding",
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
                "model_identity_verified": True,
                "request_id": request_id,
                "instruction_sha256": instruction_hash,
                "dimension": self.expected_dimension,
                "input_hashes": expected_hashes,
                "output_hashes": output_hashes,
                "vectors": vectors,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        except ImageEmbeddingError as exc:
            return {
                "available": False,
                "status": "EMBEDDING_UNAVAILABLE",
                "error": type(exc).__name__,
                "error_detail": str(exc),
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
                "instruction_sha256": hashlib.sha256(
                    REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8")
                ).hexdigest(),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            return {
                "available": False,
                "status": "EMBEDDING_UNAVAILABLE",
                "error": type(exc).__name__,
                "model": self.model,
                "model_source": self.model_source,
                "model_revision": self.model_revision,
                "instruction_sha256": hashlib.sha256(
                    REFERENCE_EMBEDDING_INSTRUCTION.encode("utf-8")
                ).hexdigest(),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }


__all__ = [
    "EmbeddingImage",
    "ImageEmbeddingError",
    "LocalImageEmbeddingClient",
    "REFERENCE_EMBEDDING_INSTRUCTION",
    "vector_sha256",
]

