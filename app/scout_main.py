from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .scout.api import create_scout_router
from .scout.auth import bearer_token
from .scout.service import ScoutService, VisionClient
from .scout.store import ScoutAuthenticationError, ScoutStore
from .services.vlm import OpenAICompatibleClient


class ScoutRequestBodyLimitMiddleware:
    def __init__(self, app, maximum_body: int) -> None:
        self.app = app
        self.maximum_body = maximum_body

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if declared:
            try:
                size = int(declared[-1].decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                response = JSONResponse(
                    status_code=400, content={"error": "INVALID_CONTENT_LENGTH"}
                )
                await response(scope, receive, send)
                return
            if size < 0 or size > self.maximum_body:
                response = JSONResponse(
                    status_code=413, content={"error": "REQUEST_TOO_LARGE"}
                )
                await response(scope, receive, send)
                return

        received = 0
        exceeded = False
        buffered_messages = []

        async def limited_receive():
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum_body:
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def buffered_send(message):
            buffered_messages.append(message)

        try:
            await self.app(scope, limited_receive, buffered_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            response = JSONResponse(
                status_code=413, content={"error": "REQUEST_TOO_LARGE"}
            )
            await response(scope, receive, send)
            return
        for message in buffered_messages:
            await send(message)


class ScoutPreAuthMiddleware:
    """Authenticate device-scoped routes before Starlette parses multipart bodies."""

    def __init__(
        self,
        app,
        settings: Settings,
        store: ScoutStore,
        maximum_concurrent_ingests: int = 2,
    ) -> None:
        self.app = app
        self.settings = settings
        self.store = store
        self._ingest_slots = asyncio.Semaphore(maximum_concurrent_ingests)
        self._authentication_slots = asyncio.Semaphore(8)

    @staticmethod
    def _headers(scope) -> dict[str, str]:
        return {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])
        }

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        protected = path.startswith("/api/v2/scout/") and path != (
            "/api/v2/scout/health"
        )
        if not protected:
            await self.app(scope, receive, send)
            return

        headers = self._headers(scope)
        device_id = headers.get("x-scout-device-id", "")
        try:
            if not device_id:
                raise ScoutAuthenticationError("missing Scout device identifier")
            try:
                await asyncio.wait_for(
                    self._authentication_slots.acquire(), timeout=0.05
                )
            except TimeoutError:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Scout authentication capacity is busy"},
                )
                await response(scope, receive, send)
                return
            try:
                if self.settings.scout_require_auth:
                    token = bearer_token(headers.get("authorization"))
                    device = await asyncio.to_thread(
                        self.store.authenticate_device, device_id, token
                    )
                else:
                    device = await asyncio.to_thread(self.store.get_device, device_id)
            finally:
                self._authentication_slots.release()
        except (ScoutAuthenticationError, ValueError):
            response = JSONResponse(
                status_code=401, content={"detail": "invalid Scout credentials"}
            )
            await response(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        state["scout_device"] = device
        is_ingest = scope.get("method") == "POST" and path == "/api/v2/scout/jobs"
        if not is_ingest:
            await self.app(scope, receive, send)
            return
        try:
            await asyncio.wait_for(self._ingest_slots.acquire(), timeout=0.05)
        except TimeoutError:
            response = JSONResponse(
                status_code=429, content={"detail": "Scout ingest capacity is busy"}
            )
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self._ingest_slots.release()


def _model_client(settings: Settings) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url=settings.vision_base_url,
        api_key=settings.vision_api_key,
        model=settings.vision_model,
        timeout_seconds=settings.model_timeout_seconds,
        model_profile=settings.model_profile,
        runtime_image=settings.vision_runtime_image,
        model_source=settings.vision_model_source,
        model_revision=settings.vision_model_revision,
        deployment_git_commit=settings.deployment_git_commit,
    )


def create_scout_app(
    settings: Optional[Settings] = None,
    *,
    model_client: Optional[VisionClient] = None,
) -> FastAPI:
    resolved = settings or Settings.from_env()
    resolved.ensure_runtime_dirs()
    resolved.validate_runtime()
    store = ScoutStore(resolved.db_path)
    store.initialize()
    client = model_client or _model_client(resolved)
    service = ScoutService(resolved, store, lambda: client)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()

    application = FastAPI(
        title="RelicScope Scout Gateway V2",
        version=resolved.service_version,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.scout_store = store
    application.state.scout_service = service
    application.state.model_client = client
    total_limit = (
        resolved.max_upload_bytes * resolved.scout_max_images_per_job + 1024 * 1024
    )
    application.add_middleware(
        ScoutRequestBodyLimitMiddleware, maximum_body=total_limit
    )
    application.add_middleware(
        ScoutPreAuthMiddleware,
        settings=resolved,
        store=store,
        maximum_concurrent_ingests=2,
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

    application.include_router(create_scout_router(resolved, store, service))

    @application.get("/health/live")
    async def liveness():
        return {
            "status": "alive",
            "service": "RelicScope Scout Gateway V2",
            "service_version": resolved.service_version,
        }

    static_dir = resolved.project_root / "app" / "scout_static"
    if static_dir.exists():
        application.mount(
            "/scout-assets", StaticFiles(directory=static_dir), name="scout-assets"
        )

        @application.get("/", include_in_schema=False)
        async def operator_console():
            return FileResponse(static_dir / "index.html")
    else:

        @application.get("/", include_in_schema=False)
        async def operator_console_placeholder():
            return {
                "service": "RelicScope Scout Gateway V2",
                "status": "operator console not packaged",
            }

    return application


app = create_scout_app()
