"""Scoring. MCQ/DI grade inline at submit; coding is graded asynchronously
against the full hidden test-case set."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Answer,
    CodingProblem,
    ExamAttempt,
    MCQOption,
    Question,
    QuestionType,
    Section,
)


async def grade_objective(db: AsyncSession, attempt: ExamAttempt) -> tuple[float, float, bool]:
    """Grades MCQ + DI answers. Returns (score_so_far, max_score, coding_pending)."""
    exam_questions = await db.execute(
        select(Question)
        .join(Section, Question.section_id == Section.id)
        .where(Section.exam_id == attempt.exam_id)
        .options(selectinload(Question.section), selectinload(Question.options))
    )
    questions = list(exam_questions.scalars())

    answers_result = await db.execute(select(Answer).where(Answer.attempt_id == attempt.id))
    answers = {a.question_id: a for a in answers_result.scalars()}

    total = 0.0
    max_score = 0.0
    coding_pending = False

    for question in questions:
        marks = question.marks if question.marks is not None else question.section.marks_per_question
        negative = (
            question.negative_marks
            if question.negative_marks is not None
            else question.section.negative_marks
        )
        max_score += marks
        answer = answers.get(question.id)

        if question.type is QuestionType.coding:
            # Scored by the judge task; whatever it already wrote counts.
            if answer is not None:
                total += answer.score
                if answer.is_correct is None and answer.code_text:
                    coding_pending = True
            continue

        if answer is None or answer.selected_option_id is None:
            continue  # unattempted: no credit, no penalty

        correct_ids = {o.id for o in question.options if o.is_correct}
        is_correct = answer.selected_option_id in correct_ids
        answer.is_correct = is_correct
        answer.score = marks if is_correct else -negative
        total += answer.score

    return total, max_score, coding_pending


async def finalize_score(db: AsyncSession, attempt: ExamAttempt) -> None:
    """Recompute the attempt total from persisted answer scores."""
    result = await db.execute(select(Answer.score).where(Answer.attempt_id == attempt.id))
    attempt.total_score = float(sum(result.scalars()))

    pending = await db.execute(
        select(Answer.id)
        .join(Question, Answer.question_id == Question.id)
        .where(
            Answer.attempt_id == attempt.id,
            Question.type == QuestionType.coding,
            Answer.is_correct.is_(None),
            Answer.code_text.isnot(None),
        )
    )
    attempt.grading_complete = pending.first() is None


async def coding_submissions(
    db: AsyncSession, attempt_id: uuid.UUID
) -> list[tuple[Answer, CodingProblem]]:
    """Coding answers with actual code, paired with their problem definition."""
    result = await db.execute(
        select(Answer, CodingProblem)
        .join(Question, Answer.question_id == Question.id)
        .join(CodingProblem, CodingProblem.question_id == Question.id)
        .where(
            Answer.attempt_id == attempt_id,
            Question.type == QuestionType.coding,
            Answer.code_text.isnot(None),
        )
        .options(selectinload(CodingProblem.test_cases))
    )
    return [(answer, problem) for answer, problem in result.all()]


async def correct_option_map(db: AsyncSession, exam_id: uuid.UUID) -> dict[uuid.UUID, set[uuid.UUID]]:
    result = await db.execute(
        select(MCQOption.question_id, MCQOption.id)
        .join(Question, MCQOption.question_id == Question.id)
        .join(Section, Question.section_id == Section.id)
        .where(Section.exam_id == exam_id, MCQOption.is_correct.is_(True))
    )
    mapping: dict[uuid.UUID, set[uuid.UUID]] = {}
    for question_id, option_id in result.all():
        mapping.setdefault(question_id, set()).add(option_id)
    return mapping
