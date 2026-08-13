from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- enums


class QuestionType(str, enum.Enum):
    mcq = "mcq"
    coding = "coding"
    di = "di"


class ExamStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    closed = "closed"


class AttemptStatus(str, enum.Enum):
    in_progress = "in_progress"
    submitted = "submitted"
    auto_submitted = "auto_submitted"
    expired = "expired"


class JudgeStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    done = "done"
    error = "error"


class JudgeMode(str, enum.Enum):
    sample = "sample"
    final = "final"
    # Ad-hoc run against student-typed input with no expected output to grade against.
    custom = "custom"


class ProblemType(str, enum.Enum):
    # The student's code is the whole program: it reads stdin, prints stdout.
    stdio = "stdio"
    # The student writes only a function body; the judge supplies a generated
    # driver that decodes structured parameters, calls it, and encodes the result.
    function = "function"


# --------------------------------------------------------------------------- users


class Admin(Base, TimestampMixin):
    __tablename__ = "admins"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="admin", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Student(Base, TimestampMixin):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    student_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Required and unique: it's the magic-link login identity, not just contact info.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    cohort: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    attempts: Mapped[list[ExamAttempt]] = relationship(back_populates="student")


# --------------------------------------------------------------------------- exam structure


class Exam(Base, TimestampMixin):
    __tablename__ = "exams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    status: Mapped[ExamStatus] = mapped_column(
        Enum(ExamStatus, name="exam_status"), default=ExamStatus.draft, nullable=False, index=True
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    randomize_questions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    randomize_options: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # NULL => no pass/fail badge is shown anywhere; admin hasn't set a threshold
    pass_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    # NULL cohort => open to every student
    cohort: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )
    requires_seb: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # SHA-256 hex Config Key shown by the SEB Config Tool once a .seb file's settings
    # are finalized. Only meaningful when requires_seb is true.
    seb_config_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    sections: Mapped[list[Section]] = relationship(
        back_populates="exam", cascade="all, delete-orphan", order_by="Section.order_index"
    )


class Section(Base, TimestampMixin):
    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    marks_per_question: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    negative_marks: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    exam: Mapped[Exam] = relationship(back_populates="sections")
    questions: Mapped[list[Question]] = relationship(
        back_populates="section", cascade="all, delete-orphan", order_by="Question.order_index"
    )
    di_groups: Mapped[list[DIGroup]] = relationship(
        back_populates="section", cascade="all, delete-orphan", order_by="DIGroup.order_index"
    )


class DIGroup(Base, TimestampMixin):
    """A Data Interpretation stimulus: one image/passage shared by several questions."""

    __tablename__ = "di_groups"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    passage_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    section: Mapped[Section] = relationship(back_populates="di_groups")
    questions: Mapped[list[Question]] = relationship(
        back_populates="di_group", order_by="Question.order_index"
    )


class Question(Base, TimestampMixin):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    di_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("di_groups.id", ondelete="CASCADE"), nullable=True, index=True
    )
    type: Mapped[QuestionType] = mapped_column(
        Enum(QuestionType, name="question_type"), nullable=False
    )
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    explanation_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    marks: Mapped[float | None] = mapped_column(Float, nullable=True)
    negative_marks: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    section: Mapped[Section] = relationship(back_populates="questions")
    di_group: Mapped[DIGroup | None] = relationship(back_populates="questions")
    options: Mapped[list[MCQOption]] = relationship(
        back_populates="question", cascade="all, delete-orphan", order_by="MCQOption.order_index"
    )
    coding_problem: Mapped[CodingProblem | None] = relationship(
        back_populates="question", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (Index("ix_questions_section_order", "section_id", "order_index"),)

    # Both properties fall back through self.section, so they lazy-load that
    # relationship. Only safe when the caller loaded it eagerly
    # (selectinload(Question.section)) or is on a sync session — otherwise they raise
    # MissingGreenlet inside an async request. Async code paths pass the section in
    # explicitly instead; see services/paper.py.
    @property
    def effective_marks(self) -> float:
        return self.marks if self.marks is not None else self.section.marks_per_question

    @property
    def effective_negative(self) -> float:
        return self.negative_marks if self.negative_marks is not None else self.section.negative_marks


class MCQOption(Base):
    __tablename__ = "mcq_options"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    question: Mapped[Question] = relationship(back_populates="options")


class CodingProblem(Base, TimestampMixin):
    __tablename__ = "coding_problems"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("questions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    statement_md: Mapped[str] = mapped_column(Text, nullable=False)
    constraints_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    allowed_languages: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=lambda: ["python", "java", "cpp"], nullable=False
    )
    time_limit_ms: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    memory_limit_mb: Mapped[int] = mapped_column(Integer, default=256, nullable=False)
    starter_code: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    problem_type: Mapped[ProblemType] = mapped_column(
        Enum(ProblemType, name="problem_type"),
        default=ProblemType.stdio,
        server_default=ProblemType.stdio.value,
        nullable=False,
    )
    # Only set (and only meaningful) when problem_type is "function".
    function_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    return_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Ordered [{"name": str, "type": str}, ...] — see app.services.harness.PARAM_TYPES
    # for the valid type strings.
    parameters: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # SQL-only fields below. Only meaningful when "sql" is in allowed_languages.
    # dialect is display metadata only — every dialect still executes on the
    # sqlite sandbox in services/sandbox.py; there's no per-dialect judge yet.
    sql_dialect: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Shared CREATE TABLE + INSERT script for the whole question — test cases no
    # longer duplicate this in their own stdin (see TestCase.stdin below).
    sql_schema_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optional display-only column names for the result grid; grading is still a
    # raw text compare of expected_stdout, this never affects that.
    sql_result_columns: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    question: Mapped[Question] = relationship(back_populates="coding_problem")
    test_cases: Mapped[list[TestCase]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    coding_problem_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("coding_problems.id", ondelete="CASCADE"), nullable=False
    )
    # For SQL problems, the schema/seed data lives on CodingProblem.sql_schema_sql
    # instead — stdin here is normally blank and only holds a rare per-case setup
    # addendum (e.g. an extra row needed just for one hidden edge case).
    stdin: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expected_stdout: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Function-mode equivalents of stdin/expected_stdout above — only one pair is
    # ever populated, depending on the parent problem's problem_type.
    param_values: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    expected_value: Mapped[object | None] = mapped_column(JSONB, nullable=True)
    # Only meaningful for sample cases — shown as the worked-example explanation.
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Sample cases are the only ones a student can see or run against.
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    problem: Mapped[CodingProblem] = relationship(back_populates="test_cases")

    __table_args__ = (Index("ix_test_cases_problem_sample", "coding_problem_id", "is_sample"),)


# --------------------------------------------------------------------------- attempts


class ExamAttempt(Base, TimestampMixin):
    __tablename__ = "exam_attempts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AttemptStatus] = mapped_column(
        Enum(AttemptStatus, name="attempt_status"),
        default=AttemptStatus.in_progress,
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Frozen per-student randomization: {"sections": [{"id":…, "questions":[…]}]}
    question_order: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grading_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Advisory anti-cheat signals for human review; never auto-fails a student.
    focus_loss_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # True only when the exam required SEB and the Config Key check passed at start.
    # Advisory record of the fact, not itself an enforcement point.
    seb_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    student: Mapped[Student] = relationship(back_populates="attempts")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The only race-free way to enforce one attempt per student per exam.
        UniqueConstraint("exam_id", "student_id", name="uq_attempt_exam_student"),
        Index("ix_attempts_exam_status", "exam_id", "status"),
        # Speeds maintenance.auto_submit_expired's deadline sweep (migration seb3) —
        # ix_attempts_exam_status doesn't cover deadline_at, so without this the
        # sweep scans every in_progress row instead of just those near expiry.
        Index(
            "ix_attempts_deadline_in_progress",
            "deadline_at",
            postgresql_where=text("status = 'in_progress'"),
        ),
    )


class Answer(Base):
    __tablename__ = "answers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    selected_option_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    code_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_marked_for_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    attempt: Mapped[ExamAttempt] = relationship(back_populates="answers")

    __table_args__ = (
        # Makes every auto-save an idempotent upsert.
        UniqueConstraint("attempt_id", "question_id", name="uq_answer_attempt_question"),
        Index("ix_answers_attempt", "attempt_id"),
    )


class JudgeRun(Base):
    __tablename__ = "judge_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_attempts.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    code_text: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[JudgeMode] = mapped_column(Enum(JudgeMode, name="judge_mode"), nullable=False)
    # Set when this run targets one specific sample case rather than all of them.
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("test_cases.id", ondelete="SET NULL"), nullable=True
    )
    # Set for JudgeMode.custom runs — student-typed input with no expected output.
    custom_stdin: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Function-mode equivalent of custom_stdin above — ordered param values.
    custom_params: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[JudgeStatus] = mapped_column(
        Enum(JudgeStatus, name="judge_status"), default=JudgeStatus.queued, nullable=False
    )
    passed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    results: Mapped[list | dict] = mapped_column(JSONB, default=list, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_judge_runs_attempt_question", "attempt_id", "question_id"),)


class ExamInvite(Base, TimestampMixin):
    """One row per (exam, student). The emailed link only ever redeems to whichever
    PIN is currently valid (never the raw token itself) — see app/routers/invites.py
    for why that's what makes the link safe against email link-scanners."""

    __tablename__ = "exam_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    exam_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # sha256 of the emailed token — never store it raw.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    pin_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pin_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pin_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("exam_id", "student_id", name="uq_invite_exam_student"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
