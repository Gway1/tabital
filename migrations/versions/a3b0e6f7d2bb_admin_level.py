"""users.admin_level: Management Access vs operations (services/access.py)

Existing admins keep full access (management), so nobody is locked out; new admins start as
operations unless created with --management.

Revision ID: a3b0e6f7d2bb
Revises: f2a9d4e5c1aa
Create Date: 2026-09-27 09:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "a3b0e6f7d2bb"
down_revision = "f2a9d4e5c1aa"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("admin_level", sa.String(length=20), nullable=True)
        )
    op.execute("UPDATE users SET admin_level = 'management' WHERE role = 'admin'")


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("admin_level")
