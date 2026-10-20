"""users.merchant_fee_tier: premium / standard / high-risk merchant fee (§6.1)

Existing merchants stay empty, which means the standard tier (the fee they pay today).

Revision ID: c5d2a8b9f4dd
Revises: b4c1f7a8e3cc
Create Date: 2026-09-27 13:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "c5d2a8b9f4dd"
down_revision = "b4c1f7a8e3cc"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("merchant_fee_tier", sa.String(length=20), nullable=True)
        )


def downgrade():
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("merchant_fee_tier")
