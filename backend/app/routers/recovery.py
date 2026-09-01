"""The student-facing half of the exam-recovery safeguard (see
app/services/recovery.py for why this only ever resumes an existing attempt).

Same shape as app/routers/invites.py on purpose — it's the closest existing
analog: a token stored only as a hash, resolved server-side to exactly one
attempt and one student, never exposed as an id in the URL. Unlike the invite
PIN flow there's nothing to mint on view — viewing the preview has no side
effect beyond recording that it was opened, so an email link-scanner visiting
this URL can never consume the recovery.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import Answer, Exam, ExamAttempt, ExamAttemptRecovery, Student
from app.routers.auth import _issue_student_session
from app.schemas import RecoveryPreviewOut, TokenPair
from app.services import recovery as recovery_service

router = APIRouter(prefix="/api/recovery", tags=["recovery"])


async def _load_record(db: AsyncSession, token: str) -> ExamAttemptRecovery:
    result = await db.execute(
        select(ExamAttemptRecovery).where(
            ExamAttemptRecovery.token_hash == recovery_service.hash_token(token)
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This recovery link is invalid.")
    return record


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/{token}", response_model=RecoveryPreviewOut)
async def preview_recovery(token: str, db: Annotated[AsyncSession, Depends(get_db)]) -> RecoveryPreviewOut:
    record = await _load_record(db, token)

    if record.status == "resumed":
        return RecoveryPreviewOut(exam_title="", answered_count=0, remaining_seconds=0, already_used=True)
    if record.token_expires_at is None or _aware(record.token_expires_at) <= datetime.now(UTC):
        raise HTTPException(status.HTTP_410_GONE, "This recovery link has expired.")

    attempt = await db.get(ExamAttempt, record.attempt_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This recovery link is invalid.")
    exam = await db.get(Exam, record.exam_id)

    if record.opened_at is None:
        record.opened_at = datetime.now(UTC)

    answered_result = await db.execute(
        select(func.count(Answer.id)).where(
            Answer.attempt_id == attempt.id,
            or_(Answer.selected_option_id.isnot(None), Answer.code_text.isnot(None)),
        )
    )
    answered_count = answered_result.scalar() or 0

    # Recomputed live (not the frozen record) so a student who waits before
    # clicking still sees an accurate preview — but what actually gets applied
    # on /continue is the frozen remaining_seconds on the record, so the wait
    # itself never costs them time.
    remaining = recovery_service.remaining_seconds_for(attempt)

    return RecoveryPreviewOut(
        exam_title=exam.title if exam else "",
        answered_count=answered_count,
        remaining_seconds=remaining,
    )


@router.post("/{token}/continue", response_model=TokenPair)
async def continue_recovery(
    token: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> TokenPair:
    record = await _load_record(db, token)

    attempt = await db.get(ExamAttempt, record.attempt_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This recovery link is invalid.")

    if record.status != "resumed":
        if record.token_expires_at is None or _aware(record.token_expires_at) <= datetime.now(UTC):
            raise HTTPException(status.HTTP_410_GONE, "This recovery link has expired.")

    outcome = await recovery_service.resume_attempt(
        db,
        attempt,
        reason=record.reason,
        note=record.admin_note,
        admin_id=None,
        recovery=record,
    )
    if not outcome.success:
        raise HTTPException(status.HTTP_409_CONFLICT, outcome.reason or "This attempt can no longer be recovered.")

    student = await db.get(Student, attempt.student_id)
    if student is None or not student.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This student account is no longer active.")

    return await _issue_student_session(db, request, student)
