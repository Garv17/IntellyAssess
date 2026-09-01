from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import secrets
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import jwt
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import cache
from app.config import settings
from app.db import get_db
from app.deps import current_admin, current_super_admin
from app.logging_config import get_logger, mask_email
from app.security import decode_token, hash_password
from app.services.monitor import build_live_snapshot
from app.ws_manager import live_connections
from app.models import (
    Admin,
    Answer,
    AttemptStatus,
    AuditLog,
    CodingProblem,
    CodingSubmission,
    DIGroup,
    EvaluationStatus,
    Exam,
    ExamAttempt,
    ExamInvite,
    ExamStatus,
    MCQOption,
    ProblemType,
    Question,
    QuestionType,
    Section,
    Student,
    TestCase,
)
from app.schemas import (
    ActivityEvent,
    ActivityTimeline,
    AdminCreate,
    AdminOut,
    AdminUpdate,
    AnalyticsOut,
    AttemptListItem,
    AttemptListPage,
    AttemptOverview,
    BoilerplatePreviewOut,
    BoilerplatePreviewRequest,
    BulkMagicLinkQueued,
    BulkMagicLinkRequest,
    BulkResult,
    CodingCreate,
    CodingGradeRequest,
    CodingProblemAdminOut,
    CodingReview,
    CodingSubmissionListItem,
    CodingSubmissionOut,
    CodingSubmissionPage,
    CodingUpdate,
    DIGroupAdminOut,
    DIGroupCreate,
    DIGroupUpdate,
    DIQuestionCreate,
    DiContext,
    ExamCreate,
    ExamOut,
    InviteCreateRequest,
    InviteQueued,
    LiveMonitorOut,
    LiveStudentRow,
    MagicLinkSent,
    MCQCreate,
    MCQUpdate,
    OptionAdminOut,
    OverviewStats,
    PublishResult,
    QuestionAdminOut,
    QuestionBankItem,
    QuestionBankPage,
    QuestionCopyRequest,
    QuestionDetailOut,
    QuestionReview,
    QuestionStat,
    SectionAdminOut,
    SectionCreate,
    SectionReorderRequest,
    SectionUpdate,
    RubricCriterionOut,
    SectionScore,
    StudentCreate,
    StudentOut,
    StudentUpdate,
    TestCaseAdminOut,
)
from app.security import create_magic_token
from app.services import coding as coding_service
from app.services import export, harness, paper
from app.services.email import send_magic_link_email
from app.services.rubric import get_rubric

router = APIRouter(prefix="/api/admin", tags=["admin"])

log = get_logger(__name__)

AdminDep = Annotated[Admin, Depends(current_admin)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


async def _get_exam(db: AsyncSession, exam_id: uuid.UUID) -> Exam:
    exam = await db.get(Exam, exam_id)
    if exam is None:
        # Logged in the lookup helper rather than at each call site: this is the
        # single place a missing exam turns into a 404, and every mutating
        # endpoint below funnels through it.
        log.warning("admin lookup failed", extra={"entity": "exam", "exam_id": str(exam_id)})
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    return exam


async def _get_section(db: AsyncSession, section_id: uuid.UUID) -> Section:
    section = await db.get(Section, section_id)
    if section is None:
        log.warning(
            "admin lookup failed", extra={"entity": "section", "section_id": str(section_id)}
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Section not found")
    return section


async def _assert_editable(db: AsyncSession, exam_id: uuid.UUID) -> Exam:
    """Content edits are blocked once an exam is live — changing a paper under students
    who have already started is never the intended outcome."""
    exam = await _get_exam(db, exam_id)
    if exam.status is ExamStatus.published:
        in_progress = await db.scalar(
            select(func.count(ExamAttempt.id)).where(ExamAttempt.exam_id == exam_id)
        )
        if in_progress:
            log.warning(
                "content edit blocked",
                extra={
                    "exam_id": str(exam_id),
                    "reason": "exam_published_with_attempts",
                    "attempt_count": in_progress,
                },
            )
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "Students have already started this exam; unpublish is not possible",
            )
    return exam


# ----------------------------------------------------------------- dashboard


@router.get("/overview-stats", response_model=OverviewStats)
async def overview_stats(admin: AdminDep, db: DbDep) -> OverviewStats:
    """Top-level counters for the admin dashboard landing page."""
    exams = await db.scalar(select(func.count(Exam.id))) or 0
    students = await db.scalar(select(func.count(Student.id))) or 0
    # "Completed" mirrors list_attempts: any terminal status, i.e. not still in progress.
    attempts = (
        await db.scalar(
            select(func.count(ExamAttempt.id)).where(
                ExamAttempt.status != AttemptStatus.in_progress
            )
        )
        or 0
    )

    scores_result = await db.execute(
        select(ExamAttempt.total_score, ExamAttempt.max_score).where(
            ExamAttempt.status != AttemptStatus.in_progress,
            ExamAttempt.total_score.isnot(None),
            ExamAttempt.max_score.isnot(None),
            ExamAttempt.max_score > 0,
        )
    )
    percentages = [round(total / max_ * 100, 2) for total, max_ in scores_result.all()]
    average_score = round(statistics.fmean(percentages), 2) if percentages else None

    return OverviewStats(
        exams=exams, students=students, attempts=attempts, average_score=average_score
    )


# ------------------------------------------------------------------- exams


@router.post("/exams", response_model=ExamOut, status_code=status.HTTP_201_CREATED)
async def create_exam(payload: ExamCreate, admin: AdminDep, db: DbDep) -> ExamOut:
    exam = Exam(**payload.model_dump(), created_by=admin.id)
    db.add(exam)
    await db.flush()
    # Exam CRUD writes no AuditLog row, so this line is the only trace that the
    # paper came into existence and who made it.
    log.info(
        "exam created",
        extra={"exam_id": str(exam.id), "admin_id": str(admin.id), "cohort": exam.cohort},
    )
    return ExamOut.model_validate(exam)


@router.get("/exams", response_model=list[ExamOut])
async def list_exams(admin: AdminDep, db: DbDep) -> list[ExamOut]:
    result = await db.execute(select(Exam).order_by(Exam.created_at.desc()))
    return [ExamOut.model_validate(e) for e in result.scalars()]


@router.get("/exams/{exam_id}", response_model=ExamOut)
async def get_exam(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> ExamOut:
    return ExamOut.model_validate(await _get_exam(db, exam_id))


@router.patch("/exams/{exam_id}", response_model=ExamOut)
async def update_exam(
    exam_id: uuid.UUID, payload: ExamCreate, admin: AdminDep, db: DbDep
) -> ExamOut:
    exam = await _assert_editable(db, exam_id)
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(exam, field, value)
    await db.flush()
    # Only the names of the changed fields: the values can include the SEB Config
    # Key, which must never reach a log sink.
    log.info(
        "exam updated",
        extra={
            "exam_id": str(exam_id),
            "admin_id": str(admin.id),
            "updated_fields": sorted(updates),
        },
    )
    return ExamOut.model_validate(exam)


@router.delete("/exams/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exam(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> None:
    exam = await _get_exam(db, exam_id)
    attempts = await db.scalar(
        select(func.count(ExamAttempt.id)).where(ExamAttempt.exam_id == exam_id)
    )
    if attempts:
        log.warning(
            "exam delete blocked",
            extra={"exam_id": str(exam_id), "reason": "has_attempts", "attempt_count": attempts},
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Exam has attempts and cannot be deleted; close it instead"
        )
    # Read the status before the delete: the instance is expired once the unit of
    # work flushes, and touching it afterwards would emit a lazy refresh.
    previous_status = exam.status.value
    await db.delete(exam)
    # Destructive and unaudited — the cascade takes every section, question and
    # option with it, so this line is the only record the paper ever existed.
    log.info(
        "exam deleted",
        extra={
            "exam_id": str(exam_id),
            "admin_id": str(admin.id),
            "previous_status": previous_status,
        },
    )


# ---------------------------------------------------------------- sections


@router.post(
    "/exams/{exam_id}/sections", response_model=SectionAdminOut, status_code=status.HTTP_201_CREATED
)
async def create_section(
    exam_id: uuid.UUID, payload: SectionCreate, admin: AdminDep, db: DbDep
) -> SectionAdminOut:
    await _assert_editable(db, exam_id)
    section = Section(exam_id=exam_id, **payload.model_dump())
    db.add(section)
    await db.flush()
    log.info(
        "section created",
        extra={
            "exam_id": str(exam_id),
            "section_id": str(section.id),
            "admin_id": str(admin.id),
        },
    )
    return SectionAdminOut.model_validate(section)


@router.get("/exams/{exam_id}/sections", response_model=list[SectionAdminOut])
async def list_sections(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> list[SectionAdminOut]:
    result = await db.execute(
        select(Section, func.count(Question.id))
        .outerjoin(Question, Question.section_id == Section.id)
        .where(Section.exam_id == exam_id)
        .group_by(Section.id)
        .order_by(Section.order_index)
    )
    out = []
    for section, count in result.all():
        item = SectionAdminOut.model_validate(section)
        item.question_count = count
        out.append(item)
    return out


@router.patch("/sections/{section_id}", response_model=SectionAdminOut)
async def update_section(
    section_id: uuid.UUID, payload: SectionUpdate, admin: AdminDep, db: DbDep
) -> SectionAdminOut:
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(section, field, value)
    await db.flush()
    log.info(
        "section updated",
        extra={
            "section_id": str(section_id),
            "exam_id": str(section.exam_id),
            "admin_id": str(admin.id),
            "updated_fields": sorted(updates),
        },
    )
    count = await db.scalar(
        select(func.count(Question.id)).where(Question.section_id == section_id)
    )
    out = SectionAdminOut.model_validate(section)
    out.question_count = count or 0
    return out


@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_section(section_id: uuid.UUID, admin: AdminDep, db: DbDep) -> None:
    """Cascades to every question and DI group in the section — the same
    all-or-nothing tradeoff `delete_exam` makes, gated by the same
    _assert_editable guard so a live exam's paper can't be pulled out from
    under a student mid-attempt."""
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    exam_id = section.exam_id
    await db.delete(section)
    log.info(
        "section deleted",
        extra={
            "section_id": str(section_id),
            "exam_id": str(exam_id),
            "admin_id": str(admin.id),
        },
    )


# --------------------------------------------------------------- questions


def _question_detail(question: Question) -> QuestionDetailOut:
    """Admin-facing view of a question — unlike the student paper, this exposes
    is_correct and every (including hidden) test case, because editing requires seeing
    them."""
    coding = None
    if question.coding_problem:
        problem = question.coding_problem
        coding = CodingProblemAdminOut(
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
            test_cases=[
                TestCaseAdminOut(
                    id=tc.id,
                    stdin=tc.stdin,
                    expected_stdout=tc.expected_stdout,
                    param_values=tc.param_values,
                    expected_value=tc.expected_value,
                    explanation=tc.explanation,
                    is_sample=tc.is_sample,
                    weight=tc.weight,
                    order_index=tc.order_index,
                )
                for tc in sorted(problem.test_cases, key=lambda t: t.order_index)
            ],
        )

    return QuestionDetailOut(
        id=question.id,
        section_id=question.section_id,
        type=question.type,
        body_md=question.body_md,
        explanation_md=question.explanation_md,
        marks=question.marks,
        negative_marks=question.negative_marks,
        order_index=question.order_index,
        di_group_id=question.di_group_id,
        options=[
            OptionAdminOut(id=o.id, body=o.body, is_correct=o.is_correct, order_index=o.order_index)
            for o in sorted(question.options, key=lambda o: o.order_index)
        ],
        coding_problem=coding,
        tags=question.meta.get("tags", []) if question.meta else [],
        difficulty=(question.meta or {}).get("difficulty"),
    )


async def _get_question_detailed(db: AsyncSession, question_id: uuid.UUID) -> Question:
    result = await db.execute(
        select(Question)
        .where(Question.id == question_id)
        .options(
            selectinload(Question.options),
            selectinload(Question.coding_problem).selectinload(CodingProblem.test_cases),
        )
    )
    question = result.scalar_one_or_none()
    if question is None:
        log.warning(
            "admin lookup failed", extra={"entity": "question", "question_id": str(question_id)}
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return question


@router.get("/sections/{section_id}/questions", response_model=list[QuestionDetailOut])
async def list_section_questions(
    section_id: uuid.UUID, admin: AdminDep, db: DbDep
) -> list[QuestionDetailOut]:
    """Standalone questions (MCQ/coding) for the edit view. DI questions travel with
    their group — see /sections/{id}/di-groups."""
    await _get_section(db, section_id)
    result = await db.execute(
        select(Question)
        .where(Question.section_id == section_id, Question.di_group_id.is_(None))
        .options(
            selectinload(Question.options),
            selectinload(Question.coding_problem).selectinload(CodingProblem.test_cases),
        )
        .order_by(Question.order_index)
    )
    return [_question_detail(q) for q in result.scalars()]


@router.get("/sections/{section_id}/di-groups", response_model=list[DIGroupAdminOut])
async def list_section_di_groups(
    section_id: uuid.UUID, admin: AdminDep, db: DbDep
) -> list[DIGroupAdminOut]:
    await _get_section(db, section_id)
    result = await db.execute(
        select(DIGroup)
        .where(DIGroup.section_id == section_id)
        .options(
            selectinload(DIGroup.questions).selectinload(Question.options),
            selectinload(DIGroup.questions)
            .selectinload(Question.coding_problem)
            .selectinload(CodingProblem.test_cases),
        )
        # created_at breaks ties between groups that share an order_index (e.g.
        # several created before order_index was assigned incrementally) so the
        # list renders in a stable order across reloads instead of shuffling.
        .order_by(DIGroup.order_index, DIGroup.created_at)
    )
    return [
        DIGroupAdminOut(
            id=group.id,
            section_id=group.section_id,
            title=group.title,
            passage_md=group.passage_md,
            image_url=group.image_url,
            order_index=group.order_index,
            questions=[
                _question_detail(q) for q in sorted(group.questions, key=lambda q: q.order_index)
            ],
        )
        for group in result.scalars()
    ]


@router.patch("/sections/{section_id}/reorder")
async def reorder_section(
    section_id: uuid.UUID, payload: SectionReorderRequest, admin: AdminDep, db: DbDep
) -> dict[str, str]:
    """Reorders standalone questions and DI groups within a section. A DI group and its
    children always move together as one contiguous block; the children keep their
    existing relative order (only the block's position changes)."""
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    standalone_result = await db.execute(
        select(Question).where(
            Question.section_id == section_id, Question.di_group_id.is_(None)
        )
    )
    standalone_by_id = {q.id: q for q in standalone_result.scalars()}

    groups_result = await db.execute(
        select(DIGroup)
        .where(DIGroup.section_id == section_id)
        .options(selectinload(DIGroup.questions))
    )
    groups_by_id = {g.id: g for g in groups_result.scalars()}

    expected = {("question", qid) for qid in standalone_by_id} | {
        ("di_group", gid) for gid in groups_by_id
    }
    received = {(item.kind, item.id) for item in payload.items}
    if received != expected:
        missing = expected - received
        extra = received - expected
        parts = []
        if missing:
            parts.append("missing " + ", ".join(f"{k}:{i}" for k, i in sorted(missing, key=str)))
        if extra:
            parts.append("unknown " + ", ".join(f"{k}:{i}" for k, i in sorted(extra, key=str)))
        log.warning(
            "section reorder rejected",
            extra={
                "section_id": str(section_id),
                "reason": "not_a_permutation",
                "missing_count": len(missing),
                "unknown_count": len(extra),
            },
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Reorder payload must be exactly a permutation of this section's questions "
            "and DI groups (" + "; ".join(parts) + ")",
        )

    idx = 0
    for item in payload.items:
        if item.kind == "question":
            standalone_by_id[item.id].order_index = idx
            idx += 1
        else:
            group = groups_by_id[item.id]
            group.order_index = idx
            for child in sorted(group.questions, key=lambda q: q.order_index):
                child.order_index = idx
                idx += 1

    await db.flush()
    log.info(
        "section reordered",
        extra={
            "section_id": str(section_id),
            "exam_id": str(section.exam_id),
            "admin_id": str(admin.id),
            "item_count": len(payload.items),
        },
    )
    return {"status": "ok"}


# Matches a Markdown image tag: ![alt text](url). Question bodies embed images
# this way (see MarkdownField's "Insert image" button on the frontend); a plain
# character-count slice for a preview can land mid-tag (e.g. cut right before the
# closing paren) and leave broken, half-rendered markdown behind.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _body_preview(body_md: str, limit: int = 140) -> str:
    """Short plain-text preview of a question body. Images collapse to a
    "[image]" placeholder before truncating, rather than truncating through raw
    markdown syntax, which would otherwise show a mangled `![alt](/uploads/f`
    with no closing paren."""
    text = _MD_IMAGE.sub("[image]", body_md)
    return f"{text[:limit]}…" if len(text) > limit else text


@router.get("/question-bank", response_model=QuestionBankPage)
async def question_bank(
    admin: AdminDep,
    db: DbDep,
    search: str | None = None,
    type: QuestionType | None = None,
    difficulty: str | None = None,
    exam_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = 20,
) -> QuestionBankPage:
    """Cross-exam search over standalone questions, so admins can reuse existing content
    instead of retyping it. DI questions are excluded — they only make sense alongside
    their stimulus, and copying a whole DI set is out of scope here."""
    query = (
        select(Question, Section, Exam)
        .join(Section, Question.section_id == Section.id)
        .join(Exam, Section.exam_id == Exam.id)
        .where(Question.di_group_id.is_(None))
    )
    if search:
        query = query.where(Question.body_md.ilike(f"%{search}%"))
    if type is not None:
        query = query.where(Question.type == type)
    if exam_id is not None:
        query = query.where(Exam.id == exam_id)
    if section_id is not None:
        query = query.where(Section.id == section_id)
    query = query.order_by(Exam.created_at.desc(), Section.order_index, Question.order_index)

    result = await db.execute(query)
    rows = result.all()

    # Filtering on meta['difficulty'] in Python rather than via a JSONB path operator:
    # this is an admin tool over a bounded result set, not a hot path, so correctness
    # and simplicity win over a fancier query.
    if difficulty:
        rows = [(q, s, e) for q, s, e in rows if (q.meta or {}).get("difficulty") == difficulty]

    total = len(rows)
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]

    items = [
        QuestionBankItem(
            question_id=q.id,
            exam_id=e.id,
            exam_title=e.title,
            section_id=s.id,
            section_title=s.title,
            type=q.type,
            body_preview=_body_preview(q.body_md),
            marks=q.marks,
            tags=(q.meta or {}).get("tags", []),
            difficulty=(q.meta or {}).get("difficulty"),
        )
        for q, s, e in page_rows
    ]
    return QuestionBankPage(items=items, total=total)


@router.post(
    "/questions/{question_id}/copy",
    response_model=QuestionAdminOut,
    status_code=status.HTTP_201_CREATED,
)
async def copy_question(
    question_id: uuid.UUID, payload: QuestionCopyRequest, admin: AdminDep, db: DbDep
) -> QuestionAdminOut:
    """Duplicates a standalone MCQ or coding question (with its options/test cases) into
    another section. DI-set questions must be copied as part of their whole set, which is
    out of scope here."""
    question = await _get_question_detailed(db, question_id)
    if question.di_group_id is not None:
        log.warning(
            "question copy refused",
            extra={"question_id": str(question_id), "reason": "belongs_to_di_group"},
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Cannot copy a question that belongs to a DI set directly — copy the whole "
            "set instead",
        )

    target_section = await _get_section(db, payload.target_section_id)
    await _assert_editable(db, target_section.exam_id)

    next_index = await db.scalar(
        select(func.coalesce(func.max(Question.order_index), -1)).where(
            Question.section_id == target_section.id
        )
    )
    new_question = Question(
        section_id=target_section.id,
        type=question.type,
        body_md=question.body_md,
        explanation_md=question.explanation_md,
        marks=question.marks,
        negative_marks=question.negative_marks,
        order_index=next_index + 1,
        meta=dict(question.meta or {}),
    )
    db.add(new_question)
    await db.flush()

    if question.type in (QuestionType.mcq, QuestionType.di):
        for option in question.options:
            db.add(
                MCQOption(
                    question_id=new_question.id,
                    body=option.body,
                    is_correct=option.is_correct,
                    order_index=option.order_index,
                )
            )
    elif question.type is QuestionType.coding and question.coding_problem:
        source_problem = question.coding_problem
        new_problem = CodingProblem(
            question_id=new_question.id,
            statement_md=source_problem.statement_md,
            constraints_md=source_problem.constraints_md,
            allowed_languages=list(source_problem.allowed_languages),
            time_limit_ms=source_problem.time_limit_ms,
            memory_limit_mb=source_problem.memory_limit_mb,
            starter_code=dict(source_problem.starter_code or {}),
            problem_type=source_problem.problem_type,
            function_name=source_problem.function_name,
            return_type=source_problem.return_type,
            parameters=list(source_problem.parameters) if source_problem.parameters else None,
            sql_dialect=source_problem.sql_dialect,
            sql_schema_sql=source_problem.sql_schema_sql,
            sql_result_columns=list(source_problem.sql_result_columns) if source_problem.sql_result_columns else None,
        )
        db.add(new_problem)
        await db.flush()
        for tc in source_problem.test_cases:
            db.add(
                TestCase(
                    coding_problem_id=new_problem.id,
                    stdin=tc.stdin,
                    expected_stdout=tc.expected_stdout,
                    param_values=list(tc.param_values) if tc.param_values else None,
                    expected_value=tc.expected_value,
                    explanation=tc.explanation,
                    is_sample=tc.is_sample,
                    weight=tc.weight,
                    order_index=tc.order_index,
                )
            )

    await db.flush()
    log.info(
        "question copied",
        extra={
            "source_question_id": str(question_id),
            "question_id": str(new_question.id),
            "target_section_id": str(target_section.id),
            "question_type": question.type.value,
            "admin_id": str(admin.id),
        },
    )
    out = QuestionAdminOut.model_validate(new_question)
    out.tags = new_question.meta.get("tags", []) if new_question.meta else []
    out.difficulty = (new_question.meta or {}).get("difficulty")
    return out


@router.post(
    "/sections/{section_id}/questions",
    response_model=QuestionAdminOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_mcq(
    section_id: uuid.UUID, payload: MCQCreate, admin: AdminDep, db: DbDep
) -> QuestionAdminOut:
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    correct = [o for o in payload.options if o.is_correct]
    if len(correct) != 1:
        log.warning(
            "mcq create rejected",
            extra={
                "section_id": str(section_id),
                "reason": "correct_option_count",
                "correct_count": len(correct),
            },
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "An MCQ must have exactly one correct option"
        )

    question = Question(
        section_id=section_id,
        type=QuestionType.mcq,
        body_md=payload.body_md,
        explanation_md=payload.explanation_md,
        marks=payload.marks,
        negative_marks=payload.negative_marks,
        order_index=payload.order_index,
        meta={"tags": payload.tags, "difficulty": payload.difficulty},
    )
    db.add(question)
    await db.flush()
    for idx, option in enumerate(payload.options):
        db.add(
            MCQOption(
                question_id=question.id,
                body=option.body,
                is_correct=option.is_correct,
                order_index=option.order_index or idx,
            )
        )
    await db.flush()
    # Question bodies and option text are exam content and never logged; the ids
    # are enough to find the row.
    log.info(
        "question created",
        extra={
            "question_id": str(question.id),
            "section_id": str(section_id),
            "question_type": QuestionType.mcq.value,
            "option_count": len(payload.options),
            "admin_id": str(admin.id),
        },
    )
    out = QuestionAdminOut.model_validate(question)
    out.tags = payload.tags
    out.difficulty = payload.difficulty
    return out


@router.post(
    "/sections/{section_id}/questions/bulk", response_model=BulkResult
)
async def bulk_upload_mcq(
    section_id: uuid.UUID,
    admin: AdminDep,
    db: DbDep,
    file: Annotated[UploadFile, File()],
) -> BulkResult:
    """CSV columns: body_md, option_a, option_b, option_c, option_d, correct (A-D),
    marks (optional), negative_marks (optional), explanation (optional)."""
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    text = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    created, skipped, errors = 0, 0, []
    letters = {"A": "option_a", "B": "option_b", "C": "option_c", "D": "option_d"}

    base_index = await db.scalar(
        select(func.coalesce(func.max(Question.order_index), -1)).where(
            Question.section_id == section_id
        )
    )

    for line_no, row in enumerate(reader, start=2):
        body = (row.get("body_md") or "").strip()
        correct = (row.get("correct") or "").strip().upper()
        if not body:
            skipped += 1
            errors.append(f"Row {line_no}: missing body_md")
            continue
        if correct not in letters:
            skipped += 1
            errors.append(f"Row {line_no}: 'correct' must be one of A, B, C, D")
            continue
        options = [(letter, (row.get(col) or "").strip()) for letter, col in letters.items()]
        if any(not text_ for _, text_ in options):
            skipped += 1
            errors.append(f"Row {line_no}: all four options are required")
            continue

        base_index += 1
        question = Question(
            section_id=section_id,
            type=QuestionType.mcq,
            body_md=body,
            explanation_md=(row.get("explanation") or "").strip() or None,
            marks=float(row["marks"]) if row.get("marks") else None,
            negative_marks=float(row["negative_marks"]) if row.get("negative_marks") else None,
            order_index=base_index,
        )
        db.add(question)
        await db.flush()
        for idx, (letter, text_) in enumerate(options):
            db.add(
                MCQOption(
                    question_id=question.id,
                    body=text_,
                    is_correct=letter == correct,
                    order_index=idx,
                )
            )
        created += 1

    await db.flush()
    # Row-level error text quotes CSV content, so only the counts are logged.
    log.info(
        "mcq bulk upload completed",
        extra={
            "section_id": str(section_id),
            "created_count": created,
            "skipped": skipped,
            "admin_id": str(admin.id),
        },
    )
    return BulkResult(created=created, skipped=skipped, errors=errors[:50])


@router.post("/sections/{section_id}/di-groups", status_code=status.HTTP_201_CREATED)
async def create_di_group(
    section_id: uuid.UUID, payload: DIGroupCreate, admin: AdminDep, db: DbDep
) -> dict:
    """A DI stimulus plus its child questions, created together so a group can never
    exist without questions."""
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    for q in payload.questions:
        if len([o for o in q.options if o.is_correct]) != 1:
            log.warning(
                "di group create rejected",
                extra={"section_id": str(section_id), "reason": "correct_option_count"},
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Each DI question must have exactly one correct option",
            )

    # Always append at the end of the section's block order (standalone questions
    # and DI groups share one ordering space — see reorder_section) rather than
    # trusting the client's order_index, which the exam builder never actually
    # sets. Every group otherwise defaults to 0 and ties sort unstably, which
    # reads as a previously-visible group/question randomly disappearing on
    # reload — see list_section_di_groups' created_at tiebreaker for the same
    # concern on the read side.
    max_question_index = await db.scalar(
        select(func.coalesce(func.max(Question.order_index), -1)).where(
            Question.section_id == section_id, Question.di_group_id.is_(None)
        )
    )
    max_group_index = await db.scalar(
        select(func.coalesce(func.max(DIGroup.order_index), -1)).where(
            DIGroup.section_id == section_id
        )
    )
    next_index = max(max_question_index, max_group_index) + 1

    group = DIGroup(
        section_id=section_id,
        title=payload.title,
        passage_md=payload.passage_md,
        image_url=payload.image_url,
        order_index=next_index,
    )
    db.add(group)
    await db.flush()

    question_ids = []
    for idx, q in enumerate(payload.questions):
        question = Question(
            section_id=section_id,
            di_group_id=group.id,
            type=QuestionType.di,
            body_md=q.body_md,
            explanation_md=q.explanation_md,
            marks=q.marks,
            order_index=q.order_index or idx,
            meta={"tags": q.tags, "difficulty": q.difficulty},
        )
        db.add(question)
        await db.flush()
        for oidx, option in enumerate(q.options):
            db.add(
                MCQOption(
                    question_id=question.id,
                    body=option.body,
                    is_correct=option.is_correct,
                    order_index=option.order_index or oidx,
                )
            )
        question_ids.append(str(question.id))

    log.info(
        "di group created",
        extra={
            "di_group_id": str(group.id),
            "section_id": str(section_id),
            "question_count": len(question_ids),
            "admin_id": str(admin.id),
        },
    )
    return {"di_group_id": str(group.id), "question_ids": question_ids}


@router.post(
    "/di-groups/{group_id}/questions",
    response_model=DIGroupAdminOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_di_group_question(
    group_id: uuid.UUID, payload: DIQuestionCreate, admin: AdminDep, db: DbDep
) -> DIGroupAdminOut:
    """Appends one more question to an already-saved DI set. Previously the only
    tool available for this was the create form, which always created a brand-new
    group instead of extending the existing one — this is the missing piece that
    lets an admin add a question to a group after it's been saved."""
    group = await db.get(DIGroup, group_id)
    if group is None:
        log.warning(
            "di group question add rejected",
            extra={"di_group_id": str(group_id), "reason": "not_found"},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DI group not found")
    section = await _get_section(db, group.section_id)
    await _assert_editable(db, section.exam_id)

    if len([o for o in payload.options if o.is_correct]) != 1:
        log.warning(
            "di group question add rejected",
            extra={"di_group_id": str(group_id), "reason": "correct_option_count"},
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Each DI question must have exactly one correct option"
        )

    next_index = await db.scalar(
        select(func.coalesce(func.max(Question.order_index), -1)).where(
            Question.di_group_id == group_id
        )
    )
    question = Question(
        section_id=group.section_id,
        di_group_id=group_id,
        type=QuestionType.di,
        body_md=payload.body_md,
        explanation_md=payload.explanation_md,
        marks=payload.marks,
        order_index=next_index + 1,
        meta={"tags": payload.tags, "difficulty": payload.difficulty},
    )
    db.add(question)
    await db.flush()
    for oidx, option in enumerate(payload.options):
        db.add(
            MCQOption(
                question_id=question.id,
                body=option.body,
                is_correct=option.is_correct,
                order_index=option.order_index or oidx,
            )
        )
    await db.flush()
    log.info(
        "di group question added",
        extra={
            "di_group_id": str(group_id),
            "question_id": str(question.id),
            "section_id": str(section.id),
            "admin_id": str(admin.id),
        },
    )

    result = await db.execute(
        select(Question)
        .where(Question.di_group_id == group_id)
        .options(selectinload(Question.options))
        .order_by(Question.order_index)
    )
    return DIGroupAdminOut(
        id=group.id,
        section_id=group.section_id,
        title=group.title,
        passage_md=group.passage_md,
        image_url=group.image_url,
        order_index=group.order_index,
        questions=[_question_detail(q) for q in result.scalars()],
    )


@router.post("/uploads/image", status_code=status.HTTP_201_CREATED)
async def upload_image(
    admin: AdminDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()] = "di",
) -> dict[str, str]:
    """Stores a DI image and returns the URL to put on the DI group."""
    allowed = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    if file.content_type not in allowed:
        log.warning(
            "image upload rejected",
            extra={"reason": "unsupported_media_type", "content_type": file.content_type},
        )
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Allowed types: {', '.join(sorted(allowed))}"
        )
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        log.warning(
            "image upload rejected",
            extra={"reason": "too_large", "size_bytes": len(data)},
        )
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Image exceeds {settings.max_upload_mb} MB",
        )

    suffix = Path(file.filename or "image.png").suffix.lower() or ".png"
    name = f"{label}-{uuid.uuid4().hex}{suffix}"
    target_dir = Path(settings.upload_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(data)
    log.info(
        "image uploaded",
        extra={"stored_name": name, "size_bytes": len(data), "admin_id": str(admin.id)},
    )
    return {"image_url": f"/uploads/{name}", "filename": name}


@router.post("/exams/{exam_id}/seb-config", status_code=status.HTTP_201_CREATED)
async def upload_seb_config(
    exam_id: uuid.UUID,
    admin: AdminDep,
    db: DbDep,
    file: Annotated[UploadFile, File()],
) -> dict[str, str]:
    """Stores the exam's one shared .seb file at the exact path Dashboard.tsx and
    InviteLanding.tsx already link to (/uploads/seb/<exam_id>.seb) — no more manual
    `docker compose cp` into the uploads volume. Doesn't touch requires_seb or
    seb_config_key; those are set via PATCH /exams/{exam_id} same as any other field,
    since the Config Key has to be copied from the Config Tool by hand regardless."""
    await _get_exam(db, exam_id)
    if not (file.filename or "").lower().endswith(".seb"):
        log.warning(
            "seb config upload rejected",
            extra={"exam_id": str(exam_id), "reason": "not_a_seb_file"},
        )
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Expected a .seb file")
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        log.warning(
            "seb config upload rejected",
            extra={"exam_id": str(exam_id), "reason": "too_large", "size_bytes": len(data)},
        )
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {settings.max_upload_mb} MB",
        )

    target_dir = Path(settings.upload_dir) / "seb"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{exam_id}.seb").write_bytes(data)
    # The .seb payload itself carries the exam's lockdown settings; only its size
    # is recorded, never its contents.
    log.info(
        "seb config uploaded",
        extra={"exam_id": str(exam_id), "size_bytes": len(data), "admin_id": str(admin.id)},
    )
    return {"seb_url": f"/uploads/seb/{exam_id}.seb"}


def _prepare_coding_fields(payload: CodingCreate | CodingUpdate) -> dict:
    """Validates and computes the problem_type-dependent fields shared by create
    and update. For problem_type="function", starter_code is always derived from
    the signature here — never taken from the client — so the boilerplate a
    student sees can never drift from what the generated driver actually expects."""
    # No id is available here — the caller is validating a payload before any row
    # exists — so these lines carry the reason only; the request id correlates
    # them with the endpoint's own access-log entry.
    if "sql" in payload.allowed_languages:
        if not (payload.sql_schema_sql or "").strip():
            log.warning(
                "coding payload rejected",
                extra={"reason": "sql_schema_missing"},
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "SQL questions need a schema/seed data script",
            )
        sql_fields = {
            "sql_dialect": payload.sql_dialect,
            "sql_schema_sql": payload.sql_schema_sql,
            "sql_result_columns": payload.sql_result_columns,
        }
    else:
        sql_fields = {"sql_dialect": None, "sql_schema_sql": None, "sql_result_columns": None}

    if payload.problem_type == "function":
        if not payload.function_name or not payload.return_type or not payload.parameters:
            log.warning(
                "coding payload rejected", extra={"reason": "incomplete_function_signature"}
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Function-signature problems require a function name, return type, "
                "and at least one parameter",
            )
        unsupported = set(payload.allowed_languages) - harness.SUPPORTED_FUNCTION_LANGUAGES
        if unsupported:
            log.warning(
                "coding payload rejected",
                extra={
                    "reason": "unsupported_function_languages",
                    "languages": sorted(unsupported),
                },
            )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Function-signature mode doesn't support: {', '.join(sorted(unsupported))}. "
                f"Choose from: {', '.join(sorted(harness.SUPPORTED_FUNCTION_LANGUAGES))}",
            )
        parameters = [p.model_dump() for p in payload.parameters]
        for tc in payload.test_cases:
            if tc.param_values is None or len(tc.param_values) != len(parameters):
                log.warning(
                    "coding payload rejected",
                    extra={
                        "reason": "test_case_param_arity",
                        "expected_params": len(parameters),
                    },
                )
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"Every test case needs exactly {len(parameters)} parameter value(s)",
                )
            if tc.expected_value is None:
                log.warning(
                    "coding payload rejected", extra={"reason": "test_case_expected_missing"}
                )
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST, "Every test case needs an expected output"
                )
        starter_code = {
            lang: harness.generate_boilerplate(
                lang, payload.function_name, payload.return_type, parameters
            )
            for lang in payload.allowed_languages
        }
        return {
            "problem_type": ProblemType.function,
            "function_name": payload.function_name,
            "return_type": payload.return_type,
            "parameters": parameters,
            "starter_code": starter_code,
            **sql_fields,
        }

    return {
        "problem_type": ProblemType.stdio,
        "function_name": None,
        "return_type": None,
        "parameters": None,
        "starter_code": payload.starter_code,
        **sql_fields,
    }


@router.post("/coding/preview-boilerplate", response_model=BoilerplatePreviewOut)
async def preview_boilerplate(payload: BoilerplatePreviewRequest, admin: AdminDep) -> BoilerplatePreviewOut:
    """No DB writes — lets the exam builder show exactly what students will see
    while the admin is still editing the function signature."""
    if payload.language not in harness.SUPPORTED_FUNCTION_LANGUAGES:
        log.warning(
            "boilerplate preview rejected",
            extra={"reason": "unsupported_language", "language": payload.language},
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Function-signature mode doesn't support: {payload.language}",
        )
    code = harness.generate_boilerplate(
        payload.language,
        payload.function_name,
        payload.return_type,
        [p.model_dump() for p in payload.parameters],
    )
    return BoilerplatePreviewOut(code=code)


# NOTE: the SQL "run reference query" preview lived here. It executed SQL in the
# judge sandbox, which this branch does not have — an admin now types the expected
# result directly, same as for any other language.


@router.post("/sections/{section_id}/coding", status_code=status.HTTP_201_CREATED)
async def create_coding_problem(
    section_id: uuid.UUID, payload: CodingCreate, admin: AdminDep, db: DbDep
) -> dict:
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    # Sample cases are the worked examples a student sees. Hidden cases are now
    # optional: nothing executes them, they only give the AI evaluator extra
    # context about the intended behaviour.
    if not any(tc.is_sample for tc in payload.test_cases):
        log.warning(
            "coding question create rejected",
            extra={"section_id": str(section_id), "reason": "no_sample_test_case"},
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one sample test case to show students as a worked example",
        )
    coding_fields = _prepare_coding_fields(payload)

    question = Question(
        section_id=section_id,
        type=QuestionType.coding,
        body_md=payload.body_md,
        marks=payload.marks,
        order_index=payload.order_index,
        meta={"tags": payload.tags, "difficulty": payload.difficulty},
    )
    db.add(question)
    await db.flush()

    problem = CodingProblem(
        question_id=question.id,
        statement_md=payload.statement_md,
        constraints_md=payload.constraints_md,
        allowed_languages=payload.allowed_languages,
        time_limit_ms=payload.time_limit_ms,
        memory_limit_mb=payload.memory_limit_mb,
        **coding_fields,
    )
    db.add(problem)
    await db.flush()

    for idx, tc in enumerate(payload.test_cases):
        db.add(
            TestCase(
                coding_problem_id=problem.id,
                stdin=tc.stdin,
                expected_stdout=tc.expected_stdout,
                param_values=tc.param_values,
                expected_value=tc.expected_value,
                explanation=tc.explanation,
                is_sample=tc.is_sample,
                weight=tc.weight,
                order_index=tc.order_index or idx,
            )
        )
    await db.flush()
    log.info(
        "question created",
        extra={
            "question_id": str(question.id),
            "section_id": str(section_id),
            "question_type": QuestionType.coding.value,
            "coding_problem_id": str(problem.id),
            "problem_type": str(coding_fields["problem_type"]),
            "test_case_count": len(payload.test_cases),
            "admin_id": str(admin.id),
        },
    )
    return {"question_id": str(question.id), "coding_problem_id": str(problem.id)}


@router.patch("/questions/{question_id}/mcq", response_model=QuestionDetailOut)
async def update_mcq(
    question_id: uuid.UUID, payload: MCQUpdate, admin: AdminDep, db: DbDep
) -> QuestionDetailOut:
    """Edits an MCQ or DI question's body, explanation, marks, and options — including
    which option is correct. Options are fully replaced rather than diffed, matching how
    they're created."""
    question = await db.get(Question, question_id)
    if question is None or question.type not in (QuestionType.mcq, QuestionType.di):
        log.warning(
            "mcq update rejected",
            extra={
                "question_id": str(question_id),
                "reason": "not_found" if question is None else "wrong_question_type",
            },
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCQ/DI question not found")
    section = await _get_section(db, question.section_id)
    await _assert_editable(db, section.exam_id)

    correct = [o for o in payload.options if o.is_correct]
    if len(correct) != 1:
        log.warning(
            "mcq update rejected",
            extra={
                "question_id": str(question_id),
                "reason": "correct_option_count",
                "correct_count": len(correct),
            },
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "An MCQ must have exactly one correct option"
        )

    question.body_md = payload.body_md
    question.explanation_md = payload.explanation_md
    question.marks = payload.marks
    question.negative_marks = payload.negative_marks
    question.meta = {"tags": payload.tags, "difficulty": payload.difficulty}

    await db.execute(delete(MCQOption).where(MCQOption.question_id == question.id))
    for idx, option in enumerate(payload.options):
        db.add(
            MCQOption(
                question_id=question.id,
                body=option.body,
                is_correct=option.is_correct,
                order_index=option.order_index or idx,
            )
        )
    await db.flush()
    # Options are replaced wholesale, so this is a destructive edit of graded
    # content — worth a trace even though nothing writes an AuditLog row.
    log.info(
        "question updated",
        extra={
            "question_id": str(question_id),
            "section_id": str(question.section_id),
            "question_type": question.type.value,
            "option_count": len(payload.options),
            "admin_id": str(admin.id),
        },
    )
    return _question_detail(await _get_question_detailed(db, question.id))


@router.patch("/questions/{question_id}/coding", response_model=QuestionDetailOut)
async def update_coding(
    question_id: uuid.UUID, payload: CodingUpdate, admin: AdminDep, db: DbDep
) -> QuestionDetailOut:
    question = await db.get(Question, question_id)
    if question is None or question.type is not QuestionType.coding:
        log.warning(
            "coding question update rejected",
            extra={
                "question_id": str(question_id),
                "reason": "not_found" if question is None else "wrong_question_type",
            },
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coding question not found")
    section = await _get_section(db, question.section_id)
    await _assert_editable(db, section.exam_id)

    problem = await db.scalar(
        select(CodingProblem).where(CodingProblem.question_id == question.id)
    )
    if problem is None:
        # A coding question whose problem row is missing is a broken row, not a
        # bad request — the create path always writes the two together.
        log.error(
            "coding question has no problem row",
            extra={"question_id": str(question_id), "section_id": str(question.section_id)},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coding problem not found")

    # Sample cases are the worked examples a student sees. Hidden cases are now
    # optional: nothing executes them, they only give the AI evaluator extra
    # context about the intended behaviour.
    if not any(tc.is_sample for tc in payload.test_cases):
        log.warning(
            "coding question update rejected",
            extra={"question_id": str(question_id), "reason": "no_sample_test_case"},
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one sample test case to show students as a worked example",
        )
    coding_fields = _prepare_coding_fields(payload)

    question.body_md = payload.body_md
    question.marks = payload.marks
    question.meta = {"tags": payload.tags, "difficulty": payload.difficulty}

    problem.statement_md = payload.statement_md
    problem.constraints_md = payload.constraints_md
    problem.allowed_languages = payload.allowed_languages
    problem.time_limit_ms = payload.time_limit_ms
    problem.memory_limit_mb = payload.memory_limit_mb
    for field, value in coding_fields.items():
        setattr(problem, field, value)

    await db.execute(delete(TestCase).where(TestCase.coding_problem_id == problem.id))
    for idx, tc in enumerate(payload.test_cases):
        db.add(
            TestCase(
                coding_problem_id=problem.id,
                stdin=tc.stdin,
                expected_stdout=tc.expected_stdout,
                param_values=tc.param_values,
                expected_value=tc.expected_value,
                explanation=tc.explanation,
                is_sample=tc.is_sample,
                weight=tc.weight,
                order_index=tc.order_index or idx,
            )
        )
    await db.flush()
    log.info(
        "question updated",
        extra={
            "question_id": str(question_id),
            "section_id": str(question.section_id),
            "question_type": QuestionType.coding.value,
            "coding_problem_id": str(problem.id),
            "test_case_count": len(payload.test_cases),
            "admin_id": str(admin.id),
        },
    )
    return _question_detail(await _get_question_detailed(db, question.id))


@router.patch("/di-groups/{group_id}", response_model=DIGroupAdminOut)
async def update_di_group(
    group_id: uuid.UUID, payload: DIGroupUpdate, admin: AdminDep, db: DbDep
) -> DIGroupAdminOut:
    """Edits the shared stimulus (title/passage/image). Child questions are edited
    individually via PATCH /questions/{id}/mcq, same as any other MCQ-shaped question."""
    group = await db.get(DIGroup, group_id)
    if group is None:
        log.warning(
            "di group update rejected",
            extra={"di_group_id": str(group_id), "reason": "not_found"},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DI group not found")
    section = await _get_section(db, group.section_id)
    await _assert_editable(db, section.exam_id)

    group.title = payload.title
    group.passage_md = payload.passage_md
    group.image_url = payload.image_url
    await db.flush()
    log.info(
        "di group updated",
        extra={
            "di_group_id": str(group_id),
            "section_id": str(group.section_id),
            "admin_id": str(admin.id),
        },
    )

    result = await db.execute(
        select(Question)
        .where(Question.di_group_id == group_id)
        .options(selectinload(Question.options))
        .order_by(Question.order_index)
    )
    return DIGroupAdminOut(
        id=group.id,
        section_id=group.section_id,
        title=group.title,
        passage_md=group.passage_md,
        image_url=group.image_url,
        order_index=group.order_index,
        questions=[_question_detail(q) for q in result.scalars()],
    )


@router.delete("/di-groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_di_group(group_id: uuid.UUID, admin: AdminDep, db: DbDep) -> None:
    """Deletes the stimulus and every question grouped under it."""
    group = await db.get(DIGroup, group_id)
    if group is None:
        log.warning(
            "di group delete rejected",
            extra={"di_group_id": str(group_id), "reason": "not_found"},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DI group not found")
    section = await _get_section(db, group.section_id)
    await _assert_editable(db, section.exam_id)

    children = await db.execute(select(Question).where(Question.di_group_id == group_id))
    deleted_children = 0
    for child in children.scalars():
        await db.delete(child)
        deleted_children += 1
    await db.flush()
    await db.delete(group)
    # Cascading delete of a whole DI set, with no audit row behind it.
    log.info(
        "di group deleted",
        extra={
            "di_group_id": str(group_id),
            "section_id": str(section.id),
            "question_count": deleted_children,
            "admin_id": str(admin.id),
        },
    )


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: uuid.UUID, admin: AdminDep, db: DbDep) -> None:
    question = await db.get(Question, question_id)
    if question is None:
        log.warning(
            "question delete rejected",
            extra={"question_id": str(question_id), "reason": "not_found"},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    section = await _get_section(db, question.section_id)
    await _assert_editable(db, section.exam_id)
    question_type = question.type.value
    await db.delete(question)
    log.info(
        "question deleted",
        extra={
            "question_id": str(question_id),
            "section_id": str(section.id),
            "exam_id": str(section.exam_id),
            "question_type": question_type,
            "admin_id": str(admin.id),
        },
    )


# ---------------------------------------------------------------- students


@router.post("/students", response_model=StudentOut, status_code=status.HTTP_201_CREATED)
async def create_student(
    payload: StudentCreate, admin: AdminDep, db: DbDep
) -> StudentOut:
    student = Student(
        student_id=payload.student_id,
        name=payload.name,
        email=payload.email.lower(),
        cohort=payload.cohort,
    )
    db.add(student)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        # Expected caller fault (duplicate enrollment ID or email), not a server
        # fault — no stack trace, and the address is masked because it is PII.
        log.warning(
            "student create conflict",
            extra={
                "enrollment_id": payload.student_id,
                "email_masked": mask_email(payload.email),
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Enrollment ID {payload.student_id} or email {payload.email} already exists",
        ) from None
    log.info(
        "student created",
        extra={
            "student_id": str(student.id),
            "enrollment_id": student.student_id,
            "cohort": student.cohort,
            "admin_id": str(admin.id),
        },
    )
    return StudentOut.model_validate(student)


@router.post("/students/bulk", response_model=BulkResult)
async def bulk_upload_students(
    admin: AdminDep,
    db: DbDep,
    file: Annotated[UploadFile, File()],
) -> BulkResult:
    """CSV columns: enrollment_id, name, email, cohort (optional). `student_id` is
    still accepted as an alias for `enrollment_id` for older CSVs. Email is required —
    it's the magic-link sign-in identity, not just contact info."""
    text = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    existing_result = await db.execute(select(Student.student_id))
    existing_ids = set(existing_result.scalars())
    existing_emails = set((await db.execute(select(Student.email))).scalars())

    created, skipped, errors = 0, 0, []

    for line_no, row in enumerate(reader, start=2):
        sid = (row.get("enrollment_id") or row.get("student_id") or "").strip()
        name = (row.get("name") or "").strip()
        email = (row.get("email") or "").strip().lower()
        if not sid or not name or not email:
            skipped += 1
            errors.append(f"Row {line_no}: enrollment_id, name and email are required")
            continue
        if sid in existing_ids:
            skipped += 1
            errors.append(f"Row {line_no}: {sid} already exists")
            continue
        if email in existing_emails:
            skipped += 1
            errors.append(f"Row {line_no}: email {email} already exists")
            continue

        db.add(
            Student(
                student_id=sid,
                name=name,
                email=email,
                cohort=(row.get("cohort") or "").strip() or None,
            )
        )
        existing_ids.add(sid)
        existing_emails.add(email)
        created += 1

    await db.flush()
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="students_bulk_upload",
            meta={"created": created, "skipped": skipped},
        )
    )
    # The CSV rows carry names and email addresses, so only the counts are logged.
    log.info(
        "student bulk upload completed",
        extra={"created_count": created, "skipped": skipped, "admin_id": str(admin.id)},
    )
    return BulkResult(created=created, skipped=skipped, errors=errors[:50])


@router.get("/students", response_model=list[StudentOut])
async def list_students(
    admin: AdminDep, db: DbDep, cohort: str | None = None, limit: int = 200, offset: int = 0
) -> list[StudentOut]:
    query = select(Student).order_by(Student.student_id).limit(min(limit, 500)).offset(offset)
    if cohort:
        query = query.where(Student.cohort == cohort)
    result = await db.execute(query)
    return [StudentOut.model_validate(s) for s in result.scalars()]


async def _get_student(db: AsyncSession, student_id: uuid.UUID) -> Student:
    student = await db.get(Student, student_id)
    if student is None:
        log.warning(
            "admin lookup failed", extra={"entity": "student", "student_id": str(student_id)}
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    return student


@router.patch("/students/{student_id}", response_model=StudentOut)
async def update_student(
    student_id: uuid.UUID, payload: StudentUpdate, admin: AdminDep, db: DbDep
) -> StudentOut:
    student = await _get_student(db, student_id)
    updates = payload.model_dump(exclude_unset=True)
    if "email" in updates and updates["email"]:
        updates["email"] = updates["email"].lower()
    for field, value in updates.items():
        setattr(student, field, value)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        log.warning(
            "student update conflict",
            extra={
                "student_id": str(student_id),
                "email_masked": mask_email(updates.get("email")),
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Email {updates.get('email')} already exists"
        ) from None
    log.info(
        "student updated",
        extra={
            "student_id": str(student_id),
            "updated_fields": sorted(updates),
            "admin_id": str(admin.id),
        },
    )
    db.add(
        AuditLog(
            actor_type="admin", actor_id=admin.id, action="student_updated", target=str(student_id)
        )
    )
    return StudentOut.model_validate(student)


@router.delete("/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_student(student_id: uuid.UUID, admin: AdminDep, db: DbDep) -> None:
    student = await _get_student(db, student_id)
    attempts = await db.scalar(
        select(func.count(ExamAttempt.id)).where(ExamAttempt.student_id == student_id)
    )
    if attempts:
        log.warning(
            "student delete blocked",
            extra={
                "student_id": str(student_id),
                "reason": "has_attempts",
                "attempt_count": attempts,
            },
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Student has exam attempts and cannot be deleted; deactivate instead",
        )
    await db.delete(student)
    db.add(
        AuditLog(
            actor_type="admin", actor_id=admin.id, action="student_deleted", target=str(student_id)
        )
    )


@router.post("/students/{student_id}/send-magic-link", response_model=MagicLinkSent)
async def send_student_magic_link(
    student_id: uuid.UUID, admin: AdminDep, db: DbDep
) -> MagicLinkSent:
    """Admin-triggered resend — e.g. a student says they never got the email. Bypasses
    the self-serve cooldown since an admin is already authenticated and vouching for
    the request."""
    student = await _get_student(db, student_id)
    token, _ = create_magic_token(str(student.id))
    link = f"{settings.frontend_base_url}/auth/magic?token={token}"
    try:
        await send_magic_link_email(student.email, student.name, link)
    except Exception as exc:
        # Server-side fault (SMTP/provider), so the stack trace earns its keep.
        # The link is a bearer credential and never appears in the log.
        log.exception(
            "magic link email send failed",
            extra={
                "student_id": str(student_id),
                "email_masked": mask_email(student.email),
                "error_type": type(exc).__name__,
            },
        )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not send the email") from exc
    log.info(
        "magic link resent by admin",
        extra={"student_id": str(student_id), "admin_id": str(admin.id)},
    )
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="magic_link_admin_resend",
            target=str(student_id),
        )
    )
    return MagicLinkSent(message=f"Sign-in link sent to {student.email}")


@router.post("/students/magic-link/bulk", response_model=BulkMagicLinkQueued)
async def bulk_send_magic_links(
    payload: BulkMagicLinkRequest, admin: AdminDep, db: DbDep
) -> BulkMagicLinkQueued:
    """Queues a magic-link send to every given (active) student — e.g. every row
    currently selected in a cohort-filtered view. Runs on a background queue rather
    than this request thread; hundreds of recipients would otherwise mean a very
    slow response (or a timeout)."""
    result = await db.execute(
        select(Student.id).where(Student.id.in_(payload.student_ids), Student.is_active.is_(True))
    )
    ids = [str(sid) for sid in result.scalars()]
    if not ids:
        log.warning(
            "bulk magic link send skipped",
            extra={"reason": "no_active_recipients", "requested": len(payload.student_ids)},
        )
        return BulkMagicLinkQueued(queued=0)

    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="magic_link_bulk_send",
            meta={"count": len(ids)},
        )
    )

    from app.tasks.mailer_tasks import send_bulk_magic_links

    send_bulk_magic_links.delay(ids)
    log.info(
        "bulk magic link send queued",
        extra={"count": len(ids), "admin_id": str(admin.id)},
    )
    return BulkMagicLinkQueued(queued=len(ids))


@router.post("/exams/{exam_id}/invites", response_model=InviteQueued)
async def create_exam_invites(
    exam_id: uuid.UUID, payload: InviteCreateRequest, admin: AdminDep, db: DbDep
) -> InviteQueued:
    """One shared Start URL / Config Key serves every invitee of this exam — see
    SEB_INTEGRATION.md §6. Each student gets their own token (never stored raw);
    re-inviting a student who already has a row just rotates their token, so an
    old, possibly-forwarded email link stops working."""
    exam = await _get_exam(db, exam_id)
    result = await db.execute(
        select(Student).where(Student.id.in_(payload.student_ids), Student.is_active.is_(True))
    )
    students = list(result.scalars())
    if not students:
        log.warning(
            "exam invites skipped",
            extra={
                "exam_id": str(exam_id),
                "reason": "no_active_recipients",
                "requested": len(payload.student_ids),
            },
        )
        return InviteQueued(queued=0)

    existing_result = await db.execute(
        select(ExamInvite).where(
            ExamInvite.exam_id == exam_id,
            ExamInvite.student_id.in_([s.id for s in students]),
        )
    )
    existing_by_student = {inv.student_id: inv for inv in existing_result.scalars()}

    items = []
    for student in students:
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        invite = existing_by_student.get(student.id)
        if invite is None:
            db.add(ExamInvite(exam_id=exam_id, student_id=student.id, token_hash=token_hash))
        else:
            invite.token_hash = token_hash
        items.append(
            {
                "email": student.email,
                "name": student.name,
                "exam_title": exam.title,
                "token": raw_token,
            }
        )

    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="seb_invite_bulk_send",
            meta={"exam_id": str(exam_id), "count": len(items)},
        )
    )

    from app.tasks.mailer_tasks import send_bulk_invites

    send_bulk_invites.delay(items)
    # `items` carries raw invite tokens and addresses; only counts are logged.
    log.info(
        "exam invites queued",
        extra={
            "exam_id": str(exam_id),
            "count": len(items),
            "rotated": len(existing_by_student),
            "admin_id": str(admin.id),
        },
    )
    return InviteQueued(queued=len(items))


# ------------------------------------------------------- admin management
# Super-admin-only: see app.deps.current_super_admin. A regular "admin" role
# can run the rest of this console but must never be able to create, promote,
# or remove admin accounts — that would let a single compromised admin turn
# itself into an unbounded number of admins.

SuperAdminDep = Annotated[Admin, Depends(current_super_admin)]


async def _get_admin(db: AsyncSession, admin_id: uuid.UUID) -> Admin:
    target = await db.get(Admin, admin_id)
    if target is None:
        log.warning("admin lookup failed", extra={"entity": "admin", "admin_id": str(admin_id)})
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Admin not found")
    return target


async def _count_active_super_admins(db: AsyncSession, exclude_id: uuid.UUID | None = None) -> int:
    query = select(func.count(Admin.id)).where(
        Admin.role == "super_admin", Admin.is_active.is_(True)
    )
    if exclude_id is not None:
        query = query.where(Admin.id != exclude_id)
    return await db.scalar(query) or 0


@router.get("/admins", response_model=list[AdminOut])
async def list_admins(admin: SuperAdminDep, db: DbDep) -> list[AdminOut]:
    result = await db.execute(select(Admin).order_by(Admin.created_at))
    return [AdminOut.model_validate(a) for a in result.scalars()]


@router.post("/admins", response_model=AdminOut, status_code=status.HTTP_201_CREATED)
async def create_admin(payload: AdminCreate, admin: SuperAdminDep, db: DbDep) -> AdminOut:
    new_admin = Admin(
        email=payload.email.lower(),
        name=payload.name,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(new_admin)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        log.warning(
            "admin create conflict",
            extra={"email_masked": mask_email(payload.email), "error_type": type(exc).__name__},
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Email {payload.email} already exists"
        ) from None
    log.info(
        "admin created",
        extra={"admin_id": str(new_admin.id), "role": new_admin.role, "created_by": str(admin.id)},
    )
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="admin_created",
            target=str(new_admin.id),
            meta={"role": new_admin.role},
        )
    )
    return AdminOut.model_validate(new_admin)


@router.patch("/admins/{admin_id}", response_model=AdminOut)
async def update_admin(
    admin_id: uuid.UUID, payload: AdminUpdate, admin: SuperAdminDep, db: DbDep
) -> AdminOut:
    target = await _get_admin(db, admin_id)
    updates = payload.model_dump(exclude_unset=True, exclude={"password"})

    # A super admin demoting/deactivating themselves (or the last other super
    # admin) is exactly how a console permanently locks everyone out of admin
    # management — block it rather than trust every caller to remember not to.
    demoting = updates.get("role") == "admin" and target.role == "super_admin"
    deactivating = updates.get("is_active") is False and target.is_active
    if (demoting or deactivating) and await _count_active_super_admins(db, exclude_id=target.id) == 0:
        log.warning(
            "admin update rejected",
            extra={
                "admin_id": str(admin_id),
                "reason": "would_remove_last_super_admin",
                "acting_admin_id": str(admin.id),
            },
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "At least one active super admin must remain; promote another admin first",
        )

    for field, value in updates.items():
        setattr(target, field, value)
    if payload.password:
        target.password_hash = hash_password(payload.password)

    await db.flush()
    log.info(
        "admin updated",
        extra={
            "admin_id": str(admin_id),
            "updated_fields": sorted(set(updates) | ({"password"} if payload.password else set())),
            "acting_admin_id": str(admin.id),
        },
    )
    db.add(
        AuditLog(
            actor_type="admin", actor_id=admin.id, action="admin_updated", target=str(admin_id)
        )
    )
    return AdminOut.model_validate(target)


@router.delete("/admins/{admin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_admin(admin_id: uuid.UUID, admin: SuperAdminDep, db: DbDep) -> None:
    if admin_id == admin.id:
        log.warning(
            "admin delete rejected",
            extra={"admin_id": str(admin_id), "reason": "self_delete"},
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account")
    target = await _get_admin(db, admin_id)
    if target.role == "super_admin" and await _count_active_super_admins(db, exclude_id=target.id) == 0:
        log.warning(
            "admin delete rejected",
            extra={"admin_id": str(admin_id), "reason": "would_remove_last_super_admin"},
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "At least one active super admin must remain; promote another admin first",
        )
    await db.delete(target)
    log.info(
        "admin deleted",
        extra={"admin_id": str(admin_id), "acting_admin_id": str(admin.id)},
    )
    db.add(
        AuditLog(
            actor_type="admin", actor_id=admin.id, action="admin_deleted", target=str(admin_id)
        )
    )


# ----------------------------------------------------------------- publish


@router.post("/exams/{exam_id}/publish", response_model=PublishResult)
async def publish_exam(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> PublishResult:
    """Validates the whole paper before flipping status. Catching a malformed question
    here is the difference between a bad question and a cancelled exam."""
    exam = await paper.load_exam_tree(db, exam_id)
    if exam is None:
        log.warning("exam publish blocked", extra={"exam_id": str(exam_id), "reason": "not_found"})
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")

    problems: list[str] = []
    warnings: list[str] = []

    if not exam.sections:
        problems.append("Exam has no sections")

    for section in exam.sections:
        if not section.questions:
            problems.append(f"Section '{section.title}' has no questions")
        for question in section.questions:
            label = f"{section.title} / Q{question.order_index + 1}"
            if question.type in (QuestionType.mcq, QuestionType.di):
                correct = [o for o in question.options if o.is_correct]
                if len(question.options) < 2:
                    problems.append(f"{label}: needs at least 2 options")
                if len(correct) != 1:
                    problems.append(f"{label}: must have exactly one correct option")
            elif question.type is QuestionType.coding:
                problem = question.coding_problem
                if problem is None:
                    problems.append(f"{label}: coding question has no problem definition")
                    continue
                samples = [tc for tc in problem.test_cases if tc.is_sample]
                if not samples:
                    problems.append(f"{label}: needs at least one sample test case")
                if not problem.allowed_languages:
                    problems.append(f"{label}: no languages allowed")
                if "sql" in problem.allowed_languages and not problem.sql_schema_sql:
                    problems.append(f"{label}: SQL questions need a schema/seed data script")

        for group in section.di_groups:
            if not group.questions:
                problems.append(f"DI group '{group.title}' has no questions")
            if not group.image_url and not group.passage_md:
                problems.append(f"DI group '{group.title}' has neither an image nor a passage")
            elif group.image_url:
                local = Path(settings.upload_dir) / Path(group.image_url).name
                if group.image_url.startswith("/uploads/") and not local.exists():
                    problems.append(f"DI group '{group.title}': image file is missing on disk")

    if exam.starts_at and exam.ends_at:
        window = (exam.ends_at - exam.starts_at).total_seconds() / 60
        if window < exam.duration_minutes:
            problems.append(
                f"Exam window ({window:.0f} min) is shorter than the duration "
                f"({exam.duration_minutes} min)"
            )

    total_questions, total_marks = paper.count_questions_and_marks(exam)
    if not exam.cohort:
        warnings.append("No cohort set — this exam will be visible to every student")
    if total_questions and exam.duration_minutes / total_questions < 0.5:
        warnings.append(
            f"{total_questions} questions in {exam.duration_minutes} min "
            "is under 30 s per question"
        )

    if problems:
        # Count only: the messages embed section and DI-group titles, which are
        # paper content. The caller already receives the detail in the 422 body.
        log.warning(
            "exam publish blocked",
            extra={
                "exam_id": str(exam_id),
                "reason": "validation_failed",
                "problem_count": len(problems),
            },
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": "Exam is not ready to publish", "problems": problems},
        )

    exam.status = ExamStatus.published
    db.add(
        AuditLog(
            actor_type="admin", actor_id=admin.id, action="exam_published", target=str(exam_id)
        )
    )
    await db.flush()

    log.info(
        "exam published",
        extra={
            "exam_id": str(exam_id),
            "admin_id": str(admin.id),
            "total_questions": total_questions,
            "total_marks": total_marks,
            "warning_count": len(warnings),
        },
    )
    return PublishResult(
        exam_id=exam.id,
        status=exam.status,
        total_questions=total_questions,
        total_marks=total_marks,
        warnings=warnings,
    )


@router.post("/exams/{exam_id}/close", response_model=ExamOut)
async def close_exam(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> ExamOut:
    exam = await _get_exam(db, exam_id)
    exam.status = ExamStatus.closed
    db.add(
        AuditLog(actor_type="admin", actor_id=admin.id, action="exam_closed", target=str(exam_id))
    )
    await db.flush()
    log.info("exam closed", extra={"exam_id": str(exam_id), "admin_id": str(admin.id)})
    return ExamOut.model_validate(exam)


# ------------------------------------------------------------- monitoring


@router.get("/exams/{exam_id}/live", response_model=LiveMonitorOut)
async def live_monitor(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> LiveMonitorOut:
    """First-paint fetch; subsequent updates arrive over live_monitor_ws below."""
    return await build_live_snapshot(db, exam_id)


@router.websocket("/exams/{exam_id}/live/ws")
async def live_monitor_ws(websocket: WebSocket, exam_id: uuid.UUID, db: DbDep) -> None:
    """Push-based replacement for polling `GET .../live`. Browsers can't set a custom
    Authorization header on a WS handshake, so auth is done manually here from a
    ?token= query param instead of via Depends(current_admin)."""
    # The WS handshake is not covered by the HTTP access log or by
    # Depends(current_admin), so every rejection below is logged here or it is
    # recorded nowhere at all. The token itself is never logged.
    token = websocket.query_params.get("token")
    if not token:
        log.warning(
            "live monitor ws auth failed",
            extra={"exam_id": str(exam_id), "reason": "no_credentials"},
        )
        await websocket.close(code=4401)
        return
    try:
        payload = decode_token(token)
    except jwt.PyJWTError as exc:
        log.warning(
            "live monitor ws auth failed",
            extra={
                "exam_id": str(exam_id),
                "reason": "token_invalid",
                "error_type": type(exc).__name__,
            },
        )
        await websocket.close(code=4401)
        return
    if payload.get("type") != "access" or payload.get("role") != "admin":
        # A student token on the invigilation socket is the shape a privilege
        # escalation attempt takes.
        log.warning(
            "live monitor ws auth failed",
            extra={
                "exam_id": str(exam_id),
                "reason": "wrong_token_type_or_role",
                "actual_role": payload.get("role"),
            },
        )
        await websocket.close(code=4401)
        return
    if await cache.is_jti_denied(payload.get("jti", "")):
        log.warning(
            "live monitor ws auth failed",
            extra={
                "exam_id": str(exam_id),
                "reason": "token_revoked",
                "jti": payload.get("jti", ""),
            },
        )
        await websocket.close(code=4401)
        return
    admin = await db.get(Admin, uuid.UUID(payload["sub"]))
    if admin is None or not admin.is_active:
        log.warning(
            "live monitor ws auth failed",
            extra={
                "exam_id": str(exam_id),
                "reason": "admin_missing_or_inactive",
                "admin_id": payload.get("sub"),
            },
        )
        await websocket.close(code=4401)
        return
    exam = await db.get(Exam, exam_id)
    if exam is None:
        log.warning(
            "live monitor ws rejected",
            extra={"exam_id": str(exam_id), "reason": "exam_not_found"},
        )
        await websocket.close(code=4404)
        return

    await websocket.accept()
    await live_connections.connect(str(exam_id), websocket)
    try:
        snapshot = await build_live_snapshot(db, exam_id)
        await websocket.send_json(snapshot.model_dump(mode="json"))
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        # Routine — the invigilator closed the tab. Recorded at INFO only so the
        # socket's lifetime is visible; there is no access-log entry for a WS.
        log.info(
            "live monitor ws disconnected",
            extra={"exam_id": str(exam_id), "admin_id": str(admin.id)},
        )
    finally:
        live_connections.disconnect(str(exam_id), websocket)


@router.post("/attempts/{attempt_id}/force-submit", status_code=status.HTTP_200_OK)
async def force_submit(attempt_id: uuid.UUID, admin: AdminDep, db: DbDep) -> dict[str, str]:
    """Invigilator override — e.g. a student caught cheating, or a stuck client."""
    attempt = await db.get(ExamAttempt, attempt_id)
    if attempt is None:
        log.warning(
            "force submit rejected",
            extra={"attempt_id": str(attempt_id), "reason": "attempt_not_found"},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found")
    if attempt.status is not AttemptStatus.in_progress:
        log.info(
            "force submit no-op",
            extra={
                "attempt_id": str(attempt_id),
                "reason": "already_submitted",
                "attempt_status": attempt.status.value,
            },
        )
        return {"status": attempt.status.value, "detail": "Already submitted"}

    from app.services import answers as answer_service
    from app.services import grading

    buffered = await cache.get_buffered_answers(str(attempt.id))
    if buffered:
        await answer_service.upsert_answers(db, attempt.id, buffered)
        await db.flush()

    attempt.status = AttemptStatus.auto_submitted
    attempt.submitted_at = datetime.now(UTC)
    score, max_score, pending = await grading.grade_objective(db, attempt)
    attempt.total_score, attempt.max_score = score, max_score
    attempt.grading_complete = not pending
    await db.flush()

    # Same snapshot + queue as the student's own submit and the sweeper. Without
    # it a force-submitted attempt has no CodingSubmission at all: nothing to
    # grade, and grading_complete pinned false forever.
    new_submissions = await coding_service.snapshot_submissions(db, attempt)

    await cache.clear_attempt(str(attempt.id))
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="attempt_force_submitted",
            target=str(attempt_id),
        )
    )

    log.info(
        "attempt force submitted",
        extra={
            "attempt_id": str(attempt_id),
            "exam_id": str(attempt.exam_id),
            "admin_id": str(admin.id),
            "buffered_answers_flushed": len(buffered) if buffered else 0,
            "grading_complete": attempt.grading_complete,
            "coding_submission_count": len(new_submissions),
        },
    )

    if new_submissions:
        # Commit before enqueueing so the worker's own connection sees the rows.
        await db.commit()

        from app.tasks.ai_tasks import evaluate_coding_submission

        for submission_id in new_submissions:
            try:
                evaluate_coding_submission.delay(str(submission_id))
            except Exception as exc:
                # A broker outage must not fail the invigilator's force-submit;
                # the rows are saved at PENDING and re-runnable from the review UI.
                log.exception(
                    "could not queue AI evaluation",
                    extra={
                        "submission_id": str(submission_id),
                        "attempt_id": str(attempt_id),
                        "error_type": type(exc).__name__,
                    },
                )

    return {"status": attempt.status.value, "detail": "Attempt submitted"}


@router.post("/attempts/{attempt_id}/extend", status_code=status.HTTP_200_OK)
async def extend_time(
    attempt_id: uuid.UUID, admin: AdminDep, db: DbDep, minutes: int = 5
) -> dict[str, str]:
    """Grants extra time for a documented technical issue. Written to the audit log
    because time grants are the most abuse-prone admin action."""
    if not 1 <= minutes <= 60:
        log.warning(
            "time extension rejected",
            extra={
                "attempt_id": str(attempt_id),
                "reason": "minutes_out_of_range",
                "minutes": minutes,
            },
        )
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "minutes must be between 1 and 60")
    attempt = await db.get(ExamAttempt, attempt_id)
    if attempt is None:
        log.warning(
            "time extension rejected",
            extra={"attempt_id": str(attempt_id), "reason": "attempt_not_found"},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found")
    if attempt.status is not AttemptStatus.in_progress:
        log.warning(
            "time extension rejected",
            extra={
                "attempt_id": str(attempt_id),
                "reason": "attempt_not_in_progress",
                "attempt_status": attempt.status.value,
            },
        )
        raise HTTPException(status.HTTP_409_CONFLICT, "Attempt is not in progress")

    from datetime import timedelta

    deadline = attempt.deadline_at
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    attempt.deadline_at = deadline + timedelta(minutes=minutes)
    await cache.set_deadline(
        str(attempt.id), attempt.deadline_at, cache.seconds_until(attempt.deadline_at) + 300
    )
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="attempt_time_extended",
            target=str(attempt_id),
            meta={"minutes": minutes},
        )
    )
    # Time grants are the most abuse-prone admin action; the audit row is in the
    # DB, this line puts the same fact in the operational log.
    log.info(
        "attempt time extended",
        extra={
            "attempt_id": str(attempt_id),
            "admin_id": str(admin.id),
            "minutes": minutes,
            "new_deadline_at": attempt.deadline_at.isoformat(),
        },
    )
    return {"deadline_at": attempt.deadline_at.isoformat()}


# -------------------------------------------------------- results & analytics


@router.get("/exams/{exam_id}/results.xlsx")
async def export_results(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> Response:
    filename, content = await export.build_results_workbook(db, exam_id)
    # An export is a bulk extraction of every student's marks — a data-access
    # event worth recording even though the endpoint is a GET.
    log.info(
        "results exported",
        extra={"exam_id": str(exam_id), "admin_id": str(admin.id), "size_bytes": len(content)},
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/exams/{exam_id}/analytics", response_model=AnalyticsOut)
async def analytics(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> AnalyticsOut:
    await _get_exam(db, exam_id)

    scores_result = await db.execute(
        select(ExamAttempt.total_score, ExamAttempt.max_score).where(
            ExamAttempt.exam_id == exam_id,
            ExamAttempt.status != AttemptStatus.in_progress,
            ExamAttempt.total_score.isnot(None),
        )
    )
    pairs = scores_result.all()
    scores = [float(s) for s, _ in pairs]
    max_score = next((float(m) for _, m in pairs if m is not None), None)

    buckets = {"0-20%": 0, "20-40%": 0, "40-60%": 0, "60-80%": 0, "80-100%": 0}
    if max_score:
        for score in scores:
            pct = max(0.0, min(100.0, score / max_score * 100))
            index = min(int(pct // 20), 4)
            buckets[list(buckets)[index]] += 1

    stats_result = await db.execute(
        select(
            Question.id,
            Question.type,
            Question.body_md,
            func.count(Answer.id),
            func.count(func.nullif(Answer.is_correct, False)),
        )
        .join(Section, Question.section_id == Section.id)
        .outerjoin(Answer, Answer.question_id == Question.id)
        .where(Section.exam_id == exam_id)
        .group_by(Question.id, Question.type, Question.body_md)
        .order_by(Question.id)
    )
    question_stats = [
        QuestionStat(
            question_id=qid,
            type=qtype,
            body_preview=_body_preview(body or "", limit=120),
            attempted=attempted,
            correct=correct,
            accuracy=round(correct / attempted * 100, 1) if attempted else 0.0,
        )
        for qid, qtype, body, attempted, correct in stats_result.all()
    ]

    return AnalyticsOut(
        exam_id=exam_id,
        submissions=len(scores),
        average_score=round(statistics.fmean(scores), 2) if scores else None,
        median_score=round(statistics.median(scores), 2) if scores else None,
        highest_score=max(scores) if scores else None,
        lowest_score=min(scores) if scores else None,
        max_score=max_score,
        score_buckets=buckets,
        question_stats=question_stats,
    )


# --------------------------------------------------------------- student details


async def _get_attempt(db: AsyncSession, attempt_id: uuid.UUID) -> ExamAttempt:
    attempt = await db.get(ExamAttempt, attempt_id)
    if attempt is None:
        log.warning(
            "admin lookup failed", extra={"entity": "attempt", "attempt_id": str(attempt_id)}
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found")
    return attempt


def _duration_minutes(attempt: ExamAttempt) -> float | None:
    if not (attempt.submitted_at and attempt.started_at):
        return None
    started = attempt.started_at.replace(tzinfo=attempt.started_at.tzinfo or UTC)
    submitted = attempt.submitted_at.replace(tzinfo=attempt.submitted_at.tzinfo or UTC)
    return round((submitted - started).total_seconds() / 60, 1)


def _percentage(attempt: ExamAttempt) -> float | None:
    if attempt.total_score is None or not attempt.max_score:
        return None
    return round(attempt.total_score / attempt.max_score * 100, 2)


async def _score_breakdown(
    db: AsyncSession, attempt_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, float]]:
    """Splits each attempt's score into aptitude (mcq + DI) vs coding, using the
    same per-question max-marks fallback (`Question.marks` or else
    `Section.marks_per_question`) as the single-attempt overview."""
    breakdown = {
        attempt_id: {"aptitude_score": 0.0, "aptitude_max": 0.0, "coding_score": 0.0, "coding_max": 0.0}
        for attempt_id in attempt_ids
    }
    if not attempt_ids:
        return breakdown

    rows_result = await db.execute(
        select(
            ExamAttempt.id,
            Question.type,
            Answer.score,
            Question.marks,
            Section.marks_per_question,
        )
        .select_from(ExamAttempt)
        .join(Section, Section.exam_id == ExamAttempt.exam_id)
        .join(Question, Question.section_id == Section.id)
        .outerjoin(
            Answer, and_(Answer.question_id == Question.id, Answer.attempt_id == ExamAttempt.id)
        )
        .where(ExamAttempt.id.in_(attempt_ids))
    )
    for attempt_id, qtype, score, marks, marks_per_question in rows_result.all():
        bucket = breakdown[attempt_id]
        max_marks = marks if marks is not None else marks_per_question
        prefix = "coding" if qtype is QuestionType.coding else "aptitude"
        bucket[f"{prefix}_score"] += score or 0.0
        bucket[f"{prefix}_max"] += max_marks or 0.0

    for bucket in breakdown.values():
        bucket["aptitude_score"] = round(bucket["aptitude_score"], 2)
        bucket["aptitude_max"] = round(bucket["aptitude_max"], 2)
        bucket["coding_score"] = round(bucket["coding_score"], 2)
        bucket["coding_max"] = round(bucket["coding_max"], 2)
    return breakdown


AttemptSort = Literal[
    "student_name",
    "exam_title",
    "score",
    "percentage",
    "started_at",
    "submitted_at",
    "aptitude_score",
    "coding_score",
]


def _attempt_conditions(
    search: str | None,
    cohort: str | None,
    exam_id: uuid.UUID | None,
    status_filter: Literal["completed", "pending"] | None,
    min_score: float | None,
    max_score: float | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> list:
    conditions = []
    if search:
        like = f"%{search}%"
        conditions.append(
            or_(Student.name.ilike(like), Student.email.ilike(like), Student.student_id.ilike(like))
        )
    if cohort:
        conditions.append(Student.cohort == cohort)
    if exam_id:
        conditions.append(ExamAttempt.exam_id == exam_id)
    if status_filter == "completed":
        conditions.append(ExamAttempt.status != AttemptStatus.in_progress)
    elif status_filter == "pending":
        conditions.append(ExamAttempt.status == AttemptStatus.in_progress)
    if min_score is not None:
        conditions.append(ExamAttempt.total_score >= min_score)
    if max_score is not None:
        conditions.append(ExamAttempt.total_score <= max_score)
    if date_from is not None:
        conditions.append(ExamAttempt.submitted_at >= date_from)
    if date_to is not None:
        conditions.append(ExamAttempt.submitted_at <= date_to)
    return conditions


def _score_by_type_subq(qtype_is_coding: bool):
    """Correlated per-attempt sum of `Answer.score` restricted to coding
    questions or non-coding (aptitude) questions, for use as a sort key.
    Mirrors the bucketing in `_score_breakdown`, just expressed as SQL instead
    of post-hoc Python so it can drive ORDER BY before pagination."""
    type_filter = (
        Question.type == QuestionType.coding
        if qtype_is_coding
        else Question.type != QuestionType.coding
    )
    return (
        select(func.coalesce(func.sum(Answer.score), 0.0))
        .select_from(Section)
        .join(Question, Question.section_id == Section.id)
        .outerjoin(
            Answer, and_(Answer.question_id == Question.id, Answer.attempt_id == ExamAttempt.id)
        )
        .where(Section.exam_id == ExamAttempt.exam_id, type_filter)
        .correlate(ExamAttempt)
        .scalar_subquery()
    )


def _attempt_sort_expr(sort: AttemptSort, order: Literal["asc", "desc"]):
    sort_columns = {
        "student_name": Student.name,
        "exam_title": Exam.title,
        "score": ExamAttempt.total_score,
        "percentage": ExamAttempt.total_score / func.nullif(ExamAttempt.max_score, 0),
        "started_at": ExamAttempt.started_at,
        "submitted_at": ExamAttempt.submitted_at,
        "aptitude_score": _score_by_type_subq(qtype_is_coding=False),
        "coding_score": _score_by_type_subq(qtype_is_coding=True),
    }
    order_expr = sort_columns[sort]
    return order_expr.desc() if order == "desc" else order_expr.asc()


@router.get("/attempts", response_model=AttemptListPage)
async def list_attempts(
    admin: AdminDep,
    db: DbDep,
    search: str | None = None,
    cohort: str | None = None,
    exam_id: uuid.UUID | None = None,
    status_filter: Annotated[
        Literal["completed", "pending"] | None, Query(alias="status")
    ] = None,
    min_score: float | None = None,
    max_score: float | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: AttemptSort = "submitted_at",
    order: Literal["asc", "desc"] = "desc",
    page: int = 1,
    page_size: int = 25,
) -> AttemptListPage:
    """Cross-exam attempt list backing the Student Details dashboard. 'Completed' means
    any terminal status (submitted/auto_submitted/expired); 'Pending' means still in
    progress."""
    conditions = _attempt_conditions(
        search, cohort, exam_id, status_filter, min_score, max_score, date_from, date_to
    )

    filtered = (
        select(ExamAttempt, Student, Exam)
        .join(Student, ExamAttempt.student_id == Student.id)
        .join(Exam, ExamAttempt.exam_id == Exam.id)
    )
    if conditions:
        filtered = filtered.where(and_(*conditions))

    total = await db.scalar(select(func.count()).select_from(filtered.subquery())) or 0

    order_expr = _attempt_sort_expr(sort, order)

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)

    rows_result = await db.execute(
        filtered.order_by(order_expr.nullslast()).limit(page_size).offset((page - 1) * page_size)
    )
    page_rows = rows_result.all()
    breakdown = await _score_breakdown(db, [attempt.id for attempt, _, _ in page_rows])

    items = [
        AttemptListItem(
            attempt_id=attempt.id,
            student_id=student.student_id,
            student_name=student.name,
            email=student.email,
            cohort=student.cohort,
            exam_id=exam.id,
            exam_title=exam.title,
            status=attempt.status,
            total_score=attempt.total_score,
            max_score=attempt.max_score,
            percentage=_percentage(attempt),
            **breakdown[attempt.id],
            started_at=attempt.started_at,
            submitted_at=attempt.submitted_at,
            time_taken_minutes=_duration_minutes(attempt),
        )
        for attempt, student, exam in page_rows
    ]

    return AttemptListPage(items=items, total=total, page=page, page_size=page_size)


@router.get("/attempts/export.xlsx")
async def export_attempts(
    admin: AdminDep,
    db: DbDep,
    search: str | None = None,
    cohort: str | None = None,
    exam_id: uuid.UUID | None = None,
    status_filter: Annotated[
        Literal["completed", "pending"] | None, Query(alias="status")
    ] = None,
    min_score: float | None = None,
    max_score: float | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sort: AttemptSort = "submitted_at",
    order: Literal["asc", "desc"] = "desc",
) -> Response:
    """Exports every attempt matching the same filters as `/attempts`, ignoring
    pagination — the filtered result set, not just the visible page."""
    conditions = _attempt_conditions(
        search, cohort, exam_id, status_filter, min_score, max_score, date_from, date_to
    )
    order_expr = _attempt_sort_expr(sort, order)
    filename, content = await export.build_attempts_workbook(db, conditions, order_expr)
    # Same reasoning as the per-exam export: bulk extraction of scored attempts,
    # so the filter shape is recorded (never the search text, which is free-form
    # and may contain a student's name or address).
    log.info(
        "attempts exported",
        extra={
            "admin_id": str(admin.id),
            "exam_id": str(exam_id) if exam_id else None,
            "cohort": cohort,
            "filter_count": len(conditions),
            "size_bytes": len(content),
        },
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/attempts/{attempt_id}/overview", response_model=AttemptOverview)
async def attempt_overview(attempt_id: uuid.UUID, admin: AdminDep, db: DbDep) -> AttemptOverview:
    attempt = await _get_attempt(db, attempt_id)
    student = await db.get(Student, attempt.student_id)
    exam = await db.get(Exam, attempt.exam_id)

    pct = _percentage(attempt)

    rank = None
    if attempt.status is not AttemptStatus.in_progress and attempt.total_score is not None:
        better = await db.scalar(
            select(func.count()).where(
                ExamAttempt.exam_id == attempt.exam_id,
                ExamAttempt.status != AttemptStatus.in_progress,
                ExamAttempt.total_score.isnot(None),
                ExamAttempt.total_score > attempt.total_score,
            )
        )
        rank = (better or 0) + 1

    passed = None
    if exam.pass_percentage is not None and pct is not None:
        passed = pct >= exam.pass_percentage

    sections_result = await db.execute(
        select(Section).where(Section.exam_id == attempt.exam_id).order_by(Section.order_index)
    )
    sections = list(sections_result.scalars())

    agg = {s.id: {"score": 0.0, "max": 0.0} for s in sections}
    if sections:
        rows_result = await db.execute(
            select(Question.section_id, Answer.score, Question.marks, Section.marks_per_question)
            .join(Section, Question.section_id == Section.id)
            .outerjoin(
                Answer, and_(Answer.question_id == Question.id, Answer.attempt_id == attempt.id)
            )
            .where(Section.exam_id == attempt.exam_id)
        )
        for section_id, score, marks, marks_per_question in rows_result.all():
            bucket = agg[section_id]
            bucket["score"] += score or 0.0
            bucket["max"] += marks if marks is not None else marks_per_question

    section_scores = [
        SectionScore(
            section_id=s.id,
            title=s.title,
            score=round(agg[s.id]["score"], 2),
            max_score=round(agg[s.id]["max"], 2),
        )
        for s in sections
    ]

    return AttemptOverview(
        attempt_id=attempt.id,
        student_id=student.student_id,
        student_name=student.name,
        email=student.email,
        cohort=student.cohort,
        exam_id=exam.id,
        exam_title=exam.title,
        status=attempt.status,
        total_score=attempt.total_score,
        max_score=attempt.max_score,
        percentage=pct,
        rank=rank,
        passed=passed,
        time_taken_minutes=_duration_minutes(attempt),
        started_at=attempt.started_at,
        submitted_at=attempt.submitted_at,
        focus_loss_count=attempt.focus_loss_count,
        sections=section_scores,
    )


@router.get("/attempts/{attempt_id}/questions", response_model=list[QuestionReview])
async def attempt_questions(
    attempt_id: uuid.UUID, admin: AdminDep, db: DbDep
) -> list[QuestionReview]:
    attempt = await _get_attempt(db, attempt_id)
    exam = await paper.load_exam_tree(db, attempt.exam_id)
    if exam is None:
        # The attempt row survived its exam — a dangling FK, not a bad request.
        log.error(
            "attempt references a missing exam",
            extra={"attempt_id": str(attempt_id), "exam_id": str(attempt.exam_id)},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")

    answers_result = await db.execute(select(Answer).where(Answer.attempt_id == attempt.id))
    answers = {a.question_id: a for a in answers_result.scalars()}

    submissions_result = await db.execute(
        select(CodingSubmission).where(CodingSubmission.attempt_id == attempt.id)
    )
    submissions = {s.question_id: s for s in submissions_result.scalars()}

    reviews: list[QuestionReview] = []
    for section in sorted(exam.sections, key=lambda s: s.order_index):
        di_groups_by_id = {g.id: g for g in section.di_groups}
        for question in sorted(section.questions, key=lambda q: q.order_index):
            marks = question.marks if question.marks is not None else section.marks_per_question
            negative = (
                question.negative_marks
                if question.negative_marks is not None
                else section.negative_marks
            )
            answer = answers.get(question.id)
            marks_awarded = round(answer.score, 4) if answer else 0.0

            di_context = None
            if question.di_group_id and question.di_group_id in di_groups_by_id:
                group = di_groups_by_id[question.di_group_id]
                di_context = DiContext(
                    title=group.title, passage_md=group.passage_md, image_url=group.image_url
                )

            coding_review: CodingReview | None = None
            student_answer: str | None = None
            correct_answer: str | None = None
            q_status: Literal["correct", "incorrect", "skipped", "pending"]

            if question.type is QuestionType.coding:
                submission = submissions.get(question.id)
                if answer is None or not (answer.code_text or "").strip():
                    q_status = "skipped"
                else:
                    # A coding answer has no correct/incorrect until an admin
                    # finalizes it. Reporting "incorrect" in the meantime — which
                    # is what the old judge-backed logic did, since is_correct is
                    # NULL until then — makes every ungraded submission look wrong.
                    if submission is None or submission.status is not EvaluationStatus.finalized:
                        q_status = "pending"
                    else:
                        q_status = "correct" if answer.is_correct else "incorrect"
                    coding_review = CodingReview(
                        submission_id=submission.id if submission else None,
                        language=answer.language or "python",
                        code_text=answer.code_text,
                        status=submission.status if submission else None,
                        ai_score=submission.ai_score if submission else None,
                        final_score=submission.final_score if submission else None,
                        max_marks=submission.max_marks if submission else marks,
                        manual_review_recommended=_needs_review(submission),
                    )
            else:
                options = {o.id: o.body for o in question.options}
                correct_ids = [o.id for o in question.options if o.is_correct]
                correct_answer = "; ".join(options[i] for i in correct_ids if i in options) or None
                if answer is None or answer.selected_option_id is None:
                    q_status = "skipped"
                else:
                    student_answer = options.get(answer.selected_option_id)
                    q_status = "correct" if answer.is_correct else "incorrect"

            reviews.append(
                QuestionReview(
                    question_id=question.id,
                    type=question.type,
                    order_index=question.order_index,
                    body_md=question.body_md,
                    marks=marks,
                    negative_marks=negative,
                    status=q_status,
                    student_answer=student_answer,
                    correct_answer=correct_answer,
                    marks_awarded=marks_awarded,
                    explanation_md=question.explanation_md,
                    di_context=di_context,
                    coding=coding_review,
                )
            )

    return reviews


@router.get("/attempts/{attempt_id}/activity", response_model=ActivityTimeline)
async def attempt_activity(attempt_id: uuid.UUID, admin: AdminDep, db: DbDep) -> ActivityTimeline:
    """Chronological trail of what a student did during an attempt, for invigilation
    review. tab_switch is an aggregate count only — there are no per-event timestamps —
    so it's surfaced as a single synthetic entry, not a real-time series."""
    attempt = await _get_attempt(db, attempt_id)

    events: list[ActivityEvent] = [
        ActivityEvent(type="started", timestamp=attempt.started_at, label="Exam started")
    ]

    answers_result = await db.execute(
        select(Answer, Question.order_index)
        .join(Question, Answer.question_id == Question.id)
        .where(Answer.attempt_id == attempt_id)
    )
    for answer, order_index in answers_result.all():
        events.append(
            ActivityEvent(
                type="answered",
                timestamp=answer.updated_at,
                label=f"Answered Q{order_index + 1}",
            )
        )

    submissions_result = await db.execute(
        select(CodingSubmission)
        .where(CodingSubmission.attempt_id == attempt_id)
        .order_by(CodingSubmission.submitted_at)
    )
    for submission in submissions_result.scalars():
        events.append(
            ActivityEvent(
                type="code_submitted",
                timestamp=submission.submitted_at,
                label=f"Submitted code ({submission.language})",
                detail=f"Evaluation: {submission.status.value}",
            )
        )

    if attempt.focus_loss_count > 0:
        events.append(
            ActivityEvent(
                type="tab_switch",
                timestamp=attempt.started_at,
                label=f"{attempt.focus_loss_count} tab switch event(s) recorded",
                detail="Aggregate count only — no per-event timestamps are tracked",
            )
        )

    if attempt.submitted_at:
        events.append(
            ActivityEvent(
                type="submitted",
                timestamp=attempt.submitted_at,
                label=f"Exam {attempt.status.value}",
            )
        )

    events.sort(key=lambda e: e.timestamp)
    return ActivityTimeline(events=events)


# --------------------------------------------------------- coding evaluation
# The AI recommendation is advisory. Nothing here lets an admin's edit overwrite
# an ai_* column, and only an explicit finalize turns a score into a mark.


def _needs_review(submission: CodingSubmission | None) -> bool:
    """Whether the review screen should show "Manual Review Recommended". An AI
    failure always needs a human; otherwise it's the model's own flag, which the
    evaluator already raises for low confidence."""
    if submission is None:
        return False
    if submission.status is EvaluationStatus.ai_failed:
        return True
    return submission.ai_requires_manual_review


async def _get_submission(db: AsyncSession, submission_id: uuid.UUID) -> CodingSubmission:
    submission = await db.get(CodingSubmission, submission_id)
    if submission is None:
        log.warning(
            "admin lookup failed",
            extra={"entity": "coding_submission", "submission_id": str(submission_id)},
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coding submission not found")
    return submission


@router.get("/coding-submissions", response_model=CodingSubmissionPage)
async def list_coding_submissions(
    admin: AdminDep,
    db: DbDep,
    exam_id: uuid.UUID | None = None,
    status_filter: Annotated[EvaluationStatus | None, Query(alias="status")] = None,
    needs_review: bool = False,
    search: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> CodingSubmissionPage:
    """The coding evaluation queue. Newest-first, so whatever an examiner most
    likely still owes a mark on is at the top."""
    query = (
        select(CodingSubmission, Student, Exam)
        .join(Student, CodingSubmission.student_id == Student.id)
        .join(ExamAttempt, CodingSubmission.attempt_id == ExamAttempt.id)
        .join(Exam, ExamAttempt.exam_id == Exam.id)
    )
    if exam_id is not None:
        query = query.where(ExamAttempt.exam_id == exam_id)
    if status_filter is not None:
        query = query.where(CodingSubmission.status == status_filter)
    if needs_review:
        query = query.where(
            or_(
                CodingSubmission.ai_requires_manual_review.is_(True),
                CodingSubmission.status == EvaluationStatus.ai_failed,
            )
        )
    if search:
        like = f"%{search}%"
        query = query.where(or_(Student.name.ilike(like), Student.student_id.ilike(like)))

    total = await db.scalar(select(func.count()).select_from(query.subquery())) or 0

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    rows = await db.execute(
        query.order_by(CodingSubmission.submitted_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rubric_max = get_rubric().max_marks

    return CodingSubmissionPage(
        items=[
            CodingSubmissionListItem(
                id=submission.id,
                attempt_id=submission.attempt_id,
                question_id=submission.question_id,
                student_name=student.name,
                student_number=student.student_id,
                exam_id=exam.id,
                exam_title=exam.title,
                language=submission.language,
                submitted_at=submission.submitted_at,
                status=submission.status,
                max_marks=submission.max_marks,
                ai_score=submission.ai_score,
                rubric_max_marks=rubric_max,
                final_score=submission.final_score,
                manual_review_recommended=_needs_review(submission),
            )
            for submission, student, exam in rows.all()
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


async def _submission_detail(db: AsyncSession, submission: CodingSubmission) -> CodingSubmissionOut:
    student = await db.get(Student, submission.student_id)
    question = await db.get(Question, submission.question_id)
    problem = await db.scalar(
        select(CodingProblem).where(CodingProblem.question_id == submission.question_id)
    )
    attempt = await db.get(ExamAttempt, submission.attempt_id)
    exam = await db.get(Exam, attempt.exam_id) if attempt else None

    rubric = get_rubric()
    awarded = submission.ai_rubric_scores or {}
    justifications = submission.ai_rubric_justifications or {}

    out = CodingSubmissionOut.model_validate(submission)
    out.student_name = student.name if student else ""
    out.student_number = student.student_id if student else ""
    out.exam_title = exam.title if exam else ""
    out.question_body_md = question.body_md if question else ""
    out.statement_md = problem.statement_md if problem else ""
    out.constraints_md = problem.constraints_md if problem else None
    out.rubric_max_marks = rubric.max_marks
    out.rubric = [
        RubricCriterionOut(
            key=c.key,
            description=c.description,
            max_marks=c.marks,
            awarded=awarded.get(c.key),
            justification=justifications.get(c.key),
        )
        for c in rubric.criteria
    ]
    out.manual_review_recommended = _needs_review(submission)
    return out


@router.get("/coding-submissions/{submission_id}", response_model=CodingSubmissionOut)
async def get_coding_submission(
    submission_id: uuid.UUID, admin: AdminDep, db: DbDep
) -> CodingSubmissionOut:
    return await _submission_detail(db, await _get_submission(db, submission_id))


@router.patch("/coding-submissions/{submission_id}", response_model=CodingSubmissionOut)
async def grade_coding_submission(
    submission_id: uuid.UUID, payload: CodingGradeRequest, admin: AdminDep, db: DbDep
) -> CodingSubmissionOut:
    """Save or finalize an admin's mark. The AI's recommendation is untouched by
    this — ai_score and the rubric breakdown stay exactly as the evaluator wrote
    them, so the original recommendation stays auditable after any override."""
    submission = await _get_submission(db, submission_id)

    if payload.final_score > submission.max_marks + 1e-6:
        log.warning(
            "coding grade rejected",
            extra={
                "submission_id": str(submission_id),
                "reason": "score_exceeds_max",
                "requested_score": payload.final_score,
                "max_marks": submission.max_marks,
            },
        )
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Final score cannot exceed this question's {submission.max_marks:g} marks",
        )

    submission.final_score = float(payload.final_score)
    submission.admin_comment = payload.admin_comment
    submission.admin_id = admin.id

    if payload.finalize:
        submission.status = EvaluationStatus.finalized
        submission.finalized_at = datetime.now(UTC)
    else:
        # A saved-but-not-finalized score must not count toward the exam total.
        submission.status = EvaluationStatus.admin_reviewed
        submission.finalized_at = None
    await db.flush()

    if payload.finalize:
        await coding_service.apply_final_score(db, submission)
    else:
        # Un-finalizing has to pull the mark back out of the attempt total.
        await coding_service.recompute_attempt_total(db, submission.attempt_id)

    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="coding_score_finalized" if payload.finalize else "coding_score_saved",
            target=str(submission_id),
            meta={"final_score": submission.final_score, "ai_score": submission.ai_score},
        )
    )
    return await _submission_detail(db, submission)


@router.post("/coding-submissions/{submission_id}/re-evaluate", response_model=CodingSubmissionOut)
async def reevaluate_coding_submission(
    submission_id: uuid.UUID, admin: AdminDep, db: DbDep
) -> CodingSubmissionOut:
    """Re-queue a failed, never-queued, or stuck evaluation — e.g. after an OpenAI
    outage. Refused once a human has reviewed the submission, so a retry can never
    clobber an examiner's decision.

    ai_evaluating is deliberately re-queueable: a worker killed mid-call leaves the
    row there permanently (no redelivery can reclaim it, since the claim already
    succeeded), and this is its only recovery. If the original call is somehow still
    alive, the loser's write is rejected by the same status check that guards the
    claim, so the worst case is a wasted API call rather than a corrupted row."""
    submission = await _get_submission(db, submission_id)
    if submission.status in (EvaluationStatus.admin_reviewed, EvaluationStatus.finalized):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot re-evaluate a submission that is {submission.status.value}",
        )

    submission.status = EvaluationStatus.pending
    submission.ai_error = None
    # Commit before enqueueing so the worker's own connection sees the reset.
    await db.commit()

    from app.tasks.ai_tasks import evaluate_coding_submission

    try:
        evaluate_coding_submission.delay(str(submission.id))
    except Exception as exc:
        # The status reset above is already committed, so the row now sits at
        # PENDING with nothing scheduled to pick it up. The admin sees a 503 and
        # can retry, but only this line records that the queue, not the
        # evaluator, is what broke.
        log.exception(
            "could not queue re-evaluation; submission left pending",
            extra={"submission_id": str(submission.id)},
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Could not queue the evaluation"
        ) from exc

    log.info(
        "coding submission re-evaluation queued",
        extra={"submission_id": str(submission.id), "attempt_id": str(submission.attempt_id)},
    )
    return await _submission_detail(db, submission)
