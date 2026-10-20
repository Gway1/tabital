"""login_attempts.kind/subject (per-account limits, reset codes, sign-ups); payment_claims

A new migration rather than an edit of b4c1f7a8e3cc, so databases that already ran that one are
upgraded too. Existing attempt rows become login attempts keyed on what was typed.

Revision ID: e7f4c1d2b6ff
Revises: d6e3b9c0a5ee
Create Date: 2026-09-27 16:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "e7f4c1d2b6ff"
down_revision = "d6e3b9c0a5ee"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("login_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "kind", sa.String(length=10), nullable=False, server_default="login"
            )
        )
        batch_op.add_column(sa.Column("subject", sa.String(length=140), nullable=True))
    op.execute(
        "UPDATE login_attempts SET subject = 'id:' || identifier WHERE subject IS NULL"
    )
    with op.batch_alter_table("login_attempts") as batch_op:
        batch_op.alter_column(
            "subject", existing_type=sa.String(length=140), nullable=False
        )
        batch_op.create_index("ix_login_attempts_kind", ["kind"])
        batch_op.create_index("ix_login_attempts_subject", ["subject"])

    op.create_table(
        "payment_claims",
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
        sa.Column(
            "customer_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("method", sa.String(length=30), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column(
            "reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_claims_payment_id", "payment_claims", ["payment_id"])
    op.create_index("ix_payment_claims_plan_id", "payment_claims", ["plan_id"])
    op.create_index("ix_payment_claims_customer_id", "payment_claims", ["customer_id"])
    op.create_index("ix_payment_claims_status", "payment_claims", ["status"])


def downgrade():
    op.drop_index("ix_payment_claims_status", table_name="payment_claims")
    op.drop_index("ix_payment_claims_customer_id", table_name="payment_claims")
    op.drop_index("ix_payment_claims_plan_id", table_name="payment_claims")
    op.drop_index("ix_payment_claims_payment_id", table_name="payment_claims")
    op.drop_table("payment_claims")
    with op.batch_alter_table("login_attempts") as batch_op:
        batch_op.drop_index("ix_login_attempts_subject")
        batch_op.drop_index("ix_login_attempts_kind")
        batch_op.drop_column("subject")
        batch_op.drop_column("kind")
