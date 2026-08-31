from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


ScoutViewCode = Literal[
    "FRONT",
    "BACK",
    "LEFT_PROFILE",
    "RIGHT_PROFILE",
    "TOP",
    "BASE",
    "INTERIOR",
    "DETAIL",
    "MARK",
    "DAMAGE",
    "OTHER",
]

AnalysisMode = Literal["standard"]
ScoutQualityFailure = Literal[
    "resolution_too_low",
    "underexposed",
    "overexposed",
    "shadow_clipping",
    "highlight_clipping",
    "not_sharp",
    "decode_failed",
]


def _normalized_external_text(value: str, *, allow_blank: bool) -> str:
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError("control and Unicode format characters are not allowed")
    normalized = value.strip()
    if not allow_blank and not normalized:
        raise ValueError("value must not be blank")
    return normalized


class DeviceQuality(BaseModel):
    algorithm: Literal["scout-android-quality-v1"] = "scout-android-quality-v1"
    passed: bool
    blur_score: float | None = Field(default=None, ge=0)
    brightness_mean: float | None = Field(default=None, ge=0, le=255)
    object_coverage: float | None = Field(default=None, ge=0, le=1)
    failed_checks: list[ScoutQualityFailure] = Field(default_factory=list, max_length=7)

    @field_validator("failed_checks")
    @classmethod
    def unique_failed_checks(
        cls, value: list[ScoutQualityFailure]
    ) -> list[ScoutQualityFailure]:
        if len(value) != len(set(value)):
            raise ValueError("device quality failed_checks must be unique")
        return value


class CaptureMetadata(BaseModel):
    client_capture_id: str = Field(min_length=8, max_length=96)
    filename: str = Field(min_length=1, max_length=180)
    view_code: ScoutViewCode
    captured_at: datetime
    client_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    device_quality: DeviceQuality | None = None

    @field_validator("filename")
    @classmethod
    def filename_only(cls, value: str) -> str:
        normalized = _normalized_external_text(value, allow_blank=False)
        if not normalized or "/" in normalized or "\\" in normalized:
            raise ValueError("filename must not contain a path")
        return normalized

    @field_validator("client_capture_id")
    @classmethod
    def normalize_capture_id(cls, value: str) -> str:
        return _normalized_external_text(value, allow_blank=False)

    @field_validator("client_sha256")
    @classmethod
    def normalize_sha256(cls, value: str | None) -> str | None:
        return value.lower() if value else None


class ScoutJobMetadata(BaseModel):
    schema_version: Literal["relicscope-scout-job-v2"] = "relicscope-scout-job-v2"
    client_job_id: str = Field(min_length=8, max_length=96)
    capture_protocol: Literal["porcelain-v1"] = "porcelain-v1"
    analysis_mode: AnalysisMode = "standard"
    subject_label: str = Field(default="现场待观察器物", min_length=1, max_length=120)
    operator_note: str = Field(default="", max_length=500)
    app_version: str = Field(min_length=1, max_length=80)
    device_model: str = Field(min_length=1, max_length=120)
    captures: list[CaptureMetadata] = Field(min_length=1, max_length=8)

    @field_validator("client_job_id", "subject_label", "app_version", "device_model")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        return _normalized_external_text(value, allow_blank=False)

    @field_validator("operator_note")
    @classmethod
    def normalize_operator_note(cls, value: str) -> str:
        return _normalized_external_text(value, allow_blank=True)

    @model_validator(mode="after")
    def unique_capture_bindings(self):
        client_ids = [item.client_capture_id for item in self.captures]
        filenames = [item.filename for item in self.captures]
        if len(client_ids) != len(set(client_ids)):
            raise ValueError("client capture identifiers must be unique")
        if len(filenames) != len(set(filenames)):
            raise ValueError("capture filenames must be unique")
        return self


class ScoutDevice(BaseModel):
    id: str
    name: str
    enabled: bool
    capabilities: dict[str, Any]
    created_at: str
    last_seen_at: str | None = None


class ScoutJobSummary(BaseModel):
    id: str
    client_job_id: str
    device_id: str
    status: str
    stage: str
    attempt: int
    capture_count: int
    created_at: str
    updated_at: str
    completed_at: str | None = None
    result_available: bool
    error_code: str | None = None
