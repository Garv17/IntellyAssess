"""Cross-worker fan-out for Live Monitor pushes.

The backend runs as 4 separate uvicorn worker processes (backend/Dockerfile), each
with its own in-memory WebSocket registry (app/ws_manager.py). A change published by
any process (a Celery task, or another worker's request handler) has to reach
whichever process actually holds the browser's socket — Redis pub/sub is the shared
bus that makes that possible. One instance of this listener runs per worker process.
"""

from __future__ import annotations

import asyncio
import uuid

from app import cache
from app.db import SessionLocal
from app.logging_config import get_logger
from app.services.monitor import build_live_snapshot
from app.ws_manager import live_connections

log = get_logger(__name__)


async def live_update_listener() -> None:
    while True:
        try:
            pubsub = cache.redis().pubsub()
            await pubsub.psubscribe("live:*")
            async for message in pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                channel: str = message["channel"]
                exam_id = channel.removeprefix("live:")
                if not live_connections.has_connections(exam_id):
                    continue
                try:
                    async with SessionLocal() as session:
                        snapshot = await build_live_snapshot(session, uuid.UUID(exam_id))
                    await live_connections.broadcast(exam_id, snapshot.model_dump(mode="json"))
                except Exception:
                    log.exception(
                        "live monitor broadcast failed",
                        extra={"exam_id": exam_id},
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("live monitor pubsub listener crashed; retrying in 1s")
            await asyncio.sleep(1)
