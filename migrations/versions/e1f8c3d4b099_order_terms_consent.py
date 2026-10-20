"""purchase_orders: which Terms the customer accepted, and when (§10)

Revision ID: e1f8c3d4b099
Revises: d0e7b2c3a988
Create Date: 2026-09-27 03:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "e1f8c3d4b099"
down_revision = "d0e7b2c3a988"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("terms_version", sa.String(length=40), nullable=True)
        )
        batch_op.add_column(
            sa.Column("terms_accepted_at", sa.DateTime(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.drop_column("terms_accepted_at")
        batch_op.drop_column("terms_version")
