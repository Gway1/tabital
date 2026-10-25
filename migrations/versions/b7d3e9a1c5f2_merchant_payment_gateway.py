"""merchant payment gateway: api keys, checkout sessions, webhook queue

Revision ID: b7d3e9a1c5f2
Revises: a1c4e7b9d2f0
Create Date: 2026-10-24 15:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b7d3e9a1c5f2"
down_revision = "a1c4e7b9d2f0"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "merchant_api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("label", sa.String(length=60)),
        sa.Column("key_prefix", sa.String(length=24), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("last4", sa.String(length=4)),
        sa.Column("status", sa.String(length=20)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("last_used_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_index("ix_merchant_api_keys_merchant_id", "merchant_api_keys", ["merchant_id"])
    op.create_index("ix_merchant_api_keys_key_prefix", "merchant_api_keys", ["key_prefix"])

    op.create_table(
        "checkout_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("merchant_reference", sa.String(length=100), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3)),
        sa.Column("description", sa.String(length=200)),
        sa.Column("items", sa.Text()),
        sa.Column("customer_hint", sa.String(length=200)),
        sa.Column("success_url", sa.String(length=500), nullable=False),
        sa.Column("cancel_url", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=20)),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("order_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("merchant_id", "merchant_reference", name="uq_checkout_merchant_reference"),
    )
    op.create_index("ix_checkout_sessions_token", "checkout_sessions", ["token"], unique=True)
    op.create_index("ix_checkout_sessions_merchant_id", "checkout_sessions", ["merchant_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("purchase_orders.id")),
        sa.Column("event", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20)),
        sa.Column("attempts", sa.Integer()),
        sa.Column("next_attempt_at", sa.DateTime()),
        sa.Column("last_error", sa.String(length=255)),
        sa.Column("last_status_code", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("delivered_at", sa.DateTime()),
    )
    op.create_index("ix_webhook_deliveries_merchant_id", "webhook_deliveries", ["merchant_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])

    with op.batch_alter_table("purchase_orders") as batch:
        batch.add_column(sa.Column("checkout_session_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("merchant_reference", sa.String(length=100)))
        batch.add_column(sa.Column("items_json", sa.Text()))
        batch.create_foreign_key(
            "fk_orders_checkout_session", "checkout_sessions", ["checkout_session_id"], ["id"]
        )
        batch.create_unique_constraint("uq_orders_checkout_session", ["checkout_session_id"])

    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("gateway_enabled", sa.Boolean(), server_default=sa.true(), nullable=True)
        )
        batch.add_column(sa.Column("webhook_url", sa.String(length=500)))
        batch.add_column(sa.Column("webhook_secret", sa.String(length=80)))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("webhook_secret")
        batch.drop_column("webhook_url")
        batch.drop_column("gateway_enabled")
    with op.batch_alter_table("purchase_orders") as batch:
        batch.drop_constraint("uq_orders_checkout_session", type_="unique")
        batch.drop_constraint("fk_orders_checkout_session", type_="foreignkey")
        batch.drop_column("items_json")
        batch.drop_column("merchant_reference")
        batch.drop_column("checkout_session_id")
    op.drop_table("webhook_deliveries")
    op.drop_table("checkout_sessions")
    op.drop_table("merchant_api_keys")
