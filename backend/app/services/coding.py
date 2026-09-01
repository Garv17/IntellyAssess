"""Coding submissions: snapshotting at submit time, and folding finalized scores
back into the exam total.

The two rules this module exists to enforce:

* A submission is a frozen snapshot. Autosave keeps rewriting `Answer`; once a
  `CodingSubmission` row exists, it is what was submitted and what gets graded.
* Only a FINALIZED submission contributes marks. An AI recommendation is never
  a mark, so `Answer.score` for a coding question stays 0 until an admin
  finalizes.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_config import get_logger
from app.models import (
    Answer,
    CodingSubmission,
    EvaluationStatus,
    ExamAttempt,
    Question,
    QuestionType,
    Section,
)

log = get_logger(__name__)


async def snapshot_submissions(db: AsyncSession, attempt: ExamAttempt) -> list[uuid.UUID]:
    """Freeze every non-empty coding answer on this attempt into a
    CodingSubmission. Returns the ids of rows created *by this call* — i.e. the
    ones that still need an evaluation queued.

    Idempotent: the (attempt_id, question_id) unique constraint plus
    ON CONFLICT DO NOTHING means a retried submit re-inserts nothing and returns
    an empty list, so no duplicate evaluation is ever dispatched.
    """
    rows_result = await db.execute(
        select(Answer, Question, Section)
        .join(Question, Answer.question_id == Question.id)
        .join(Section, Question.section_id == Section.id)
        .where(
            Answer.attempt_id == attempt.id,
            Question.type == QuestionType.coding,
            Answer.code_text.isnot(None),
        )
    )

    values = []
    for answer, question, section in rows_result.all():
        code = (answer.code_text or "").strip()
        if not code:
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
        # Every submit of an MCQ-only exam lands here, so this is expected and
        # not a warning — but it is also the answer to "why was nothing queued
        # for evaluation", which is worth having in production.
        log.info("no coding answers to snapshot", extra={"attempt_id": str(attempt.id)})
        return []

    stmt = (
        insert(CodingSubmission)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_coding_submission_attempt_question")
        .returning(CodingSubmission.id)
    )
    result = await db.execute(stmt)
    created = list(result.scalars())
    # candidates > created means ON CONFLICT swallowed a re-submit: the gap is the
    # evidence that the idempotency guard worked rather than that snapshots were lost.
    log.info(
        "coding submissions snapshotted",
        extra={
            "attempt_id": str(attempt.id),
            "student_id": str(attempt.student_id),
            "candidates": len(values),
            "created": len(created),
        },
    )
    return created


async def has_unfinalized(db: AsyncSession, attempt_id: uuid.UUID) -> bool:
    """True while any coding submission on this attempt is still awaiting an
    admin's final score — which is what keeps the attempt's `grading_complete`
    false and its result marked pending."""
    pending = await db.execute(
        select(CodingSubmission.id).where(
            CodingSubmission.attempt_id == attempt_id,
            CodingSubmission.status != EvaluationStatus.finalized,
        )
    )
    return pending.first() is not None


async def apply_final_score(db: AsyncSession, submission: CodingSubmission) -> None:
    """Write a finalized submission's mark onto its Answer row, then recompute
    the attempt total. This is the only path by which a coding question ever
    earns marks."""
    answer = await db.scalar(
        select(Answer).where(
            Answer.attempt_id == submission.attempt_id,
            Answer.question_id == submission.question_id,
        )
    )
    if answer is None:
        # The Answer row is what carries the mark into the attempt total, so a
        # finalize with no Answer to write to silently awards nothing. Should be
        # impossible — the submission was snapshotted from that very row.
        log.error(
            "finalized coding submission has no answer row",
            extra={
                "submission_id": str(submission.id),
                "attempt_id": str(submission.attempt_id),
                "question_id": str(submission.question_id),
            },
        )
    if answer is not None:
        answer.score = float(submission.final_score or 0.0)
        # "Correct" for a partially-marked coding answer means full marks; it only
        # drives the correct/incorrect counters in the admin review UI.
        answer.is_correct = (
            submission.max_marks > 0 and answer.score >= submission.max_marks - 1e-6
        )
        # The one place a coding question ever earns marks — the audit trail for
        # "why does this student have this score" starts here.
        log.info(
            "coding final score applied",
            extra={
                "submission_id": str(submission.id),
                "attempt_id": str(submission.attempt_id),
                "question_id": str(submission.question_id),
                "score": answer.score,
                "max_marks": submission.max_marks,
            },
        )
    await db.flush()
    await recompute_attempt_total(db, submission.attempt_id)


async def recompute_attempt_total(db: AsyncSession, attempt_id: uuid.UUID) -> None:
    """Rebuild the attempt total from persisted answer scores, preserving the
    existing objective/MCQ scoring — those scores are already on their Answer
    rows, so summing is all that's needed."""
    attempt = await db.get(ExamAttempt, attempt_id)
    if attempt is None:
        # Swallowed by design, but it means a mark was just written against an
        # attempt that no longer exists — the total is now permanently stale.
        log.warning(
            "cannot recompute total; attempt not found",
            extra={"attempt_id": str(attempt_id)},
        )
        return
    scores = await db.execute(select(Answer.score).where(Answer.attempt_id == attempt_id))
    attempt.total_score = float(sum(scores.scalars()))
    attempt.grading_complete = not await has_unfinalized(db, attempt_id)
    await db.flush()
    log.info(
        "attempt total recomputed",
        extra={
            "attempt_id": str(attempt_id),
            "score": attempt.total_score,
            "max_score": attempt.max_score,
            "grading_complete": attempt.grading_complete,
        },
    )
