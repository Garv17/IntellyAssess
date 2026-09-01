"""Resume a prematurely auto-submitted exam attempt without losing progress.

Temporary safeguard while the underlying premature-auto-submit bug (false 410s
under load) is fixed separately. `start_exam()` (app/routers/student.py) is
already idempotent for an `in_progress` attempt — it re-issues tokens, re-seeds
the Redis deadline from `deadline_at`, and returns the merged persisted +
buffered answers without resetting anything. So this module's only job is to
atomically flip a stale `auto_submitted` attempt back to `in_progress` with the
correct remaining time; the existing exam-loading endpoints handle the rest.

`deadline_at` is set once at real exam start and is never mutated by
`submit_exam`, the auto-submit sweeper, or `force_submit` — only `submitted_at`
and `status` change. So for a prematurely auto-submitted attempt, the unused
time is simply `deadline_at - submitted_at`, computed from fields that already
exist. If the attempt genuinely ran out the clock, `submitted_at >= deadline_at`
and this is naturally <= 0, which blocks recovery.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.config import settings
from app.models import (
    AttemptStatus,
    AuditLog,
    CodingSubmission,
    EvaluationStatus,
    ExamAttempt,
    ExamAttemptRecovery,
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def remaining_seconds_for(attempt: ExamAttempt) -> int:
    if attempt.submitted_at is None:
        return 0
    deadline = _aware(attempt.deadline_at)
    submitted = _aware(attempt.submitted_at)
    return max(0, int((deadline - submitted).total_seconds()))


@dataclass
class EligibilityResult:
    eligible: bool
    remaining_seconds: int
    reason: str | None = None


async def check_eligible(db: AsyncSession, attempt: ExamAttempt) -> EligibilityResult:
    """Pre-check used by the candidate listing and before creating a recovery
    record. The authoritative gate against a real race is still the guarded
    UPDATE in resume_attempt() below — this is for a clear error message."""
    if attempt.status is not AttemptStatus.auto_submitted:
        return EligibilityResult(False, 0, "Attempt is not eligible for recovery")
    if attempt.reopen_count >= settings.max_reopen_count:
        return EligibilityResult(
            False,
            0,
            f"Maximum recovery limit reached ({settings.max_reopen_count}/{settings.max_reopen_count})",
        )
    remaining = remaining_seconds_for(attempt)
    if remaining <= 0:
        return EligibilityResult(False, 0, "No remaining time to restore")

    finalized = await db.execute(
        select(CodingSubmission.id)
        .where(
            CodingSubmission.attempt_id == attempt.id,
            CodingSubmission.status == EvaluationStatus.finalized,
        )
        .limit(1)
    )
    if finalized.first() is not None:
        return EligibilityResult(
            False, 0, "A coding answer on this attempt has already been finalized by an admin"
        )
    return EligibilityResult(True, remaining)


@dataclass
class ResumeResult:
    success: bool
    already_resumed: bool
    remaining_seconds: int
    new_deadline_at: datetime | None
    previous_status: str | None
    previous_deadline_at: datetime | None
    reopen_count: int
    reason: str | None = None


async def resume_attempt(
    db: AsyncSession,
    attempt: ExamAttempt,
    *,
    reason: str,
    note: str | None,
    admin_id: uuid.UUID | None,
    recovery: ExamAttemptRecovery | None = None,
) -> ResumeResult:
    """Atomically flips `attempt` from auto_submitted back to in_progress with the
    frozen remaining time. Safe to call twice for the same attempt (double-click,
    a retried request, or a re-used-but-already-consumed recovery link) — a
    second call is a no-op success, never a second time grant or a duplicated
    reopen_count increment."""
    if attempt.status is AttemptStatus.in_progress:
        deadline = _aware(attempt.deadline_at)
        return ResumeResult(
            success=True,
            already_resumed=True,
            remaining_seconds=cache.seconds_until(deadline),
            new_deadline_at=deadline,
            previous_status=None,
            previous_deadline_at=None,
            reopen_count=attempt.reopen_count,
        )

    eligibility = await check_eligible(db, attempt)
    if not eligibility.eligible:
        return ResumeResult(
            False, False, 0, None, None, None, attempt.reopen_count, eligibility.reason
        )

    now = datetime.now(UTC)
    new_deadline = now + timedelta(seconds=eligibility.remaining_seconds)
    previous_status = attempt.status
    previous_deadline = _aware(attempt.deadline_at)

    # The WHERE clause is the concurrency guard: two simultaneous callers race on
    # this row's lock, the loser's WHERE no longer matches once the winner
    # commits, so it returns zero rows instead of double-incrementing
    # reopen_count or granting a second block of time.
    result = await db.execute(
        update(ExamAttempt)
        .where(
            ExamAttempt.id == attempt.id,
            ExamAttempt.status == AttemptStatus.auto_submitted,
            ExamAttempt.reopen_count < settings.max_reopen_count,
        )
        .values(
            status=AttemptStatus.in_progress,
            deadline_at=new_deadline,
            submitted_at=None,
            reopen_count=ExamAttempt.reopen_count + 1,
        )
        .returning(ExamAttempt.reopen_count)
    )
    row = result.first()
    if row is None:
        await db.refresh(attempt)
        if attempt.status is AttemptStatus.in_progress:
            deadline = _aware(attempt.deadline_at)
            return ResumeResult(
                True, True, cache.seconds_until(deadline), deadline, None, None, attempt.reopen_count
            )
        return ResumeResult(
            False, False, 0, None, None, None, attempt.reopen_count,
            "Attempt is no longer eligible for recovery",
        )

    new_reopen_count = row[0]

    # Non-finalized coding snapshots from the premature auto-submit must go, or
    # the real final submit's ON CONFLICT DO NOTHING (services/coding.py) would
    # keep the stale snapshot instead of whatever the student ends up with.
    await db.execute(
        CodingSubmission.__table__.delete().where(
            CodingSubmission.attempt_id == attempt.id,
            CodingSubmission.status != EvaluationStatus.finalized,
        )
    )

    await cache.set_deadline(str(attempt.id), new_deadline, cache.seconds_until(new_deadline) + 300)

    db.add(
        AuditLog(
            actor_type="admin" if admin_id else "student",
            actor_id=admin_id,
            action="attempt_recovered",
            target=str(attempt.id),
            meta={
                "reason": reason,
                "note": note,
                "reopen_count": new_reopen_count,
                "remaining_seconds": eligibility.remaining_seconds,
            },
        )
    )

    if recovery is not None:
        recovery.status = "resumed"
        recovery.resumed_at = now
        recovery.previous_status = previous_status.value
        recovery.previous_deadline_at = previous_deadline
        recovery.new_deadline_at = new_deadline

    # Keep the already-attached ORM instance in sync with the raw UPDATE above.
    attempt.status = AttemptStatus.in_progress
    attempt.deadline_at = new_deadline
    attempt.submitted_at = None
    attempt.reopen_count = new_reopen_count

    return ResumeResult(
        True,
        False,
        eligibility.remaining_seconds,
        new_deadline,
        previous_status.value,
        previous_deadline,
        new_reopen_count,
    )
