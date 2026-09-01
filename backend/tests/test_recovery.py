"""Tests for the exam-recovery safeguard (app/services/recovery.py) that don't
need a live database — same style as test_core_logic.py: SimpleNamespace fakes
duck-typing an ExamAttempt/CodingSubmission where a real DB row isn't needed.

Concurrency (two admins resuming the same attempt at once) is not covered here:
the guard is a single guarded SQL UPDATE ("WHERE status = 'auto_submitted' AND
reopen_count < max"), and correctly exercising the race needs a live Postgres
connection to observe the second UPDATE matching zero rows. That's an
integration-test concern, not a unit one — see recovery.resume_attempt's
docstring for the mechanism itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import AttemptStatus, EvaluationStatus, ExamAttempt, ExamAttemptRecovery
from app.services import recovery


def _fake_attempt(
    status=AttemptStatus.auto_submitted,
    minutes_used=5,
    duration_minutes=75,
    reopen_count=0,
):
    started = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    deadline = started + timedelta(minutes=duration_minutes)
    submitted = started + timedelta(minutes=minutes_used) if status is not AttemptStatus.in_progress else None
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        started_at=started,
        deadline_at=deadline,
        submitted_at=submitted,
        reopen_count=reopen_count,
    )


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeDb:
    """Only supports what check_eligible needs: db.execute(select(...)).first()."""

    def __init__(self, finalized_exists: bool = False):
        self.finalized_exists = finalized_exists

    async def execute(self, _stmt):
        return _FakeResult([("row",)] if self.finalized_exists else [])


# --------------------------------------------------------------- remaining time


def test_case1_auto_submit_after_5_minutes():
    attempt = _fake_attempt(minutes_used=5, duration_minutes=75)
    assert recovery.remaining_seconds_for(attempt) == 70 * 60


def test_case2_interrupted_after_60_minutes():
    attempt = _fake_attempt(minutes_used=60, duration_minutes=75)
    assert recovery.remaining_seconds_for(attempt) == 15 * 60


def test_case3_used_all_time_yields_zero_not_negative():
    attempt = _fake_attempt(minutes_used=75, duration_minutes=75)
    assert recovery.remaining_seconds_for(attempt) == 0


def test_case4_overrun_never_goes_negative():
    attempt = _fake_attempt(minutes_used=90, duration_minutes=75)
    assert recovery.remaining_seconds_for(attempt) == 0


def test_remaining_seconds_is_zero_without_a_submitted_at():
    attempt = _fake_attempt(status=AttemptStatus.in_progress)
    assert recovery.remaining_seconds_for(attempt) == 0


# ------------------------------------------------------------------- eligibility


@pytest.mark.asyncio
async def test_auto_submitted_with_time_left_is_eligible():
    attempt = _fake_attempt(minutes_used=5, duration_minutes=75)
    result = await recovery.check_eligible(_FakeDb(), attempt)
    assert result.eligible is True
    assert result.remaining_seconds == 70 * 60


@pytest.mark.asyncio
async def test_manually_submitted_attempt_is_not_eligible():
    attempt = _fake_attempt(status=AttemptStatus.submitted, minutes_used=5, duration_minutes=75)
    result = await recovery.check_eligible(_FakeDb(), attempt)
    assert result.eligible is False


@pytest.mark.asyncio
async def test_in_progress_attempt_is_not_eligible():
    attempt = _fake_attempt(status=AttemptStatus.in_progress)
    result = await recovery.check_eligible(_FakeDb(), attempt)
    assert result.eligible is False


@pytest.mark.asyncio
async def test_expired_time_used_is_not_eligible():
    attempt = _fake_attempt(minutes_used=75, duration_minutes=75)
    result = await recovery.check_eligible(_FakeDb(), attempt)
    assert result.eligible is False


@pytest.mark.asyncio
async def test_reopen_count_at_max_is_not_eligible():
    attempt = _fake_attempt(minutes_used=5, duration_minutes=75, reopen_count=settings.max_reopen_count)
    result = await recovery.check_eligible(_FakeDb(), attempt)
    assert result.eligible is False


@pytest.mark.asyncio
async def test_reopen_count_one_below_max_is_eligible():
    attempt = _fake_attempt(minutes_used=5, duration_minutes=75, reopen_count=settings.max_reopen_count - 1)
    result = await recovery.check_eligible(_FakeDb(), attempt)
    assert result.eligible is True


@pytest.mark.asyncio
async def test_finalized_coding_submission_blocks_recovery():
    attempt = _fake_attempt(minutes_used=5, duration_minutes=75)
    result = await recovery.check_eligible(_FakeDb(finalized_exists=True), attempt)
    assert result.eligible is False
    assert "finalized" in (result.reason or "")


# ------------------------------------------------------------------------ token


def test_hash_token_is_deterministic_and_distinguishes_tokens():
    a = recovery.hash_token("token-a")
    b = recovery.hash_token("token-a")
    c = recovery.hash_token("token-b")
    assert a == b
    assert a != c
    assert len(a) == 64  # sha256 hex digest


# -------------------------------------------------------------------- schema


def test_exam_attempt_has_reopen_count_column():
    assert "reopen_count" in ExamAttempt.__table__.columns


def test_recovery_table_has_expected_audit_columns():
    columns = ExamAttemptRecovery.__table__.columns
    for name in (
        "attempt_id", "student_id", "exam_id", "created_by", "reason", "admin_note",
        "status", "token_hash", "token_expires_at", "remaining_seconds",
        "previous_status", "previous_deadline_at", "new_deadline_at",
        "sent_at", "opened_at", "resumed_at",
    ):
        assert name in columns


def test_evaluation_status_finalized_is_a_real_status():
    # Guards check_eligible's guard clause against a renamed/removed enum member.
    assert EvaluationStatus.finalized.value == "finalized"
