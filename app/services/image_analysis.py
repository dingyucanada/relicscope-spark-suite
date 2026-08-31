from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import numpy as np
from PIL import Image, ImageOps


MAX_IMAGE_WIDTH = 12_000
MAX_IMAGE_HEIGHT = 12_000
MAX_IMAGE_PIXELS = 24_000_000


class _ImageDimensionError(ValueError):
    pass


@dataclass(frozen=True)
class DecodedImage:
    image: Image.Image
    raw_bytes: bytes
    sha256: str
    detected_format: str
    detected_mime: str


def _validate_image_dimensions(width: int, height: int) -> None:
    if width < 32 or height < 32:
        raise _ImageDimensionError("image is too small")
    if width > MAX_IMAGE_WIDTH or height > MAX_IMAGE_HEIGHT:
        raise _ImageDimensionError("image dimensions exceed the configured limit")
    if width * height > MAX_IMAGE_PIXELS:
        raise _ImageDimensionError("image pixel count exceeds the configured limit")


def decode_image(raw_bytes: bytes) -> DecodedImage:
    if not raw_bytes:
        raise ValueError("empty image")
    try:
        with Image.open(io.BytesIO(raw_bytes)) as probe:
            detected_format = (probe.format or "").upper()
            _validate_image_dimensions(probe.width, probe.height)
            probe.verify()
        with Image.open(io.BytesIO(raw_bytes)) as decoded:
            _validate_image_dimensions(decoded.width, decoded.height)
            image = ImageOps.exif_transpose(decoded).convert("RGB")
            _validate_image_dimensions(image.width, image.height)
    except _ImageDimensionError:
        raise
    except Exception as exc:
        raise ValueError("unsupported or damaged image") from exc
    return DecodedImage(
        image=image,
        raw_bytes=raw_bytes,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        detected_format=detected_format,
        detected_mime={
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
            "TIFF": "image/tiff",
        }.get(detected_format, "application/octet-stream"),
    )


def _normalized_entropy(gray: np.ndarray) -> float:
    hist, _ = np.histogram(gray, bins=256, range=(0, 256))
    probabilities = hist.astype(np.float64)
    probabilities /= max(float(probabilities.sum()), 1.0)
    probabilities = probabilities[probabilities > 0]
    entropy = -float(np.sum(probabilities * np.log2(probabilities)))
    return entropy / 8.0


def _gradient_metrics(gray: np.ndarray) -> Tuple[float, float, np.ndarray]:
    normalized = gray.astype(np.float32) / 255.0
    gx = np.diff(normalized, axis=1, prepend=normalized[:, :1])
    gy = np.diff(normalized, axis=0, prepend=normalized[:1, :])
    magnitude = np.sqrt(gx * gx + gy * gy)
    sharpness = float(np.var(gx) + np.var(gy)) * 10_000.0
    edge_density = float(np.mean(magnitude > 0.08))
    return sharpness, edge_density, magnitude


def _symmetry_score(gray: np.ndarray) -> float:
    width = gray.shape[1]
    half = width // 2
    if half < 2:
        return 0.0
    left = gray[:, :half].astype(np.float32)
    right = np.fliplr(gray[:, width - half :]).astype(np.float32)
    difference = float(np.mean(np.abs(left - right))) / 255.0
    return max(0.0, 1.0 - difference)


def _dhash(image: Image.Image) -> str:
    gray = ImageOps.grayscale(image).resize((9, 8), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray)
    bits = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in bits.flatten():
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def _salient_regions(magnitude: np.ndarray, count: int = 2) -> List[Dict[str, Any]]:
    height, width = magnitude.shape
    rows, columns = 3, 3
    candidates: List[Tuple[float, int, int, int, int]] = []
    for row in range(rows):
        for column in range(columns):
            x0 = int(column * width / columns)
            x1 = int((column + 1) * width / columns)
            y0 = int(row * height / rows)
            y1 = int((row + 1) * height / rows)
            score = float(np.mean(magnitude[y0:y1, x0:x1]))
            candidates.append((score, x0, y0, x1, y1))
    candidates.sort(reverse=True, key=lambda value: value[0])
    result = []
    for index, (score, x0, y0, x1, y1) in enumerate(candidates[:count], start=1):
        result.append(
            {
                "id": f"ROI-{index}",
                "score": round(score, 4),
                "bbox_normalized": [
                    round(x0 / width, 4),
                    round(y0 / height, 4),
                    round(x1 / width, 4),
                    round(y1 / height, 4),
                ],
            }
        )
    return result


def analyze_image(image: Image.Image, sha256: str) -> Dict[str, Any]:
    resized = image.copy()
    resized.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    rgb = np.asarray(resized, dtype=np.uint8)
    gray = np.asarray(ImageOps.grayscale(resized), dtype=np.uint8)

    brightness = float(np.mean(gray))
    p01, p99 = np.percentile(gray, [1, 99])
    clipped_black = float(np.mean(gray <= 5))
    clipped_white = float(np.mean(gray >= 250))
    sharpness, edge_density, magnitude = _gradient_metrics(gray)

    rgb_float = rgb.astype(np.float32)
    max_channel = np.max(rgb_float, axis=2)
    min_channel = np.min(rgb_float, axis=2)
    saturation = np.zeros_like(max_channel, dtype=np.float32)
    np.divide(
        max_channel - min_channel,
        max_channel,
        out=saturation,
        where=max_channel > 0,
    )
    blue = rgb_float[:, :, 2]
    red = rgb_float[:, :, 0]
    green = rgb_float[:, :, 1]
    blue_ratio = float(np.mean((blue > red * 1.10) & (blue > green * 1.05) & (blue > 45)))

    metrics = {
        "width": image.width,
        "height": image.height,
        "brightness_mean": round(brightness, 2),
        "dynamic_range_p01_p99": round(float(p99 - p01), 2),
        "clipped_black_ratio": round(clipped_black, 4),
        "clipped_white_ratio": round(clipped_white, 4),
        "sharpness_score": round(sharpness, 2),
        "edge_density": round(edge_density, 4),
        "entropy_normalized": round(_normalized_entropy(gray), 4),
        "saturation_mean": round(float(np.mean(saturation)), 4),
        "blue_ratio": round(blue_ratio, 4),
        "symmetry_score": round(_symmetry_score(gray), 4),
    }
    checks = {
        "resolution": image.width >= 256 and image.height >= 256,
        "exposure": 30.0 <= brightness <= 225.0,
        "clipping": clipped_black <= 0.25 and clipped_white <= 0.25,
        "sharpness": sharpness >= 8.0,
        "dynamic_range": float(p99 - p01) >= 35.0,
    }

    feature_vector = [
        brightness / 255.0,
        min(1.0, float(p99 - p01) / 255.0),
        min(1.0, sharpness / 120.0),
        edge_density,
        float(np.mean(saturation)),
        blue_ratio,
        _symmetry_score(gray),
        _normalized_entropy(gray),
    ]
    dhash = _dhash(resized)
    fingerprint_payload = ":".join(f"{value:.6f}" for value in feature_vector)
    fingerprint_id = hashlib.sha256(
        f"{sha256}:{dhash}:{fingerprint_payload}".encode("utf-8")
    ).hexdigest()

    return {
        "metrics": metrics,
        "quality_gate": {
            "passed": all(checks.values()),
            "checks": checks,
            "failed_checks": [name for name, passed in checks.items() if not passed],
        },
        "salient_regions": _salient_regions(magnitude),
        "fingerprint": {
            "id": fingerprint_id,
            "dhash": dhash,
            "feature_vector": [round(value, 6) for value in feature_vector],
            "algorithm": "relicscope-visual-fingerprint-v1",
        },
    }


def retrieve_references(
    feature_vector: List[float], reference_catalog: List[Dict[str, Any]], limit: int = 3
) -> List[Dict[str, Any]]:
    query = np.asarray(feature_vector, dtype=np.float64)
    matches: List[Dict[str, Any]] = []
    for reference in reference_catalog:
        reference_vector = np.asarray(reference["feature_vector"], dtype=np.float64)
        distance = float(np.linalg.norm(query - reference_vector)) / math.sqrt(len(query))
        similarity = max(0.0, 1.0 - distance)
        matches.append(
            {
                "id": reference["id"],
                "label": reference["label"],
                "category": reference["category"],
                "similarity": round(similarity, 4),
                "note": reference["note"],
                "demo_reference": True,
            }
        )
    matches.sort(key=lambda match: match["similarity"], reverse=True)
    return matches[:limit]
