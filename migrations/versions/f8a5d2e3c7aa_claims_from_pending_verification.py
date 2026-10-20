"""Data: instalments a customer marked 'pending_verification' (old /customer/payments/make) become
payment claims, and go back to pending/overdue so late fees, days past due and collections apply.
Payment 1 (down payments waiting for verification) is left as it is.

Revision ID: f8a5d2e3c7aa
Revises: e7f4c1d2b6ff
Create Date: 2026-09-27 18:00:00

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "f8a5d2e3c7aa"
down_revision = "e7f4c1d2b6ff"
branch_labels = None
depends_on = None

NOTE = "Moved from pending_verification by migration f8a5d2e3c7aa"


def upgrade():
    conn = op.get_bind()
    now = datetime.utcnow()
    rows = conn.execute(
        sa.text(
            "SELECT p.id, p.plan_id, pl.customer_id, p.payment_method, p.payment_reference, p.due_date "
            "FROM instalment_payments p JOIN instalment_plans pl ON pl.id = p.plan_id "
            "WHERE p.status = 'pending_verification' AND p.installment_number > 1"
        )
    ).fetchall()
    for pid, plan_id, customer_id, method, reference, due in rows:
        conn.execute(
            sa.text(
                "INSERT INTO payment_claims (payment_id, plan_id, customer_id, method, reference, status, review_note, "
                "created_at) VALUES (:pid, :plan, :cust, :method, :ref, 'pending', :note, :now)"
            ),
            {
                "pid": pid,
                "plan": plan_id,
                "cust": customer_id,
                "method": method or "mobile_money",
                "ref": (reference or "not given")[:100],
                "note": NOTE,
                "now": now,
            },
        )
        due_dt = (
            due
            if isinstance(due, datetime)
            else (datetime.fromisoformat(str(due)) if due else None)
        )
        status = "overdue" if due_dt and due_dt < now else "pending"
        conn.execute(
            sa.text(
                "UPDATE instalment_payments SET status = :s, payment_reference = NULL, payment_method = NULL "
                "WHERE id = :pid"
            ),
            {"s": status, "pid": pid},
        )


def downgrade():
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT payment_id, method, reference FROM payment_claims WHERE review_note = :note AND status = 'pending'"
        ),
        {"note": NOTE},
    ).fetchall()
    for pid, method, reference in rows:
        conn.execute(
            sa.text(
                "UPDATE instalment_payments SET status = 'pending_verification', payment_method = :m, "
                "payment_reference = :r WHERE id = :pid AND status IN ('pending', 'overdue')"
            ),
            {"m": method, "r": reference, "pid": pid},
        )
    conn.execute(
        sa.text(
            "DELETE FROM payment_claims WHERE review_note = :note AND status = 'pending'"
        ),
        {"note": NOTE},
    )
