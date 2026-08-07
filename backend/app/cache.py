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

# ---- key layout ------------------------------------------------------------
ANSWERS = "attempt:{attempt_id}:answers"  # hash: question_id -> json payload
DEADLINE = "attempt:{attempt_id}:deadline"  # string: ISO timestamp
DIRTY = "dirty_attempts"  # set of attempt_ids awaiting flush
DENYLIST = "jti:denied:{jti}"  # string: "1"
SESSION = "student:{student_id}:session"  # string: active jti (single-session)
MAGIC_COOLDOWN = "magic:cooldown:{student_id}"  # string: "1"


def redis() -> aioredis.Redis:
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url, encoding="utf-8", decode_responses=True, max_connections=50
        )
    return _pool


async def close_redis() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


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
        pipe = redis().pipeline()
        pipe.hset(ANSWERS.format(attempt_id=attempt_id), mapping=payload)
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


def seconds_until(deadline: datetime) -> int:
    return max(0, int((deadline - datetime.now(UTC)).total_seconds()))
