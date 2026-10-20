"""phase 1: append-only ledger_entries table

Revision ID: b7c2e4f81a10
Revises: a1f0c3d9e201
Create Date: 2026-09-25 20:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "b7c2e4f81a10"
down_revision = "a1f0c3d9e201"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("instalment_plans.id"),
            nullable=False,
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("instalment_payments.id"),
            nullable=True,
        ),
        sa.Column("account", sa.String(length=20), nullable=False),
        sa.Column("entry_type", sa.String(length=40), nullable=False),
        sa.Column("amount_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_plan_id", "ledger_entries", ["plan_id"])
    op.create_index("ix_ledger_entries_payment_id", "ledger_entries", ["payment_id"])


def downgrade():
    op.drop_index("ix_ledger_entries_payment_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_plan_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
