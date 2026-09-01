"""Add admins.role and promote existing admins to super_admin

Admin Management (add/remove/update admin users) is gated on role ==
"super_admin". Before this revision every admin row's role was just the
meaningless default "admin", so nobody could reach the new endpoints.
Promoting every admin that exists as of this migration preserves exactly the
access they already had (full admin console access) and additionally lets
them manage other admins — new admins created afterwards default to the
plain "admin" role and must be promoted explicitly.

The column itself is added here too. `Admin.role` was declared on the model
without a migration ever creating it, so it only existed on databases built by
seed.py's `create_all`. On any longer-lived database the column was simply
missing, which broke *every* admin login — the SELECT behind `current_admin`
names `admins.role` — long before it could break Admin Management. The
server_default is what makes the NOT NULL safe on a table that already has
rows; the model supplies the same default for new inserts.

Revision ID: ai03
Revises: ai02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ai03"
down_revision = "ai02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "admins",
        sa.Column("role", sa.String(32), nullable=False, server_default="admin"),
    )
    op.execute(
        sa.text("UPDATE admins SET role = 'super_admin' WHERE role = 'admin'")
    )


def downgrade() -> None:
    op.drop_column("admins", "role")
