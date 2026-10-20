"""Phase 4: daily servicing, reminders, autopay, disputes, collections."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db
from app.models.dispute import Dispute
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.message_outbox import MessageOutbox
from app.models.notifications import Notifications
from app.models.payment_intent import PaymentIntent
from app.models.payment_method import PaymentMethod
from app.models.user import User
from app.services import ledger, paystack, risk, servicing

from tests.test_ledger import approved_plan, token

D = Decimal


@pytest.fixture()
def env():
    app = create_app()
    app.config["TESTING"] = True
    app.config["PAYSTACK_SECRET_KEY"] = None
    with app.app_context():
        db.drop_all()
        db.create_all()
        client, admin_headers, plan = approved_plan(
            app
        )  # GHS 4,050 plan, down payment paid
        customer = User.query.filter_by(role="customer").one()
        yield {
            "app": app,
            "client": client,
            "admin": admin_headers,
            "plan": plan,
            "customer": customer,
            "customer_headers": token(client, customer.phone),
        }
        db.session.remove()
        db.drop_all()


def instalment(plan, n):
    return InstalmentPayment.query.filter_by(
        plan_id=plan.id, installment_number=n
    ).one()


def day_after(p, days):
    return p.due_date.date() + timedelta(days=days)


def run(day):
    return servicing.run_daily(day, send_reminders=False, run_autopay=False)


# ---------------------------------------------------------------- daily job


def test_daily_job_is_idempotent_and_buckets_the_plan(env):
    p2 = instalment(env["plan"], 2)
    s1 = run(day_after(p2, 1))
    s2 = run(day_after(p2, 1))
    assert s1.first_fees == 1 and s2.first_fees == 0  # charged once
    db.session.refresh(env["plan"])
    assert env["plan"].days_past_due == 1
    assert (
        env["plan"].dpd_bucket == "dpd_1_30"
        and env["plan"].collection_stage == "reminders"
    )
    assert ledger.customer_balance(env["plan"]) == D("2480.00")  # 2,400 + 80


def test_no_fee_on_the_due_date(env):
    p2 = instalment(env["plan"], 2)
    assert run(p2.due_date.date()).first_fees == 0


def test_second_fee_at_31_days_past_due(env):
    p2 = instalment(env["plan"], 2)
    run(day_after(p2, 1))
    s = run(day_after(p2, 31))
    assert s.second_fees == 1
    db.session.refresh(p2)
    assert p2.late_fee == 160.0 and p2.late_fee_stage == 2  # 80 + another 10% of 800
    assert run(day_after(p2, 32)).second_fees == 0  # only once
    db.session.refresh(env["plan"])
    assert (
        env["plan"].dpd_bucket == "dpd_31_60"
        and env["plan"].collection_stage == "call_centre"
    )


def test_charge_off_after_90_days_and_bureau_not_legal_for_small_orders(env):
    p2 = instalment(env["plan"], 2)
    run(day_after(p2, 1))
    s = run(day_after(p2, 91))
    db.session.refresh(env["plan"])
    assert s.charged_off == 1
    assert env["plan"].status == "defaulted" and env["plan"].charged_off_at is not None
    assert env["plan"].dpd_bucket == "dpd_90_plus"
    assert (
        env["plan"].collection_stage == "bureau_reporting"
    )  # GHS 4,050 < 10,000 high-ticket


def test_legal_recovery_only_for_high_ticket(env):
    assert servicing.collection_stage(95, 12000, 10000) == "legal_recovery"
    assert servicing.collection_stage(95, 4050, 10000) == "bureau_reporting"
    assert servicing.collection_stage(45, 12000, 10000) == "call_centre"


def test_paused_plan_is_skipped(env):
    env["plan"].paused_at = datetime.utcnow()
    db.session.commit()
    p2 = instalment(env["plan"], 2)
    s = run(day_after(p2, 5))
    assert s.paused_skipped == 1 and s.first_fees == 0


# ---------------------------------------------------------------- reminders


def test_reminders_follow_the_schedule_and_are_not_repeated(env):
    from app.services import reminders

    p2 = instalment(env["plan"], 2)
    assert (
        reminders.queue_due_reminders(p2.due_date.date() - timedelta(days=3)) == 2
    )  # sms + in_app
    assert (
        reminders.queue_due_reminders(p2.due_date.date() - timedelta(days=3)) == 0
    )  # deduped
    assert (
        reminders.queue_due_reminders(p2.due_date.date() - timedelta(days=2)) == 0
    )  # not a reminder day
    sent = reminders.dispatch_pending()
    assert sent == 2
    sms = MessageOutbox.query.filter_by(channel="sms").one()
    assert (
        sms.status == "sent"
        and sms.provider == "log"
        and sms.to_address.startswith("+233")
    )
    assert "GHS 800.00" in sms.body
    assert (
        Notifications.query.filter_by(
            user_id=env["customer"].id, type="reminder"
        ).count()
        == 1
    )


def test_late_notice_mentions_grace_window_after_fee(env):
    p2 = instalment(env["plan"], 2)
    s = servicing.run_daily(day_after(p2, 1), run_autopay=False)
    assert s.reminders_queued == 2
    msg = MessageOutbox.query.filter_by(
        template="late_fee_charged", channel="sms"
    ).one()
    assert "GHS 880.00" in msg.body and "7 days" in msg.body


def test_no_reminders_for_paused_plans(env):
    from app.services import reminders

    env["plan"].paused_at = datetime.utcnow()
    db.session.commit()
    p2 = instalment(env["plan"], 2)
    assert reminders.queue_due_reminders(p2.due_date.date()) == 0


def test_admin_manual_reminder_really_sends(env):
    p2 = instalment(env["plan"], 2)
    res = env["client"].post(
        f"/admin/collection/{p2.id}/reminder",
        headers=env["admin"],
        json={"reminder_type": "sms"},
    )
    assert res.status_code == 200 and res.get_json()["status"] == "sent"
    assert MessageOutbox.query.filter_by(template="manual_reminder").count() == 1


# ---------------------------------------------------------------- autopay


class FakeCharge:
    def __init__(self, status="success"):
        self.calls = []
        self.status = status

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "status": self.status,
            "reference": kwargs["reference"],
            "amount": kwargs["amount_pesewas"],
            "currency": "GHS",
            "channel": "card",
            "gateway_response": "Approved",
        }


def saved_card(customer, autopay=True):
    m = PaymentMethod(
        customer_id=customer.id,
        authorization_code="AUTH_test_123",
        signature="SIG_1",
        email="customer@example.com",
        channel="card",
        last4="4081",
        reusable=True,
        autopay_enabled=autopay,
        is_default=True,
    )
    db.session.add(m)
    db.session.commit()
    return m


def test_autopay_charges_on_due_date_once(env, monkeypatch):
    from app.services import autopay

    env["app"].config["PAYSTACK_SECRET_KEY"] = "sk_test_fake"
    fake = FakeCharge()
    monkeypatch.setattr(paystack, "charge_authorization", fake)
    saved_card(env["customer"])
    p2 = instalment(env["plan"], 2)
    assert autopay.run(p2.due_date.date()) == 1
    assert autopay.run(p2.due_date.date()) == 0  # once per day
    assert fake.calls[0]["amount_pesewas"] == 80000
    db.session.refresh(p2)
    assert p2.status == "paid" and p2.payment_method == "paystack_card"
    assert ledger.customer_balance(env["plan"]) == D("1600.00")


def test_autopay_off_or_not_a_retry_day_does_nothing(env, monkeypatch):
    from app.services import autopay

    env["app"].config["PAYSTACK_SECRET_KEY"] = "sk_test_fake"
    fake = FakeCharge()
    monkeypatch.setattr(paystack, "charge_authorization", fake)
    card = saved_card(env["customer"], autopay=False)
    p2 = instalment(env["plan"], 2)
    assert autopay.run(p2.due_date.date()) == 0
    card.autopay_enabled = True
    db.session.commit()
    assert (
        autopay.run(p2.due_date.date() + timedelta(days=2)) == 0
    )  # retry days are 0, 1, 3
    assert autopay.run(p2.due_date.date() + timedelta(days=3)) == 1


def test_autopay_failure_is_recorded_and_not_paid(env, monkeypatch):
    from app.services import autopay

    env["app"].config["PAYSTACK_SECRET_KEY"] = "sk_test_fake"

    def boom(**kwargs):
        raise paystack.PaystackError("Insufficient funds")

    monkeypatch.setattr(paystack, "charge_authorization", boom)
    saved_card(env["customer"])
    p2 = instalment(env["plan"], 2)
    autopay.run(p2.due_date.date())
    intent = PaymentIntent.query.filter_by(channel="autopay").one()
    assert intent.status == "failed" and "Insufficient" in intent.gateway_response
    db.session.refresh(p2)
    assert p2.status == "pending"


def test_reusable_card_is_saved_after_payment_with_autopay_off(env):
    from app.services import autopay

    method = autopay.save_authorization(
        env["customer"].id,
        "c@example.com",
        {
            "authorization": {
                "authorization_code": "AUTH_x",
                "reusable": True,
                "signature": "SIG_X",
                "last4": "1234",
                "channel": "card",
                "card_type": "visa",
            }
        },
    )
    db.session.commit()
    assert method.autopay_enabled is False and method.is_default
    assert (
        autopay.save_authorization(
            env["customer"].id,
            "c@example.com",
            {"authorization": {"authorization_code": "A", "reusable": False}},
        )
        is None
    )


def test_customer_manages_saved_cards(env):
    card = saved_card(env["customer"], autopay=False)
    h = env["customer_headers"]
    listing = env["client"].get("/customer/payment-methods", headers=h).get_json()
    assert listing["payment_methods"][0]["last4"] == "4081"
    assert "authorization_code" not in listing["payment_methods"][0]  # never exposed
    res = env["client"].put(
        f"/customer/payment-methods/{card.id}",
        headers=h,
        json={"autopay_enabled": True},
    )
    assert (
        res.status_code == 200
        and res.get_json()["payment_method"]["autopay_enabled"] is True
    )
    assert (
        env["client"]
        .delete(f"/customer/payment-methods/{card.id}", headers=h)
        .status_code
        == 200
    )
    assert (
        env["client"]
        .get("/customer/payment-methods", headers=h)
        .get_json()["payment_methods"]
        == []
    )


# ---------------------------------------------------------------- disputes


def open_dispute(env):
    return env["client"].post(
        "/customer/disputes",
        headers=env["customer_headers"],
        json={
            "plan_id": env["plan"].id,
            "reason": "product_not_received",
            "description": "The phone was never delivered to my address.",
        },
    )


def test_dispute_pauses_the_plan_and_blocks_duplicates(env):
    res = open_dispute(env)
    assert res.status_code == 201, res.get_json()
    db.session.refresh(env["plan"])
    assert env["plan"].paused_at is not None
    assert open_dispute(env).status_code == 409


def test_paused_plan_is_not_overdue_for_underwriting(env):
    open_dispute(env)
    p2 = instalment(env["plan"], 2)
    facts = risk.facts_for(env["customer"], today=day_after(p2, 10))
    assert facts.currently_overdue is False and facts.max_days_past_due == 0


def test_merchant_won_resumes_with_due_dates_moved(env):
    open_dispute(env)
    d = Dispute.query.one()
    p2 = instalment(env["plan"], 2)
    original_due = p2.due_date
    env["plan"].paused_at = datetime.utcnow() - timedelta(days=10)  # paused 10 days ago
    db.session.commit()
    res = env["client"].put(
        f"/admin/disputes/{d.id}/resolve",
        headers=env["admin"],
        json={
            "outcome": "merchant_won",
            "notes": "Courier confirmed delivery with signature",
        },
    )
    assert res.status_code == 200, res.get_json()
    db.session.refresh(p2)
    db.session.refresh(env["plan"])
    assert env["plan"].paused_at is None
    assert (p2.due_date - original_due).days == 10


def test_customer_won_writes_off_the_balance(env):
    open_dispute(env)
    d = Dispute.query.one()
    res = env["client"].put(
        f"/admin/disputes/{d.id}/resolve",
        headers=env["admin"],
        json={"outcome": "customer_won", "notes": "Merchant could not prove delivery"},
    )
    assert res.status_code == 200
    db.session.refresh(env["plan"])
    assert env["plan"].status == "cancelled"
    assert ledger.customer_balance(env["plan"]) == D("0.00")
    assert res.get_json()["dispute"]["refund_amount"] == 1650.0  # what was already paid
    assert all(
        p.status in ("paid", "cancelled")
        for p in InstalmentPayment.query.filter_by(plan_id=env["plan"].id)
    )


def test_resolution_needs_outcome_and_notes(env):
    open_dispute(env)
    d = Dispute.query.one()
    assert (
        env["client"]
        .put(
            f"/admin/disputes/{d.id}/resolve",
            headers=env["admin"],
            json={"outcome": "customer_won"},
        )
        .status_code
        == 400
    )


# ---------------------------------------------------------------- collections screens


def test_collection_stats_use_standard_buckets(env):
    p2 = instalment(env["plan"], 2)
    run(day_after(p2, 1))
    stats = (
        env["client"].get("/admin/collection/stats", headers=env["admin"]).get_json()
    )
    assert "count_dpd_1_30" in stats and "overdue_1_15" not in stats


def test_collection_timeline_shows_only_real_events(env):
    p2 = instalment(env["plan"], 2)
    p2.due_date = datetime.utcnow() - timedelta(days=5)
    db.session.commit()
    servicing.run_daily(run_autopay=False)
    body = (
        env["client"]
        .get(f"/admin/collection/overdue/{p2.id}", headers=env["admin"])
        .get_json()
    )
    stages = [t["stage"] for t in body["collection_timeline"]]
    assert "Late fee charged" in stages
    assert not any("Legal" in s or "Agent" in s for s in stages)
    assert body["collection_stage"] == "Automated reminders"
