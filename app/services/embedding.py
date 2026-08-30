from __future__ import annotations

import hashlib
import ipaddress
import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence
from urllib.parse import urlparse

import httpx


class EmbeddingUnavailable(RuntimeError):
    """Raised when an embedding provider cannot safely serve a request."""


class EmbeddingProvider(Protocol):
    algorithm: str
    model: str
    networked: bool

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        ...


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: List[List[float]]
    provider: str
    model: str
    algorithm: str
    degraded: bool
    degraded_reason: Optional[str]


_ASCII_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*", re.IGNORECASE)
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def tokenize(text: str) -> List[str]:
    """Tokenize Chinese and Latin text without language packs or network access."""

    normalized = " ".join(str(text).strip().lower().split())
    tokens = _ASCII_TOKEN.findall(normalized)
    for run in _CJK_RUN.findall(normalized):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


class DeterministicEmbeddingProvider:
    """Stable signed feature hashing used as the offline-only fallback."""

    algorithm = "relicscope-signed-hash-embedding-v1"
    model = "deterministic-local-no-network"
    networked = False

    def __init__(self, dimension: int = 96) -> None:
        if dimension < 16:
            raise ValueError("deterministic embedding dimension must be at least 16")
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for text in texts:
            vector = [0.0] * self.dimension
            for token in tokenize(text):
                digest = hashlib.sha256(token.encode("utf-8")).digest()
                index = int.from_bytes(digest[:4], "big") % self.dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                vector[index] += sign
            norm = math.sqrt(sum(value * value for value in vector))
            if norm:
                vector = [value / norm for value in vector]
            vectors.append(vector)
        return vectors


def _is_private_or_local_host(hostname: str) -> bool:
    value = hostname.strip().lower().rstrip(".")
    if not value:
        return False
    if value in {"localhost", "host.docker.internal"} or value.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        # A single-label Docker/Compose service name is resolved only inside the
        # configured container network and cannot denote a public DNS suffix.
        return "." not in value
    return address.is_private or address.is_loopback or address.is_link_local


class OpenAICompatibleEmbeddingProvider:
    """Opt-in client for a locally approved OpenAI-compatible embedding service.

    Network use is disabled by default. Even when explicitly enabled, public
    hostnames are rejected unless the caller separately opts into them.
    """

    algorithm = "openai-compatible-embeddings-v1"
    networked = True

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout_seconds: float = 20.0,
        allow_network: bool = False,
        allow_public_endpoint: bool = False,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("embedding base_url must be an absolute HTTP(S) URL")
        if not allow_public_endpoint and not _is_private_or_local_host(parsed.hostname):
            raise ValueError("public embedding endpoints are blocked by local-data policy")
        self.local_only_endpoint = not allow_public_endpoint
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.allow_network = allow_network
        if not self.model:
            raise ValueError("embedding model is required")

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        if not self.allow_network:
            raise EmbeddingUnavailable("network embedding is disabled by policy")
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(
                f"{self.base_url}/embeddings",
                headers=headers,
                json={"model": self.model, "input": list(texts), "encoding_format": "float"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload: Dict[str, Any] = response.json()
            data = payload.get("data")
            if not isinstance(data, list) or len(data) != len(texts):
                raise EmbeddingUnavailable("embedding response has an invalid item count")
            ordered = sorted(data, key=lambda item: item.get("index", -1))
            vectors: List[List[float]] = []
            dimension: Optional[int] = None
            for item in ordered:
                raw = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(raw, list) or not raw:
                    raise EmbeddingUnavailable("embedding response contains an invalid vector")
                vector = [float(value) for value in raw]
                if not all(math.isfinite(value) for value in vector):
                    raise EmbeddingUnavailable("embedding response contains a non-finite value")
                dimension = len(vector) if dimension is None else dimension
                if len(vector) != dimension:
                    raise EmbeddingUnavailable("embedding response dimensions are inconsistent")
                vectors.append(vector)
            return vectors
        except EmbeddingUnavailable:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise EmbeddingUnavailable(type(exc).__name__) from exc


class EmbeddingRuntime:
    """Use an optional approved provider and fail closed to a local algorithm."""

    def __init__(
        self,
        primary: Optional[EmbeddingProvider] = None,
        *,
        offline: bool = True,
        fallback: Optional[EmbeddingProvider] = None,
    ) -> None:
        self.primary = primary
        self.offline = offline
        self.fallback = fallback or DeterministicEmbeddingProvider()

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        reason: Optional[str] = None
        if self.primary is None:
            reason = "EMBEDDING_SERVICE_NOT_CONFIGURED"
        elif (
            self.offline
            and getattr(self.primary, "networked", False)
            and not getattr(self.primary, "local_only_endpoint", False)
        ):
            reason = "OFFLINE_POLICY_BLOCKED_NETWORK_PROVIDER"
        else:
            try:
                vectors = self.primary.embed(texts)
                return EmbeddingBatch(
                    vectors=vectors,
                    provider=type(self.primary).__name__,
                    model=self.primary.model,
                    algorithm=self.primary.algorithm,
                    degraded=False,
                    degraded_reason=None,
                )
            except Exception as exc:  # The fallback must survive a provider implementation failure.
                reason = f"EMBEDDING_PROVIDER_UNAVAILABLE:{type(exc).__name__}"

        vectors = self.fallback.embed(texts)
        return EmbeddingBatch(
            vectors=vectors,
            provider=type(self.fallback).__name__,
            model=self.fallback.model,
            algorithm=self.fallback.algorithm,
            degraded=True,
            degraded_reason=reason,
        )


__all__ = [
    "DeterministicEmbeddingProvider",
    "EmbeddingBatch",
    "EmbeddingProvider",
    "EmbeddingRuntime",
    "EmbeddingUnavailable",
    "OpenAICompatibleEmbeddingProvider",
    "tokenize",
]
