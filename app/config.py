from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _service_url_is_private(url: str) -> bool:
    """Accept only local/private endpoints when the offline boundary is enabled."""
    if not url:
        return True
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower()
    if hostname in {"localhost", "host.docker.internal"} or hostname.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # Docker Compose service names contain no dots and resolve only in the
        # private project network. Public DNS names are rejected by default.
        return "." not in hostname
    return bool(address.is_private or address.is_loopback or address.is_link_local)


_INSECURE_API_KEY_PLACEHOLDERS = {
    "change-me",
    "changeme",
    "demo",
    "demo-key",
    "password",
    "replace-me",
    "secret",
    "test",
}


def _api_key_is_configured(value: str) -> bool:
    normalized = value.strip()
    return bool(normalized) and normalized.lower() not in _INSECURE_API_KEY_PLACEHOLDERS


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_dir: Path
    db_path: Path
    upload_dir: Path
    host: str
    port: int
    max_upload_bytes: int
    max_request_bytes: int
    max_video_bytes: int
    max_native_video_bytes: int
    max_native_video_duration_ms: int
    max_video_frames: int
    max_frame_bytes: int
    demo_mode: bool
    offline_mode: bool
    runtime_mode: str
    service_version: str
    deployment_git_commit: str
    node_id: str
    compute_node_id: str
    model_profile: str
    knowledge_manifest_path: Path
    scout_enabled: bool
    scout_require_auth: bool
    scout_media_dir: Path
    scout_max_images_per_job: int
    scout_worker_poll_seconds: float
    scout_model_max_attempts: int
    scout_model_retry_base_seconds: float
    scout_min_free_bytes: int
    scout_max_outstanding_jobs_per_device: int
    scout_capture_protocol_version: str
    reference_library_enabled: bool
    reference_library_dir: Path
    reference_library_manifest_path: Path
    reference_library_index_path: Path
    reference_library_vector_index_path: Path
    reference_library_calibration_path: Path
    reference_library_min_artifacts: int
    reference_library_min_views: int
    counterfeit_library_min_records: int
    vision_base_url: str
    vision_api_key: str
    vision_runtime_image: str
    vision_model_source: str
    vision_model_revision: str
    vision_model: str
    embedding_base_url: str
    embedding_api_key: str
    embedding_model: str
    reference_embedding_base_url: str
    reference_embedding_api_key: str
    reference_embedding_model: str
    reference_embedding_model_source: str
    reference_embedding_model_revision: str
    reference_embedding_dimension: int
    reasoner_base_url: str
    reasoner_api_key: str
    reasoner_model: str
    model_timeout_seconds: float
    model_max_concurrency: int
    require_private_endpoints: bool

    @classmethod
    def from_env(cls) -> "Settings":
        project_root = Path(__file__).resolve().parents[1]
        data_dir = Path(
            os.getenv("RELICSCOPE_DATA_DIR", project_root / "runtime")
        ).expanduser()
        max_upload_bytes = int(
            os.getenv("RELICSCOPE_MAX_UPLOAD_BYTES", str(8 * 1024 * 1024))
        )
        default_request_bytes = ((max_upload_bytes + 2) // 3) * 4 + 256 * 1024
        max_video_bytes = int(
            os.getenv("RELICSCOPE_MAX_VIDEO_BYTES", str(256 * 1024 * 1024))
        )
        max_frame_bytes = int(
            os.getenv(
                "RELICSCOPE_MAX_FRAME_BYTES",
                str(min(max_upload_bytes, 2 * 1024 * 1024)),
            )
        )
        reference_library_dir = Path(
            os.getenv(
                "RELICSCOPE_REFERENCE_LIBRARY_DIR",
                data_dir / "reference-library",
            )
        ).expanduser()
        return cls(
            project_root=project_root,
            data_dir=data_dir,
            db_path=data_dir / "relicscope_demo.sqlite3",
            upload_dir=data_dir / "uploads",
            host=os.getenv("RELICSCOPE_HOST", "0.0.0.0"),
            port=int(os.getenv("RELICSCOPE_PORT", "8088")),
            max_upload_bytes=max_upload_bytes,
            max_request_bytes=int(
                os.getenv("RELICSCOPE_MAX_REQUEST_BYTES", str(default_request_bytes))
            ),
            max_video_bytes=max_video_bytes,
            max_native_video_bytes=int(
                os.getenv("RELICSCOPE_MAX_NATIVE_VIDEO_BYTES", str(32 * 1024 * 1024))
            ),
            max_native_video_duration_ms=int(
                os.getenv("RELICSCOPE_MAX_NATIVE_VIDEO_DURATION_MS", "15000")
            ),
            max_video_frames=int(os.getenv("RELICSCOPE_MAX_VIDEO_FRAMES", "12")),
            max_frame_bytes=max_frame_bytes,
            demo_mode=_env_bool("RELICSCOPE_DEMO_MODE", True),
            offline_mode=_env_bool("RELICSCOPE_OFFLINE_MODE", True),
            runtime_mode=os.getenv("RELICSCOPE_RUNTIME_MODE", "single-degraded"),
            service_version=os.getenv("RELICSCOPE_SERVICE_VERSION", "1.2.0"),
            deployment_git_commit=os.getenv("RELICSCOPE_GIT_COMMIT", "unknown"),
            node_id=os.getenv("RELICSCOPE_NODE_ID", "spark-b"),
            compute_node_id=os.getenv("RELICSCOPE_COMPUTE_NODE_ID", "spark-a"),
            model_profile=os.getenv("MODEL_PROFILE", "qwen3-vl"),
            knowledge_manifest_path=Path(
                os.getenv(
                    "RELICSCOPE_KNOWLEDGE_MANIFEST",
                    project_root / "data" / "knowledge_manifest.json",
                )
            ).expanduser(),
            scout_enabled=_env_bool("RELICSCOPE_SCOUT_ENABLED", False),
            scout_require_auth=_env_bool("RELICSCOPE_SCOUT_REQUIRE_AUTH", True),
            scout_media_dir=Path(
                os.getenv("RELICSCOPE_SCOUT_MEDIA_DIR", data_dir / "scout-media")
            ).expanduser(),
            scout_max_images_per_job=int(
                os.getenv("RELICSCOPE_SCOUT_MAX_IMAGES_PER_JOB", "8")
            ),
            scout_worker_poll_seconds=float(
                os.getenv("RELICSCOPE_SCOUT_WORKER_POLL_SECONDS", "0.5")
            ),
            scout_model_max_attempts=int(
                os.getenv("RELICSCOPE_SCOUT_MODEL_MAX_ATTEMPTS", "3")
            ),
            scout_model_retry_base_seconds=float(
                os.getenv("RELICSCOPE_SCOUT_MODEL_RETRY_BASE_SECONDS", "5")
            ),
            scout_min_free_bytes=int(
                os.getenv("RELICSCOPE_SCOUT_MIN_FREE_BYTES", str(20 * 1024**3))
            ),
            scout_max_outstanding_jobs_per_device=int(
                os.getenv("RELICSCOPE_SCOUT_MAX_OUTSTANDING_JOBS_PER_DEVICE", "20")
            ),
            scout_capture_protocol_version=os.getenv(
                "RELICSCOPE_SCOUT_CAPTURE_PROTOCOL_VERSION", "porcelain-v1"
            ),
            reference_library_enabled=_env_bool(
                "RELICSCOPE_REFERENCE_LIBRARY_ENABLED", False
            ),
            reference_library_dir=reference_library_dir,
            reference_library_manifest_path=Path(
                os.getenv(
                    "RELICSCOPE_REFERENCE_LIBRARY_MANIFEST",
                    reference_library_dir / "manifest.json",
                )
            ).expanduser(),
            reference_library_index_path=Path(
                os.getenv(
                    "RELICSCOPE_REFERENCE_LIBRARY_INDEX",
                    reference_library_dir / "index.sqlite3",
                )
            ).expanduser(),
            reference_library_vector_index_path=Path(
                os.getenv(
                    "RELICSCOPE_REFERENCE_LIBRARY_VECTOR_INDEX",
                    reference_library_dir / "embeddings.npz",
                )
            ).expanduser(),
            reference_library_calibration_path=Path(
                os.getenv(
                    "RELICSCOPE_REFERENCE_LIBRARY_CALIBRATION",
                    reference_library_dir / "calibration.json",
                )
            ).expanduser(),
            reference_library_min_artifacts=int(
                os.getenv("RELICSCOPE_REFERENCE_LIBRARY_MIN_ARTIFACTS", "50")
            ),
            reference_library_min_views=int(
                os.getenv("RELICSCOPE_REFERENCE_LIBRARY_MIN_VIEWS", "5")
            ),
            counterfeit_library_min_records=int(
                os.getenv("RELICSCOPE_COUNTERFEIT_LIBRARY_MIN_RECORDS", "10")
            ),
            vision_base_url=os.getenv("VISION_BASE_URL", "").rstrip("/"),
            vision_api_key=os.getenv("VISION_API_KEY", ""),
            vision_runtime_image=os.getenv("VISION_RUNTIME_IMAGE", "unknown"),
            vision_model_source=os.getenv(
                "VISION_MODEL_SOURCE", "Qwen/Qwen3-VL-30B-A3B-Instruct"
            ),
            vision_model_revision=os.getenv("VISION_MODEL_REVISION", "unknown"),
            vision_model=os.getenv("VISION_MODEL", "qwen3_vl_30b_a3b"),
            embedding_base_url=os.getenv("EMBEDDING_BASE_URL", "").rstrip("/"),
            embedding_api_key=os.getenv("EMBEDDING_API_KEY", ""),
            embedding_model=os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            reference_embedding_base_url=os.getenv(
                "REFERENCE_EMBEDDING_BASE_URL", ""
            ).rstrip("/"),
            reference_embedding_api_key=os.getenv(
                "REFERENCE_EMBEDDING_API_KEY",
                os.getenv("EMBEDDING_API_KEY", ""),
            ),
            reference_embedding_model=os.getenv(
                "REFERENCE_EMBEDDING_MODEL", "qwen3_vl_embedding_2b"
            ),
            reference_embedding_model_source=os.getenv(
                "REFERENCE_EMBEDDING_MODEL_SOURCE",
                "Qwen/Qwen3-VL-Embedding-2B",
            ),
            reference_embedding_model_revision=os.getenv(
                "REFERENCE_EMBEDDING_MODEL_REVISION", "unknown"
            ),
            reference_embedding_dimension=int(
                os.getenv("REFERENCE_EMBEDDING_DIMENSION", "2048")
            ),
            reasoner_base_url=os.getenv("REASONER_BASE_URL", "").rstrip("/"),
            reasoner_api_key=os.getenv("REASONER_API_KEY", ""),
            reasoner_model=os.getenv("REASONER_MODEL", "qwen3_vl_30b_a3b"),
            model_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "45")),
            model_max_concurrency=int(os.getenv("MODEL_MAX_CONCURRENCY", "2")),
            require_private_endpoints=_env_bool(
                "RELICSCOPE_REQUIRE_PRIVATE_ENDPOINTS", True
            ),
        )

    def ensure_runtime_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.scout_media_dir.mkdir(parents=True, exist_ok=True)
        self.reference_library_dir.mkdir(parents=True, exist_ok=True)

    def validate_runtime(self) -> None:
        if self.runtime_mode not in {
            "dual-node",
            "single-spark",
            "single-degraded",
            "local-development",
        }:
            raise ValueError(f"unsupported runtime mode: {self.runtime_mode}")
        if (
            self.max_upload_bytes <= 0
            or self.max_request_bytes <= 0
            or self.max_video_bytes <= 0
            or self.max_native_video_bytes <= 0
            or self.max_native_video_duration_ms <= 0
            or self.max_frame_bytes <= 0
        ):
            raise ValueError("request and upload limits must be positive")
        if not 3 <= self.max_video_frames <= 24:
            raise ValueError("video frame limit must be between 3 and 24")
        if not 1 <= self.model_max_concurrency <= 8:
            raise ValueError("model concurrency must be between 1 and 8")
        if not 1 <= self.scout_max_images_per_job <= 8:
            raise ValueError("Scout image limit must be between 1 and 8")
        if not 0.05 <= self.scout_worker_poll_seconds <= 30.0:
            raise ValueError("Scout worker poll interval is outside the safe range")
        if not 1 <= self.scout_model_max_attempts <= 10:
            raise ValueError("Scout model attempt limit must be between 1 and 10")
        if not 0.05 <= self.scout_model_retry_base_seconds <= 300.0:
            raise ValueError("Scout model retry delay is outside the safe range")
        if not 64 * 1024**2 <= self.scout_min_free_bytes <= 4 * 1024**4:
            raise ValueError("Scout minimum free-space reserve is outside the safe range")
        if not 1 <= self.scout_max_outstanding_jobs_per_device <= 1_000:
            raise ValueError("Scout outstanding-job limit is outside the safe range")
        if self.scout_capture_protocol_version != "porcelain-v1":
            raise ValueError("unsupported Scout capture protocol")
        if (
            self.scout_enabled
            and self.vision_base_url
            and self.vision_model_source != self.vision_model
        ):
            raise ValueError(
                "Scout V2 model source must equal the model identity loaded by vLLM"
            )
        if self.scout_enabled and self.runtime_mode == "single-spark":
            immutable_hex = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
            if not immutable_hex.fullmatch(self.vision_model_revision):
                raise ValueError("Scout V2 requires an immutable model revision")
            if not immutable_hex.fullmatch(self.deployment_git_commit):
                raise ValueError("Scout V2 requires an immutable deployment Git commit")
            if not re.search(
                r"@sha256:[0-9a-fA-F]{64}$", self.vision_runtime_image
            ):
                raise ValueError("Scout V2 requires an immutable VLM runtime image")
        if not 1 <= self.reference_library_min_artifacts <= 100_000:
            raise ValueError("reference library artifact minimum is invalid")
        if not 3 <= self.reference_library_min_views <= 32:
            raise ValueError("reference library view minimum must be between 3 and 32")
        if not 0 <= self.counterfeit_library_min_records <= 100_000:
            raise ValueError("counterfeit library record minimum is invalid")
        if not 64 <= self.reference_embedding_dimension <= 8192:
            raise ValueError("reference embedding dimension is outside the safe range")
        endpoints = {
            "VISION_BASE_URL": self.vision_base_url,
            "EMBEDDING_BASE_URL": self.embedding_base_url,
            "REFERENCE_EMBEDDING_BASE_URL": self.reference_embedding_base_url,
            "REASONER_BASE_URL": self.reasoner_base_url,
        }
        if self.require_private_endpoints:
            invalid = [
                name
                for name, value in endpoints.items()
                if not _service_url_is_private(value)
            ]
            if invalid:
                raise ValueError(
                    "external model endpoints are blocked by the local-data boundary: "
                    + ", ".join(invalid)
                )
        endpoint_credentials = {
            "VISION_API_KEY": (self.vision_base_url, self.vision_api_key),
            "EMBEDDING_API_KEY": (self.embedding_base_url, self.embedding_api_key),
            "REFERENCE_EMBEDDING_API_KEY": (
                self.reference_embedding_base_url,
                self.reference_embedding_api_key,
            ),
            "REASONER_API_KEY": (self.reasoner_base_url, self.reasoner_api_key),
        }
        missing_credentials = [
            name
            for name, (endpoint, credential) in endpoint_credentials.items()
            if endpoint and not _api_key_is_configured(credential)
        ]
        if missing_credentials:
            raise ValueError(
                "configured model endpoints require non-default API keys: "
                + ", ".join(missing_credentials)
            )
