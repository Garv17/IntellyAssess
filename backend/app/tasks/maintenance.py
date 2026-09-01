"""Background jobs that keep the exam correct without the student's browser
having to cooperate: buffer flush and the auto-submit sweeper."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.cache import ANSWERS, DIRTY
from app.logging_config import get_logger
from app.models import (
    Answer,
    AttemptStatus,
    AuditLog,
    CodingSubmission,
    EvaluationStatus,
    ExamAttempt,
    MCQOption,
    Question,
    QuestionType,
    Section,
)
from app.sync_db import publish_live_update_sync, session_scope, sync_redis
from app.tasks.celery_app import celery_app

log = get_logger(__name__)


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
        # Beat fires this every autosave_flush_seconds. Logging an idle run would
        # be ~4 lines a minute per worker saying nothing happened.
        return {"attempts": 0, "answers": 0}

    written = 0
    failed = 0
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
                failed += 1
                log.exception(
                    "answer flush failed for attempt; re-queueing",
                    extra={"attempt_id": attempt_id},
                )
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

    # DESIGN.md §8 calls flush lag the number that predicts an exam-day incident.
    # `attempts` hitting the 500 cap two runs in a row is what that looks like
    # here: the sweeper is no longer keeping up with the dirty set.
    log.info(
        "answer buffer flushed",
        extra={
            "attempts": len(attempt_ids),
            "answers_written": written,
            "failed_attempts": failed,
            "exams_notified": len(exam_ids),
            "batch_capped": len(attempt_ids) >= 500,
        },
    )

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
                # See grading.grade_objective: .strip() keeps this in step with
                # what _snapshot_submissions_sync below actually snapshots.
                if answer.is_correct is None and (answer.code_text or "").strip():
                    coding_pending = True
            continue

        if answer is None or answer.selected_option_id is None:
            continue
        is_correct = answer.selected_option_id in correct_options.get(question.id, set())
        answer.is_correct = is_correct
        answer.score = marks if is_correct else -negative
        total += answer.score

    return total, max_score, coding_pending


def _snapshot_submissions_sync(session, attempt: ExamAttempt) -> list[str]:
    """Sync mirror of app.services.coding.snapshot_submissions — freezes coding
    answers when the sweeper, rather than the student, ends the attempt. Same
    ON CONFLICT DO NOTHING idempotency: only rows created here come back, so a
    re-swept attempt never queues a second evaluation."""
    rows = session.execute(
        select(Answer, Question, Section)
        .join(Question, Answer.question_id == Question.id)
        .join(Section, Question.section_id == Section.id)
        .where(
            Answer.attempt_id == attempt.id,
            Question.type == QuestionType.coding,
            Answer.code_text.isnot(None),
        )
    ).all()

    values = []
    for answer, question, section in rows:
        if not (answer.code_text or "").strip():
            continue
        values.append(
            {
                "id": uuid.uuid4(),
                "attempt_id": attempt.id,
                "question_id": question.id,
                "student_id": attempt.student_id,
                "language": answer.language or "python",
                "code_text": answer.code_text,
                "max_marks": (
                    question.marks if question.marks is not None else section.marks_per_question
                ),
                "status": EvaluationStatus.pending,
            }
        )
    if not values:
        return []

    stmt = (
        insert(CodingSubmission)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_coding_submission_attempt_question")
        .returning(CodingSubmission.id)
    )
    return [str(sid) for sid in session.execute(stmt).scalars()]


@celery_app.task(name="maintenance.auto_submit_expired")
def auto_submit_expired() -> dict[str, int]:
    """The safety net behind the client countdown. A student whose laptop dies at
    minute 59 still gets a graded submission."""
    now = datetime.now(UTC)
    submitted = 0
    new_submission_ids: list[str] = []
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
            new_submission_ids += _snapshot_submissions_sync(session, attempt)
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

    for exam_id in exam_ids:
        publish_live_update_sync(str(exam_id))

    # Dispatched only after the `with` block commits: the evaluation worker reads
    # these rows over its own connection, and would otherwise race the commit
    # above. A broker failure here leaves the submissions saved at PENDING for an
    # admin to re-run — it must never undo an auto-submit.
    queued = 0
    if new_submission_ids:
        from app.tasks.ai_tasks import evaluate_coding_submission

        for submission_id in new_submission_ids:
            try:
                evaluate_coding_submission.delay(submission_id)
                queued += 1
            except Exception:
                log.exception(
                    "could not queue AI evaluation for submission",
                    extra={"submission_id": submission_id},
                )
        if queued != len(new_submission_ids):
            log.error(
                "some swept submissions were not queued for AI evaluation",
                extra={"queued": queued, "expected": len(new_submission_ids)},
            )

    if submitted:
        # Only when it actually did something — beat runs this every
        # autosubmit_sweep_seconds and it is idle for most of an exam.
        log.info(
            "auto-submitted expired attempts",
            extra={
                "submitted": submitted,
                "exams_affected": len(exam_ids),
                "coding_submissions_queued": queued,
            },
        )
    return {"submitted": submitted}
