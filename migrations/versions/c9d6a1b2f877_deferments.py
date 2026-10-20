"""deferments (§4): deferments table, instalment_payments.original_due_date

Revision ID: c9d6a1b2f877
Revises: b8c5f0a1e766
Create Date: 2026-09-27 00:30:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c9d6a1b2f877"
down_revision = "b8c5f0a1e766"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("instalment_payments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("original_due_date", sa.DateTime(), nullable=True)
        )

    op.create_table(
        "deferments",
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
            nullable=False,
        ),
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "intent_id",
            sa.Integer(),
            sa.ForeignKey("payment_intents.id"),
            nullable=True,
        ),
        sa.Column("fee_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("original_due_date", sa.DateTime(), nullable=False),
        sa.Column("new_due_date", sa.DateTime(), nullable=True),
        sa.Column("moved_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("intent_id", name="uq_deferments_intent_id"),
    )
    op.create_index("ix_deferments_plan_id", "deferments", ["plan_id"])
    op.create_index("ix_deferments_payment_id", "deferments", ["payment_id"])
    op.create_index("ix_deferments_customer_id", "deferments", ["customer_id"])


def downgrade():
    op.drop_index("ix_deferments_customer_id", table_name="deferments")
    op.drop_index("ix_deferments_payment_id", table_name="deferments")
    op.drop_index("ix_deferments_plan_id", table_name="deferments")
    op.drop_table("deferments")
    with op.batch_alter_table("instalment_payments", schema=None) as batch_op:
        batch_op.drop_column("original_due_date")
