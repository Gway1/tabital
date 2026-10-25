"""password reset: hashed codes, sign out sessions after a password change

Revision ID: a1c4e7b9d2f0
Revises: 7adf3644fd2c
Create Date: 2026-10-24 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1c4e7b9d2f0"
down_revision = "7adf3644fd2c"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("password_changed_at", sa.DateTime(), nullable=True))
        batch.alter_column(
            "reset_otp",
            existing_type=sa.String(length=10),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
    # Codes and tokens in flight were stored in plaintext and can't be read as hashes
    op.execute(
        "UPDATE users SET reset_otp = NULL, reset_otp_expiry = NULL, "
        "reset_token = NULL, reset_token_expiry = NULL"
    )


def downgrade():
    op.execute("UPDATE users SET reset_otp = NULL, reset_token = NULL")
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "reset_otp",
            existing_type=sa.String(length=64),
            type_=sa.String(length=10),
            existing_nullable=True,
        )
        batch.drop_column("password_changed_at")
