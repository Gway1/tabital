"""phase 6: identity checks (Smile ID), fraud signals, devices, employment verification

Revision ID: b8c5f0a1e766
Revises: a7b4e9f0d655
Create Date: 2026-09-26 23:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "b8c5f0a1e766"
down_revision = "a7b4e9f0d655"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("employment_verified_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("employment_verified_by", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "employment_verification_method", sa.String(length=30), nullable=True
            )
        )
        batch_op.add_column(
            sa.Column(
                "employment_verification_note", sa.String(length=500), nullable=True
            )
        )

    op.create_table(
        "identity_checks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("product", sa.String(length=40), nullable=False),
        sa.Column("reference", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column("id_type", sa.String(length=40), nullable=True),
        sa.Column("id_number", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("provider_status", sa.String(length=20), nullable=True),
        sa.Column("provider_message", sa.String(length=255), nullable=True),
        sa.Column("name_match", sa.Boolean(), nullable=True),
        sa.Column("dob_match", sa.Boolean(), nullable=True),
        sa.Column("reasons_json", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column(
            "reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference", name="uq_identity_checks_reference"),
        sa.UniqueConstraint("job_id", name="uq_identity_checks_job_id"),
    )
    op.create_index("ix_identity_checks_user_id", "identity_checks", ["user_id"])
    op.create_index("ix_identity_checks_status", "identity_checks", ["status"])

    op.create_table(
        "fraud_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "related_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column(
            "reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_fraud_signals_dedupe_key"),
    )
    op.create_index("ix_fraud_signals_user_id", "fraud_signals", ["user_id"])
    op.create_index("ix_fraud_signals_code", "fraud_signals", ["code"])
    op.create_index("ix_fraud_signals_status", "fraud_signals", ["status"])

    op.create_table(
        "devices_seen",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("last_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("flags", sa.String(length=200), nullable=True),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "user_id", name="uq_devices_seen_device_user"),
    )
    op.create_index("ix_devices_seen_device_id", "devices_seen", ["device_id"])
    op.create_index("ix_devices_seen_user_id", "devices_seen", ["user_id"])


def downgrade():
    op.drop_index("ix_devices_seen_user_id", table_name="devices_seen")
    op.drop_index("ix_devices_seen_device_id", table_name="devices_seen")
    op.drop_table("devices_seen")
    op.drop_index("ix_fraud_signals_status", table_name="fraud_signals")
    op.drop_index("ix_fraud_signals_code", table_name="fraud_signals")
    op.drop_index("ix_fraud_signals_user_id", table_name="fraud_signals")
    op.drop_table("fraud_signals")
    op.drop_index("ix_identity_checks_status", table_name="identity_checks")
    op.drop_index("ix_identity_checks_user_id", table_name="identity_checks")
    op.drop_table("identity_checks")
    with op.batch_alter_table("users", schema=None) as batch_op:
        for col in (
            "employment_verification_note",
            "employment_verification_method",
            "employment_verified_by",
            "employment_verified_at",
        ):
            batch_op.drop_column(col)
