"""instalment_part_payments: money received towards an instalment before it's fully paid

Revision ID: d6e3b9c0a5ee
Revises: c5d2a8b9f4dd
Create Date: 2026-09-27 14:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "d6e3b9c0a5ee"
down_revision = "c5d2a8b9f4dd"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "instalment_part_payments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("instalment_payments.id"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("instalment_plans.id"),
            nullable=False,
        ),
        sa.Column("amount_pesewas", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column(
            "recorded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_instalment_part_payments_reference"),
    )
    op.create_index(
        "ix_instalment_part_payments_payment_id",
        "instalment_part_payments",
        ["payment_id"],
    )
    op.create_index(
        "ix_instalment_part_payments_plan_id", "instalment_part_payments", ["plan_id"]
    )


def downgrade():
    op.drop_index(
        "ix_instalment_part_payments_plan_id", table_name="instalment_part_payments"
    )
    op.drop_index(
        "ix_instalment_part_payments_payment_id", table_name="instalment_part_payments"
    )
    op.drop_table("instalment_part_payments")
