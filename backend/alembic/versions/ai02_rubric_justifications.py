"""Per-criterion justifications on coding submissions

Adds the "why this many marks" text the evaluator now returns alongside each
rubric score, so an examiner reviewing a mark can see what was earned and what
was withheld without re-reading the whole submission.

Nullable: every row written before this revision has scores but no
justifications, and the review screen renders those as blank rather than
pretending an explanation exists.

Revision ID: ai02
Revises: ai01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ai02"
down_revision = "ai01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "coding_submissions",
        sa.Column("ai_rubric_justifications", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coding_submissions", "ai_rubric_justifications")
