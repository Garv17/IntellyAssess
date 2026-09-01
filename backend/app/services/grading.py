"""Scoring. MCQ/DI grade inline at submit. Coding never grades itself: a coding
answer's score stays 0 until an admin finalizes its submission, which is the only
thing that writes a mark (see app.services.coding.apply_final_score)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging_config import get_logger
from app.models import (
    Answer,
    ExamAttempt,
    MCQOption,
    Question,
    QuestionType,
    Section,
)

log = get_logger(__name__)


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
    # Log-only counter. Grading is per-submission and a per-question line would
    # be thousands of lines a cohort, so the loop aggregates and logs once below.
    objective_graded = 0

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
            # Only an admin-finalized score is ever written to answer.score, so
            # whatever is there already counts. is_correct stays NULL until then,
            # which is what marks the attempt as still awaiting a coding mark.
            if answer is not None:
                total += answer.score
                # .strip(): must match what coding.snapshot_submissions actually
                # snapshots. A whitespace-only answer creates no submission, so
                # counting it as pending would pin the attempt at "grading
                # pending" with nothing in the queue to finalize.
                if answer.is_correct is None and (answer.code_text or "").strip():
                    coding_pending = True
            continue

        if answer is None or answer.selected_option_id is None:
            continue  # unattempted: no credit, no penalty

        correct_ids = {o.id for o in question.options if o.is_correct}
        is_correct = answer.selected_option_id in correct_ids
        answer.is_correct = is_correct
        answer.score = marks if is_correct else -negative
        total += answer.score
        objective_graded += 1

    log.info(
        "objective grading complete",
        extra={
            "attempt_id": str(attempt.id),
            "exam_id": str(attempt.exam_id),
            "questions_total": len(questions),
            "answers_saved": len(answers),
            "questions_graded": objective_graded,
            "score": total,
            "max_score": max_score,
            "coding_pending": coding_pending,
        },
    )
    return total, max_score, coding_pending


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
