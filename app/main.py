from __future__ import annotations

import json
import re
from typing import Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .orchestrator import RelicScopeService
from .schemas import (
    CreateSessionRequest,
    DemoScenarioRequest,
    ExecuteActionRequest,
    ImageAnalyzeRequest,
    ImageCompareRequest,
    KnowledgeSearchRequest,
    VideoFramesAnalyzeRequest,
)
from .services.embedding import OpenAICompatibleEmbeddingProvider
from .services.knowledge import KnowledgeBase, KnowledgeError
from .store import SessionNotFound, SessionStore


_SENSITIVE_DETAIL = re.compile(
    r"(?i)\b(api[_-]?key|authorization|bearer|token|secret)"
    r"(?:\s*[:=]\s*|\s+)[^\s,;]+"
)


def _security_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _error_response(status_code: int, error: str, detail: str) -> JSONResponse:
    return _security_headers(
        JSONResponse(
            status_code=status_code,
            content={"error": error, "detail": detail},
            headers={"Connection": "close"} if status_code == 413 else None,
        )
    )


class RequestBodyLimitMiddleware:
    """Enforce route-aware byte limits without buffering whole video bodies."""

    def __init__(
        self,
        app,
        maximum_body: int,
        video_upload_maximum_body: int,
        frame_batch_maximum_body: int,
    ) -> None:
        self.app = app
        self.maximum_body = maximum_body
        self.video_upload_maximum_body = video_upload_maximum_body
        self.frame_batch_maximum_body = frame_batch_maximum_body

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if path.endswith("/videos/register"):
            maximum_body = self.video_upload_maximum_body
        elif "/videos/" in path and path.endswith("/analyze"):
            maximum_body = self.frame_batch_maximum_body
        else:
            maximum_body = self.maximum_body
        declared_values = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if len(set(declared_values)) > 1:
            response = _error_response(
                400, "INVALID_CONTENT_LENGTH", "conflicting content-length headers"
            )
            await response(scope, receive, send)
            return
        if declared_values:
            try:
                declared_length = int(declared_values[0].decode("ascii"))
                if declared_length < 0:
                    raise ValueError
            except (UnicodeDecodeError, ValueError):
                response = _error_response(
                    400, "INVALID_CONTENT_LENGTH", "invalid content-length"
                )
                await response(scope, receive, send)
                return
            if declared_length > maximum_body:
                response = _error_response(
                    413, "REQUEST_TOO_LARGE", "request body exceeds the configured limit"
                )
                await response(scope, receive, send)
                return

        has_transfer_encoding = any(
            name.lower() == b"transfer-encoding" for name, _ in scope.get("headers", [])
        )
        expects_body = (
            scope.get("method", "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"}
            or bool(declared_values)
            or has_transfer_encoding
        )
        if not expects_body:
            await self.app(scope, receive, send)
            return

        received_bytes = 0
        too_large = False
        response_messages = []

        async def limited_receive():
            nonlocal received_bytes, too_large
            if too_large:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > maximum_body:
                    too_large = True
                    return {"type": "http.disconnect"}
            return message

        async def tracked_send(message):
            response_messages.append(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except Exception:
            if not too_large:
                raise
        if too_large:
            response = _error_response(
                413,
                "REQUEST_TOO_LARGE",
                "request body exceeds the configured limit",
            )
            await response(scope, receive, send)
            return
        for message in response_messages:
            await send(message)


def _redact_detail(detail: str, settings: Settings) -> str:
    safe = _SENSITIVE_DETAIL.sub(lambda match: f"{match.group(1)}=[REDACTED]", detail)
    for credential in (
        settings.vision_api_key,
        settings.embedding_api_key,
        settings.reasoner_api_key,
    ):
        if credential:
            safe = safe.replace(credential, "[REDACTED]")
    return safe[:500]


def create_app(
    settings: Optional[Settings] = None,
    *,
    knowledge: Optional[KnowledgeBase] = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    resolved.ensure_runtime_dirs()
    resolved.validate_runtime()
    store = SessionStore(resolved.db_path)
    store.initialize()
    embedding_provider = None
    if resolved.embedding_base_url:
        embedding_provider = OpenAICompatibleEmbeddingProvider(
            base_url=resolved.embedding_base_url,
            model=resolved.embedding_model,
            api_key=resolved.embedding_api_key,
            timeout_seconds=resolved.model_timeout_seconds,
            allow_network=True,
            allow_public_endpoint=False,
        )
    knowledge_base = knowledge or KnowledgeBase.from_path(
        resolved.knowledge_manifest_path,
        embedding_provider=embedding_provider,
        offline=resolved.offline_mode,
    )
    service = RelicScopeService(resolved, store, knowledge_base)

    application = FastAPI(
        title="RelicScope AI Spark Demo",
        version=resolved.service_version,
        docs_url=None,
        redoc_url=None,
    )
    application.state.service = service
    frame_request_bytes = (
        (((resolved.max_frame_bytes + 2) // 3) * 4 + 2048)
        * resolved.max_video_frames
        + 256 * 1024
    )
    application.add_middleware(
        RequestBodyLimitMiddleware,
        maximum_body=resolved.max_request_bytes,
        video_upload_maximum_body=resolved.max_video_bytes + 2 * 1024 * 1024,
        frame_batch_maximum_body=frame_request_bytes,
    )

    @application.middleware("http")
    async def local_security_boundary(request: Request, call_next):
        response = await call_next(request)
        return _security_headers(response)

    @application.exception_handler(SessionNotFound)
    async def session_not_found(_: Request, exc: SessionNotFound):
        return JSONResponse(
            status_code=404,
            content={"error": "SESSION_NOT_FOUND", "detail": str(exc.args[0])},
        )

    @application.exception_handler(ValueError)
    @application.exception_handler(KnowledgeError)
    async def invalid_operation(_: Request, exc: Exception):
        return JSONResponse(
            status_code=400,
            content={
                "error": type(exc).__name__.upper(),
                "detail": _redact_detail(str(exc), resolved),
            },
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_failed(_: Request, exc: RequestValidationError):
        issues = [
            {
                "type": item.get("type", "validation_error"),
                "location": [str(part) for part in item.get("loc", ())],
                "message": _redact_detail(str(item.get("msg", "invalid value")), resolved),
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": "REQUEST_VALIDATION_ERROR",
                "detail": "request validation failed",
                "issues": issues,
            },
        )

    @application.get("/health/live")
    async def liveness():
        return {"status": "alive", "service_version": resolved.service_version}

    @application.get("/health/ready")
    async def readiness():
        health = await service.health()
        degraded_components = [
            item["name"]
            for item in health["components"]
            if item["status"] in {"degraded", "disabled"}
        ]
        required_unavailable = []
        if resolved.runtime_mode == "dual-node":
            required_unavailable = [
                item["name"]
                for item in health["components"]
                if item.get("role") == "multimodal-compute"
                and item.get("status") != "online"
            ]
        payload = {
            "status": "not_ready" if required_unavailable else "ready",
            "mode": health["mode"],
            "knowledge_version": health["knowledge_version"],
            "degraded_components": degraded_components,
            "required_unavailable": required_unavailable,
        }
        if required_unavailable:
            return JSONResponse(status_code=503, content=payload)
        return payload

    @application.get("/api/health")
    async def api_health():
        return await service.health()

    @application.post("/api/demo/scenarios/p01", status_code=201)
    async def run_p01_demo(payload: Optional[DemoScenarioRequest] = None):
        return await service.run_p01_demo(payload or DemoScenarioRequest())

    @application.post("/api/sessions", status_code=201)
    async def create_session(payload: CreateSessionRequest):
        return service.create_session(payload)

    @application.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        return service.envelope(session_id)

    @application.post("/api/sessions/{session_id}/images/analyze")
    async def analyze_session_image(session_id: str, payload: ImageAnalyzeRequest):
        return await service.analyze_image(session_id, payload)

    @application.post("/api/sessions/{session_id}/images/compare")
    async def compare_session_images(session_id: str, payload: ImageCompareRequest):
        return service.compare_images(session_id, payload)

    @application.post("/api/sessions/{session_id}/videos/register", status_code=201)
    async def register_session_video(
        session_id: str,
        file: UploadFile = File(...),
        modality: str = Form("RGB_VIDEO"),
        region_id: str = Form("R1"),
        duration_ms: Optional[int] = Form(None),
        capture_note: str = Form(""),
    ):
        return await service.register_video(
            session_id,
            file,
            modality=modality,
            region_id=region_id,
            duration_ms=duration_ms,
            capture_note=capture_note,
        )

    @application.post("/api/sessions/{session_id}/videos/{video_id}/analyze")
    async def analyze_session_video(
        session_id: str,
        video_id: str,
        payload: VideoFramesAnalyzeRequest,
    ):
        return await service.analyze_video_frames(session_id, video_id, payload)

    @application.post("/api/sessions/{session_id}/knowledge/search")
    async def search_session_knowledge(session_id: str, payload: KnowledgeSearchRequest):
        return service.search_knowledge(session_id, payload)

    @application.post("/api/sessions/{session_id}/plan")
    async def plan_next_action(session_id: str):
        return service.plan(session_id)

    @application.post("/api/sessions/{session_id}/execute")
    async def execute_action(session_id: str, payload: ExecuteActionRequest):
        return service.execute(session_id, payload)

    @application.get("/api/sessions/{session_id}/evidence")
    async def get_evidence(session_id: str):
        state = store.get_session(session_id)
        return {
            "session_id": session_id,
            "graph": state["evidence_graph"],
            "claim_consistency": state["claim_consistency"],
            "uncertainty": state["uncertainty"],
            "source_category": state["source_category"],
            "data_provenance": service.provenance_summary(state),
            "integrity": service.integrity_manifest(session_id, state=state),
        }

    @application.get("/api/sessions/{session_id}/integrity")
    async def get_integrity(session_id: str):
        state = store.get_session(session_id)
        return {
            "session_id": session_id,
            "integrity": service.integrity_manifest(session_id, state=state),
            "data_provenance": service.provenance_summary(state),
        }

    @application.get("/api/sessions/{session_id}/audit")
    async def get_audit(session_id: str):
        store.get_session(session_id)
        return {
            "session_id": session_id,
            "verification": store.verify_audit_chain_details(session_id),
            "events": store.get_audit_events(session_id),
        }

    @application.post("/api/sessions/{session_id}/report")
    async def generate_report(session_id: str):
        return await service.generate_report(session_id)

    @application.get("/api/sessions/{session_id}/report.json")
    async def download_report_json(session_id: str):
        report = service.get_report(session_id)
        return Response(
            content=json.dumps(report, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="relicscope-{session_id}.json"'
            },
        )

    @application.get("/api/sessions/{session_id}/report.html")
    async def download_report_html(session_id: str):
        return HTMLResponse(
            content=service.get_report_html(session_id),
            headers={
                "Content-Disposition": f'attachment; filename="relicscope-{session_id}.html"'
            },
        )

    static_dir = resolved.project_root / "app" / "static"
    if static_dir.exists():
        application.mount("/static", StaticFiles(directory=static_dir), name="static")

        @application.get("/", include_in_schema=False)
        async def index():
            return FileResponse(static_dir / "index.html")
    else:

        @application.get("/", include_in_schema=False)
        async def index_placeholder():
            return {
                "name": "RelicScope AI Spark Demo",
                "status": "frontend pending",
                "api": "/api/health",
            }

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    runtime_settings = Settings.from_env()
    uvicorn.run(
        "app.main:app",
        host=runtime_settings.host,
        port=runtime_settings.port,
        reload=False,
    )
