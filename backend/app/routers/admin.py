from __future__ import annotations

import csv
import io
import statistics
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import cache
from app.config import settings
from app.db import get_db
from app.deps import current_admin
from app.models import (
    Admin,
    Answer,
    AttemptStatus,
    AuditLog,
    CodingProblem,
    DIGroup,
    Exam,
    ExamAttempt,
    ExamStatus,
    JudgeMode,
    JudgeRun,
    JudgeStatus,
    MCQOption,
    Question,
    QuestionType,
    Section,
    Student,
    TestCase,
)
from app.schemas import (
    ActivityEvent,
    ActivityTimeline,
    AnalyticsOut,
    AttemptListItem,
    AttemptListPage,
    AttemptOverview,
    BulkMagicLinkQueued,
    BulkMagicLinkRequest,
    BulkResult,
    CodingCaseReview,
    CodingCreate,
    CodingProblemAdminOut,
    CodingReview,
    CodingUpdate,
    DIGroupAdminOut,
    DIGroupCreate,
    DIGroupUpdate,
    DiContext,
    ExamCreate,
    ExamOut,
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
    SectionScore,
    StudentCreate,
    StudentOut,
    StudentUpdate,
    TestCaseAdminOut,
)
from app.security import create_magic_token
from app.services import export, paper
from app.services.email import send_magic_link_email

router = APIRouter(prefix="/api/admin", tags=["admin"])

AdminDep = Annotated[Admin, Depends(current_admin)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


async def _get_exam(db: AsyncSession, exam_id: uuid.UUID) -> Exam:
    exam = await db.get(Exam, exam_id)
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    return exam


async def _get_section(db: AsyncSession, section_id: uuid.UUID) -> Section:
    section = await db.get(Section, section_id)
    if section is None:
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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(exam, field, value)
    await db.flush()
    return ExamOut.model_validate(exam)


@router.delete("/exams/{exam_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exam(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> None:
    exam = await _get_exam(db, exam_id)
    attempts = await db.scalar(
        select(func.count(ExamAttempt.id)).where(ExamAttempt.exam_id == exam_id)
    )
    if attempts:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Exam has attempts and cannot be deleted; close it instead"
        )
    await db.delete(exam)


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
            allowed_languages=list(problem.allowed_languages),
            time_limit_ms=problem.time_limit_ms,
            memory_limit_mb=problem.memory_limit_mb,
            starter_code=dict(problem.starter_code or {}),
            test_cases=[
                TestCaseAdminOut(
                    id=tc.id,
                    stdin=tc.stdin,
                    expected_stdout=tc.expected_stdout,
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
        .options(selectinload(DIGroup.questions).selectinload(Question.options))
        .order_by(DIGroup.order_index)
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
    return {"status": "ok"}


@router.get("/question-bank", response_model=QuestionBankPage)
async def question_bank(
    admin: AdminDep,
    db: DbDep,
    search: str | None = None,
    type: QuestionType | None = None,
    difficulty: str | None = None,
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
            body_preview=(q.body_md[:140] + "…") if len(q.body_md) > 140 else q.body_md,
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
            allowed_languages=list(source_problem.allowed_languages),
            time_limit_ms=source_problem.time_limit_ms,
            memory_limit_mb=source_problem.memory_limit_mb,
            starter_code=dict(source_problem.starter_code or {}),
        )
        db.add(new_problem)
        await db.flush()
        for tc in source_problem.test_cases:
            db.add(
                TestCase(
                    coding_problem_id=new_problem.id,
                    stdin=tc.stdin,
                    expected_stdout=tc.expected_stdout,
                    is_sample=tc.is_sample,
                    weight=tc.weight,
                    order_index=tc.order_index,
                )
            )

    await db.flush()
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
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Each DI question must have exactly one correct option",
            )

    group = DIGroup(
        section_id=section_id,
        title=payload.title,
        passage_md=payload.passage_md,
        image_url=payload.image_url,
        order_index=payload.order_index,
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

    return {"di_group_id": str(group.id), "question_ids": question_ids}


@router.post("/uploads/image", status_code=status.HTTP_201_CREATED)
async def upload_image(
    admin: AdminDep,
    file: Annotated[UploadFile, File()],
    label: Annotated[str, Form()] = "di",
) -> dict[str, str]:
    """Stores a DI image and returns the URL to put on the DI group."""
    allowed = {"image/png", "image/jpeg", "image/webp", "image/gif"}
    if file.content_type not in allowed:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Allowed types: {', '.join(sorted(allowed))}"
        )
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Image exceeds {settings.max_upload_mb} MB",
        )

    suffix = Path(file.filename or "image.png").suffix.lower() or ".png"
    name = f"{label}-{uuid.uuid4().hex}{suffix}"
    target_dir = Path(settings.upload_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_bytes(data)
    return {"image_url": f"/uploads/{name}", "filename": name}


@router.post("/sections/{section_id}/coding", status_code=status.HTTP_201_CREATED)
async def create_coding_problem(
    section_id: uuid.UUID, payload: CodingCreate, admin: AdminDep, db: DbDep
) -> dict:
    section = await _get_section(db, section_id)
    await _assert_editable(db, section.exam_id)

    if not any(tc.is_sample for tc in payload.test_cases):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one sample test case so students can run their code",
        )
    if not any(not tc.is_sample for tc in payload.test_cases):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one hidden test case; sample-only grading is trivially gamed",
        )

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
        allowed_languages=payload.allowed_languages,
        time_limit_ms=payload.time_limit_ms,
        memory_limit_mb=payload.memory_limit_mb,
        starter_code=payload.starter_code,
    )
    db.add(problem)
    await db.flush()

    for idx, tc in enumerate(payload.test_cases):
        db.add(
            TestCase(
                coding_problem_id=problem.id,
                stdin=tc.stdin,
                expected_stdout=tc.expected_stdout,
                is_sample=tc.is_sample,
                weight=tc.weight,
                order_index=tc.order_index or idx,
            )
        )
    await db.flush()
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "MCQ/DI question not found")
    section = await _get_section(db, question.section_id)
    await _assert_editable(db, section.exam_id)

    correct = [o for o in payload.options if o.is_correct]
    if len(correct) != 1:
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
    return _question_detail(await _get_question_detailed(db, question.id))


@router.patch("/questions/{question_id}/coding", response_model=QuestionDetailOut)
async def update_coding(
    question_id: uuid.UUID, payload: CodingUpdate, admin: AdminDep, db: DbDep
) -> QuestionDetailOut:
    question = await db.get(Question, question_id)
    if question is None or question.type is not QuestionType.coding:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coding question not found")
    section = await _get_section(db, question.section_id)
    await _assert_editable(db, section.exam_id)

    problem = await db.scalar(
        select(CodingProblem).where(CodingProblem.question_id == question.id)
    )
    if problem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coding problem not found")

    if not any(tc.is_sample for tc in payload.test_cases):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one sample test case so students can run their code",
        )
    if not any(not tc.is_sample for tc in payload.test_cases):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Provide at least one hidden test case; sample-only grading is trivially gamed",
        )

    question.body_md = payload.body_md
    question.marks = payload.marks
    question.meta = {"tags": payload.tags, "difficulty": payload.difficulty}

    problem.statement_md = payload.statement_md
    problem.allowed_languages = payload.allowed_languages
    problem.time_limit_ms = payload.time_limit_ms
    problem.memory_limit_mb = payload.memory_limit_mb
    problem.starter_code = payload.starter_code

    await db.execute(delete(TestCase).where(TestCase.coding_problem_id == problem.id))
    for idx, tc in enumerate(payload.test_cases):
        db.add(
            TestCase(
                coding_problem_id=problem.id,
                stdin=tc.stdin,
                expected_stdout=tc.expected_stdout,
                is_sample=tc.is_sample,
                weight=tc.weight,
                order_index=tc.order_index or idx,
            )
        )
    await db.flush()
    return _question_detail(await _get_question_detailed(db, question.id))


@router.patch("/di-groups/{group_id}", response_model=DIGroupAdminOut)
async def update_di_group(
    group_id: uuid.UUID, payload: DIGroupUpdate, admin: AdminDep, db: DbDep
) -> DIGroupAdminOut:
    """Edits the shared stimulus (title/passage/image). Child questions are edited
    individually via PATCH /questions/{id}/mcq, same as any other MCQ-shaped question."""
    group = await db.get(DIGroup, group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DI group not found")
    section = await _get_section(db, group.section_id)
    await _assert_editable(db, section.exam_id)

    group.title = payload.title
    group.passage_md = payload.passage_md
    group.image_url = payload.image_url
    await db.flush()

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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "DI group not found")
    section = await _get_section(db, group.section_id)
    await _assert_editable(db, section.exam_id)

    children = await db.execute(select(Question).where(Question.di_group_id == group_id))
    for child in children.scalars():
        await db.delete(child)
    await db.flush()
    await db.delete(group)


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(question_id: uuid.UUID, admin: AdminDep, db: DbDep) -> None:
    question = await db.get(Question, question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    section = await _get_section(db, question.section_id)
    await _assert_editable(db, section.exam_id)
    await db.delete(question)


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
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Enrollment ID {payload.student_id} or email {payload.email} already exists",
        ) from None
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Student not found")
    return student


@router.patch("/students/{student_id}", response_model=StudentOut)
async def update_student(
    student_id: uuid.UUID, payload: StudentUpdate, admin: AdminDep, db: DbDep
) -> StudentOut:
    student = await _get_student(db, student_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(student, field, value)
    await db.flush()
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
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Could not send the email") from exc
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
    return BulkMagicLinkQueued(queued=len(ids))


# ----------------------------------------------------------------- publish


@router.post("/exams/{exam_id}/publish", response_model=PublishResult)
async def publish_exam(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> PublishResult:
    """Validates the whole paper before flipping status. Catching a malformed question
    here is the difference between a bad question and a cancelled exam."""
    exam = await paper.load_exam_tree(db, exam_id)
    if exam is None:
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
                hidden = [tc for tc in problem.test_cases if not tc.is_sample]
                if not samples:
                    problems.append(f"{label}: needs at least one sample test case")
                if not hidden:
                    problems.append(f"{label}: needs at least one hidden test case")
                if not problem.allowed_languages:
                    problems.append(f"{label}: no languages allowed")

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
    return ExamOut.model_validate(exam)


# ------------------------------------------------------------- monitoring


@router.get("/exams/{exam_id}/live", response_model=LiveMonitorOut)
async def live_monitor(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> LiveMonitorOut:
    """Polled every ~5 s by the dashboard. One query per panel, no per-student N+1."""
    exam = await _get_exam(db, exam_id)

    students_query = select(Student).order_by(Student.student_id)
    if exam.cohort:
        students_query = students_query.where(Student.cohort == exam.cohort)
    students_result = await db.execute(students_query)
    students = list(students_result.scalars())

    attempts_result = await db.execute(
        select(ExamAttempt).where(ExamAttempt.exam_id == exam_id)
    )
    attempts = {a.student_id: a for a in attempts_result.scalars()}

    counts_result = await db.execute(
        select(Answer.attempt_id, func.count(Answer.id))
        .join(ExamAttempt, Answer.attempt_id == ExamAttempt.id)
        .where(
            ExamAttempt.exam_id == exam_id,
            (Answer.selected_option_id.isnot(None)) | (Answer.code_text.isnot(None)),
        )
        .group_by(Answer.attempt_id)
    )
    answered = dict(counts_result.all())

    now = datetime.now(UTC)
    rows: list[LiveStudentRow] = []
    not_started = in_progress = submitted = 0

    for student in students:
        attempt = attempts.get(student.id)
        if attempt is None:
            not_started += 1
            rows.append(
                LiveStudentRow(
                    student_id=student.student_id,
                    name=student.name,
                    attempt_status=None,
                    started_at=None,
                    seconds_remaining=None,
                    answered_count=0,
                    focus_loss_count=0,
                    submitted_at=None,
                )
            )
            continue

        if attempt.status is AttemptStatus.in_progress:
            in_progress += 1
            deadline = attempt.deadline_at
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=UTC)
            remaining = max(0, int((deadline - now).total_seconds()))
        else:
            submitted += 1
            remaining = None

        rows.append(
            LiveStudentRow(
                attempt_id=attempt.id,
                student_id=student.student_id,
                name=student.name,
                attempt_status=attempt.status,
                started_at=attempt.started_at,
                seconds_remaining=remaining,
                # Buffered answers aren't flushed yet, so this can lag by one flush interval.
                answered_count=answered.get(attempt.id, 0),
                focus_loss_count=attempt.focus_loss_count,
                submitted_at=attempt.submitted_at,
            )
        )

    return LiveMonitorOut(
        exam_id=exam_id,
        title=exam.title,
        not_started=not_started,
        in_progress=in_progress,
        submitted=submitted,
        total_students=len(students),
        server_time=now,
        rows=rows,
    )


@router.post("/attempts/{attempt_id}/force-submit", status_code=status.HTTP_200_OK)
async def force_submit(attempt_id: uuid.UUID, admin: AdminDep, db: DbDep) -> dict[str, str]:
    """Invigilator override — e.g. a student caught cheating, or a stuck client."""
    attempt = await db.get(ExamAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found")
    if attempt.status is not AttemptStatus.in_progress:
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
    await cache.clear_attempt(str(attempt.id))
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=admin.id,
            action="attempt_force_submitted",
            target=str(attempt_id),
        )
    )
    return {"status": attempt.status.value, "detail": "Attempt submitted"}


@router.post("/attempts/{attempt_id}/extend", status_code=status.HTTP_200_OK)
async def extend_time(
    attempt_id: uuid.UUID, admin: AdminDep, db: DbDep, minutes: int = 5
) -> dict[str, str]:
    """Grants extra time for a documented technical issue. Written to the audit log
    because time grants are the most abuse-prone admin action."""
    if not 1 <= minutes <= 60:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "minutes must be between 1 and 60")
    attempt = await db.get(ExamAttempt, attempt_id)
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found")
    if attempt.status is not AttemptStatus.in_progress:
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
    return {"deadline_at": attempt.deadline_at.isoformat()}


# -------------------------------------------------------- results & analytics


@router.get("/exams/{exam_id}/results.xlsx")
async def export_results(exam_id: uuid.UUID, admin: AdminDep, db: DbDep) -> Response:
    filename, content = await export.build_results_workbook(db, exam_id)
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
            body_preview=(body or "")[:120],
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


AttemptSort = Literal["student_name", "exam_title", "score", "percentage", "started_at", "submitted_at"]


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


def _attempt_sort_expr(sort: AttemptSort, order: Literal["asc", "desc"]):
    sort_columns = {
        "student_name": Student.name,
        "exam_title": Exam.title,
        "score": ExamAttempt.total_score,
        "percentage": ExamAttempt.total_score / func.nullif(ExamAttempt.max_score, 0),
        "started_at": ExamAttempt.started_at,
        "submitted_at": ExamAttempt.submitted_at,
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
            started_at=attempt.started_at,
            submitted_at=attempt.submitted_at,
            time_taken_minutes=_duration_minutes(attempt),
        )
        for attempt, student, exam in rows_result.all()
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")

    answers_result = await db.execute(select(Answer).where(Answer.attempt_id == attempt.id))
    answers = {a.question_id: a for a in answers_result.scalars()}

    judge_result = await db.execute(
        select(JudgeRun)
        .where(JudgeRun.attempt_id == attempt.id, JudgeRun.mode == JudgeMode.final)
        .order_by(JudgeRun.created_at.desc())
    )
    judge_runs: dict[uuid.UUID, JudgeRun] = {}
    for run in judge_result.scalars():
        judge_runs.setdefault(run.question_id, run)  # first (latest) wins per question

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
            q_status: Literal["correct", "incorrect", "skipped"]

            if question.type is QuestionType.coding:
                if answer is None or not answer.code_text:
                    q_status = "skipped"
                else:
                    q_status = "correct" if answer.is_correct else "incorrect"
                    run = judge_runs.get(question.id)
                    coding_review = CodingReview(
                        language=answer.language or "python",
                        code_text=answer.code_text,
                        status=run.status if run else JudgeStatus.queued,
                        passed=run.passed if run else 0,
                        total=run.total if run else 0,
                        score=run.score if run else 0.0,
                        cases=[CodingCaseReview(**c) for c in (run.results if run else [])],
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

    runs_result = await db.execute(
        select(JudgeRun).where(JudgeRun.attempt_id == attempt_id).order_by(JudgeRun.created_at)
    )
    for run in runs_result.scalars():
        events.append(
            ActivityEvent(
                type="code_run",
                timestamp=run.created_at,
                label=f"Ran code ({run.language})",
                detail=f"{run.passed}/{run.total} passed" if run.total else run.status.value,
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
