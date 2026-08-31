from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ClaimInput(BaseModel):
    period: str = Field(default="清代", max_length=80)
    kiln: str = Field(default="景德镇窑", max_length=80)
    material: str = Field(default="青花瓷", max_length=80)
    provenance_note: str = Field(default="来源待核验", max_length=240)


class CreateSessionRequest(BaseModel):
    artifact_name: str = Field(default="疑似清代青花瓷", min_length=1, max_length=120)
    operator: str = Field(default="Demo Operator", max_length=80)
    institution: str = Field(default="RelicScope Demo Lab", max_length=120)
    claim: ClaimInput = Field(default_factory=ClaimInput)

    @field_validator("artifact_name", "operator", "institution")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ImageAnalyzeRequest(BaseModel):
    filename: str = Field(default="artifact.jpg", max_length=180)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg"
    image_base64: str = Field(min_length=8)
    modality: Literal["RGB", "UV", "NIR"] = "RGB"
    region_id: str = Field(default="R1", max_length=30)
    view_code: Literal[
        "UNSPECIFIED",
        "FRONT",
        "BACK",
        "LEFT_PROFILE",
        "RIGHT_PROFILE",
        "TOP",
        "BASE",
        "INTERIOR",
        "FRONT_LEFT_45",
        "FRONT_RIGHT_45",
        "BACK_LEFT_45",
        "BACK_RIGHT_45",
        "DETAIL",
        "MARK",
        "DAMAGE",
    ] = "UNSPECIFIED"

    @field_validator("image_base64")
    @classmethod
    def strip_data_url_prefix(cls, value: str) -> str:
        if value.startswith("data:"):
            try:
                return value.split(",", 1)[1]
            except IndexError as exc:
                raise ValueError("invalid data URL") from exc
        return value


class ReferenceRecognitionRequest(BaseModel):
    image_analysis_ids: List[str] = Field(min_length=1, max_length=5)
    top_k: int = Field(default=5, ge=1, le=10)

    @field_validator("image_analysis_ids")
    @classmethod
    def require_unique_analysis_ids(cls, value: List[str]) -> List[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError("image analysis identifiers must not be blank")
        if len(normalized) != len(set(normalized)):
            raise ValueError("image analysis identifiers must be unique")
        return normalized


class ImageCompareRequest(BaseModel):
    baseline_analysis_id: str = Field(max_length=80)
    comparison_analysis_id: str = Field(max_length=80)

    @model_validator(mode="after")
    def require_distinct_analyses(self):
        if self.baseline_analysis_id == self.comparison_analysis_id:
            raise ValueError("baseline and comparison analyses must be different")
        return self


class VideoFrameInput(BaseModel):
    timestamp_ms: int = Field(ge=0, le=86_400_000)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg"
    image_base64: str = Field(min_length=8)

    @field_validator("image_base64")
    @classmethod
    def strip_frame_data_url_prefix(cls, value: str) -> str:
        if value.startswith("data:"):
            try:
                return value.split(",", 1)[1]
            except IndexError as exc:
                raise ValueError("invalid frame data URL") from exc
        return value


class VideoFramesAnalyzeRequest(BaseModel):
    duration_ms: int = Field(ge=1, le=86_400_000)
    sampling_strategy: Literal[
        "uniform-browser-v1", "manual-keyframes-v1"
    ] = "uniform-browser-v1"
    frames: List[VideoFrameInput] = Field(min_length=3, max_length=24)

    @model_validator(mode="after")
    def validate_frame_timeline(self):
        timestamps = [item.timestamp_ms for item in self.frames]
        if len(timestamps) != len(set(timestamps)):
            raise ValueError("frame timestamps must be unique")
        if any(value > self.duration_ms for value in timestamps):
            raise ValueError("frame timestamp exceeds video duration")
        return self


class ExecuteActionRequest(BaseModel):
    replay_profile: Optional[str] = Field(default=None, max_length=80)
    action_run_id: Optional[str] = Field(default=None, max_length=80)


class DemoScenarioRequest(BaseModel):
    artifact_name: str = Field(default="P01 演示青花瓷", min_length=1, max_length=120)
    operator: str = Field(default="Demo Operator", max_length=80)
    institution: str = Field(default="RelicScope Demo Lab", max_length=120)
    deterministic_only: bool = True
    include_report: bool = True

    @field_validator("artifact_name", "operator", "institution")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(default="青花釉料与材料响应", max_length=500)
    limit: int = Field(default=3, ge=1, le=10)
    space: str = Field(default="demo", pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    region_id: str = Field(default="R1", max_length=30)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(value.split())


class SessionEnvelope(BaseModel):
    session: Dict[str, Any]
    audit_verified: bool
    audit_event_count: int


class HealthComponent(BaseModel):
    name: str
    status: str
    detail: str
    model: Optional[str] = None
    latency_ms: Optional[int] = None


class HealthResponse(BaseModel):
    status: str
    mode: str
    components: List[HealthComponent]
