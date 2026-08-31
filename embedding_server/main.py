from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import math
import os
import re
from contextlib import asynccontextmanager
from typing import Any, Literal
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from PIL import Image, UnidentifiedImageError


_REVISION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", re.IGNORECASE)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)
_FORMAT_MIME = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MODEL_SOURCE = os.getenv("REFERENCE_EMBEDDING_MODEL_SOURCE", "Qwen/Qwen3-VL-Embedding-2B")
MODEL_REVISION = os.getenv("REFERENCE_EMBEDDING_MODEL_REVISION", "").lower()
SERVED_MODEL = os.getenv("REFERENCE_EMBEDDING_MODEL", "qwen3_vl_embedding_2b")
EXPECTED_DIMENSION = int(os.getenv("REFERENCE_EMBEDDING_DIMENSION", "2048"))
MAX_IMAGE_BYTES = int(os.getenv("REFERENCE_EMBEDDING_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("REFERENCE_EMBEDDING_MAX_IMAGE_PIXELS", "25000000"))
BATCH_SIZE = int(os.getenv("REFERENCE_EMBEDDING_BATCH_SIZE", "4"))
GPU_MEMORY_FRACTION = float(os.getenv("REFERENCE_EMBEDDING_GPU_MEMORY_FRACTION", "0.12"))


class ImageInput(BaseModel):
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    image_base64: str = Field(min_length=8)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("image_base64")
    @classmethod
    def no_data_url(cls, value: str) -> str:
        if value.startswith("data:"):
            raise ValueError("data URL prefixes are not accepted")
        return value


class EmbeddingRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    instruction: str = Field(min_length=20, max_length=1000)
    inputs: list[ImageInput] = Field(min_length=1, max_length=8)


def _load_service_key() -> str:
    path = os.getenv("REFERENCE_EMBEDDING_API_KEY_FILE", "/run/secrets/service_api_key")
    try:
        value = open(path, "r", encoding="utf-8").read().strip()
    except OSError as exc:
        raise RuntimeError("embedding service API key file is unavailable") from exc
    if len(value) < 32:
        raise RuntimeError("embedding service API key is shorter than 32 characters")
    return value


def _decode_image(item: ImageInput) -> tuple[Image.Image, str]:
    try:
        content = base64.b64decode(item.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="invalid base64 image") from exc
    if len(content) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="image exceeds configured limit")
    digest = hashlib.sha256(content).hexdigest()
    if digest != item.sha256 or _SHA256.fullmatch(digest) is None:
        raise HTTPException(status_code=400, detail="image SHA-256 mismatch")
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image decode failed") from exc
    detected_mime = _FORMAT_MIME.get(str(image.format).upper())
    if detected_mime != item.mime_type:
        raise HTTPException(status_code=400, detail="image MIME mismatch")
    if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_IMAGE_PIXELS:
        raise HTTPException(status_code=400, detail="image dimensions outside safe limits")
    return image.convert("RGB"), digest


def _vector_hash(vector: list[float]) -> str:
    payload = json.dumps(vector, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize_vector(raw: Any) -> list[float]:
    values = [float(value) for value in raw]
    if len(values) != EXPECTED_DIMENSION or not all(math.isfinite(value) for value in values):
        raise RuntimeError("model returned an invalid embedding vector")
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        raise RuntimeError("model returned a zero embedding vector")
    values = [value / norm for value in values]
    return values


@asynccontextmanager
async def lifespan(application: FastAPI):
    if not _REVISION.fullmatch(MODEL_REVISION):
        raise RuntimeError("REFERENCE_EMBEDDING_MODEL_REVISION must be an immutable commit")
    if not 64 <= EXPECTED_DIMENSION <= 8192:
        raise RuntimeError("REFERENCE_EMBEDDING_DIMENSION is outside the safe range")
    if not 0.02 <= GPU_MEMORY_FRACTION <= 0.30:
        raise RuntimeError("REFERENCE_EMBEDDING_GPU_MEMORY_FRACTION is outside the safe range")
    service_key = _load_service_key()
    import torch
    from sentence_transformers import SentenceTransformer

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is required for the formal embedding service")
    torch.cuda.set_per_process_memory_fraction(GPU_MEMORY_FRACTION, device=0)
    model = SentenceTransformer(
        MODEL_SOURCE,
        revision=MODEL_REVISION,
        device="cuda",
        local_files_only=True,
        model_kwargs={"torch_dtype": torch.bfloat16},
    )
    if not model.supports("image"):
        raise RuntimeError("loaded embedding model does not advertise image support")
    if model.get_sentence_embedding_dimension() != EXPECTED_DIMENSION:
        raise RuntimeError("loaded embedding model dimension does not match configuration")
    application.state.model = model
    application.state.service_key = service_key
    application.state.ready = True
    try:
        yield
    finally:
        application.state.ready = False


app = FastAPI(
    title="RelicScope private image embedding",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


def require_api_key(authorization: str | None = Header(default=None)) -> None:
    expected = getattr(app.state, "service_key", "")
    if not authorization or authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    return {
        "ready": bool(getattr(app.state, "ready", False)),
        "model": SERVED_MODEL,
        "model_source": MODEL_SOURCE,
        "model_revision": MODEL_REVISION,
        "dimension": EXPECTED_DIMENSION,
        "device": "cuda",
    }


@app.post("/v1/image-embeddings", dependencies=[Depends(require_api_key)])
async def image_embeddings(payload: EmbeddingRequest) -> dict[str, Any]:
    if payload.model != SERVED_MODEL:
        raise HTTPException(status_code=400, detail="served model mismatch")
    decoded = [_decode_image(item) for item in payload.inputs]
    # Sentence Transformers 5.4 routes multimodal dictionaries through the
    # checkpoint's modality_config. Bind the same task instruction to both
    # reference and query images so they remain in one reproducible space.
    model_inputs = [
        {"image": image, "text": payload.instruction} for image, _ in decoded
    ]
    try:
        output = app.state.model.encode(
            model_inputs,
            normalize_embeddings=True,
            batch_size=min(BATCH_SIZE, len(model_inputs)),
            show_progress_bar=False,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"embedding failed: {type(exc).__name__}") from exc
    vectors = [_normalize_vector(vector) for vector in output]
    request_id = f"emb-{uuid4().hex}"
    return {
        "request_id": request_id,
        "model": SERVED_MODEL,
        "model_source": MODEL_SOURCE,
        "model_revision": MODEL_REVISION,
        "dimension": EXPECTED_DIMENSION,
        "instruction_sha256": hashlib.sha256(payload.instruction.encode("utf-8")).hexdigest(),
        "data": [
            {
                "index": index,
                "input_sha256": decoded[index][1],
                "embedding": vector,
                "output_sha256": _vector_hash(vector),
            }
            for index, vector in enumerate(vectors)
        ],
    }
