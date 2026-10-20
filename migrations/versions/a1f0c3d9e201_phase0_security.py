"""phase 0 security: OTP attempt counter, drop unique referee phone

Revision ID: a1f0c3d9e201
Revises: 09e22ec62bb6
Create Date: 2026-09-25 18:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "a1f0c3d9e201"
down_revision = "09e22ec62bb6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "reset_otp_attempts", sa.Integer(), nullable=True, server_default="0"
            )
        )

    # The initial migration created an unnamed UNIQUE(ref_phone); Postgres names it users_ref_phone_key
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_ref_phone_key")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.create_unique_constraint("users_ref_phone_key", "users", ["ref_phone"])

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_column("reset_otp_attempts")
