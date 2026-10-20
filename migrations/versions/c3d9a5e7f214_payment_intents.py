"""phase 2: payment_intents table for Paystack

Revision ID: c3d9a5e7f214
Revises: b7c2e4f81a10
Create Date: 2026-09-25 21:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c3d9a5e7f214"
down_revision = "b7c2e4f81a10"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_intents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
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
            nullable=False,
        ),
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("amount_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=True),
        sa.Column("gateway_response", sa.String(length=255), nullable=True),
        sa.Column("paid_amount_pesewas", sa.BigInteger(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_intents_reference", "payment_intents", ["reference"], unique=True
    )
    op.create_index("ix_payment_intents_plan_id", "payment_intents", ["plan_id"])
    op.create_index("ix_payment_intents_payment_id", "payment_intents", ["payment_id"])
    op.create_index(
        "ix_payment_intents_customer_id", "payment_intents", ["customer_id"]
    )


def downgrade():
    op.drop_index("ix_payment_intents_customer_id", table_name="payment_intents")
    op.drop_index("ix_payment_intents_payment_id", table_name="payment_intents")
    op.drop_index("ix_payment_intents_plan_id", table_name="payment_intents")
    op.drop_index("ix_payment_intents_reference", table_name="payment_intents")
    op.drop_table("payment_intents")
