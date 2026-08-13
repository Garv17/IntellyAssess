"""Tests for the warm container pool (app/services/judge_pool.py). Redis is
mocked (same style as test_scaling.py's cache tests — no fakeredis dependency
exists in this project) and Docker is mocked too: this suite verifies the pool's
own coordination logic (locking, fallback, cleanup-then-release ordering), not
real container/Docker behavior. The live-Docker leak test
(scripts/judge_pool_leak_test.py) covers actual isolation between reuses —
this codebase has no Docker-in-CI precedent (see test_harness.py's docstring)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services import judge_pool
from app.services.judge_pool import PooledContainer


def _mock_container(status="running", ps_count=2, exec_ok=True):
    container = MagicMock()
    container.status = status
    container.reload = MagicMock()

    def exec_run(cmd, **kwargs):
        if not exec_ok:
            return (1, (b"", b"boom"))
        if cmd[-1] == "ps aux | wc -l" or "ps aux | wc -l" in cmd:
            return (0, (str(ps_count).encode(), b""))
        return (0, (b"", b""))  # cleanup command

    container.exec_run.side_effect = exec_run
    return container


# --------------------------------------------------------------------- checkout


def test_checkout_returns_none_when_pooling_disabled():
    with patch("app.services.judge_pool.settings.judge_pool_enabled", False):
        assert judge_pool.checkout("python", "python:3.11-alpine") is None


def test_checkout_returns_none_when_every_slot_locked():
    with (
        patch("app.services.judge_pool.settings.judge_pool_enabled", True),
        patch("app.services.judge_pool.settings.judge_pool_size_per_language", 3),
        patch("app.services.judge_pool.sync_redis") as redis_mock,
    ):
        redis_mock.set.return_value = False  # every SET NX fails -> pool exhausted
        result = judge_pool.checkout("python", "python:3.11-alpine")
    assert result is None
    assert redis_mock.set.call_count == 3  # tried every slot


def test_checkout_returns_none_when_redis_unavailable():
    with (
        patch("app.services.judge_pool.settings.judge_pool_enabled", True),
        patch("app.services.judge_pool.sync_redis") as redis_mock,
    ):
        redis_mock.set.side_effect = ConnectionError("redis down")
        result = judge_pool.checkout("python", "python:3.11-alpine")
    assert result is None  # falls back to ephemeral, never raises


def test_checkout_claims_slot_and_returns_healthy_container():
    container = _mock_container(status="running", ps_count=2)
    with (
        patch("app.services.judge_pool.settings.judge_pool_enabled", True),
        patch("app.services.judge_pool.settings.judge_pool_size_per_language", 3),
        patch("app.services.judge_pool.sync_redis") as redis_mock,
        patch("app.services.judge_pool._get_or_create", return_value=container),
    ):
        redis_mock.set.return_value = True  # first slot tried wins
        result = judge_pool.checkout("python", "python:3.11-alpine")
    assert isinstance(result, PooledContainer)
    assert result.container is container
    assert result.language == "python"


def test_checkout_rebuilds_when_existing_container_unhealthy():
    dead = _mock_container(status="exited")
    fresh = _mock_container(status="running", ps_count=2)
    with (
        patch("app.services.judge_pool.settings.judge_pool_enabled", True),
        patch("app.services.judge_pool.sync_redis") as redis_mock,
        patch("app.services.judge_pool._get_or_create", side_effect=[dead, fresh]),
    ):
        redis_mock.set.return_value = True
        result = judge_pool.checkout("python", "python:3.11-alpine")
    assert result.container is fresh
    dead.remove.assert_called_once_with(force=True)


def test_checkout_releases_lock_on_docker_error():
    with (
        patch("app.services.judge_pool.settings.judge_pool_enabled", True),
        patch("app.services.judge_pool.settings.judge_pool_size_per_language", 1),
        patch("app.services.judge_pool.sync_redis") as redis_mock,
        patch("app.services.judge_pool._get_or_create", side_effect=judge_pool.DockerException("boom")),
    ):
        redis_mock.set.return_value = True
        result = judge_pool.checkout("python", "python:3.11-alpine")
    assert result is None
    redis_mock.delete.assert_called_once()  # lock released, not leaked


# ---------------------------------------------------------------------- checkin


def test_checkin_releases_lock_even_when_cleanup_fails():
    container = _mock_container(exec_ok=False)
    pooled = PooledContainer(container=container, language="python", slot=0)
    with patch("app.services.judge_pool.sync_redis") as redis_mock:
        judge_pool.checkin(pooled, healthy=True)
    container.remove.assert_called_once_with(force=True)  # unhealthy -> discarded
    redis_mock.delete.assert_called_once()  # lock still released


def test_checkin_keeps_healthy_container_without_removing_it():
    container = _mock_container(exec_ok=True, ps_count=2)
    pooled = PooledContainer(container=container, language="python", slot=0)
    with patch("app.services.judge_pool.sync_redis") as redis_mock:
        judge_pool.checkin(pooled, healthy=True)
    container.remove.assert_not_called()
    redis_mock.delete.assert_called_once()


def test_checkin_discards_container_reported_unhealthy_by_caller():
    container = _mock_container()
    pooled = PooledContainer(container=container, language="python", slot=0)
    with patch("app.services.judge_pool.sync_redis") as redis_mock:
        judge_pool.checkin(pooled, healthy=False)
    container.remove.assert_called_once_with(force=True)
    container.exec_run.assert_not_called()  # no point cleaning up a known-bad container
    redis_mock.delete.assert_called_once()


# ------------------------------------------------------- execute() integration


def test_execute_skips_pool_when_memory_exceeds_pool_cap():
    from app.services import sandbox

    with (
        patch("app.services.sandbox.settings.judge_pool_enabled", True),
        patch("app.services.sandbox.settings.judge_pool_memory_mb", 256),
        patch("app.services.judge_pool.checkout") as checkout_mock,
    ):
        # memory_limit_mb=512 > judge_pool_memory_mb=256 -> must not even try the pool
        result = sandbox._execute_pooled(
            sandbox.LANGUAGES["python"], "python", "print(1)", ["1"], 1000, 512
        )
    assert result is None
    checkout_mock.assert_not_called()


def test_execute_falls_back_to_none_when_checkout_misses():
    from app.services import sandbox

    with (
        patch("app.services.sandbox.settings.judge_pool_enabled", True),
        patch("app.services.sandbox.settings.judge_pool_memory_mb", 512),
        patch("app.services.judge_pool.checkout", return_value=None),
    ):
        result = sandbox._execute_pooled(
            sandbox.LANGUAGES["python"], "python", "print(1)", ["1"], 1000, 128
        )
    assert result is None
