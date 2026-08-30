from __future__ import annotations

import io
import warnings

import numpy as np
import pytest
from PIL import Image

from app.services.image_analysis import (
    MAX_IMAGE_HEIGHT,
    MAX_IMAGE_PIXELS,
    MAX_IMAGE_WIDTH,
    analyze_image,
    decode_image,
)


def _checkerboard_bytes(size: int = 320) -> bytes:
    grid = np.indices((size, size)).sum(axis=0) % 2
    array = np.stack(
        [grid * 205 + 25, grid * 90 + 35, grid * 180 + 55], axis=2
    ).astype(np.uint8)
    image = Image.fromarray(array)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_image_decode_mime_quality_and_fingerprint_are_reproducible():
    raw = _checkerboard_bytes()
    decoded = decode_image(raw)
    first = analyze_image(decoded.image, decoded.sha256)
    second = analyze_image(decode_image(raw).image, decoded.sha256)

    assert decoded.detected_mime == "image/png"
    assert len(decoded.sha256) == 64
    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"]["algorithm"] == "relicscope-visual-fingerprint-v1"
    assert len(first["salient_regions"]) == 2
    assert set(first["quality_gate"]["checks"]) == {
        "resolution",
        "exposure",
        "clipping",
        "sharpness",
        "dynamic_range",
    }


def test_tiny_and_damaged_images_are_rejected():
    tiny = Image.new("RGB", (16, 16), "white")
    buffer = io.BytesIO()
    tiny.save(buffer, format="PNG")
    for raw in (buffer.getvalue(), b"not-an-image"):
        try:
            decode_image(raw)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid image should be rejected")


@pytest.mark.parametrize(
    "size, message",
    [
        ((MAX_IMAGE_WIDTH + 1, 32), "dimensions"),
        ((32, MAX_IMAGE_HEIGHT + 1), "dimensions"),
        ((5_000, MAX_IMAGE_PIXELS // 5_000 + 1), "pixel count"),
    ],
)
def test_excessive_image_dimensions_and_pixel_counts_are_rejected(size, message):
    image = Image.new("L", size, 0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    with pytest.raises(ValueError, match=message):
        decode_image(buffer.getvalue())


def test_black_image_saturation_does_not_emit_divide_by_zero_warning():
    image = Image.new("RGB", (320, 320), "black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    decoded = decode_image(buffer.getvalue())

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = analyze_image(decoded.image, decoded.sha256)

    assert result["metrics"]["saturation_mean"] == 0.0
