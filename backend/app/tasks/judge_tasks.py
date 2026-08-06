"""Code judging. Runs on the dedicated `judge` queue with bounded concurrency so a
burst of submissions queues instead of starving the host."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select

from app.models import (
    Answer,
    CodingProblem,
    ExamAttempt,
    JudgeMode,
    JudgeRun,
    JudgeStatus,
    Question,
    Section,
    TestCase,
)
from app.services import sandbox
from app.sync_db import session_scope
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


def _load_problem(session, question_id: uuid.UUID) -> CodingProblem | None:
    return session.execute(
        select(CodingProblem).where(CodingProblem.question_id == question_id)
    ).scalar_one_or_none()


def _test_cases(session, problem_id: uuid.UUID, samples_only: bool) -> list[TestCase]:
    query = select(TestCase).where(TestCase.coding_problem_id == problem_id)
    if samples_only:
        query = query.where(TestCase.is_sample.is_(True))
    return list(session.execute(query.order_by(TestCase.order_index)).scalars())


def _evaluate(
    problem: CodingProblem, cases: list[TestCase], language: str, code: str, reveal: bool
) -> tuple[int, int, float, list[dict], str | None]:
    """Returns (passed, total, weighted_score_fraction, per_case_results, error)."""
    results, compile_error = sandbox.execute(
        language=language,
        code=code,
        stdin_batch=[c.stdin for c in cases],
        time_limit_ms=problem.time_limit_ms,
        memory_limit_mb=problem.memory_limit_mb,
    )
    if compile_error:
        return 0, len(cases), 0.0, [], compile_error

    passed = 0
    earned = 0
    total_weight = sum(c.weight for c in cases) or 1
    payload: list[dict] = []

    for index, (case, result) in enumerate(zip(cases, results, strict=False)):
        if result.verdict == "tle":
            verdict = "tle"
            ok = False
        elif result.verdict == "runtime_error":
            verdict = "runtime_error"
            ok = False
        else:
            ok = sandbox.normalize(result.stdout) == sandbox.normalize(case.expected_stdout)
            verdict = "accepted" if ok else "wrong_answer"

        if ok:
            passed += 1
            earned += case.weight

        payload.append(
            {
                "index": index,
                "passed": ok,
                # Hidden case contents are never revealed, only the verdict.
                "stdin": case.stdin if reveal else None,
                "expected": case.expected_stdout if reveal else None,
                "actual": result.stdout if reveal else None,
                "stderr": result.stderr if reveal else None,
                "time_ms": result.time_ms,
                "verdict": verdict,
            }
        )

    return passed, len(cases), earned / total_weight, payload, None


@celery_app.task(name="judge.run", bind=True, max_retries=1)
def judge_run(self, run_id: str) -> dict:
    """Student-triggered run against sample test cases only."""
    with session_scope() as session:
        run = session.get(JudgeRun, uuid.UUID(run_id))
        if run is None:
            return {"error": "run not found"}
        run.status = JudgeStatus.running
        session.flush()

        problem = _load_problem(session, run.question_id)
        if problem is None:
            run.status = JudgeStatus.error
            run.error = "Coding problem not found"
            return {"error": run.error}

        cases = _test_cases(session, problem.id, samples_only=run.mode is JudgeMode.sample)
        if not cases:
            run.status = JudgeStatus.error
            run.error = "No test cases available"
            return {"error": run.error}

        reveal = run.mode is JudgeMode.sample
        passed, total, fraction, payload, error = _evaluate(
            problem, cases, run.language, run.code_text, reveal=reveal
        )

        run.passed, run.total, run.results = passed, total, payload
        run.score = fraction
        if error:
            run.status = JudgeStatus.error
            run.error = error
        else:
            run.status = JudgeStatus.done

        return {"run_id": run_id, "passed": passed, "total": total, "error": error}


@celery_app.task(name="judge.grade_attempt", bind=True, max_retries=2, default_retry_delay=30)
def grade_attempt_coding(self, attempt_id: str) -> dict:
    """Final grading against the full hidden test-case set, run once at submission."""
    with session_scope() as session:
        attempt = session.get(ExamAttempt, uuid.UUID(attempt_id))
        if attempt is None:
            return {"error": "attempt not found"}

        rows = session.execute(
            select(Answer, CodingProblem, Question)
            .join(Question, Answer.question_id == Question.id)
            .join(CodingProblem, CodingProblem.question_id == Question.id)
            .join(Section, Question.section_id == Section.id)
            .where(Answer.attempt_id == attempt.id, Answer.code_text.isnot(None))
        ).all()

        graded = 0
        for answer, problem, question in rows:
            cases = _test_cases(session, problem.id, samples_only=False)
            if not cases:
                continue

            marks = (
                question.marks
                if question.marks is not None
                else session.get(Section, question.section_id).marks_per_question
            )
            # reveal=True: unlike the student-facing sample run, the persisted final
            # JudgeRun is only ever read by admins reviewing a submission, so hidden
            # case content is safe to store here.
            passed, total, fraction, payload, error = _evaluate(
                problem,
                cases,
                answer.language or "python",
                answer.code_text or "",
                reveal=True,
            )

            run = JudgeRun(
                attempt_id=attempt.id,
                question_id=question.id,
                language=answer.language or "python",
                code_text=answer.code_text or "",
                mode=JudgeMode.final,
                status=JudgeStatus.error if error else JudgeStatus.done,
                passed=passed,
                total=total,
                score=fraction,
                results=payload,
                error=error,
            )
            session.add(run)

            # Partial credit by test-case weight; a compile error scores zero but is
            # still recorded as graded so the attempt doesn't hang in "pending".
            answer.score = round(marks * fraction, 4)
            answer.is_correct = passed == total and total > 0
            graded += 1

        session.flush()
        total_score = sum(
            score
            for score in session.execute(
                select(Answer.score).where(Answer.attempt_id == attempt.id)
            ).scalars()
        )
        attempt.total_score = float(total_score)
        attempt.grading_complete = True

        return {"attempt_id": attempt_id, "graded": graded}
