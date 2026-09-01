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
from app.pubsub import live_update_listener
from app.routers import admin, auth, invites, recovery, student

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("exam")


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    log.info("starting %s (%s)", settings.app_name, settings.environment)
    listener_task = asyncio.create_task(live_update_listener())
    yield
    listener_task.cancel()
    await asyncio.gather(listener_task, return_exceptions=True)
    await cache.close_redis()
    await engine.dispose()


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
    expose_headers=["X-Attempt-Token", "X-Response-Time"],
)


@app.middleware("http")
async def timing(request: Request, call_next):
    """Auto-save p99 is the number that predicts an exam-day incident, so every
    response carries its own latency and slow writes are logged."""
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Response-Time"] = f"{elapsed_ms:.1f}ms"
    if elapsed_ms > 500 and request.url.path.startswith("/api/exam"):
        log.warning("slow request %s %s %.0fms", request.method, request.url.path, elapsed_ms)
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation failed", "errors": exc.errors()[:10]},
    )


app.include_router(auth.router)
app.include_router(invites.router)
app.include_router(recovery.router)
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
    except Exception as exc:
        log.error("db readiness failed: %s", exc)

    checks["redis"] = "up" if await cache.ping() else "down"

    if checks["database"] != "up":
        return JSONResponse(status_code=503, content={"status": "unavailable", "checks": checks})
    state = "ok" if checks["redis"] == "up" else "degraded"
    return JSONResponse(status_code=200, content={"status": state, "checks": checks})
