"""Background jobs that keep the exam correct without the student's browser
having to cooperate: buffer flush and the auto-submit sweeper."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.cache import ANSWERS, DIRTY
from app.models import Answer, AttemptStatus, AuditLog, ExamAttempt, MCQOption, Question, QuestionType, Section
from app.sync_db import publish_live_update_sync, session_scope, sync_redis
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


def _upsert(session, attempt_id: uuid.UUID, answers: dict[str, dict]) -> int:
    rows = []
    for question_id, payload in answers.items():
        option_id = payload.get("selected_option_id")
        rows.append(
            {
                "id": uuid.uuid4(),
                "attempt_id": attempt_id,
                "question_id": uuid.UUID(str(question_id)),
                "selected_option_id": uuid.UUID(option_id) if option_id else None,
                "code_text": payload.get("code_text"),
                "language": payload.get("language"),
                "is_marked_for_review": bool(payload.get("is_marked_for_review", False)),
                "score": 0.0,
            }
        )
    if not rows:
        return 0
    stmt = insert(Answer).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_answer_attempt_question",
        set_={
            "selected_option_id": stmt.excluded.selected_option_id,
            "code_text": stmt.excluded.code_text,
            "language": stmt.excluded.language,
            "is_marked_for_review": stmt.excluded.is_marked_for_review,
        },
    )
    session.execute(stmt)
    return len(rows)


@celery_app.task(name="maintenance.flush_answers")
def flush_answers() -> dict[str, int]:
    """Drain the dirty set into Postgres. This is what turns ~40 write-rps of
    auto-save into a couple of bulk writes per second."""
    attempt_ids: list[str] = []
    # Pop atomically so a concurrent save that re-adds the id is not lost — worst case
    # we flush the same answers twice, which the upsert makes harmless.
    while True:
        attempt_id = sync_redis.spop(DIRTY)
        if attempt_id is None:
            break
        attempt_ids.append(attempt_id)
        if len(attempt_ids) >= 500:
            break

    if not attempt_ids:
        return {"attempts": 0, "answers": 0}

    written = 0
    exam_ids: set[uuid.UUID] = set()
    with session_scope() as session:
        for attempt_id in attempt_ids:
            try:
                raw = sync_redis.hgetall(ANSWERS.format(attempt_id=attempt_id))
                if not raw:
                    continue
                answers = {qid: json.loads(body) for qid, body in raw.items()}
                # A SAVEPOINT per attempt: one bad row (e.g. a stale attempt_id whose
                # exam_attempts row is gone) must not poison the whole 500-item batch's
                # transaction and roll back everyone else's answers along with it.
                with session.begin_nested():
                    written += _upsert(session, uuid.UUID(attempt_id), answers)
            except Exception:
                log.exception("flush failed for attempt %s; re-queueing", attempt_id)
                sync_redis.sadd(DIRTY, attempt_id)

        if written:
            exam_ids = set(
                session.execute(
                    select(ExamAttempt.exam_id).where(
                        ExamAttempt.id.in_([uuid.UUID(a) for a in attempt_ids])
                    )
                ).scalars()
            )

    # Published after the block above commits, so Live Monitor's rebuild reads
    # the answer counts this flush just wrote, not a stale pre-commit view.
    for exam_id in exam_ids:
        publish_live_update_sync(str(exam_id))

    return {"attempts": len(attempt_ids), "answers": written}


def _grade_objective_sync(session, attempt: ExamAttempt) -> tuple[float, float, bool]:
    questions = session.execute(
        select(Question, Section)
        .join(Section, Question.section_id == Section.id)
        .where(Section.exam_id == attempt.exam_id)
    ).all()

    answers = {
        a.question_id: a
        for a in session.execute(select(Answer).where(Answer.attempt_id == attempt.id)).scalars()
    }
    correct_options: dict[uuid.UUID, set[uuid.UUID]] = {}
    for question_id, option_id in session.execute(
        select(MCQOption.question_id, MCQOption.id)
        .join(Question, MCQOption.question_id == Question.id)
        .join(Section, Question.section_id == Section.id)
        .where(Section.exam_id == attempt.exam_id, MCQOption.is_correct.is_(True))
    ).all():
        correct_options.setdefault(question_id, set()).add(option_id)

    total = 0.0
    max_score = 0.0
    coding_pending = False

    for question, section in questions:
        marks = question.marks if question.marks is not None else section.marks_per_question
        negative = (
            question.negative_marks
            if question.negative_marks is not None
            else section.negative_marks
        )
        max_score += marks
        answer = answers.get(question.id)

        if question.type is QuestionType.coding:
            if answer is not None:
                total += answer.score
                if answer.is_correct is None and answer.code_text:
                    coding_pending = True
            continue

        if answer is None or answer.selected_option_id is None:
            continue
        is_correct = answer.selected_option_id in correct_options.get(question.id, set())
        answer.is_correct = is_correct
        answer.score = marks if is_correct else -negative
        total += answer.score

    return total, max_score, coding_pending


@celery_app.task(name="maintenance.auto_submit_expired")
def auto_submit_expired() -> dict[str, int]:
    """The safety net behind the client countdown. A student whose laptop dies at
    minute 59 still gets a graded submission."""
    now = datetime.now(UTC)
    submitted = 0
    needs_coding_grade: list[str] = []
    exam_ids: set[uuid.UUID] = set()

    with session_scope() as session:
        expired = session.execute(
            select(ExamAttempt)
            .where(
                ExamAttempt.status == AttemptStatus.in_progress,
                ExamAttempt.deadline_at <= now,
            )
            .limit(500)
            # Skip rows another worker is already finalizing.
            .with_for_update(skip_locked=True)
        ).scalars()

        for attempt in expired:
            raw = sync_redis.hgetall(ANSWERS.format(attempt_id=str(attempt.id)))
            if raw:
                _upsert(session, attempt.id, {qid: json.loads(b) for qid, b in raw.items()})
                session.flush()

            attempt.status = AttemptStatus.auto_submitted
            attempt.submitted_at = now
            score, max_score, pending = _grade_objective_sync(session, attempt)
            attempt.total_score = score
            attempt.max_score = max_score
            attempt.grading_complete = not pending
            session.add(
                AuditLog(
                    actor_type="system",
                    action="exam_auto_submitted_by_sweeper",
                    target=str(attempt.id),
                )
            )
            sync_redis.delete(
                ANSWERS.format(attempt_id=str(attempt.id)),
                f"attempt:{attempt.id}:deadline",
            )
            sync_redis.srem(DIRTY, str(attempt.id))
            submitted += 1
            exam_ids.add(attempt.exam_id)

            if pending:
                needs_coding_grade.append(str(attempt.id))

    for exam_id in exam_ids:
        publish_live_update_sync(str(exam_id))

    # Dispatched only after the `with` block commits: the judge worker reads
    # attempts over its own connection, and would otherwise race the commit above.
    if needs_coding_grade:
        from app.tasks.judge_tasks import grade_attempt_coding

        for attempt_id in needs_coding_grade:
            grade_attempt_coding.delay(attempt_id)

    if submitted:
        log.info("auto-submitted %s expired attempts", submitted)
    return {"submitted": submitted}
