from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import cache
from app.config import settings
from app.db import engine
from app.logging_config import (
    bind_request_context,
    clear_request_context,
    get_logger,
    new_request_id,
    setup_logging,
)
from app.pubsub import live_update_listener
from app.routers import admin, auth, invites, student

# Must run before anything else emits a record, so startup logs are formatted
# the same way request logs are.
setup_logging()
log = get_logger("app.main")

# The header a caller (or an upstream proxy) can set to join its own trace to
# ours. Echoed back on every response so a browser or a support ticket can
# quote the exact ID to grep for.
REQUEST_ID_HEADER = "X-Request-ID"

# Health probes fire every few seconds per pod. Logging them at INFO would be
# most of the log volume and none of its value; failures still surface via the
# handler's own error logging and the 5xx path below.
_QUIET_PATHS = frozenset({"/health", "/ready", "/"})


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    log.info(
        "application starting",
        extra={
            "app": settings.app_name,
            "environment": settings.environment,
            "debug": settings.debug,
            "log_level": settings.log_level,
            "upload_dir": settings.upload_dir,
            "ai_evaluation_enabled": settings.ai_evaluation_enabled,
        },
    )
    listener_task = asyncio.create_task(live_update_listener())
    log.info("live monitor pubsub listener started")
    try:
        yield
    finally:
        log.info("application shutting down")
        listener_task.cancel()
        await asyncio.gather(listener_task, return_exceptions=True)
        try:
            await cache.close_redis()
        except Exception:
            log.exception("redis shutdown failed")
        try:
            await engine.dispose()
        except Exception:
            log.exception("database engine disposal failed")
        log.info("application shutdown complete")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Attempt-Token", "X-Response-Time", REQUEST_ID_HEADER],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Correlation ID, access log, and latency.

    Auto-save p99 is the number that predicts an exam-day incident, so every
    response still carries its own latency and slow writes are still warned
    about. The correlation ID set here is what lets a single auto-save be
    followed from this line down through the service and cache layers.
    """
    request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
    bind_request_context(request_id=request_id)
    request.state.request_id = request_id

    path = request.url.path
    quiet = path in _QUIET_PATHS
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        # Starlette re-raises to its own 500 handler, which logs nothing about
        # *which* request died. This is the only place that association exists.
        log.exception(
            "unhandled exception in request",
            extra={
                "method": request.method,
                "path": path,
                "duration_ms": round(elapsed_ms, 1),
                "client_ip": request.client.host if request.client else None,
            },
        )
        clear_request_context()
        raise

    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
    response.headers[REQUEST_ID_HEADER] = request_id

    if not quiet or response.status_code >= 500:
        # 5xx is an app fault, 4xx is a caller fault worth seeing but not paging on.
        level = (
            logging.ERROR
            if response.status_code >= 500
            else logging.WARNING
            if response.status_code >= 400
            else logging.INFO
        )
        log.log(
            level,
            "%s %s -> %s",
            request.method,
            path,
            response.status_code,
            extra={
                "method": request.method,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": round(elapsed_ms, 1),
                "client_ip": request.client.host if request.client else None,
            },
        )

    if elapsed_ms > 500 and path.startswith("/api/exam"):
        log.warning(
            "slow request %s %s %.0fms",
            request.method,
            path,
            elapsed_ms,
            extra={"method": request.method, "path": path, "duration_ms": round(elapsed_ms, 1)},
        )

    clear_request_context()
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Body values are never logged — a validation failure on the login or
    # pin-login route would otherwise write the rejected credential out.
    log.warning(
        "request validation failed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "error_count": len(exc.errors()),
            "error_fields": [".".join(str(p) for p in e.get("loc", ())) for e in exc.errors()[:10]],
        },
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation failed", "errors": exc.errors()[:10]},
    )


app.include_router(auth.router)
app.include_router(invites.router)
app.include_router(student.router)
app.include_router(admin.router)

Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.upload_dir), name="uploads")


@app.get("/", tags=["ops"])
async def root() -> dict[str, str]:
    return {"app": settings.app_name, "status": "ok", "environment": settings.environment}


@app.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
async def ready() -> JSONResponse:
    """Readiness distinguishes 'degraded' from 'down': Redis being unavailable
    slows the exam but does not stop it, so the pod stays in rotation."""
    checks = {"database": "down", "redis": "down"}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "up"
    except Exception:
        log.exception("readiness probe: database check failed")

    checks["redis"] = "up" if await cache.ping() else "down"

    if checks["database"] != "up":
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    state = "ok" if checks["redis"] == "up" else "degraded"
    if state == "degraded":
        log.warning("readiness probe: degraded", extra={"checks": checks})
    return JSONResponse(status_code=200, content={"status": state, "checks": checks})
