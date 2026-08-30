import pytest
from pydantic import ValidationError

from app.schemas import ImageAnalyzeRequest


@pytest.mark.parametrize("modality", ["RGB", "UV", "NIR"])
def test_image_request_accepts_controlled_modalities(modality):
    request = ImageAnalyzeRequest(image_base64="aGVsbG8=", modality=modality)
    assert request.modality == modality


@pytest.mark.parametrize("modality", ["XRF", "Raman", "rgb", "unknown"])
def test_image_request_rejects_uncontrolled_modalities(modality):
    with pytest.raises(ValidationError):
        ImageAnalyzeRequest(image_base64="aGVsbG8=", modality=modality)


def test_image_request_rejects_unsupported_mime_type():
    with pytest.raises(ValidationError):
        ImageAnalyzeRequest(image_base64="aGVsbG8=", mime_type="image/tiff")
