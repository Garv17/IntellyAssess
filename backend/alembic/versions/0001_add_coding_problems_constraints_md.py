"""add coding_problems.constraints_md

Revision ID: 0001
Revises:
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("coding_problems", sa.Column("constraints_md", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("coding_problems", "constraints_md")
