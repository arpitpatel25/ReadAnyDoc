"""FastAPI app: Reducto's v3 routes on top of the readanydoc pipeline.

    uv run readanydoc serve --port 8000        (or: python -m readanydoc.api)
"""

from __future__ import annotations

import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..store import ACTIVE, Database, FileStore, Job, JobStore
from . import errors, schemas, webhooks
from .config import API_KEY_ENV, ServerSettings
from .inputs import ApiGone
from .service import PIPELINE_GONE, Service
from .worker import Worker

# GET /job only knows these four in Reducto's schema; the rest are folded unless READANYDOC_JOB_STATUS_DETAIL=1
PUBLIC_STATUS = {"Pending": "Pending", "InProgress": "Pending", "Completing": "Pending",
                 "Completed": "Completed", "Failed": "Failed", "Cancelled": "Failed"}


def require_auth(request: Request) -> None:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        return  # open server (local use)
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode(), key.encode()):
        raise errors.unauthorized()


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None


def create_app(settings: Optional[ServerSettings] = None) -> FastAPI:
    settings = settings or ServerSettings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.data_dir / "readanydoc.sqlite3")
    files = FileStore(db, settings.data_dir / "files")
    jobs = JobStore(db, settings.data_dir / "jobs")
    service = Service(settings, files, jobs)
    worker = Worker(settings, db, files, jobs, service)
    jobs.recover()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker.ensure_started()
        yield
        worker.stop()

    app = FastAPI(title="readanydoc (Reducto-compatible API)", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.db, app.state.files = settings, db, files
    app.state.jobs, app.state.service, app.state.worker = jobs, service, worker
    auth = [Depends(require_auth)]

    # ---- errors ----
    @app.exception_handler(errors.ApiError)
    async def _api_error(_: Request, exc: errors.ApiError):
        return JSONResponse(exc.body(), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        name = {401: "AUTH_ERROR", 404: "NOT_FOUND", 429: "RATE_LIMIT"}.get(exc.status_code, "INVALID_CONFIG")
        err = errors.ApiError(exc.status_code, name, str(exc.detail), headers=getattr(exc, "headers", None))
        return JSONResponse(err.body(), status_code=exc.status_code, headers=err.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        detail = [{k: v for k, v in e.items() if k in ("loc", "msg", "type")} | {"loc": list(e.get("loc", ()))}
                  for e in exc.errors()]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        err = errors.from_exception(exc)
        return JSONResponse(err.body(), status_code=err.status)

    # ---- helpers ----
    def base_url(request: Request) -> str:
        return settings.public_url or str(request.base_url).rstrip("/")

    def submit(request: Request, type_: str, body: schemas._M, *, sync: bool) -> Any:
        req = body.dump()
        if type_ == "Edit":
            wh = req.get("webhook") or {}
            priority, metadata = bool(req.get("priority")), wh.get("metadata")
            webhook = wh if wh.get("mode", "disabled") != "disabled" else None
            source = req.get("document_url")
        else:
            a = req.get("async") or {}
            priority, metadata, webhook = bool(a.get("priority")), a.get("metadata"), a.get("webhook")
            source = req.get("input")
        job = jobs.create(type_, req, sync=sync, priority=priority, queue_priority=req.get("queue_priority", "auto"),
                          metadata=metadata, webhook=None if sync else webhook, base_url=base_url(request),
                          source=source, raw_config=json.dumps(body.model_dump(by_alias=True, mode="json",
                                                                               exclude_unset=True)))
        if not sync:
            worker.notify()
            return JSONResponse({"job_id": job.id})
        _, response, err = service.process(job)
        if err is not None:
            raise err
        return JSONResponse(response)

    def get_job(job_id: str) -> Job:
        job = jobs.get(job_id)
        if job is None:
            raise errors.not_found(f"Job not found: {job_id}", "job_not_found")
        if job.deleted_at is not None:
            raise ApiGone(job_id)
        return job

    # ---- upload + file store ----
    @app.post("/upload", dependencies=auth)
    async def upload(request: Request, extension: Optional[str] = None):
        upload_file = None
        if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
            form = await request.form()
            candidate = form.get("file")
            if hasattr(candidate, "file") and hasattr(candidate, "filename"):
                upload_file = candidate
        if upload_file is None:
            rec = files.create("upload", None, extension=extension or "", ttl=settings.upload_ttl, pending=True)
            url = f"{base_url(request)}/upload/presigned/{rec.id}?token={rec.token}"
            return {"file_id": rec.file_id, "presigned_url": url}
        try:
            rec = await run_in_threadpool(
                files.put, "upload", upload_file.file, upload_file.filename, extension=extension or "",
                ttl=settings.upload_ttl, content_type=upload_file.content_type, max_bytes=settings.max_upload_bytes)
        except OverflowError:
            raise errors.ApiError(413, "DOCUMENT_TOO_LARGE", "File exceeds the direct upload limit "
                                  f"({settings.max_upload_bytes // (1024 * 1024)} MB)", "file_too_large") from None
        return {"file_id": rec.file_id, "presigned_url": None}

    @app.put("/upload/presigned/{file_id}", include_in_schema=False)
    async def presigned_put(file_id: str, request: Request, token: str = ""):
        rec = files.get(file_id, include_pending=True)
        if rec is None:
            raise errors.not_found("Upload URL not found or expired", "file_not_found")
        if not hmac.compare_digest(token.encode(), rec.token.encode()):
            raise errors.ApiError(403, "AUTH_ERROR", "Invalid upload token", "permission_error")
        size = 0
        with open(rec.path, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > 50 * settings.max_upload_bytes:  # presigned route is the large-file path
                    raise errors.ApiError(413, "DOCUMENT_TOO_LARGE", "File is too large", "file_too_large")
                fh.write(chunk)
        files.finish(rec, size)
        return JSONResponse({"file_id": rec.file_id})

    @app.delete("/upload/{file_id:path}", dependencies=auth)
    def delete_upload(file_id: str):
        rec = files.get(file_id, include_pending=True)
        if rec is None or rec.kind != "upload":
            raise errors.not_found(f"File not found: {file_id}", "file_not_found")
        files.delete(rec.id)
        return {"file_id": rec.file_id}

    @app.get("/files/{file_id}", include_in_schema=False)
    def get_file(file_id: str, token: str = ""):
        rec = files.get(file_id)
        if rec is None or not rec.path.exists():
            raise errors.not_found("File not found or expired", "file_not_found")
        if not hmac.compare_digest(token.encode(), rec.token.encode()):
            raise errors.ApiError(403, "AUTH_ERROR", "Invalid file token", "permission_error")
        return FileResponse(rec.path, media_type=rec.content_type or "application/octet-stream",
                            filename=rec.filename)

    # ---- parse / extract / split / classify / edit ----
    @app.post("/parse", dependencies=auth)
    def parse(request: Request, body: schemas.AsyncParseConfig):
        # Reducto's /parse takes SyncParseConfig or AsyncParseConfig: an explicit `async` block queues the job
        return submit(request, "Parse", body, sync="async_" not in body.model_fields_set)

    @app.post("/parse_async", dependencies=auth)
    def parse_async(request: Request, body: schemas.AsyncParseConfig):
        return submit(request, "Parse", body, sync=False)

    @app.post("/extract", dependencies=auth)
    def extract(request: Request, body: schemas.AsyncExtractConfig):
        return submit(request, "Extract", body, sync="async_" not in body.model_fields_set)

    @app.post("/extract_async", dependencies=auth)
    def extract_async(request: Request, body: schemas.AsyncExtractConfig):
        return submit(request, "Extract", body, sync=False)

    @app.post("/split", dependencies=auth)
    def split(request: Request, body: schemas.SyncSplitConfig):
        return submit(request, "Split", body, sync=True)

    @app.post("/split_async", dependencies=auth)
    def split_async(request: Request, body: schemas.AsyncSplitConfig):
        return submit(request, "Split", body, sync=False)

    @app.post("/classify", dependencies=auth)
    def classify(request: Request, body: schemas.ClassifyConfig):
        return submit(request, "Classify", body, sync=True)

    @app.post("/classify_async", dependencies=auth)
    def classify_async(request: Request, body: schemas.ClassifyConfig):
        return submit(request, "Classify", body, sync=False)

    @app.post("/edit", dependencies=auth)
    def edit(request: Request, body: schemas.EditConfig):
        return submit(request, "Edit", body, sync=True)

    @app.post("/edit_async", dependencies=auth)
    def edit_async(request: Request, body: schemas.AsyncEditConfig):
        return submit(request, "Edit", body, sync=False)

    @app.post("/pipeline", dependencies=auth, deprecated=True)
    def pipeline(body: schemas.V3PipelineConfig):
        raise errors.not_implemented(PIPELINE_GONE)

    @app.post("/pipeline_async", dependencies=auth, deprecated=True)
    def pipeline_async(body: schemas.V3AsyncPipelineConfig):
        raise errors.not_implemented(PIPELINE_GONE)

    # ---- jobs ----
    @app.get("/job/{job_id}", dependencies=auth)
    def retrieve_job(job_id: str):
        job = get_job(job_id)
        status = job.status if settings.job_status_detail else PUBLIC_STATUS[job.status]
        result = jobs.read_result(job.id) if job.status == "Completed" else None
        return {"status": status, "result": result, "progress": job.progress, "reason": job.reason,
                "error": job.error, "type": job.type, "num_pages": job.num_pages, "total_pages": job.total_pages,
                "source": job.source, "duration": job.duration, "created_at": _iso(job.created_at),
                "raw_config": job.raw_config, "bucket": None}

    @app.delete("/job/{job_id}", dependencies=auth, status_code=202)
    def delete_job(job_id: str, include_persisted: bool = False):
        job = get_job(job_id)  # 404 unknown, 410 already deleted
        if job.status in ACTIVE:
            raise errors.ApiError(409, "JOB_NOT_COMPLETE", f"Job {job_id} is still {job.status}; cancel it or wait "
                                  "for it to finish before deleting", "job_not_complete", job_id)
        jobs.mark_deleted(job_id)
        files.delete_for_job(job_id)
        return {"job_id": job_id}

    @app.post("/cancel/{job_id}", dependencies=auth)
    def cancel_job(job_id: str):
        get_job(job_id)
        status = jobs.cancel(job_id)
        if status in ("Completed", "Failed"):
            raise errors.ApiError(409, "JOB_STATE_ERROR", f"Job {job_id} already finished ({status})",
                                  "job_state_error", job_id)
        return {}

    @app.get("/jobs", dependencies=auth)
    def list_jobs(exclude_configs: bool = False, cursor: Optional[str] = None,
                  limit: int = Query(default=100, ge=1, le=500)):
        try:
            page, nxt = jobs.list(limit=limit, cursor=cursor)
        except Exception:
            raise errors.bad_request("Invalid cursor", type="invalid_cursor") from None
        return {"jobs": [{"job_id": j.id, "status": j.status, "type": j.type,
                          "raw_config": "" if exclude_configs else j.raw_config, "created_at": _iso(j.created_at),
                          "source": j.source, "num_pages": j.num_pages, "total_pages": j.total_pages,
                          "duration": j.duration, "bucket": None} for j in page],
                "next_cursor": nxt}

    # ---- misc ----
    @app.post("/configure_webhook", dependencies=auth)
    async def configure_webhook(request: Request, url: Optional[str] = None):
        """Reducto returns its Svix portal URL. We have no portal: this stores (``?url=`` or ``{"url": ...}``)
        and returns the default URL used for svix-mode webhooks."""
        if url is None and request.headers.get("content-type", "").startswith("application/json"):
            try:
                data = await request.json()
                url = data.get("url") if isinstance(data, dict) else None
            except Exception:
                url = None
        if url is not None:
            if url and not url.startswith(("http://", "https://")):
                raise errors.bad_request("url must be http(s)", type="invalid_url")
            db.set_setting(webhooks.DEFAULT_URL_KEY, url or None)
        return JSONResponse(db.get_setting(webhooks.DEFAULT_URL_KEY) or settings.webhook_url or "")

    @app.get("/version")
    def version():
        return JSONResponse(f"readanydoc-{__version__}")

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "version": __version__, "auth": bool(os.environ.get(API_KEY_ENV))}

    return app
