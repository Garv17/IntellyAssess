"""Redis-backed exam state: answer buffer, timer authority, token denylist.

Everything here is best-effort with a Postgres fallback in the callers. Redis
going down must slow the exam, not stop it.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import redis.asyncio as aioredis

from app.config import settings

log = logging.getLogger(__name__)

_pool: aioredis.Redis | None = None
_broker_pool: aioredis.Redis | None = None

# ---- key layout ------------------------------------------------------------
ANSWERS = "attempt:{attempt_id}:answers"  # hash: question_id -> json payload
DEADLINE = "attempt:{attempt_id}:deadline"  # string: ISO timestamp
DIRTY = "dirty_attempts"  # set of attempt_ids awaiting flush
DENYLIST = "jti:denied:{jti}"  # string: "1"
SESSION = "student:{student_id}:session"  # string: active jti (single-session)
MAGIC_COOLDOWN = "magic:cooldown:{student_id}"  # string: "1"
LIVE_CHANNEL = "live:{exam_id}"  # pub/sub: marker only, no payload
RUN_COOLDOWN = "run:cooldown:{attempt_id}"  # string: "1" — per-attempt Run rate limit
RUN_DEDUP = "run:dedup:{dedup_key}"  # string: run_id — collapses duplicate/retried runs
JUDGE_COUNTER = "judge:counter:{name}"  # string: incrementing counter, see incr_judge_counter


def redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True, max_connections=50
        )
    return _pool


async def close_redis() -> None:
    global _pool, _broker_pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
    if _broker_pool is not None:
        await _broker_pool.aclose()
        _broker_pool = None


def _broker_redis() -> aioredis.Redis:
    """Separate client on the Celery broker's Redis DB (celery_broker_url, distinct
    from redis_url) — only used to read queue depth for the admin metrics endpoint."""
    global _broker_pool
    if _broker_pool is None:
        _broker_pool = aioredis.from_url(
            settings.celery_broker_url, encoding="utf-8", decode_responses=True, max_connections=5
        )
    return _broker_pool


async def get_queue_depths(queue_names: list[str]) -> dict[str, int]:
    """Tasks still waiting in the broker, not yet claimed by any worker. Celery's
    Redis transport stores each queue as a plain list keyed by queue name."""
    try:
        pipe = _broker_redis().pipeline()
        for name in queue_names:
            pipe.llen(name)
        lengths = await pipe.execute()
        return dict(zip(queue_names, lengths, strict=True))
    except Exception as exc:  # pragma: no cover
        log.warning("redis get_queue_depths failed: %s", exc)
        return dict.fromkeys(queue_names, 0)


async def ping() -> bool:
    try:
        return bool(await redis().ping())
    except Exception:
        return False


# --------------------------------------------------------------------- timer


async def set_deadline(attempt_id: str, deadline: datetime, ttl_seconds: int) -> None:
    try:
        await redis().set(
            DEADLINE.format(attempt_id=attempt_id),
            deadline.isoformat(),
            ex=max(ttl_seconds, 60),
        )
    except Exception as exc:  # pragma: no cover - degraded path
        log.warning("redis set_deadline failed: %s", exc)


async def get_deadline(attempt_id: str) -> datetime | None:
    """None means 'unknown' — the caller must fall back to Postgres, not assume expiry."""
    try:
        raw = await redis().get(DEADLINE.format(attempt_id=attempt_id))
        return datetime.fromisoformat(raw) if raw else None
    except Exception as exc:  # pragma: no cover
        log.warning("redis get_deadline failed: %s", exc)
        return None


async def clear_attempt(attempt_id: str) -> None:
    try:
        await redis().delete(
            ANSWERS.format(attempt_id=attempt_id), DEADLINE.format(attempt_id=attempt_id)
        )
        await redis().srem(DIRTY, attempt_id)
    except Exception as exc:  # pragma: no cover
        log.warning("redis clear_attempt failed: %s", exc)


# -------------------------------------------------------------- answer buffer


async def buffer_answers(attempt_id: str, answers: dict[str, dict]) -> bool:
    """Write answers to the buffer and mark the attempt dirty. False => caller must
    write straight to Postgres."""
    if not answers:
        return True
    try:
        payload = {qid: json.dumps(body) for qid, body in answers.items()}
        key = ANSWERS.format(attempt_id=attempt_id)
        pipe = redis().pipeline()
        pipe.hset(key, mapping=payload)
        # Defensive-only bound on an orphaned buffer — see Settings.answer_buffer_ttl_hours.
        pipe.expire(key, settings.answer_buffer_ttl_hours * 3600)
        pipe.sadd(DIRTY, attempt_id)
        await pipe.execute()
        return True
    except Exception as exc:
        log.warning("redis buffer_answers failed, falling back to db: %s", exc)
        return False


async def get_buffered_answers(attempt_id: str) -> dict[str, dict]:
    try:
        raw = await redis().hgetall(ANSWERS.format(attempt_id=attempt_id))
        return {qid: json.loads(body) for qid, body in raw.items()}
    except Exception as exc:  # pragma: no cover
        log.warning("redis get_buffered_answers failed: %s", exc)
        return {}


# ------------------------------------------------------------------ denylist


async def deny_jti(jti: str, ttl_seconds: int) -> None:
    try:
        await redis().set(DENYLIST.format(jti=jti), "1", ex=max(ttl_seconds, 60))
    except Exception as exc:  # pragma: no cover
        log.warning("redis deny_jti failed: %s", exc)


async def is_jti_denied(jti: str) -> bool:
    try:
        return await redis().exists(DENYLIST.format(jti=jti)) == 1
    except Exception:
        # Fail open: a dead Redis must not lock every student out mid-exam.
        return False


# -------------------------------------------------------- single active session


async def register_session(student_pk: str, jti: str, ttl_seconds: int) -> str | None:
    """Records the new active jti and returns the previous one, if any."""
    try:
        key = SESSION.format(student_id=student_pk)
        previous = await redis().getset(key, jti)
        await redis().expire(key, max(ttl_seconds, 60))
        return previous
    except Exception as exc:  # pragma: no cover
        log.warning("redis register_session failed: %s", exc)
        return None


# -------------------------------------------------------- magic-link cooldown


async def start_magic_cooldown(student_pk: str, ttl_seconds: int) -> bool:
    """Atomically claims the cooldown slot. True means the caller may send a link now;
    False means one went out too recently. Fails open (allows sending) if Redis is
    down — the email provider's own quota is the backstop, not this check."""
    try:
        key = MAGIC_COOLDOWN.format(student_id=student_pk)
        return bool(await redis().set(key, "1", ex=max(ttl_seconds, 1), nx=True))
    except Exception as exc:  # pragma: no cover
        log.warning("redis start_magic_cooldown failed: %s", exc)
        return True


# ---------------------------------------------------------------- run rate limit


async def claim_run_cooldown(attempt_id: str, cooldown_seconds: int) -> bool:
    """Atomically claims the per-attempt Run cooldown slot. True means the caller may
    enqueue a run now; False means one was enqueued too recently. Fails open (allows
    the run) if Redis is down — a dead Redis must slow abuse protection, not block
    every student's Run button mid-exam."""
    try:
        key = RUN_COOLDOWN.format(attempt_id=attempt_id)
        return bool(await redis().set(key, "1", ex=max(cooldown_seconds, 1), nx=True))
    except Exception as exc:  # pragma: no cover
        log.warning("redis claim_run_cooldown failed: %s", exc)
        return True


# --------------------------------------------------------------------- run dedup


async def claim_or_get_run_dedup(dedup_key: str, run_id: str, ttl_seconds: int) -> str | None:
    """Atomically claims dedup_key -> run_id if no identical run is already in
    flight (returns None, meaning the caller's run_id is the one to use), or returns
    the run_id an earlier identical request (double-click, frontend/network retry)
    already claimed. Fails open (treats as non-duplicate) if Redis is down."""
    try:
        key = RUN_DEDUP.format(dedup_key=dedup_key)
        claimed = await redis().set(key, run_id, ex=max(ttl_seconds, 1), nx=True)
        if claimed:
            return None
        return await redis().get(key)
    except Exception as exc:  # pragma: no cover
        log.warning("redis claim_or_get_run_dedup failed: %s", exc)
        return None


# ------------------------------------------------------------------ judge metrics


async def incr_judge_counter(name: str, amount: int = 1) -> None:
    """Best-effort counter for the admin judge-metrics endpoint. Never raises —
    losing a counter increment must not fail the judge task or request it's in."""
    try:
        await redis().incrby(JUDGE_COUNTER.format(name=name), amount)
    except Exception as exc:  # pragma: no cover
        log.warning("redis incr_judge_counter(%s) failed: %s", name, exc)


async def get_judge_counters(names: list[str]) -> dict[str, int]:
    try:
        keys = [JUDGE_COUNTER.format(name=name) for name in names]
        values = await redis().mget(keys)
        return {name: int(value) if value else 0 for name, value in zip(names, values, strict=True)}
    except Exception as exc:  # pragma: no cover
        log.warning("redis get_judge_counters failed: %s", exc)
        return dict.fromkeys(names, 0)


# ---------------------------------------------------------- live monitor pub/sub


async def publish_live_update(exam_id: str) -> None:
    """Marker-only publish — whichever worker process holds the connected sockets
    recomputes and pushes the snapshot itself (see app/pubsub.py)."""
    try:
        await redis().publish(LIVE_CHANNEL.format(exam_id=exam_id), "changed")
    except Exception as exc:  # pragma: no cover - degraded path
        log.warning("redis publish_live_update failed: %s", exc)


def seconds_until(deadline: datetime) -> int:
    return max(0, int((deadline - datetime.now(UTC)).total_seconds()))
