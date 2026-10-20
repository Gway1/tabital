"""setting_changes: audit log for business settings

Revision ID: d0e7b2c3a988
Revises: c9d6a1b2f877
Create Date: 2026-09-27 02:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "d0e7b2c3a988"
down_revision = "c9d6a1b2f877"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "setting_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("setting_key", sa.String(length=100), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column(
            "changed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_setting_changes_setting_key", "setting_changes", ["setting_key"]
    )


def downgrade():
    op.drop_index("ix_setting_changes_setting_key", table_name="setting_changes")
    op.drop_table("setting_changes")
