"""add partial index on exam_attempts.deadline_at for in_progress attempts

Revision ID: seb3
Revises: seb2
Create Date: 2026-08-12

The auto-submit sweeper (maintenance.auto_submit_expired) filters on
status='in_progress' AND deadline_at<=now() every autosubmit_sweep_seconds. The
existing ix_attempts_exam_status composite index covers exam_id+status but not
deadline_at, so that query falls back to scanning every in_progress row. At 200
concurrent attempts this partial index keeps the sweep's cost proportional to
attempts actually near their deadline, not the total in-progress count.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "seb3"
down_revision: str | Sequence[str] | None = "seb2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_attempts_deadline_in_progress",
        "exam_attempts",
        ["deadline_at"],
        postgresql_where=sa.text("status = 'in_progress'"),
    )


def downgrade() -> None:
    op.drop_index("ix_attempts_deadline_in_progress", table_name="exam_attempts")
