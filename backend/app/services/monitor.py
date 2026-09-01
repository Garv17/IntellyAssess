"""Live-monitor snapshot: shared by the REST poll fallback and the WebSocket push
path (backend/app/pubsub.py) so both compute the exact same view."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import Answer, AttemptStatus, Exam, ExamAttempt, Student
from app.schemas import LiveMonitorOut, LiveStudentRow

log = get_logger(__name__)


async def build_live_snapshot(db: AsyncSession, exam_id: uuid.UUID) -> LiveMonitorOut:
    started = time.monotonic()
    exam = await db.get(Exam, exam_id)
    if exam is None:
        log.warning("live snapshot failed", extra={"reason": "exam_not_found", "exam_id": str(exam_id)})
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")

    students_query = select(Student).order_by(Student.student_id)
    if exam.cohort:
        students_query = students_query.where(Student.cohort == exam.cohort)
    students_result = await db.execute(students_query)
    students = list(students_result.scalars())

    attempts_result = await db.execute(select(ExamAttempt).where(ExamAttempt.exam_id == exam_id))
    attempts = {a.student_id: a for a in attempts_result.scalars()}

    counts_result = await db.execute(
        select(Answer.attempt_id, func.count(Answer.id))
        .join(ExamAttempt, Answer.attempt_id == ExamAttempt.id)
        .where(
            ExamAttempt.exam_id == exam_id,
            (Answer.selected_option_id.isnot(None)) | (Answer.code_text.isnot(None)),
        )
        .group_by(Answer.attempt_id)
    )
    answered = dict(counts_result.all())

    now = datetime.now(UTC)
    rows: list[LiveStudentRow] = []
    not_started = in_progress = submitted = 0

    for student in students:
        attempt = attempts.get(student.id)
        if attempt is None:
            not_started += 1
            rows.append(
                LiveStudentRow(
                    student_id=student.student_id,
                    name=student.name,
                    attempt_status=None,
                    started_at=None,
                    seconds_remaining=None,
                    answered_count=0,
                    focus_loss_count=0,
                    submitted_at=None,
                )
            )
            continue

        if attempt.status is AttemptStatus.in_progress:
            in_progress += 1
            deadline = attempt.deadline_at
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            remaining = max(0, int((deadline - now).total_seconds()))
        else:
            submitted += 1
            remaining = None

        rows.append(
            LiveStudentRow(
                attempt_id=attempt.id,
                student_id=student.student_id,
                name=student.name,
                attempt_status=attempt.status,
                started_at=attempt.started_at,
                seconds_remaining=remaining,
                # Buffered answers aren't flushed yet, so this can lag by one flush interval.
                answered_count=answered.get(attempt.id, 0),
                focus_loss_count=attempt.focus_loss_count,
                submitted_at=attempt.submitted_at,
            )
        )

    # Runs on every monitor poll and every pub/sub push, so this is the
    # highest-volume line in the system — but it is also the only record of
    # snapshot latency, which is what "the Live Monitor is lagging" turns into.
    # If the volume becomes a problem, raise LOG_LEVEL rather than deleting it.
    log.info(
        "live snapshot built",
        extra={
            "exam_id": str(exam_id),
            "students": len(students),
            "not_started": not_started,
            "in_progress": in_progress,
            "submitted": submitted,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return LiveMonitorOut(
        exam_id=exam_id,
        title=exam.title,
        not_started=not_started,
        in_progress=in_progress,
        submitted=submitted,
        total_students=len(students),
        server_time=now,
        rows=rows,
    )
