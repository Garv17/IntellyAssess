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
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cohort: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
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
    randomize_options: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # NULL => no pass/fail badge is shown anywhere; admin hasn't set a threshold
    pass_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    # NULL cohort => open to every student
    cohort: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admins.id", ondelete="SET NULL"), nullable=True
    )

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
    allowed_languages: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), default=lambda: ["python", "java", "cpp"], nullable=False
    )
    time_limit_ms: Mapped[int] = mapped_column(Integer, default=2000, nullable=False)
    memory_limit_mb: Mapped[int] = mapped_column(Integer, default=128, nullable=False)
    starter_code: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

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
    stdin: Mapped[str] = mapped_column(Text, default="", nullable=False)
    expected_stdout: Mapped[str] = mapped_column(Text, default="", nullable=False)
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

    student: Mapped[Student] = relationship(back_populates="attempts")
    answers: Mapped[list[Answer]] = relationship(
        back_populates="attempt", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # The only race-free way to enforce one attempt per student per exam.
        UniqueConstraint("exam_id", "student_id", name="uq_attempt_exam_student"),
        Index("ix_attempts_exam_status", "exam_id", "status"),
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
