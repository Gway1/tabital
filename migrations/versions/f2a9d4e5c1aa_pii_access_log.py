"""pii_access_log: admins revealing masked personal data

Revision ID: f2a9d4e5c1aa
Revises: e1f8c3d4b099
Create Date: 2026-09-27 05:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "f2a9d4e5c1aa"
down_revision = "e1f8c3d4b099"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pii_access_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("field", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pii_access_log_admin_id", "pii_access_log", ["admin_id"])
    op.create_index("ix_pii_access_log_user_id", "pii_access_log", ["user_id"])
    op.create_index("ix_pii_access_log_created_at", "pii_access_log", ["created_at"])


def downgrade():
    op.drop_index("ix_pii_access_log_created_at", table_name="pii_access_log")
    op.drop_index("ix_pii_access_log_user_id", table_name="pii_access_log")
    op.drop_index("ix_pii_access_log_admin_id", table_name="pii_access_log")
    op.drop_table("pii_access_log")
