"""Late payments paid within 7 days with the late fee don't hurt the limit (founder, 2026-09-26)."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db, guard
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.user import User
from app.services import risk
from tests.helpers import eligible_customer

D = Decimal
TODAY = datetime(2026, 9, 26).date()


@pytest.fixture()
def customer():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        merchant = User(
            phone="0200000501",
            role="merchant",
            status="approved",
            password=guard.hash_password("x"),
        )
        c = User(
            phone="0200000502",
            role="customer",
            status="approved",
            password=guard.hash_password("x"),
            **eligible_customer(
                {"kyc_status": "verified", "monthly_salary": D("5000")}, "0200000502"
            ),
        )
        db.session.add_all([merchant, c])
        db.session.commit()
        c._merchant_id = merchant.id
        yield c
        db.session.remove()
        db.drop_all()


def late_instalment(
    c, *, days_late_paid=None, fee_paid=True, waived=False, days_overdue_unpaid=None
):
    """A plan with one instalment that was charged a late fee, paid or not."""
    due = datetime(2026, 8, 1)
    plan = InstalmentPlan(
        plan_id=f"IP{InstalmentPlan.query.count() + 1:03d}",
        merchant_id=c._merchant_id,
        customer_id=c.id,
        plan_name="Phone",
        total_amount=4000,
        down_payment=1600,
        remaining_amount=0,
        number_of_installments=2,
        installment_amount=800,
        start_date=due - timedelta(days=30),
        status="completed",
        completed_at=datetime(2026, 9, 1),
    )
    db.session.add(plan)
    db.session.flush()
    p = InstalmentPayment(
        payment_id=f"PAY{plan.id}",
        plan_id=plan.id,
        installment_number=2,
        amount=800,
        due_date=due,
        late_fee=0 if waived else 80,
        late_fee_applied_date=due + timedelta(days=1),
    )
    if days_overdue_unpaid is not None:
        p.due_date = datetime.combine(TODAY, datetime.min.time()) - timedelta(
            days=days_overdue_unpaid
        )
        p.status = "overdue"
        plan.status = "active"
        plan.completed_at = None
    else:
        p.status = "paid"
        p.paid_date = due + timedelta(days=days_late_paid)
        p.late_fee_paid = fee_paid and not waived
    db.session.add(p)
    db.session.commit()


def clean_plan(c):
    """A completed plan paid entirely on time."""
    plan = InstalmentPlan(
        plan_id=f"IP{InstalmentPlan.query.count() + 1:03d}",
        merchant_id=c._merchant_id,
        customer_id=c.id,
        plan_name="Laptop",
        total_amount=3000,
        down_payment=1200,
        remaining_amount=0,
        number_of_installments=2,
        installment_amount=1800,
        start_date=datetime(2026, 5, 1),
        status="completed",
        completed_at=datetime(2026, 6, 1),
    )
    db.session.add(plan)
    db.session.flush()
    db.session.add(
        InstalmentPayment(
            payment_id=f"PAYC{plan.id}",
            plan_id=plan.id,
            installment_number=2,
            amount=1800,
            due_date=datetime(2026, 6, 1),
            status="paid",
            paid_date=datetime(2026, 5, 30),
        )
    )
    db.session.commit()


def test_paid_within_7_days_with_fee_keeps_limit_but_earns_no_growth(customer):
    late_instalment(customer, days_late_paid=5)
    f = risk.facts_for(customer, today=TODAY)
    assert f.late_payments_total == 0 and f.late_payments_cured == 1
    assert f.completed_clean_plans == 0  # doesn't count toward an increase
    d = risk.assess(f)
    assert d.tier == "medium"
    assert d.credit_limit == D("5000.00")  # medium 1.0: no reduction, no growth
    assert any("grace window" in r for r in d.reasons)


def test_low_tier_keeps_its_tier_after_a_cured_late_payment(customer):
    """Founder 2026-09-26: cured late payments don't lower the limit or tier on any tier."""
    clean_plan(customer)
    clean_plan(customer)
    before = risk.assess(risk.facts_for(customer, today=TODAY))
    assert before.tier == "low" and before.limit_multiplier == D("1.7")  # 1.5 + 2 x 0.1

    late_instalment(customer, days_late_paid=4)  # a third plan, cured
    after = risk.assess(risk.facts_for(customer, today=TODAY))
    assert after.tier == "low" and after.limit_multiplier == D(
        "1.7"
    )  # kept; the 3rd plan adds nothing
    assert after.extended_plans_eligible is False  # streak broken by the late plan


def test_day_7_is_still_cured(customer):
    late_instalment(customer, days_late_paid=7)
    assert risk.facts_for(customer, today=TODAY).late_payments_total == 0


def test_paid_after_7_days_counts(customer):
    late_instalment(customer, days_late_paid=8)
    f = risk.facts_for(customer, today=TODAY)
    assert f.late_payments_total == 1 and f.late_payments_cured == 0
    assert risk.assess(f).credit_limit == D("2500.00")  # 1.0 - 0.5


def test_waived_fee_is_not_cured(customer):
    late_instalment(customer, days_late_paid=3, waived=True)
    assert risk.facts_for(customer, today=TODAY).late_payments_total == 1


def test_unpaid_inside_window_is_not_counted_yet_but_freezes_credit(customer):
    late_instalment(customer, days_overdue_unpaid=4)
    f = risk.facts_for(customer, today=TODAY)
    assert f.late_payments_total == 0 and f.currently_overdue
    assert risk.assess(f).available_limit == 0


def test_unpaid_past_window_counts(customer):
    late_instalment(customer, days_overdue_unpaid=9)
    assert risk.facts_for(customer, today=TODAY).late_payments_total == 1


def test_uncured_late_payment_expires_after_12_months(customer):
    late_instalment(customer, days_late_paid=20)  # due 2026-08-01, not cured
    assert risk.assess(risk.facts_for(customer, today=TODAY)).credit_limit == D(
        "2500.00"
    )

    later = datetime(2027, 8, 2).date()  # 12 months after the due date
    f = risk.facts_for(customer, today=later)
    assert f.late_payments_total == 0 and f.late_payments_expired == 1
    assert f.completed_clean_plans == 0  # still no growth from that plan
    d = risk.assess(f)
    assert d.credit_limit == D("5000.00")  # back to medium 1.0
    assert any("older than 12 months" in r for r in d.reasons)


def test_two_cured_payments_dont_make_high_tier(customer):
    late_instalment(customer, days_late_paid=2)
    late_instalment(customer, days_late_paid=6)
    d = risk.assess(risk.facts_for(customer, today=TODAY))
    assert d.tier != "high" and d.credit_limit >= D("5000.00")
