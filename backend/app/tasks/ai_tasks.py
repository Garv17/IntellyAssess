"""Asynchronous AI evaluation of coding submissions.

The contract this file exists to keep: **AI failure is never submission failure.**
The submission row is already committed before this task is dispatched, and every
path out of here — transport error, refusal, malformed response, missing rubric —
leaves that row intact and lands on AI_FAILED so an admin can grade it by hand.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.models import CodingProblem, CodingSubmission, EvaluationStatus, Question, TestCase
from app.services import ai_evaluator
from app.sync_db import session_scope
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)

# Hidden cases become "expected behaviour" context for the evaluator. Capped
# because the whole set can be large and the model only needs a sense of the
# intended contract, not an exhaustive listing.
MAX_REFERENCE_CASES = 8


def _reference_cases(session, problem: CodingProblem) -> list[dict]:
    cases = session.execute(
        select(TestCase)
        .where(TestCase.coding_problem_id == problem.id)
        .order_by(TestCase.is_sample.desc(), TestCase.order_index)
        .limit(MAX_REFERENCE_CASES)
    ).scalars()

    out: list[dict] = []
    for case in cases:
        if case.param_values is not None:
            out.append({"input": case.param_values, "expected_output": case.expected_value})
        else:
            out.append({"input": case.stdin, "expected_output": case.expected_stdout})
    return out


def _claim(session, submission_id: uuid.UUID) -> bool:
    """Atomically move pending/ai_failed -> ai_evaluating.

    This conditional UPDATE is the idempotency guard: two deliveries of the same
    submission_id race here and exactly one wins the row. Statuses past
    ai_evaluated (admin_reviewed, finalized) are deliberately not claimable — a
    duplicate job must never overwrite a decision a human already made."""
    result = session.execute(
        update(CodingSubmission)
        .where(
            CodingSubmission.id == submission_id,
            CodingSubmission.status.in_(
                [EvaluationStatus.pending, EvaluationStatus.ai_failed]
            ),
        )
        .values(status=EvaluationStatus.ai_evaluating)
    )
    return result.rowcount == 1


def _fail(session, submission_id: uuid.UUID, message: str) -> dict:
    """Records the failure only if we still hold the claim. An admin who graded
    the submission while the call was in flight has already produced a better
    answer than this task ever will — never move them back to ai_failed."""
    result = session.execute(
        update(CodingSubmission)
        .where(
            CodingSubmission.id == submission_id,
            CodingSubmission.status == EvaluationStatus.ai_evaluating,
        )
        .values(status=EvaluationStatus.ai_failed, ai_error=message[:2000])
    )
    log.warning("AI evaluation failed for submission %s: %s", submission_id, message)
    if result.rowcount != 1:
        return {"submission_id": str(submission_id), "status": "superseded"}
    return {"submission_id": str(submission_id), "status": EvaluationStatus.ai_failed.value}


@celery_app.task(name="ai.evaluate_coding_submission", bind=True, max_retries=0)
def evaluate_coding_submission(self, submission_id: str) -> dict:
    """Evaluate one submission. `submission_id` is the idempotency key.

    max_retries=0 on purpose: a failure here is recorded as AI_FAILED and is
    re-runnable on demand from the admin UI, which is more predictable for an
    examiner than a silent background retry storm against a paid API.
    """
    sid = uuid.UUID(submission_id)

    with session_scope() as session:
        if not _claim(session, sid):
            submission = session.get(CodingSubmission, sid)
            if submission is None:
                return {"submission_id": submission_id, "status": "not_found"}
            # Already in flight, or already past AI evaluation. Either way this
            # delivery has nothing to do.
            return {"submission_id": submission_id, "status": "skipped"}

    # Claimed. From here on, every exit must leave a terminal status behind.
    try:
        with session_scope() as session:
            submission = session.get(CodingSubmission, sid)
            if submission is None:
                return {"submission_id": submission_id, "status": "not_found"}

            question = session.get(Question, submission.question_id)
            problem = session.execute(
                select(CodingProblem).where(CodingProblem.question_id == submission.question_id)
            ).scalar_one_or_none()
            if question is None or problem is None:
                return _fail(session, sid, "Coding problem definition not found")

            evaluation = ai_evaluator.evaluate(
                question_md=question.body_md,
                statement_md=problem.statement_md,
                constraints_md=problem.constraints_md,
                language=submission.language,
                code_text=submission.code_text,
                reference_cases=_reference_cases(session, problem),
                # Only populated for SQL problems; the evaluator grades against
                # this schema instead of guessing at table and column names.
                sql_dialect=problem.sql_dialect,
                schema_sql=problem.sql_schema_sql,
            )

            # Conditional on still holding the claim. The admin UI actively
            # invites grading a submission while its evaluation is in flight, so
            # an unconditional write here would flip a row an admin had already
            # finalized back to ai_evaluated — leaving the mark applied on an
            # attempt whose submission reads as unfinalized.
            written = session.execute(
                update(CodingSubmission)
                .where(
                    CodingSubmission.id == sid,
                    CodingSubmission.status == EvaluationStatus.ai_evaluating,
                )
                .values(
                    ai_score=evaluation.recommended_score,
                    ai_rubric_scores=evaluation.rubric_scores,
                    ai_rubric_justifications=evaluation.rubric_justifications,
                    ai_reasoning=evaluation.reasoning,
                    ai_strengths=evaluation.strengths,
                    ai_issues=evaluation.issues,
                    ai_confidence=evaluation.confidence,
                    ai_requires_manual_review=evaluation.requires_manual_review,
                    ai_model=evaluation.model,
                    ai_rubric_version=evaluation.rubric_version,
                    ai_evaluated_at=datetime.now(UTC),
                    ai_error=None,
                    status=EvaluationStatus.ai_evaluated,
                )
            )
            if written.rowcount != 1:
                log.info("submission %s was graded while evaluating; discarding", submission_id)
                return {"submission_id": submission_id, "status": "superseded"}

            return {
                "submission_id": submission_id,
                "status": EvaluationStatus.ai_evaluated.value,
                "recommended_score": evaluation.recommended_score,
            }

    except ai_evaluator.AIEvaluationError as exc:
        with session_scope() as session:
            return _fail(session, sid, str(exc))
    except Exception as exc:  # noqa: BLE001 — the row must never be left mid-flight
        log.exception("Unexpected error evaluating submission %s", submission_id)
        with session_scope() as session:
            return _fail(session, sid, f"Unexpected evaluator error: {exc}")
