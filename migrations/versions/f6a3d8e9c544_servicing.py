"""phase 4: servicing (DPD, second late fee), plan pause, reminders outbox, saved cards

Revision ID: f6a3d8e9c544
Revises: e5f2c7d8b433
Create Date: 2026-09-26 16:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "f6a3d8e9c544"
down_revision = "e5f2c7d8b433"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("instalment_payments", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("late_fee_stage", sa.Integer(), nullable=True, server_default="0")
        )
        batch_op.add_column(
            sa.Column("second_late_fee_applied_date", sa.DateTime(), nullable=True)
        )

    with op.batch_alter_table("instalment_plans", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("days_past_due", sa.Integer(), nullable=True, server_default="0")
        )
        batch_op.add_column(
            sa.Column(
                "dpd_bucket",
                sa.String(length=20),
                nullable=True,
                server_default="current",
            )
        )
        batch_op.add_column(
            sa.Column("collection_stage", sa.String(length=30), nullable=True)
        )
        batch_op.add_column(sa.Column("charged_off_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("paused_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("paused_reason", sa.String(length=255), nullable=True)
        )

    # Existing late fees count as stage 1
    op.execute(
        "UPDATE instalment_payments SET late_fee_stage = 1 WHERE late_fee_applied_date IS NOT NULL"
    )

    op.create_table(
        "message_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("to_address", sa.String(length=50), nullable=True),
        sa.Column("template", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=30), nullable=True),
        sa.Column("provider_message_id", sa.String(length=100), nullable=True),
        sa.Column("dedupe_key", sa.String(length=120), nullable=False),
        sa.Column(
            "plan_id", sa.Integer(), sa.ForeignKey("instalment_plans.id"), nullable=True
        ),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("instalment_payments.id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_message_outbox_dedupe_key"),
    )
    op.create_index("ix_message_outbox_user_id", "message_outbox", ["user_id"])
    op.create_index("ix_message_outbox_status", "message_outbox", ["status"])
    op.create_index("ix_message_outbox_plan_id", "message_outbox", ["plan_id"])

    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("authorization_code", sa.String(length=100), nullable=False),
        sa.Column("signature", sa.String(length=100), nullable=True),
        sa.Column("email", sa.String(length=200), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=True),
        sa.Column("card_type", sa.String(length=30), nullable=True),
        sa.Column("bank", sa.String(length=100), nullable=True),
        sa.Column("last4", sa.String(length=4), nullable=True),
        sa.Column("exp_month", sa.String(length=2), nullable=True),
        sa.Column("exp_year", sa.String(length=4), nullable=True),
        sa.Column("reusable", sa.Boolean(), nullable=True),
        sa.Column("autopay_enabled", sa.Boolean(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_payment_methods_customer_id", "payment_methods", ["customer_id"]
    )
    op.create_index("ix_payment_methods_signature", "payment_methods", ["signature"])


def downgrade():
    op.drop_index("ix_payment_methods_signature", table_name="payment_methods")
    op.drop_index("ix_payment_methods_customer_id", table_name="payment_methods")
    op.drop_table("payment_methods")
    op.drop_index("ix_message_outbox_plan_id", table_name="message_outbox")
    op.drop_index("ix_message_outbox_status", table_name="message_outbox")
    op.drop_index("ix_message_outbox_user_id", table_name="message_outbox")
    op.drop_table("message_outbox")
    with op.batch_alter_table("instalment_plans", schema=None) as batch_op:
        for col in (
            "paused_reason",
            "paused_at",
            "charged_off_at",
            "collection_stage",
            "dpd_bucket",
            "days_past_due",
        ):
            batch_op.drop_column(col)
    with op.batch_alter_table("instalment_payments", schema=None) as batch_op:
        batch_op.drop_column("second_late_fee_applied_date")
        batch_op.drop_column("late_fee_stage")
