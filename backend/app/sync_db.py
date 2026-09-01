"""Synchronous session + Redis client for Celery workers.

The web app is async; Celery is not. Keeping the two access paths separate is
simpler and safer than running an event loop inside a worker.
"""

from __future__ import annotations

from contextlib import contextmanager

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.logging_config import get_logger

log = get_logger(__name__)

sync_engine = create_engine(
    settings.database_url_sync, pool_size=10, max_overflow=5, pool_pre_ping=True
)
SyncSession = sessionmaker(bind=sync_engine, expire_on_commit=False)

sync_redis = redis.from_url(settings.redis_url, decode_responses=True)


def publish_live_update_sync(exam_id: str) -> None:
    """Sync counterpart to app.cache.publish_live_update, for Celery call sites
    (maintenance.py) which have no event loop. Same channel, same Redis instance."""
    try:
        sync_redis.publish(f"live:{exam_id}", "changed")
    except Exception:
        # Still swallowed — a Live Monitor push is advisory and must never fail
        # the auto-submit sweep that called it. But silently: the admin's
        # dashboard going stale during a sitting had no trace anywhere.
        log.warning(
            "live monitor publish failed; dashboard may be stale",
            extra={"exam_id": exam_id},
            exc_info=True,
        )


@contextmanager
def session_scope() -> Session:
    session = SyncSession()
    try:
        yield session
        session.commit()
    except Exception as exc:
        log.warning(
            "worker database session rolled back",
            extra={"error_type": type(exc).__name__},
            exc_info=True,
        )
        session.rollback()
        raise
    finally:
        session.close()
