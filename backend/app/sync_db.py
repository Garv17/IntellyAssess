"""Synchronous session + Redis client for Celery workers.

The web app is async; Celery is not. Keeping the two access paths separate is
simpler and safer than running an event loop inside a worker.
"""

from __future__ import annotations

from contextlib import contextmanager

import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.cache import JUDGE_COUNTER
from app.config import settings

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
        pass


def incr_judge_counter_sync(name: str, amount: int = 1) -> None:
    """Sync counterpart to app.cache.incr_judge_counter/get_judge_counters, for Celery
    call sites (judge_tasks.py, maintenance.py) which have no event loop. Writes to the
    same `judge:counter:*` keyspace the admin metrics endpoint reads asynchronously."""
    try:
        sync_redis.incrby(JUDGE_COUNTER.format(name=name), amount)
    except Exception:
        pass


@contextmanager
def session_scope() -> Session:
    session = SyncSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
