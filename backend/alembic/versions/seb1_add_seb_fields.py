"""add seb config to exams and seb_verified to exam_attempts

Revision ID: seb1
Revises: 1365ff87ccfa
Create Date: 2026-08-10

Rebased onto the feature/magic-link baseline (0001 -> 9adcd82108f8 ->
05ad3e828f25 -> 1365ff87ccfa) after merging that branch in twice — first
onto 05ad3e828f25, then re-pointed here once magic-link added its own SQL
question migration on top of that same revision, which would otherwise have
left two heads.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "seb1"
down_revision: str | Sequence[str] | None = "1365ff87ccfa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "exams",
        sa.Column("requires_seb", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("exams", sa.Column("seb_config_key", sa.String(length=64), nullable=True))
    op.add_column(
        "exam_attempts",
        sa.Column("seb_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # server_default only exists to backfill existing rows; drop it so future inserts
    # go through the ORM's Python-side default instead of relying on the DB default.
    op.alter_column("exams", "requires_seb", server_default=None)
    op.alter_column("exam_attempts", "seb_verified", server_default=None)


def downgrade() -> None:
    op.drop_column("exam_attempts", "seb_verified")
    op.drop_column("exams", "seb_config_key")
    op.drop_column("exams", "requires_seb")
