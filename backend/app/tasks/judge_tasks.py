"""Code judging. Runs on the dedicated `judge` queue with bounded concurrency so a
burst of submissions queues instead of starving the host."""

from __future__ import annotations

import json
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
    ProblemType,
    Question,
    Section,
    TestCase,
)
from app.services import harness, sandbox
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
    """Returns (passed, total, weighted_score_fraction, per_case_results, error).
    Dispatches on problem_type — everything past this point is identical for both
    callers (judge_run's targeted/sample runs and grade_attempt_coding's final
    hidden-case grading), so neither needs to know which judge mode it's using."""
    if problem.problem_type is ProblemType.function:
        return _evaluate_function(problem, cases, language, code, reveal)
    return _evaluate_stdio(problem, cases, language, code, reveal)


def _effective_stdin(problem: CodingProblem, language: str, case: TestCase) -> str:
    """The script actually fed to the sandbox for one test case. For SQL, that's
    the question's shared schema/seed script plus this case's (usually empty)
    per-case addendum — schema no longer lives on the test case itself."""
    if language == "sql" and problem.sql_schema_sql:
        return f"{problem.sql_schema_sql}\n{case.stdin}" if case.stdin else problem.sql_schema_sql
    return case.stdin


def _evaluate_stdio(
    problem: CodingProblem, cases: list[TestCase], language: str, code: str, reveal: bool
) -> tuple[int, int, float, list[dict], str | None]:
    stdin_batch = [_effective_stdin(problem, language, c) for c in cases]
    results, compile_error = sandbox.execute(
        language=language,
        code=code,
        stdin_batch=stdin_batch,
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
        elif result.verdict == "mle":
            verdict = "mle"
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
                # Hidden case contents are never revealed, only the verdict. The
                # effective (schema-combined) script is stored here rather than
                # the raw case.stdin, which is usually blank for SQL.
                "stdin": stdin_batch[index] if reveal else None,
                "expected": case.expected_stdout if reveal else None,
                "actual": result.stdout if reveal else None,
                "stderr": result.stderr if reveal else None,
                "time_ms": result.time_ms,
                "verdict": verdict,
            }
        )

    return passed, len(cases), earned / total_weight, payload, None


def _evaluate_function(
    problem: CodingProblem, cases: list[TestCase], language: str, code: str, reveal: bool
) -> tuple[int, int, float, list[dict], str | None]:
    """Same shape/contract as _evaluate_stdio, but the "program" is the student's
    function wrapped in a generated driver, "stdin" is a JSON param blob, and
    grading is JSON-decode-and-deep-equal instead of a string compare."""
    program = harness.build_program(
        language, problem.function_name, problem.return_type, problem.parameters, code
    )
    results, compile_error = sandbox.execute(
        language=language,
        code=program,
        stdin_batch=[harness.encode_stdin(c.param_values or []) for c in cases],
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
        if result.verdict in ("tle", "mle", "runtime_error"):
            verdict = result.verdict
            ok = False
        else:
            matches, parse_error = harness.compare(problem.return_type, result.stdout, case.expected_value)
            if parse_error:
                verdict = "runtime_error"
                ok = False
            else:
                ok = matches
                verdict = "accepted" if ok else "wrong_answer"

        if ok:
            passed += 1
            earned += case.weight

        payload.append(
            {
                "index": index,
                "passed": ok,
                # Structured values are JSON-stringified so this payload keeps the
                # same str|None shape TestCaseResult already expects; the frontend
                # JSON.parses them back for display when it knows the problem is
                # function-mode.
                "stdin": json.dumps(case.param_values) if reveal else None,
                "expected": json.dumps(case.expected_value) if reveal else None,
                "actual": result.stdout if reveal else None,
                "stderr": result.stderr if reveal else None,
                "time_ms": result.time_ms,
                "verdict": verdict,
            }
        )

    return passed, len(cases), earned / total_weight, payload, None


def _run_custom(run: JudgeRun, problem: CodingProblem) -> dict:
    """Ad-hoc run against student-typed input. There's no expected output, so this
    never grades anything — just executes and reports what happened."""
    if problem.problem_type is ProblemType.function:
        program = harness.build_program(
            run.language, problem.function_name, problem.return_type, problem.parameters, run.code_text
        )
        stdin = harness.encode_stdin(run.custom_params or [])
    else:
        program = run.code_text
        stdin = run.custom_stdin or ""
        if run.language == "sql" and problem.sql_schema_sql:
            stdin = f"{problem.sql_schema_sql}\n{stdin}" if stdin else problem.sql_schema_sql

    results, compile_error = sandbox.execute(
        language=run.language,
        code=program,
        stdin_batch=[stdin],
        time_limit_ms=problem.time_limit_ms,
        memory_limit_mb=problem.memory_limit_mb,
    )
    run.passed, run.total = 0, 0
    if compile_error:
        run.results = []
        run.status = JudgeStatus.error
        run.error = compile_error
        return {"run_id": str(run.id), "passed": 0, "total": 0, "error": compile_error}

    result = results[0]
    run.results = [
        {
            "index": 0,
            "passed": False,  # no expected output to grade against
            "stdin": json.dumps(run.custom_params) if problem.problem_type is ProblemType.function else run.custom_stdin,
            "expected": None,
            "actual": result.stdout,
            "stderr": result.stderr,
            "time_ms": result.time_ms,
            "verdict": result.verdict,
        }
    ]
    run.status = JudgeStatus.done
    return {"run_id": str(run.id), "passed": 0, "total": 0, "error": None}


@celery_app.task(name="judge.preview_sql", bind=True, max_retries=0)
def preview_sql(self, setup_sql: str, query_sql: str) -> dict:
    """Admin-builder "run reference query" preview — no DB writes, no grading,
    just executes and reports what happened. Runs on the judge queue (routed via
    the "judge.*" task_routes entry in celery_app.py) rather than in the api
    process, since only the judge worker container has Docker socket access
    (see docker-compose.yml) — the api container intentionally does not."""
    results, compile_error = sandbox.execute(
        language="sql", code=query_sql, stdin_batch=[setup_sql], time_limit_ms=5000, memory_limit_mb=128
    )
    if compile_error:
        return {"stdout": "", "error": compile_error}
    result = results[0]
    if result.verdict != "ok":
        message = result.stderr.strip() or f"Query failed ({result.verdict})"
        return {"stdout": sandbox.normalize(result.stdout), "error": message}
    return {"stdout": sandbox.normalize(result.stdout), "error": None}


@celery_app.task(name="judge.run", bind=True, max_retries=1)
def judge_run(self, run_id: str) -> dict:
    """Student-triggered run: one selected sample case, ad-hoc custom input, or
    (when neither is specified) every sample case."""
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

        if run.mode is JudgeMode.custom:
            return _run_custom(run, problem)

        cases = _test_cases(session, problem.id, samples_only=run.mode is JudgeMode.sample)
        if run.test_case_id is not None:
            cases = [c for c in cases if c.id == run.test_case_id]
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

        if not rows:
            # This task is only ever dispatched when a pending coding answer was just
            # written on another connection. Finding none here almost always means that
            # write hasn't committed yet — retry instead of marking the attempt "done"
            # with whatever score it happens to have (i.e. never actually graded).
            raise self.retry(exc=RuntimeError(f"no gradable coding answers yet for {attempt_id}"))

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
