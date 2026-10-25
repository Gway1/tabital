"""Review batch 4: payment claims, waivers after part payments, customer amounts, reset-code lockout,
refused sign-ins don't extend a lock, merchant fee tier order."""
from datetime import datetime, timedelta

from app.services.auth_service import _digest
from app.extensions import db
from app.models.instalment_payment import InstalmentPayment
from app.models.login_attempt import LoginAttempt
from app.models.payment_claim import PaymentClaim
from app.models.user import User
from app.services import payments

from tests.test_ledger import (
    app_ctx,
    approved_plan,
    make_user,
    token,
)  # noqa: F401  (fixture)


def _overdue(plan, number=2, days=5):
    p = InstalmentPayment.query.filter_by(
        plan_id=plan.id, installment_number=number
    ).one()
    p.due_date = datetime.utcnow() - timedelta(days=days)
    db.session.commit()
    assert p.apply_late_fee()
    return p


def test_a_claim_never_changes_the_instalment_and_can_be_rejected(
    app_ctx,
):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p = _overdue(plan)
    customer = User.query.filter_by(role="customer").one()
    ch = token(client, customer.phone)
    body = {
        "plan_id": plan.id,
        "payment_method": "mobile_money",
        "payment_reference": "x",
    }
    res = client.post("/customer/payments/make", headers=ch, json=body)
    assert res.status_code == 202
    assert (
        client.post("/customer/payments/make", headers=ch, json=body).status_code == 409
    )  # one at a time
    p = InstalmentPayment.query.get(p.id)
    assert (
        p.status == "overdue" and p.payment_reference is None
    )  # still overdue, reference not taken
    claim = PaymentClaim.query.one()
    assert (
        client.get("/admin/payment-claims", headers=admin_h).get_json()["pending"] == 1
    )

    assert (
        client.post(
            f"/admin/payment-claims/{claim.id}/reject", headers=admin_h, json={}
        ).status_code
        == 400
    )
    res = client.post(
        f"/admin/payment-claims/{claim.id}/reject",
        headers=admin_h,
        json={"reason": "No MoMo payment with that reference"},
    )
    assert (
        res.status_code == 200 and PaymentClaim.query.get(claim.id).status == "rejected"
    )
    # The real receipt can still be recorded by collections
    assert payments.record_payment(p, 880, "mobile_money", "MM-REAL-1") == "paid"


def test_confirming_a_claim_for_part_of_the_amount(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p = _overdue(plan)
    customer = User.query.filter_by(role="customer").one()
    client.post(
        "/customer/payments/make",
        headers=token(client, customer.phone),
        json={
            "plan_id": plan.id,
            "payment_method": "mobile_money",
            "payment_reference": "MM-CLAIM-7",
        },
    )
    claim = PaymentClaim.query.one()
    res = client.post(
        f"/admin/payment-claims/{claim.id}/confirm",
        headers=admin_h,
        json={"amount_received": 500},
    )
    assert (
        res.status_code == 200
        and res.get_json()["outcome"] == "part"
        and res.get_json()["still_owed"] == 380.0
    )
    # The customer's schedule shows what's still owed and the part payment
    sched = client.get(
        "/customer/instalments", headers=token(client, customer.phone)
    ).get_json()
    rows = [
        r
        for pl in sched.get("plans", sched.get("instalments", []))
        for r in pl.get("payment_schedule", [])
    ]
    row = next(r for r in rows if r["installment_number"] == 2)
    assert row["amount_due"] == 380.0 and row["part_paid"] == 500.0


def test_waiving_after_part_payments_waives_only_what_is_unpaid(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p = _overdue(plan)  # 800 + 80 = 880
    payments.record_payment(p, 850, "mobile_money", "MM-850")
    db.session.commit()
    res = client.post(
        f"/admin/instalments/payments/{p.id}/waive-late-fee",
        headers=admin_h,
        json={"reason": "Customer paid most of it on time"},
    )
    body = res.get_json()
    assert res.status_code == 200, body
    # 50 of the fee was already paid (850 - 800), so only 30 is waived and the instalment is complete
    assert (
        "30.00" in body["message"]
        and body["status"] == "paid"
        and body["still_owed"] == 0.0
    )
    p = InstalmentPayment.query.get(p.id)
    assert p.paid_amount == 850.0 and p.late_fee == 50.0 and p.late_fee_paid


def test_reset_code_guesses_without_a_code_dont_use_up_the_owners_quota(
    app_ctx,
):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000601", business_email="esi@example.com")
    for i in range(15):  # a stranger, no code issued
        client.post(
            "/api/verify-otp",
            json={"email": "esi@example.com", "otp": "000000"},
            headers={"X-Forwarded-For": f"10.7.7.{i % 3}"},
        )
    user.reset_otp, user.reset_otp_expiry = _digest("246810"), datetime.utcnow() + timedelta(
        minutes=10
    )
    db.session.commit()
    res = client.post(
        "/api/verify-otp",
        json={"email": "esi@example.com", "otp": "246810"},
        headers={"X-Forwarded-For": "10.8.8.8"},
    )
    assert res.status_code == 200 and res.get_json()["reset_token"]


def test_refused_sign_ins_dont_extend_the_lock(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    make_user("customer", "0200000602")
    for _ in range(5):
        client.post("/login", json={"phone": "0200000602", "password": "wrong-pass"})
    for _ in range(3):  # refused: not counted
        assert (
            client.post(
                "/login", json={"phone": "0200000602", "password": "wrong-pass"}
            ).status_code
            == 429
        )
    assert LoginAttempt.query.filter_by(kind="login").count() == 5
    LoginAttempt.query.update(
        {LoginAttempt.created_at: datetime.utcnow() - timedelta(minutes=16)}
    )
    db.session.commit()
    assert (
        client.post(
            "/login", json={"phone": "0200000602", "password": "Secret123!"}
        ).status_code
        == 200
    )


def test_merchant_fee_tiers_must_stay_in_order(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    admin = make_user("admin", "0200000603")
    res = client.put(
        "/admin/business-settings",
        headers=token(client, admin.phone),
        json={
            "changes": {"merchant_fee_premium_percentage": 11},
            "reason": "Testing the order check",
        },
    )
    assert res.status_code == 400 and "in order" in str(res.get_json())
