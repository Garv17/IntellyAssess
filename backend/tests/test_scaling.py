"""Tests for the 200-student scaling changes: judge_run/judge_grade queue
separation, the Redis-backed run cooldown/dedup primitives, and judge metrics.
Redis is mocked (no fakeredis dependency exists in this project) — these check
that our code issues the right Redis commands with the right arguments, not
real Redis semantics."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import cache
from app.services import sandbox
from app.tasks.celery_app import celery_app


# ------------------------------------------------------------------ queue routing


@pytest.mark.parametrize(
    "task_name,expected_queue",
    [
        ("judge.run", "judge_run"),
        ("judge.grade_attempt", "judge_grade"),
        ("judge.preview_sql", "judge_grade"),
        ("maintenance.flush_answers", "default"),
        ("maintenance.auto_submit_expired", "default"),
        ("mailer.send", "default"),
    ],
)
def test_task_routing(task_name, expected_queue):
    """A grading spike must never be able to delay an interactive Run: judge.run has
    to land on its own queue, distinct from judge.grade_attempt/preview_sql."""
    routed = celery_app.amqp.router.route({}, task_name)
    assert routed["queue"].name == expected_queue


def test_run_and_grade_queues_are_distinct():
    run_queue = celery_app.amqp.router.route({}, "judge.run")["queue"].name
    grade_queue = celery_app.amqp.router.route({}, "judge.grade_attempt")["queue"].name
    assert run_queue != grade_queue


# --------------------------------------------------------------- sandbox validation


def test_execute_rejects_unsupported_language_without_docker():
    # Must fail before ever touching the Docker client — these two checks run first.
    results, error, error_kind = sandbox.execute(
        language="cobol", code="x", stdin_batch=[], time_limit_ms=1000, memory_limit_mb=64
    )
    assert results == []
    assert error is not None
    assert error_kind == "validation"


def test_execute_rejects_oversized_submission_without_docker():
    from app.config import settings

    oversized = "a" * (settings.judge_max_code_bytes + 1)
    results, error, error_kind = sandbox.execute(
        language="python", code=oversized, stdin_batch=[], time_limit_ms=1000, memory_limit_mb=64
    )
    assert results == []
    assert error is not None
    assert error_kind == "validation"


# --------------------------------------------------------------------- run cooldown


def _mock_redis(**methods):
    client = MagicMock()
    for name, value in methods.items():
        setattr(client, name, AsyncMock(return_value=value))
    return client


@pytest.mark.asyncio
async def test_claim_run_cooldown_allows_first_call():
    client = _mock_redis(set=True)
    with patch("app.cache.redis", return_value=client):
        allowed = await cache.claim_run_cooldown("attempt-1", 20)
    assert allowed is True
    client.set.assert_awaited_once()
    _, kwargs = client.set.call_args
    assert kwargs["nx"] is True
    assert kwargs["ex"] == 20


@pytest.mark.asyncio
async def test_claim_run_cooldown_blocks_within_window():
    # redis SET NX returns None/False when the key already exists.
    client = _mock_redis(set=None)
    with patch("app.cache.redis", return_value=client):
        allowed = await cache.claim_run_cooldown("attempt-1", 20)
    assert allowed is False


@pytest.mark.asyncio
async def test_claim_run_cooldown_fails_open_on_redis_error():
    client = MagicMock()
    client.set = AsyncMock(side_effect=ConnectionError("redis down"))
    with patch("app.cache.redis", return_value=client):
        allowed = await cache.claim_run_cooldown("attempt-1", 20)
    assert allowed is True  # a dead Redis must not block every student's Run button


# ------------------------------------------------------------------------ run dedup


@pytest.mark.asyncio
async def test_dedup_claims_when_no_duplicate_in_flight():
    client = _mock_redis(set=True)
    with patch("app.cache.redis", return_value=client):
        existing = await cache.claim_or_get_run_dedup("key-1", "run-1", 210)
    assert existing is None  # None means "your run_id is the one to use"


@pytest.mark.asyncio
async def test_dedup_returns_existing_run_on_double_click():
    client = _mock_redis(set=None, get="run-original")
    with patch("app.cache.redis", return_value=client):
        existing = await cache.claim_or_get_run_dedup("key-1", "run-retry", 210)
    assert existing == "run-original"


# ------------------------------------------------------------------- judge counters


@pytest.mark.asyncio
async def test_get_judge_counters_defaults_missing_keys_to_zero():
    client = MagicMock()
    client.mget = AsyncMock(return_value=["3", None])
    with patch("app.cache.redis", return_value=client):
        counts = await cache.get_judge_counters(["run_done", "run_error"])
    assert counts == {"run_done": 3, "run_error": 0}
