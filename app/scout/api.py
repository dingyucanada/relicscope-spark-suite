from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.config import Settings

from .auth import bearer_token
from .protocol import capture_protocol
from .schemas import ScoutJobMetadata
from .service import (
    IncomingCapture,
    ScoutService,
    ScoutStorageReserveError,
)
from .store import (
    ScoutAuthenticationError,
    ScoutCapacityError,
    ScoutConflict,
    ScoutJobNotFound,
    ScoutStore,
    TERMINAL_STATUSES,
)


def create_scout_router(
    settings: Settings, store: ScoutStore, service: ScoutService
) -> APIRouter:
    router = APIRouter(prefix="/api/v2/scout", tags=["Scout V2"])

    def current_device(
        request: Request,
        x_scout_device_id: Annotated[str | None, Header()] = None,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        preauthenticated = getattr(request.state, "scout_device", None)
        if preauthenticated is not None:
            return preauthenticated
        if not x_scout_device_id:
            raise HTTPException(status_code=401, detail="missing Scout device identifier")
        try:
            if settings.scout_require_auth:
                return store.authenticate_device(
                    x_scout_device_id, bearer_token(authorization)
                )
            return store.get_device(x_scout_device_id)
        except (ScoutAuthenticationError, ValueError) as exc:
            raise HTTPException(status_code=401, detail="invalid Scout credentials") from exc

    @router.get("/health")
    async def health():
        value = await service.health()
        status_code = 200 if value["status"] == "ready" else 503
        return JSONResponse(status_code=status_code, content=value)

    @router.get("/capabilities")
    async def capabilities(_: dict[str, Any] = Depends(current_device)):
        return service.capabilities()

    @router.get("/capture-protocols/porcelain-v1")
    async def porcelain_protocol(_: dict[str, Any] = Depends(current_device)):
        return capture_protocol()

    @router.get("/me")
    async def me(device: dict[str, Any] = Depends(current_device)):
        return device

    @router.post("/jobs", status_code=202)
    async def create_job(
        metadata_json: Annotated[str, Form(min_length=2, max_length=32_768)],
        files: Annotated[list[UploadFile], File(min_length=1, max_length=8)],
        device: dict[str, Any] = Depends(current_device),
    ):
        try:
            metadata = ScoutJobMetadata.model_validate_json(metadata_json)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail="invalid immutable Scout job metadata"
            ) from exc
        if len(files) > settings.scout_max_images_per_job:
            raise HTTPException(status_code=413, detail="too many Scout images")
        uploads = []
        for item in files:
            payload = await item.read(settings.max_upload_bytes + 1)
            await item.close()
            if len(payload) > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail="Scout image exceeds limit")
            uploads.append(
                IncomingCapture(
                    filename=item.filename or "capture.jpg",
                    mime_type=item.content_type or "application/octet-stream",
                    raw_bytes=payload,
                )
            )
        try:
            job, created = service.create_job(device["id"], metadata, uploads)
        except ScoutCapacityError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ScoutStorageReserveError as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except ScoutConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "created": created,
            "job": service.summarize_job(job),
            "poll_url": f"/api/v2/scout/jobs/{job['id']}",
            "result_url": f"/api/v2/scout/jobs/{job['id']}/result",
        }

    @router.get("/jobs")
    async def list_jobs(
        limit: int = 20, device: dict[str, Any] = Depends(current_device)
    ):
        return {
            "jobs": [
                service.summarize_job(item)
                for item in store.list_jobs(device["id"], limit=limit)
            ]
        }

    @router.get("/jobs/{job_id}")
    async def get_job(job_id: str, device: dict[str, Any] = Depends(current_device)):
        try:
            job = store.get_job(job_id, device_id=device["id"])
        except ScoutJobNotFound as exc:
            raise HTTPException(status_code=404, detail="Scout job not found") from exc
        return service.summarize_job(job)

    @router.get("/jobs/{job_id}/events")
    async def get_events(
        job_id: str, device: dict[str, Any] = Depends(current_device)
    ):
        try:
            return {"job_id": job_id, "events": store.events(job_id, device["id"])}
        except ScoutJobNotFound as exc:
            raise HTTPException(status_code=404, detail="Scout job not found") from exc

    @router.get("/jobs/{job_id}/result")
    async def get_result(
        job_id: str, device: dict[str, Any] = Depends(current_device)
    ):
        try:
            job = store.get_job(job_id, device_id=device["id"])
        except ScoutJobNotFound as exc:
            raise HTTPException(status_code=404, detail="Scout job not found") from exc
        if not job["result_available"]:
            if job["status"] in TERMINAL_STATUSES:
                return JSONResponse(
                    status_code=409,
                    content={
                        "job": service.summarize_job(job),
                        "result": None,
                        "detail": "terminal Scout job has no result document",
                    },
                )
            return JSONResponse(
                status_code=202,
                content={"job": service.summarize_job(job), "result": None},
            )
        return {
            "job": service.summarize_job(job),
            "result": job["result"],
        }

    @router.post("/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str, device: dict[str, Any] = Depends(current_device)
    ):
        try:
            job = store.cancel_job(job_id, device["id"])
        except ScoutJobNotFound as exc:
            raise HTTPException(status_code=404, detail="Scout job not found") from exc
        except ScoutConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return service.summarize_job(job)

    @router.post("/jobs/{job_id}/retry")
    async def retry_model_job(
        job_id: str, device: dict[str, Any] = Depends(current_device)
    ):
        try:
            job = service.retry_model_unavailable_job(job_id, device["id"])
        except ScoutJobNotFound as exc:
            raise HTTPException(status_code=404, detail="Scout job not found") from exc
        except ScoutCapacityError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ScoutConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.wake()
        return service.summarize_job(job)

    return router
