"""Deferment (CLAUDE.md §4, interim rule §13 #8): pay 10% of an instalment to push it back a month."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db
from app.models.deferment import Deferment
from app.models.instalment_payment import InstalmentPayment
from app.models.ledger import LedgerEntry
from app.models.message_outbox import MessageOutbox
from app.models.user import User
from app.services import deferment, ledger, paystack, reminders, risk
from app.services.plan_engine import add_months

from tests.test_ledger import approved_plan, token
from tests.test_paystack import FAKE_SECRET, FakePaystack


@pytest.fixture()
def env(monkeypatch):
    app = create_app()
    app.config["TESTING"] = True
    app.config[
        "PAYSTACK_SECRET_KEY"
    ] = None  # approved_plan uses the manual down payment
    fake = FakePaystack()
    monkeypatch.setattr(paystack, "initialize_transaction", fake.initialize)
    monkeypatch.setattr(paystack, "verify_transaction", fake.verify)
    with app.app_context():
        db.drop_all()
        db.create_all()
        client, admin_h, plan = approved_plan(app)
        app.config["PAYSTACK_SECRET_KEY"] = FAKE_SECRET
        customer = User.query.filter_by(role="customer").one()
        yield {
            "app": app,
            "client": client,
            "admin_h": admin_h,
            "plan": plan,
            "fake": fake,
            "cust_h": token(client, customer.phone),
            "customer": customer,
        }
        db.session.remove()
        db.drop_all()


def instalments(plan):
    return (
        InstalmentPayment.query.filter_by(plan_id=plan.id)
        .order_by(InstalmentPayment.installment_number)
        .all()
    )


def pay_fee(env, payment_id=None, amount=8000):
    url = f"/customer/plans/{env['plan'].id}/deferment"
    body = {"agree": True, **({"payment_id": payment_id} if payment_id else {})}
    res = env["client"].post(url, headers=env["cust_h"], json=body)
    assert res.status_code == 201, res.get_json()
    ref = res.get_json()["reference"]
    env["fake"].verify_result = {"status": "success", "amount": amount}
    return (
        env["client"]
        .get(f"/customer/payments/paystack/verify/{ref}", headers=env["cust_h"])
        .get_json()
    )


def test_quote_shows_fee_new_dates_and_total(env):
    p2, p3, p4 = instalments(env["plan"])[1:]
    q = (
        env["client"]
        .get(f"/customer/plans/{env['plan'].id}/deferment", headers=env["cust_h"])
        .get_json()
    )
    assert q["allowed"] and q["installment_number"] == 2
    assert q["fee"] == 80.0  # 10% of the GHS 800 instalment (§4)
    assert [s["installment_number"] for s in q["schedule"]] == [2, 3, 4]
    assert (
        q["schedule"][0]["new_due_date"]
        == add_months(p2.due_date, 1).date().isoformat()
    )
    assert q["total_payable_after"] == q["total_payable_before"] + 80
    assert q["deferments_left"] == 1


def test_paying_the_fee_moves_this_and_later_instalments(env):
    plan = env["plan"]
    before = {p.installment_number: p.due_date for p in instalments(plan)}
    balance_before = ledger.customer_balance(plan)
    out = pay_fee(env)
    assert out["outcome"] == "deferred"
    after = {p.installment_number: p for p in instalments(plan)}
    assert after[1].due_date == before[1]  # Payment 1 untouched
    for n in (2, 3, 4):
        assert after[n].due_date == add_months(before[n], 1)
        assert after[n].original_due_date == before[n]
        assert after[n].status == "pending"
    # Fee recorded as charged and paid: what's still owed doesn't change
    entries = (
        LedgerEntry.query.filter_by(plan_id=plan.id)
        .filter(LedgerEntry.reference.like("TBD-%"))
        .all()
    )
    assert sorted(e.amount_pesewas for e in entries) == [-8000, 8000]
    assert ledger.customer_balance(plan) == balance_before
    d = Deferment.query.one()
    assert (
        d.status == Deferment.APPLIED
        and d.fee_pesewas == 8000
        and len(d.to_dict()["moved"]) == 3
    )


def test_only_one_deferment_per_plan(env):
    pay_fee(env)
    q = (
        env["client"]
        .get(f"/customer/plans/{env['plan'].id}/deferment", headers=env["cust_h"])
        .get_json()
    )
    assert not q["allowed"] and any("already used" in r for r in q["reasons"])
    res = env["client"].post(
        f"/customer/plans/{env['plan'].id}/deferment",
        headers=env["cust_h"],
        json={"agree": True},
    )
    assert res.status_code == 400


def test_needs_agreement(env):
    res = env["client"].post(
        f"/customer/plans/{env['plan'].id}/deferment", headers=env["cust_h"], json={}
    )
    assert res.status_code == 400 and "agree" in res.get_json()["error"]


def test_overdue_or_down_payment_cant_be_deferred(env):
    p1, p2 = instalments(env["plan"])[:2]
    q = deferment.quote(env["plan"], p1)
    assert not q["allowed"] and any("down payment" in r for r in q["reasons"])
    p2.due_date = datetime.utcnow() - timedelta(days=1)
    db.session.commit()
    q = deferment.quote(env["plan"], p2)
    assert not q["allowed"] and any("overdue" in r for r in q["reasons"])
    # A later instalment can't be deferred while an earlier one is overdue
    q = deferment.quote(env["plan"], instalments(env["plan"])[2])
    assert any("overdue instalment first" in r for r in q["reasons"])


def test_can_defer_on_the_due_date(env):
    p2 = instalments(env["plan"])[1]
    p2.due_date = datetime.combine(datetime.utcnow().date(), datetime.min.time())
    db.session.commit()
    assert deferment.quote(env["plan"], p2)["allowed"]


def test_paused_plan_cant_be_deferred(env):
    env["plan"].paused_at = datetime.utcnow()
    db.session.commit()
    q = (
        env["client"]
        .get(f"/customer/plans/{env['plan'].id}/deferment", headers=env["cust_h"])
        .get_json()
    )
    assert not q["allowed"] and any("dispute" in r for r in q["reasons"])


def test_instalment_paid_meanwhile_means_refund(env):
    plan = env["plan"]
    res = env["client"].post(
        f"/customer/plans/{plan.id}/deferment",
        headers=env["cust_h"],
        json={"agree": True},
    )
    ref = res.get_json()["reference"]
    p2 = instalments(plan)[1]
    assert (
        env["client"]
        .put(
            f"/admin/instalments/payments/{p2.id}/mark-paid",
            headers=env["admin_h"],
            json={"payment_reference": "MOMO-2"},
        )
        .status_code
        == 200
    )
    due3 = instalments(plan)[2].due_date
    env["fake"].verify_result = {"status": "success", "amount": 8000}
    out = (
        env["client"]
        .get(f"/customer/payments/paystack/verify/{ref}", headers=env["cust_h"])
        .get_json()
    )
    assert out["outcome"] == "refund_required"
    assert Deferment.query.one().status == Deferment.REFUND_REQUIRED
    assert instalments(plan)[2].due_date == due3  # nothing moved
    listing = (
        env["client"]
        .get("/admin/deferments?status=refund_required", headers=env["admin_h"])
        .get_json()
    )
    assert len(listing["deferments"]) == 1


def test_late_fee_charged_while_paying_is_reversed(env):
    plan = env["plan"]
    res = env["client"].post(
        f"/customer/plans/{plan.id}/deferment",
        headers=env["cust_h"],
        json={"agree": True},
    )
    ref = res.get_json()["reference"]
    p2 = instalments(plan)[1]
    p2.due_date = datetime.utcnow() - timedelta(days=2)
    db.session.commit()
    p2.apply_late_fee()
    assert p2.late_fee == 80
    env["fake"].verify_result = {"status": "success", "amount": 8000}
    out = (
        env["client"]
        .get(f"/customer/payments/paystack/verify/{ref}", headers=env["cust_h"])
        .get_json()
    )
    assert out["outcome"] == "deferred"
    p2 = instalments(plan)[1]
    assert p2.late_fee == 0 and p2.late_fee_applied_date is None
    assert ledger.late_fees_net(plan) == Decimal("0.00")


def test_short_payment_isnt_applied(env):
    out = pay_fee(env, amount=5000)
    assert out["outcome"] == "amount_mismatch"
    assert Deferment.query.count() == 0


def test_reminders_run_again_for_the_new_date(env):
    plan = env["plan"]
    p2 = instalments(plan)[1]
    day_before = (p2.due_date - timedelta(days=1)).date()
    reminders.queue_due_reminders(day_before)
    sent_before = MessageOutbox.query.filter_by(payment_id=p2.id).count()
    assert sent_before >= 1
    pay_fee(env)
    p2 = instalments(plan)[1]
    reminders.queue_due_reminders((p2.due_date - timedelta(days=1)).date())
    assert MessageOutbox.query.filter_by(payment_id=p2.id).count() > sent_before


def test_deferment_never_lowers_the_limit_but_earns_no_growth(env):
    plan = env["plan"]
    customer = env["customer"]
    before, _ = risk.decide(customer)
    pay_fee(env)
    for p in instalments(plan)[1:]:
        assert (
            env["client"]
            .put(
                f"/admin/instalments/payments/{p.id}/mark-paid",
                headers=env["admin_h"],
                json={"payment_reference": f"MOMO-{p.installment_number}"},
            )
            .status_code
            == 200
        )
    db.session.refresh(plan)
    assert plan.status == "completed"
    facts = risk.facts_for(customer)
    assert facts.late_payments_total == 0  # not a late payment
    assert facts.completed_clean_plans == 0  # but no growth credit (default)
    after, _ = risk.decide(customer)
    assert after.credit_limit >= before.credit_limit
