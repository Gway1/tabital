"""checkout API: POS medium (phone-initiated checkout)

Revision ID: c8e4f0b2d6a3
Revises: b7d3e9a1c5f2
Create Date: 2026-10-24 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "c8e4f0b2d6a3"
down_revision = "b7d3e9a1c5f2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("checkout_sessions") as batch:
        batch.add_column(
            sa.Column("medium", sa.String(length=12), nullable=False, server_default="ecommerce")
        )
        batch.add_column(sa.Column("customer_phone", sa.String(length=20)))
        batch.add_column(sa.Column("terminal_id", sa.String(length=60)))
        batch.add_column(sa.Column("notify_status", sa.Text()))
        batch.alter_column("success_url", existing_type=sa.String(length=500), nullable=True)
        batch.alter_column("cancel_url", existing_type=sa.String(length=500), nullable=True)
    with op.batch_alter_table("message_outbox") as batch:
        batch.add_column(sa.Column("link", sa.String(length=500)))
        batch.alter_column("to_address", existing_type=sa.String(length=50), type_=sa.String(length=120))


def downgrade():
    with op.batch_alter_table("message_outbox") as batch:
        batch.alter_column("to_address", existing_type=sa.String(length=120), type_=sa.String(length=50))
        batch.drop_column("link")
    with op.batch_alter_table("checkout_sessions") as batch:
        batch.drop_column("notify_status")
        batch.drop_column("terminal_id")
        batch.drop_column("customer_phone")
        batch.drop_column("medium")
