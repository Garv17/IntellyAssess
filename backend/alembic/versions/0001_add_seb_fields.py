"""add seb config to exams and seb_verified to exam_attempts

Revision ID: 0001
Revises:
Create Date: 2026-08-10

This is the first migration in this repo. It assumes the schema already exists
(created via scripts.seed's create_all, per README's dev workflow) and only adds the
three SEB-related columns — it is not a full baseline. If you're pointing this at a
brand-new, empty database, running `python -m scripts.seed` will create every table
including these columns directly from the models, and this migration becomes a no-op
(the ALTERs below are idempotent via IF NOT EXISTS-style checks left to the operator —
review before running against a real database, as noted in SEB_INTEGRATION.md).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
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
