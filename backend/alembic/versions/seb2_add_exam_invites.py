"""add exam_invites table

Revision ID: seb2
Revises: seb1
Create Date: 2026-08-11

Backs the PIN-based multi-student SEB login flow (SEB_INTEGRATION.md §5): one row
per (exam, student), token stored only as a hash, PIN likewise.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "seb2"
down_revision: str | Sequence[str] | None = "seb1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "exam_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "exam_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("pin_hash", sa.String(length=64), nullable=True),
        sa.Column("pin_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pin_consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("exam_id", "student_id", name="uq_invite_exam_student"),
    )
    op.create_index("ix_exam_invites_exam_id", "exam_invites", ["exam_id"])
    op.create_index("ix_exam_invites_student_id", "exam_invites", ["student_id"])
    op.create_index("ix_exam_invites_token_hash", "exam_invites", ["token_hash"])


def downgrade() -> None:
    op.drop_table("exam_invites")