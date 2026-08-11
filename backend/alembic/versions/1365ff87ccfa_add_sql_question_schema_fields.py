"""add sql question schema fields

Revision ID: 1365ff87ccfa
Revises: 05ad3e828f25
Create Date: 2026-08-11 00:00:00.000000
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '1365ff87ccfa'
down_revision: str | None = '05ad3e828f25'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # SQL questions now define their schema/seed data once on the problem itself
    # instead of duplicating it inside every test case's stdin. All nullable, no
    # backfill needed — no SQL questions exist in any environment yet.
    op.add_column('coding_problems', sa.Column('sql_dialect', sa.String(length=32), nullable=True))
    op.add_column('coding_problems', sa.Column('sql_schema_sql', sa.Text(), nullable=True))
    op.add_column('coding_problems', sa.Column('sql_result_columns', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('coding_problems', 'sql_result_columns')
    op.drop_column('coding_problems', 'sql_schema_sql')
    op.drop_column('coding_problems', 'sql_dialect')
