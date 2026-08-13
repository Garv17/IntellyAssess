from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import cache
from app.db import get_db
from app.deps import (
    attempt_exam,
    current_student,
    resolve_deadline,
    writable_attempt,
)
from app.models import (
    Answer,
    AttemptStatus,
    AuditLog,
    Exam,
    ExamAttempt,
    ExamStatus,
    Question,
    Section,
    Student,
)
from app.schemas import (
    AnswerBatchSave,
    AnswerSave,
    AnswerState,
    ExamPaperOut,
    ExamStateOut,
    ExamSummaryOut,
    HeartbeatOut,
    SaveAck,
    SubmitReceipt,
)
from app.security import create_access_token
from app.services import answers as answer_service
from app.services import coding, grading, paper
from app.seb import verify_seb_request

router = APIRouter(prefix="/api/exam", tags=["student"])

log = logging.getLogger(__name__)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@router.get("/available", response_model=list[ExamSummaryOut])
async def available_exams(
    student: Annotated[Student, Depends(current_student)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ExamSummaryOut]:
    now = datetime.now(UTC)
    result = await db.execute(
        select(Exam).where(
            Exam.status == ExamStatus.published,
            (Exam.cohort.is_(None)) | (Exam.cohort == student.cohort),
            (Exam.ends_at.is_(None)) | (Exam.ends_at > now),
        )
    )
    exams = list(result.scalars())
    if not exams:
        return []

    attempts_result = await db.execute(
        select(ExamAttempt.exam_id, ExamAttempt.status).where(
            ExamAttempt.student_id == student.id,
            ExamAttempt.exam_id.in_([e.id for e in exams]),
        )
    )
    attempt_status = dict(attempts_result.all())

    return [
        ExamSummaryOut(
            id=exam.id,
            title=exam.title,
            description=exam.description,
            duration_minutes=exam.duration_minutes,
            starts_at=exam.starts_at,
            ends_at=exam.ends_at,
            status=exam.status,
            attempt_status=attempt_status.get(exam.id),
            requires_seb=exam.requires_seb,
        )
        for exam in exams
    ]


@router.post("/{exam_id}/start", response_model=ExamStateOut)
async def start_exam(
    exam_id: uuid.UUID,
    request: Request,
    response: Response,
    student: Annotated[Student, Depends(current_student)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ExamStateOut:
    """Idempotent. A second call — including a refresh mid-exam — returns the existing
    attempt rather than creating a new one or erroring."""
    exam = await paper.load_exam_tree(db, exam_id)
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    if exam.status is not ExamStatus.published:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Exam is not open")

    now = datetime.now(UTC)
    if exam.starts_at and now < _aware(exam.starts_at):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Exam has not started yet")
    if exam.ends_at and now > _aware(exam.ends_at):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Exam window has closed")
    if exam.cohort and exam.cohort != student.cohort:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not enrolled in this exam")
    if exam.requires_seb:
        verify_seb_request(request, exam.seb_config_key)

    existing = await db.execute(
        select(ExamAttempt).where(
            ExamAttempt.exam_id == exam_id, ExamAttempt.student_id == student.id
        )
    )
    attempt = existing.scalar_one_or_none()

    if attempt is not None and attempt.status is not AttemptStatus.in_progress:
        raise HTTPException(status.HTTP_409_CONFLICT, "You have already submitted this exam")

    if attempt is None:
        seed = hashlib.sha256(f"{exam_id}:{student.id}".encode()).hexdigest()[:16]
        deadline = now + timedelta(minutes=exam.duration_minutes)
        # Never let an attempt outlive the exam window.
        if exam.ends_at:
            deadline = min(deadline, _aware(exam.ends_at))

        attempt = ExamAttempt(
            exam_id=exam_id,
            student_id=student.id,
            started_at=now,
            deadline_at=deadline,
            question_order=paper.build_question_order(exam, seed),
            ip_address=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:512],
            seb_verified=exam.requires_seb,
        )
        db.add(attempt)
        try:
            await db.flush()
        except IntegrityError:
            # Lost the race against a concurrent start (double-click, two tabs).
            # The unique constraint did its job; reload the winner.
            await db.rollback()
            existing = await db.execute(
                select(ExamAttempt).where(
                    ExamAttempt.exam_id == exam_id, ExamAttempt.student_id == student.id
                )
            )
            attempt = existing.scalar_one()
        else:
            db.add(
                AuditLog(
                    actor_type="student",
                    actor_id=student.id,
                    action="exam_started",
                    target=str(exam_id),
                )
            )

    deadline = _aware(attempt.deadline_at)
    await cache.set_deadline(str(attempt.id), deadline, cache.seconds_until(deadline) + 300)
    await cache.publish_live_update(str(exam_id))

    # Re-issue an attempt-bound token so subsequent saves skip the attempt lookup.
    token, _ = create_access_token(str(student.id), "student", attempt_id=str(attempt.id))
    response.headers["X-Attempt-Token"] = token

    persisted = await answer_service.load_answers(db, attempt.id)
    buffered = await cache.get_buffered_answers(str(attempt.id))
    merged = answer_service.merge(persisted, buffered)

    return ExamStateOut(
        attempt_id=attempt.id,
        exam_id=attempt.exam_id,
        status=attempt.status,
        started_at=_aware(attempt.started_at),
        deadline_at=deadline,
        seconds_remaining=cache.seconds_until(deadline),
        question_order=attempt.question_order,
        answers=[
            AnswerState(question_id=uuid.UUID(qid), **payload) for qid, payload in merged.items()
        ],
    )


@router.get("/state", response_model=ExamStateOut)
async def exam_state(
    attempt: Annotated[ExamAttempt, Depends(attempt_exam)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ExamStateOut:
    """Everything needed to rebuild the UI after a refresh or device switch."""
    deadline = await resolve_deadline(attempt)
    persisted = await answer_service.load_answers(db, attempt.id)
    buffered = await cache.get_buffered_answers(str(attempt.id))
    merged = answer_service.merge(persisted, buffered)

    return ExamStateOut(
        attempt_id=attempt.id,
        exam_id=attempt.exam_id,
        status=attempt.status,
        started_at=_aware(attempt.started_at),
        deadline_at=deadline,
        seconds_remaining=cache.seconds_until(deadline),
        question_order=attempt.question_order,
        answers=[
            AnswerState(question_id=uuid.UUID(qid), **payload) for qid, payload in merged.items()
        ],
    )


@router.get("/questions", response_model=ExamPaperOut)
async def exam_questions(
    attempt: Annotated[ExamAttempt, Depends(attempt_exam)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ExamPaperOut:
    exam = await paper.load_exam_tree(db, attempt.exam_id)
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    return paper.render_paper(exam, attempt.question_order)


@router.patch("/answers", response_model=SaveAck)
async def save_answers(
    payload: AnswerBatchSave,
    attempt: Annotated[ExamAttempt, Depends(writable_attempt)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SaveAck:
    """Auto-save. Buffers to Redis; falls back to a direct upsert if Redis is down."""
    batch = {str(item.question_id): answer_service.payload_from_schema(item) for item in payload.answers}

    buffered = await cache.buffer_answers(str(attempt.id), batch)
    if not buffered:
        await answer_service.upsert_answers(db, attempt.id, batch)

    deadline = await resolve_deadline(attempt)
    return SaveAck(
        saved=len(batch),
        seconds_remaining=cache.seconds_until(deadline),
        server_time=datetime.now(UTC),
    )


@router.put("/answers/{question_id}", response_model=SaveAck)
async def save_single_answer(
    question_id: uuid.UUID,
    payload: AnswerSave,
    attempt: Annotated[ExamAttempt, Depends(writable_attempt)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SaveAck:
    payload.question_id = question_id
    return await save_answers(AnswerBatchSave(answers=[payload]), attempt, db)


@router.post("/heartbeat", response_model=HeartbeatOut)
async def heartbeat(
    attempt: Annotated[ExamAttempt, Depends(attempt_exam)],
    db: Annotated[AsyncSession, Depends(get_db)],
    focus_lost: bool = False,
) -> HeartbeatOut:
    """Timer sync. Also collects advisory focus-loss signals for the admin dashboard."""
    deadline = await resolve_deadline(attempt)
    if focus_lost:
        attempt.focus_loss_count += 1
        await db.flush()
        await cache.publish_live_update(str(attempt.exam_id))
    return HeartbeatOut(
        seconds_remaining=cache.seconds_until(deadline),
        status=attempt.status,
        server_time=datetime.now(UTC),
    )


# NOTE: there is deliberately no code-execution endpoint on this branch. Coding
# questions are submission-only: student code is persisted and evaluated by
# app.tasks.ai_tasks + a human admin, and is never compiled or run anywhere.


@router.post("/submit", response_model=SubmitReceipt)
async def submit_exam(
    attempt: Annotated[ExamAttempt, Depends(attempt_exam)],
    db: Annotated[AsyncSession, Depends(get_db)],
    auto: bool = False,
) -> SubmitReceipt:
    """Final submit. Flushes the answer buffer first so nothing in Redis is lost."""
    if attempt.status is not AttemptStatus.in_progress:
        # Idempotent: a double-click or a race with the sweeper returns the same receipt.
        return await _receipt(db, attempt)

    buffered = await cache.get_buffered_answers(str(attempt.id))
    if buffered:
        await answer_service.upsert_answers(db, attempt.id, buffered)
        await db.flush()

    attempt.status = AttemptStatus.auto_submitted if auto else AttemptStatus.submitted
    attempt.submitted_at = datetime.now(UTC)

    score, max_score, coding_pending = await grading.grade_objective(db, attempt)
    attempt.total_score = score
    attempt.max_score = max_score
    attempt.grading_complete = not coding_pending
    await db.flush()

    # Freeze the code before anything else can touch it. Returns only the rows
    # this call actually created, so a retried submit queues nothing twice.
    new_submissions = await coding.snapshot_submissions(db, attempt)

    db.add(
        AuditLog(
            actor_type="student",
            actor_id=attempt.student_id,
            action="exam_auto_submitted" if auto else "exam_submitted",
            target=str(attempt.exam_id),
        )
    )
    await cache.clear_attempt(str(attempt.id))
    await cache.publish_live_update(str(attempt.exam_id))

    receipt = await _receipt(db, attempt)

    if new_submissions:
        # Commit before enqueueing: the evaluation worker reads these rows over
        # its own connection, and get_db()'s commit doesn't happen until after
        # this function returns. Without this the worker can (and under low
        # latency reliably does) query before the rows are visible.
        await db.commit()

        # Local import avoids a circular import at module load.
        from app.tasks.ai_tasks import evaluate_coding_submission

        for submission_id in new_submissions:
            try:
                evaluate_coding_submission.delay(str(submission_id))
            except Exception:
                # A broker outage must never fail the student's submission. The
                # row is committed and sits at PENDING; an admin can re-run the
                # evaluation, or grade it by hand, from the review screen.
                log.exception("Could not queue AI evaluation for submission %s", submission_id)

    return receipt


async def _receipt(db: AsyncSession, attempt: ExamAttempt) -> SubmitReceipt:
    answered = await db.scalar(
        select(func.count(Answer.id)).where(
            Answer.attempt_id == attempt.id,
            (Answer.selected_option_id.isnot(None)) | (Answer.code_text.isnot(None)),
        )
    )
    total = await db.scalar(
        select(func.count(Question.id))
        .join(Section, Question.section_id == Section.id)
        .where(Section.exam_id == attempt.exam_id)
    )
    return SubmitReceipt(
        attempt_id=attempt.id,
        status=attempt.status,
        submitted_at=_aware(attempt.submitted_at or datetime.now(UTC)),
        answered_count=answered or 0,
        total_questions=total or 0,
        # Short, human-readable proof of submission the student can quote to an invigilator.
        receipt_code=hashlib.sha256(
            f"{attempt.id}:{attempt.submitted_at}".encode()
        ).hexdigest()[:12].upper(),
        grading_pending=not attempt.grading_complete,
    )


@router.get("/result", response_model=SubmitReceipt)
async def my_result(
    student: Annotated[Student, Depends(current_student)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SubmitReceipt:
    result = await db.execute(
        select(ExamAttempt)
        .where(
            ExamAttempt.student_id == student.id,
            ExamAttempt.status != AttemptStatus.in_progress,
        )
        .order_by(ExamAttempt.submitted_at.desc())
        .limit(1)
    )
    attempt = result.scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No submitted exam found")
    return await _receipt(db, attempt)
