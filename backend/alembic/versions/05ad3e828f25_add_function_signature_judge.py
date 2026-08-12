"""add function signature judge

Revision ID: 05ad3e828f25
Revises: 9adcd82108f8
Create Date: 2026-08-10 18:50:36.851289
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = '05ad3e828f25'
down_revision: str | None = '9adcd82108f8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


problem_type = postgresql.ENUM('stdio', 'function', name='problem_type')


def upgrade() -> None:
    # Missed by autogenerate in the previous revision: judge_mode was extended with
    # a 'custom' member on the Python side, but the Postgres enum type was never
    # altered to match, so a real custom-input run fails against this DB today.
    # ADD VALUE is only safe inside an ordinary transaction on fairly recent
    # Postgres, so it goes through autocommit_block() to work regardless of version.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE judge_mode ADD VALUE IF NOT EXISTS 'custom'")

    problem_type.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'coding_problems',
        sa.Column(
            'problem_type', problem_type, nullable=False, server_default='stdio'
        ),
    )
    op.add_column('coding_problems', sa.Column('function_name', sa.String(length=128), nullable=True))
    op.add_column('coding_problems', sa.Column('return_type', sa.String(length=32), nullable=True))
    op.add_column('coding_problems', sa.Column('parameters', postgresql.JSONB(), nullable=True))

    op.add_column('test_cases', sa.Column('param_values', postgresql.JSONB(), nullable=True))
    op.add_column('test_cases', sa.Column('expected_value', postgresql.JSONB(), nullable=True))

    op.add_column('judge_runs', sa.Column('custom_params', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('judge_runs', 'custom_params')
    op.drop_column('test_cases', 'expected_value')
    op.drop_column('test_cases', 'param_values')
    op.drop_column('coding_problems', 'parameters')
    op.drop_column('coding_problems', 'return_type')
    op.drop_column('coding_problems', 'function_name')
    op.drop_column('coding_problems', 'problem_type')
    problem_type.drop(op.get_bind(), checkfirst=True)
    # judge_mode's 'custom' value is intentionally not removed on downgrade —
    # Postgres has no ALTER TYPE ... DROP VALUE, and any existing 'custom' rows
    # would make the column un-droppable-then-recreatable without a full rebuild.
