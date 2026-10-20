"""phase 5: settlements, settlement lines, payment links, merchant payout fields

Revision ID: a7b4e9f0d655
Revises: f6a3d8e9c544
Create Date: 2026-09-26 20:00:00

"""
from alembic import op
import sqlalchemy as sa


revision = "a7b4e9f0d655"
down_revision = "f6a3d8e9c544"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "settlement_period_days",
                sa.Integer(),
                nullable=True,
                server_default="7",
            )
        )
        batch_op.add_column(
            sa.Column("payout_method", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("payout_bank_code", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("paystack_recipient_code", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("payout_details_updated_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("payout_hold_until", sa.DateTime(), nullable=True)
        )

    op.create_table(
        "settlements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("settlement_id", sa.String(length=40), nullable=False),
        sa.Column(
            "merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("gross_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("fees_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("clawbacks_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("net_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("hold_reason", sa.String(length=255), nullable=True),
        sa.Column("transfer_reference", sa.String(length=64), nullable=True),
        sa.Column("transfer_code", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transfer_reference", name="uq_settlements_transfer_reference"
        ),
    )
    op.create_index(
        "ix_settlements_settlement_id", "settlements", ["settlement_id"], unique=True
    )
    op.create_index("ix_settlements_merchant_id", "settlements", ["merchant_id"])
    op.create_index("ix_settlements_status", "settlements", ["status"])

    op.create_table(
        "settlement_lines",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "settlement_id",
            sa.Integer(),
            sa.ForeignKey("settlements.id"),
            nullable=True,
        ),
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("instalment_plans.id"),
            nullable=False,
        ),
        sa.Column(
            "transaction_id",
            sa.Integer(),
            sa.ForeignKey("transactions.id"),
            nullable=True,
        ),
        sa.Column("line_type", sa.String(length=20), nullable=False),
        sa.Column("gross_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("fee_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("net_pesewas", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_settlement_lines_merchant_id", "settlement_lines", ["merchant_id"]
    )
    op.create_index(
        "ix_settlement_lines_settlement_id", "settlement_lines", ["settlement_id"]
    )
    op.create_index("ix_settlement_lines_plan_id", "settlement_lines", ["plan_id"])

    op.create_table(
        "payment_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token", sa.String(length=40), nullable=False),
        sa.Column(
            "merchant_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "used_by_order_id",
            sa.Integer(),
            sa.ForeignKey(
                "purchase_orders.id", name="fk_payment_links_used_by_order_id"
            ),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_links_token", "payment_links", ["token"], unique=True)
    op.create_index("ix_payment_links_merchant_id", "payment_links", ["merchant_id"])

    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("transaction_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("payment_link_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_purchase_orders_transaction_id",
            "transactions",
            ["transaction_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_purchase_orders_payment_link_id",
            "payment_links",
            ["payment_link_id"],
            ["id"],
        )


def downgrade():
    with op.batch_alter_table("purchase_orders", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_purchase_orders_payment_link_id", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "fk_purchase_orders_transaction_id", type_="foreignkey"
        )
        batch_op.drop_column("payment_link_id")
        batch_op.drop_column("transaction_id")
    op.drop_index("ix_payment_links_merchant_id", table_name="payment_links")
    op.drop_index("ix_payment_links_token", table_name="payment_links")
    op.drop_table("payment_links")
    op.drop_index("ix_settlement_lines_plan_id", table_name="settlement_lines")
    op.drop_index("ix_settlement_lines_settlement_id", table_name="settlement_lines")
    op.drop_index("ix_settlement_lines_merchant_id", table_name="settlement_lines")
    op.drop_table("settlement_lines")
    op.drop_index("ix_settlements_status", table_name="settlements")
    op.drop_index("ix_settlements_merchant_id", table_name="settlements")
    op.drop_index("ix_settlements_settlement_id", table_name="settlements")
    op.drop_table("settlements")
    with op.batch_alter_table("users", schema=None) as batch_op:
        for col in (
            "payout_hold_until",
            "payout_details_updated_at",
            "paystack_recipient_code",
            "payout_bank_code",
            "payout_method",
            "settlement_period_days",
        ):
            batch_op.drop_column(col)
