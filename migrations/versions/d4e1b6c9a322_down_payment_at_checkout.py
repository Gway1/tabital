"""down payment at checkout: order payment fields, order-linked payment intents

Revision ID: d4e1b6c9a322
Revises: c3d9a5e7f214
Create Date: 2026-09-26 09:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "d4e1b6c9a322"
down_revision = "c3d9a5e7f214"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "down_payment_status",
                sa.String(length=30),
                nullable=True,
                server_default="unpaid",
            )
        )
        batch_op.add_column(
            sa.Column("down_payment_reference", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column("down_payment_method", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(
            sa.Column("down_payment_paid_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("refund_status", sa.String(length=30), nullable=True)
        )
        batch_op.add_column(
            sa.Column("refund_reference", sa.String(length=100), nullable=True)
        )

    with op.batch_alter_table("payment_intents", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "purpose",
                sa.String(length=20),
                nullable=False,
                server_default="instalment",
            )
        )
        batch_op.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_payment_intents_order_id", "purchase_orders", ["order_id"], ["id"]
        )
        batch_op.create_index("ix_payment_intents_order_id", ["order_id"])
        batch_op.alter_column("plan_id", existing_type=sa.Integer(), nullable=True)
        batch_op.alter_column("payment_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    with op.batch_alter_table("payment_intents", schema=None) as batch_op:
        batch_op.alter_column("payment_id", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("plan_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_index("ix_payment_intents_order_id")
        batch_op.drop_constraint("fk_payment_intents_order_id", type_="foreignkey")
        batch_op.drop_column("order_id")
        batch_op.drop_column("purpose")

    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.drop_column("refund_reference")
        batch_op.drop_column("refund_status")
        batch_op.drop_column("down_payment_paid_at")
        batch_op.drop_column("down_payment_method")
        batch_op.drop_column("down_payment_reference")
        batch_op.drop_column("down_payment_status")
