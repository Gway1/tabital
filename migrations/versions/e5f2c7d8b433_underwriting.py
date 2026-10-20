"""phase 3: underwriting fields on users, risk_assessments table, order link

Revision ID: e5f2c7d8b433
Revises: d4e1b6c9a322
Create Date: 2026-09-26 12:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "e5f2c7d8b433"
down_revision = "d4e1b6c9a322"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("tier", sa.String(length=10), nullable=True),
        sa.Column("pay_in_4_dp_rate", sa.Numeric(5, 4), nullable=True),
        sa.Column("limit_multiplier", sa.Numeric(6, 3), nullable=True),
        sa.Column("credit_limit", sa.Numeric(12, 2), nullable=False),
        sa.Column("outstanding", sa.Numeric(12, 2), nullable=False),
        sa.Column("available_limit", sa.Numeric(12, 2), nullable=False),
        sa.Column("extended_plans_eligible", sa.Boolean(), nullable=True),
        sa.Column("reasons", sa.Text(), nullable=True),
        sa.Column("inputs", sa.Text(), nullable=True),
        sa.Column("rules_version", sa.String(length=40), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risk_assessments_user_id", "risk_assessments", ["user_id"])

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("monthly_salary", sa.Numeric(12, 2), nullable=True)
        )
        batch_op.add_column(
            sa.Column("employment_start_date", sa.Date(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "salary_paid_to_bank",
                sa.Boolean(),
                nullable=True,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "salary_verified",
                sa.Boolean(),
                nullable=True,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("risk_tier", sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column("credit_limit", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(
            sa.Column("credit_limit_override", sa.Numeric(12, 2), nullable=True)
        )
        batch_op.add_column(sa.Column("limit_updated_at", sa.DateTime(), nullable=True))

    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("risk_assessment_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_purchase_orders_risk_assessment_id",
            "risk_assessments",
            ["risk_assessment_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_purchase_orders_risk_assessment_id", type_="foreignkey"
        )
        batch_op.drop_column("risk_assessment_id")

    with op.batch_alter_table("users", schema=None) as batch_op:
        for col in (
            "limit_updated_at",
            "credit_limit_override",
            "credit_limit",
            "risk_tier",
            "salary_verified",
            "salary_paid_to_bank",
            "employment_start_date",
            "monthly_salary",
        ):
            batch_op.drop_column(col)

    op.drop_index("ix_risk_assessments_user_id", table_name="risk_assessments")
    op.drop_table("risk_assessments")
