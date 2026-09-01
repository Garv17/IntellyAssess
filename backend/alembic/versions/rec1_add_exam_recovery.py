"""add exam recovery table and reopen_count

Revision ID: rec1
Revises: ai02
Create Date: 2026-09-01

Backs the temporary "resume a prematurely auto-submitted exam" safeguard
(admin-initiated recovery of ExamAttempt rows stuck in auto_submitted while the
underlying premature-auto-submit bug is fixed separately). exam_attempts gains
a reopen_count guard column; exam_attempt_recoveries is the audit trail / token
store, one row per recovery (send-link or resume-now).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "rec1"
down_revision: str | Sequence[str] | None = "ai02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exam_attempts",
        sa.Column("reopen_count", sa.Integer(), server_default="0", nullable=False),
    )

    op.create_table(
        "exam_attempt_recoveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "exam_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admins.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.String(length=64), nullable=False),
        sa.Column("admin_note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=True, unique=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("remaining_seconds", sa.Integer(), nullable=False),
        sa.Column("previous_status", sa.String(length=32), nullable=False),
        sa.Column("previous_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("new_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_attempt_recoveries_attempt_id", "exam_attempt_recoveries", ["attempt_id"])
    op.create_index("ix_attempt_recoveries_token_hash", "exam_attempt_recoveries", ["token_hash"])
    op.create_index(
        "ix_attempt_recoveries_attempt_created",
        "exam_attempt_recoveries",
        ["attempt_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_attempt_recoveries_attempt_created", table_name="exam_attempt_recoveries")
    op.drop_index("ix_attempt_recoveries_token_hash", table_name="exam_attempt_recoveries")
    op.drop_index("ix_attempt_recoveries_attempt_id", table_name="exam_attempt_recoveries")
    op.drop_table("exam_attempt_recoveries")
    op.drop_column("exam_attempts", "reopen_count")
