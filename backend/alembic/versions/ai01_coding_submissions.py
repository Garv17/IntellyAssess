"""Submission-only coding: add coding_submissions, drop judge_runs.

This branch does not execute student code. `judge_runs` recorded compiler/judge
output and has no writer left, so it goes; `coding_submissions` replaces it with
a frozen snapshot of what a student submitted plus the AI recommendation and the
admin's final decision, kept in disjoint column sets.

Revision ID: ai01
Revises: seb2
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "ai01"
down_revision: str | Sequence[str] | None = "seb2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


EVALUATION_STATUSES = (
    "pending",
    "ai_evaluating",
    "ai_evaluated",
    "ai_failed",
    "admin_reviewed",
    "finalized",
)


def upgrade() -> None:
    postgresql.ENUM(*EVALUATION_STATUSES, name="evaluation_status").create(
        op.get_bind(), checkfirst=True
    )
    # create_type=False: the type is created explicitly above (idempotently), and
    # without this SQLAlchemy emits a second unguarded CREATE TYPE for the column.
    evaluation_status = postgresql.ENUM(
        *EVALUATION_STATUSES, name="evaluation_status", create_type=False
    )

    op.create_table(
        "coding_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("code_text", sa.Text(), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("max_marks", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "status",
            evaluation_status,
            server_default="pending",
            nullable=False,
        ),
        # AI recommendation — written only by the evaluator.
        sa.Column("ai_score", sa.Float(), nullable=True),
        sa.Column("ai_rubric_scores", postgresql.JSONB(), nullable=True),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        sa.Column("ai_strengths", postgresql.JSONB(), nullable=True),
        sa.Column("ai_issues", postgresql.JSONB(), nullable=True),
        sa.Column("ai_confidence", sa.Float(), nullable=True),
        sa.Column(
            "ai_requires_manual_review", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("ai_model", sa.String(64), nullable=True),
        sa.Column("ai_rubric_version", sa.String(32), nullable=True),
        sa.Column("ai_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_error", sa.Text(), nullable=True),
        # Admin decision — the only thing that becomes a mark.
        sa.Column("final_score", sa.Float(), nullable=True),
        sa.Column(
            "admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admins.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("admin_comment", sa.Text(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # Also the idempotency key: one submission per question per attempt is what
        # stops a retried submit from queueing a second evaluation.
        sa.UniqueConstraint(
            "attempt_id", "question_id", name="uq_coding_submission_attempt_question"
        ),
    )
    op.create_index("ix_coding_submissions_attempt", "coding_submissions", ["attempt_id"])
    op.create_index("ix_coding_submissions_status", "coding_submissions", ["status"])

    op.drop_table("judge_runs")
    for enum_name in ("judge_status", "judge_mode"):
        postgresql.ENUM(name=enum_name).drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    postgresql.ENUM("queued", "running", "done", "error", name="judge_status").create(
        op.get_bind(), checkfirst=True
    )
    postgresql.ENUM("sample", "final", "custom", name="judge_mode").create(
        op.get_bind(), checkfirst=True
    )
    # See upgrade(): the types are created explicitly above, so the columns must
    # not try to create them again.
    judge_status = postgresql.ENUM(
        "queued", "running", "done", "error", name="judge_status", create_type=False
    )
    judge_mode = postgresql.ENUM(
        "sample", "final", "custom", name="judge_mode", create_type=False
    )

    op.create_table(
        "judge_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("code_text", sa.Text(), nullable=False),
        sa.Column("mode", judge_mode, nullable=False),
        sa.Column(
            "test_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("test_cases.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("custom_stdin", sa.Text(), nullable=True),
        sa.Column("custom_params", postgresql.JSONB(), nullable=True),
        sa.Column("status", judge_status, server_default="queued", nullable=False),
        sa.Column("passed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total", sa.Integer(), server_default="0", nullable=False),
        sa.Column("score", sa.Float(), server_default="0", nullable=False),
        sa.Column("results", postgresql.JSONB(), server_default="[]", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_judge_runs_attempt_question", "judge_runs", ["attempt_id", "question_id"])

    op.drop_index("ix_coding_submissions_status", table_name="coding_submissions")
    op.drop_index("ix_coding_submissions_attempt", table_name="coding_submissions")
    op.drop_table("coding_submissions")
    postgresql.ENUM(name="evaluation_status").drop(op.get_bind(), checkfirst=True)
