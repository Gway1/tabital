"""Late fees on a plan are capped at 25% of the order's total payable (founder, 2026-09-26)."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db, guard
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.user import User
from app.services import ledger, servicing

D = Decimal


@pytest.fixture()
def plan():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        merchant = User(
            phone="0200000601",
            role="merchant",
            status="approved",
            password=guard.hash_password("x"),
        )
        customer = User(
            phone="0200000602",
            role="customer",
            status="approved",
            password=guard.hash_password("x"),
        )
        db.session.add_all([merchant, customer])
        db.session.flush()
        # Small order: GHS 1,000 total payable, three instalments of GHS 600 overdue (fees of 60 each).
        # Cap = 25% of 1,000 = 250.
        p = InstalmentPlan(
            plan_id="IP777",
            merchant_id=merchant.id,
            customer_id=customer.id,
            plan_name="Phone",
            total_amount=1000,
            down_payment=0,
            remaining_amount=1000,
            number_of_installments=6,
            installment_amount=600,
            start_date=datetime.utcnow() - timedelta(days=200),
        )
        db.session.add(p)
        db.session.flush()
        ledger.record(p, "plan_opened", 1000)
        for n in range(1, 6):
            db.session.add(
                InstalmentPayment(
                    payment_id=f"PAY77{n}",
                    plan_id=p.id,
                    installment_number=n,
                    amount=600,
                    status="pending",
                    due_date=datetime.utcnow() - timedelta(days=10 * n),
                )
            )
        db.session.commit()
        yield p
        db.session.remove()
        db.drop_all()


def test_fees_stop_at_the_cap(plan):
    assert servicing.run_daily(send_reminders=False, run_autopay=False).first_fees == 5
    fees = sorted(
        float(p.late_fee) for p in InstalmentPayment.query.filter_by(plan_id=plan.id)
    )
    # 60 + 60 + 60 + 60 = 240, then only 10 left under the 250 cap, then nothing
    assert sum(fees) == 250.0
    assert ledger.late_fees_net(plan) == D("250.00")
    assert ledger.late_fee_cap_remaining(plan) == D("0.00")
    # Every overdue instalment is still marked overdue even when no fee was left to charge
    assert all(
        p.status == "overdue"
        for p in InstalmentPayment.query.filter_by(plan_id=plan.id)
    )


def test_waived_fees_free_up_the_cap(plan):
    servicing.run_daily(send_reminders=False, run_autopay=False).first_fees
    p = InstalmentPayment.query.filter(
        InstalmentPayment.plan_id == plan.id, InstalmentPayment.late_fee > 0
    ).first()
    ledger.late_fee_waived(plan, p, p.late_fee, reason="Goodwill")
    p.late_fee = 0
    db.session.commit()
    assert ledger.late_fee_cap_remaining(plan) > 0
