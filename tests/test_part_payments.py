"""Part payments (§5.3 example: GHS 800 instalment missed -> 80 late fee -> 880 due).

Money goes to the instalment first, then late fees; the instalment stays overdue until fully paid;
each receipt is one ledger entry with a unique reference; overpayment is refused."""
from datetime import datetime, timedelta
from decimal import Decimal

from app.extensions import db
from app.models.instalment_payment import InstalmentPayment
from app.models.ledger import LedgerEntry
from app.models.part_payment import InstalmentPartPayment
from app.services import ledger, payments

from tests.test_ledger import app_ctx, approved_plan  # noqa: F401  (fixture)


def _overdue_instalment(plan, days=5):
    p = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    p.due_date = datetime.utcnow() - timedelta(days=days)
    db.session.commit()
    assert p.apply_late_fee() and p.late_fee == 80.0
    return p


def _received(plan):
    rows = LedgerEntry.query.filter_by(
        plan_id=plan.id, entry_type=LedgerEntry.PAYMENT_RECEIVED
    ).all()
    return [float(-ledger.to_cedis(r.amount_pesewas)) for r in rows]


def test_part_then_rest_through_collections(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p = _overdue_instalment(plan)
    url = f"/admin/collection/{p.id}/mark-received"
    before = _received(plan)

    res = client.put(
        url,
        headers=admin_h,
        json={
            "amount_received": 300,
            "payment_method": "mobile_money",
            "payment_reference": "MM-PART-1",
        },
    )
    body = res.get_json()
    assert (
        res.status_code == 200
        and body["outcome"] == "part"
        and body["still_owed"] == 580.0
    )
    p = InstalmentPayment.query.get(p.id)
    assert p.status == "overdue" and p.get_total_due() == 580.0 and not p.late_fee_paid
    assert _received(plan) == before + [300.0]
    assert InstalmentPlan_paid(plan) == 1  # only the down payment counts as paid

    # Same reference twice is refused; more than owed is refused
    assert (
        client.put(
            url,
            headers=admin_h,
            json={
                "amount_received": 100,
                "payment_method": "mobile_money",
                "payment_reference": "MM-PART-1",
            },
        ).status_code
        == 400
    )
    assert (
        client.put(
            url,
            headers=admin_h,
            json={
                "amount_received": 580.01,
                "payment_method": "mobile_money",
                "payment_reference": "MM-PART-X",
            },
        ).status_code
        == 400
    )

    res = client.put(
        url,
        headers=admin_h,
        json={
            "amount_received": 580,
            "payment_method": "cash",
            "payment_reference": "RCPT-0042",
        },
    )
    assert res.status_code == 200 and res.get_json()["outcome"] == "paid"
    p = InstalmentPayment.query.get(p.id)
    assert (
        p.status == "paid"
        and p.paid_amount == 880.0
        and p.late_fee_paid
        and p.get_total_due() == 0.0
    )
    assert _received(plan) == before + [
        300.0,
        580.0,
    ]  # one entry per receipt, no double count
    assert InstalmentPlan_paid(plan) == 2
    # Ledger balance: 4,000 total + 80 fee - 1,650 down payment... - 880 = what's left on instalments 3 and 4
    assert ledger.plan_balance(plan)["outstanding"] == Decimal("1600.00")


def InstalmentPlan_paid(plan):
    from app.models.instalment import InstalmentPlan

    return InstalmentPlan.query.get(plan.id).paid_installments


def test_second_late_fee_is_on_the_still_overdue_amount(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p = _overdue_instalment(plan, days=40)
    payments.record_payment(p, 300, "mobile_money", "MM-PART-2")
    db.session.commit()
    assert p.apply_second_late_fee()
    # 10% of the 500 still unpaid on the instalment, not of 800
    assert p.late_fee == 130.0
    assert p.get_total_due() == 630.0  # 800 + 80 + 50 - 300


def test_part_payment_is_not_paid_on_time(app_ctx):  # noqa: F811
    """A part payment before the due date doesn't make the instalment paid; the rest paid late is late."""
    client, admin_h, plan = approved_plan(app_ctx)
    p = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    payments.record_payment(p, 400, "mobile_money", "MM-EARLY-1")
    db.session.commit()
    assert p.status == "pending" and p.paid_date is None and p.get_total_due() == 400.0
    assert InstalmentPartPayment.query.count() == 1


def test_paystack_checkout_pays_only_what_is_left(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p = _overdue_instalment(plan)
    payments.record_payment(p, 300, "mobile_money", "MM-PART-3")
    db.session.commit()
    from app.services.ledger import to_pesewas

    assert to_pesewas(p.get_total_due()) == 58000
    payments.mark_instalment_paid(p, "paystack_card", "PSK-REF-1", amount_received=580)
    db.session.commit()
    assert p.paid_amount == 880.0 and _received(plan)[-2:] == [300.0, 580.0]
