"""Builds the exam paper a specific student sees.

Randomization is seeded per (attempt, exam) and *frozen* into
ExamAttempt.question_order at start time. Recomputing it per request would
reshuffle the paper on every refresh, which is the classic way this feature
breaks in production.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging_config import get_logger
from app.models import CodingProblem, DIGroup, Exam, Question, QuestionType, Section
from app.schemas import (
    CodingProblemOut,
    DIGroupOut,
    ExamPaperOut,
    OptionOut,
    QuestionOut,
    SectionOut,
    TestCaseOut,
)

log = get_logger(__name__)


async def load_exam_tree(db: AsyncSession, exam_id: uuid.UUID) -> Exam | None:
    result = await db.execute(
        select(Exam)
        .where(Exam.id == exam_id)
        .options(
            selectinload(Exam.sections).selectinload(Section.di_groups),
            selectinload(Exam.sections)
            .selectinload(Section.questions)
            .selectinload(Question.options),
            # test_cases must be eager too — the serializer filters samples out of it,
            # and a lazy load there would raise MissingGreenlet mid-request.
            selectinload(Exam.sections)
            .selectinload(Section.questions)
            .selectinload(Question.coding_problem)
            .selectinload(CodingProblem.test_cases),
            selectinload(Exam.sections)
            .selectinload(Section.di_groups)
            .selectinload(DIGroup.questions),
        )
    )
    exam = result.scalar_one_or_none()
    if exam is None:
        # Callers turn this into a 404. Logged because the id came from a token
        # or a route the caller already passed authorization for, so a miss is
        # more likely a deleted exam than a bad request.
        log.warning("exam tree not found", extra={"exam_id": str(exam_id)})
    return exam


def build_question_order(exam: Exam, seed: str) -> dict[str, Any]:
    """Freeze the per-student ordering. DI children stay grouped with their stimulus —
    shuffling them apart from the shared image would make the questions unanswerable."""
    rng = random.Random(seed)
    sections: list[dict[str, Any]] = []

    for section in sorted(exam.sections, key=lambda s: s.order_index):
        standalone = [q for q in section.questions if q.di_group_id is None]
        grouped: dict[uuid.UUID, list[Question]] = {}
        for q in section.questions:
            if q.di_group_id is not None:
                grouped.setdefault(q.di_group_id, []).append(q)

        standalone.sort(key=lambda q: q.order_index)
        blocks: list[dict[str, Any]] = [
            {"kind": "question", "question_id": str(q.id)} for q in standalone
        ]
        for group_id, questions in grouped.items():
            questions.sort(key=lambda q: q.order_index)
            blocks.append(
                {
                    "kind": "di_group",
                    "di_group_id": str(group_id),
                    "question_ids": [str(q.id) for q in questions],
                }
            )

        if exam.randomize_questions:
            rng.shuffle(blocks)

        option_order: dict[str, list[str]] = {}
        if exam.randomize_options:
            for q in section.questions:
                if q.options:
                    ids = [str(o.id) for o in sorted(q.options, key=lambda o: o.order_index)]
                    rng.shuffle(ids)
                    option_order[str(q.id)] = ids

        sections.append(
            {
                "id": str(section.id),
                "title": section.title,
                "blocks": blocks,
                "option_order": option_order,
            }
        )

    # Deliberately not logged here. This is a pure, deterministic function of
    # (exam, seed) with no I/O and no failure mode, its only caller already logs
    # the attempt start that this feeds, and it is called with plain fixtures in
    # tests — touching ORM attributes for a log line would be the only reason
    # this function needed a real Exam.
    return {"seed": seed, "sections": sections}


def _question_out(
    question: Question, section: Section, option_order: list[str] | None
) -> QuestionOut:
    # The owning section is passed in rather than read off question.section: that
    # back-reference is not eagerly loaded, and touching it here would emit a lazy
    # load inside the async request and raise MissingGreenlet.
    options = sorted(question.options, key=lambda o: o.order_index)
    if option_order:
        index = {str(o.id): o for o in options}
        options = [index[oid] for oid in option_order if oid in index]

    coding = None
    if question.type is QuestionType.coding and question.coding_problem:
        problem = question.coding_problem
        coding = CodingProblemOut(
            id=problem.id,
            statement_md=problem.statement_md,
            constraints_md=problem.constraints_md,
            allowed_languages=list(problem.allowed_languages),
            time_limit_ms=problem.time_limit_ms,
            memory_limit_mb=problem.memory_limit_mb,
            starter_code=dict(problem.starter_code or {}),
            problem_type=problem.problem_type,
            function_name=problem.function_name,
            return_type=problem.return_type,
            parameters=problem.parameters,
            sql_dialect=problem.sql_dialect,
            sql_schema_sql=problem.sql_schema_sql,
            sql_result_columns=problem.sql_result_columns,
            # Only sample cases cross the wire. Hidden cases stay server-side.
            sample_test_cases=[
                TestCaseOut(
                    id=tc.id,
                    stdin=tc.stdin,
                    expected_stdout=tc.expected_stdout,
                    param_values=tc.param_values,
                    expected_value=tc.expected_value,
                    explanation=tc.explanation,
                )
                for tc in sorted(problem.test_cases, key=lambda t: t.order_index)
                if tc.is_sample
            ],
        )

    return QuestionOut(
        id=question.id,
        type=question.type,
        body_md=question.body_md,
        marks=question.marks if question.marks is not None else section.marks_per_question,
        negative_marks=(
            question.negative_marks
            if question.negative_marks is not None
            else section.negative_marks
        ),
        di_group_id=question.di_group_id,
        options=[OptionOut(id=o.id, body=o.body) for o in options],
        coding_problem=coding,
    )


def render_paper(exam: Exam, question_order: dict[str, Any]) -> ExamPaperOut:
    """Materialize the frozen order into the student-facing payload."""
    questions_by_id = {
        str(q.id): q for section in exam.sections for q in section.questions
    }
    groups_by_id = {
        str(g.id): g for section in exam.sections for g in section.di_groups
    }
    sections_by_id = {str(s.id): s for s in exam.sections}

    # Log-only counters. The frozen order can name a section, question or DI
    # group that has since been deleted from the exam; each is skipped silently,
    # which quietly shortens a live paper. Counted here and reported once below
    # rather than per item, since one deletion drops one block per student.
    dropped_sections = 0
    dropped_questions = 0
    dropped_groups = 0

    out_sections: list[SectionOut] = []
    for idx, ordered in enumerate(question_order.get("sections", [])):
        section = sections_by_id.get(ordered["id"])
        if section is None:
            dropped_sections += 1
            continue
        option_order = ordered.get("option_order", {})

        out_questions: list[QuestionOut] = []
        used_groups: list[DIGroupOut] = []
        for block in ordered["blocks"]:
            if block["kind"] == "question":
                q = questions_by_id.get(block["question_id"])
                if q is None:
                    dropped_questions += 1
                if q is not None:
                    out_questions.append(
                        _question_out(q, section, option_order.get(block["question_id"]))
                    )
            else:
                group = groups_by_id.get(block["di_group_id"])
                if group is None:
                    dropped_groups += 1
                if group is not None:
                    used_groups.append(
                        DIGroupOut(
                            id=group.id,
                            title=group.title,
                            passage_md=group.passage_md,
                            image_url=group.image_url,
                        )
                    )
                for qid in block["question_ids"]:
                    q = questions_by_id.get(qid)
                    if q is None:
                        dropped_questions += 1
                    if q is not None:
                        out_questions.append(_question_out(q, section, option_order.get(qid)))

        out_sections.append(
            SectionOut(
                id=section.id,
                title=section.title,
                instructions=section.instructions,
                order_index=idx,
                questions=out_questions,
                di_groups=used_groups,
            )
        )

    if dropped_sections or dropped_questions or dropped_groups:
        log.warning(
            "frozen question order references missing content",
            extra={
                "exam_id": str(exam.id),
                "dropped_sections": dropped_sections,
                "dropped_questions": dropped_questions,
                "dropped_di_groups": dropped_groups,
            },
        )

    return ExamPaperOut(
        exam_id=exam.id,
        title=exam.title,
        instructions_md=exam.instructions_md,
        duration_minutes=exam.duration_minutes,
        sections=out_sections,
    )


def count_questions_and_marks(exam: Exam) -> tuple[int, float]:
    total = 0
    marks = 0.0
    for section in exam.sections:
        for question in section.questions:
            total += 1
            marks += question.marks if question.marks is not None else section.marks_per_question
    return total, marks
