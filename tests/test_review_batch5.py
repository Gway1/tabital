"""Review batch 5: account statuses can't bypass KYB or lock out someone who owes; approvals don't lift
restrictions; waived fees never cure; receipts can't be reused; list sorting and statuses; reset lockout."""
from datetime import datetime, timedelta

from app.services.auth_service import _digest
from app.extensions import db
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.user import User
from app.services import payments, risk

from tests.test_ledger import (
    app_ctx,
    approved_plan,
    make_user,
    token,
)  # noqa: F401  (fixture)


def test_status_changes_cant_approve_new_or_unverified_accounts(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    admin_h = token(client, make_user("admin", "0200000701").phone)
    new_merchant = make_user("merchant", "0200000702")
    new_merchant.status, new_merchant.kyc_status = "pending", "pending"
    db.session.commit()
    for url in (
        f"/admin/merchants/{new_merchant.id}/status",
        f"/admin/users/{new_merchant.id}/status",
    ):
        res = client.put(
            url,
            headers=admin_h,
            json={"status": "active", "reason": "Trying a shortcut"},
        )
        assert res.status_code == 409 and "KYB" in res.get_json()["error"]
    assert User.query.get(new_merchant.id).status == "pending"
    # A restricted merchant without KYB can't be made active either
    new_merchant.status = "restricted"
    db.session.commit()
    res = client.put(
        f"/admin/merchants/{new_merchant.id}/status",
        headers=admin_h,
        json={"status": "approved", "reason": "Reinstate"},
    )
    assert res.status_code == 409


def test_kyb_approval_never_lifts_a_restriction(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    admin_h = token(client, make_user("admin", "0200000703").phone)
    m = make_user("merchant", "0200000704")
    m.status = "restricted"
    db.session.commit()
    assert (
        client.put(f"/admin/kyc/approve/{m.id}", headers=admin_h, json={}).status_code
        == 200
    )
    m = User.query.get(m.id)
    assert m.kyc_status == "verified" and m.status == "restricted"


def test_any_status_that_blocks_sign_in_needs_nothing_owed(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    assert (
        client.put(
            f"/admin/users/{customer.id}/status",
            headers=admin_h,
            json={"status": "pending", "reason": "Testing the guard"},
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/admin/reject/{customer.id}", headers=admin_h, json={}
        ).status_code
        == 409
    )
    # A charged-off (defaulted) plan still owes money
    InstalmentPlan.query.get(plan.id).status = "defaulted"
    db.session.commit()
    assert (
        client.delete(f"/admin/customers/{customer.id}", headers=admin_h).status_code
        == 409
    )


def test_all_users_leaves_admins_out(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    manager = make_user("admin", "0200000705")
    other = make_user("admin", "0200000706")
    make_user("customer", "0200000707")
    h = token(client, manager.phone)
    users = client.get("/admin/users", headers=h).get_json()
    listed = users.get("users", users.get("data", []))
    assert listed and all(u["role"] != "admin" for u in listed)
    assert (
        client.put(
            f"/admin/users/{other.id}/status",
            headers=h,
            json={"status": "suspended", "reason": "Trying"},
        ).status_code
        == 403
    )
    assert client.delete(f"/admin/users/{other.id}", headers=h).status_code == 403


def test_lists_sort_safely_and_show_the_real_status(app_ctx):  # noqa: F811
    client, admin_h, _ = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    customer.status = "restricted"
    db.session.commit()
    for url in (
        "/admin/customers?sort_by=outstanding",
        "/admin/customers?sort_by=password",
        "/admin/merchants?sort_by=total_gmv",
        "/admin/users?sort_by=reset_token",
    ):
        assert client.get(url, headers=admin_h).status_code == 200, url
    body = client.get("/admin/customers", headers=admin_h).get_json()
    rows = body if isinstance(body, list) else body.get("customers", [])
    assert rows[0]["status"] == "restricted"


def test_a_waived_fee_never_counts_as_cured(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    p.due_date = datetime.utcnow() - timedelta(days=3)
    db.session.commit()
    assert p.apply_late_fee()
    payments.record_payment(p, 830, "mobile_money", "MM-830")
    db.session.commit()
    res = client.post(
        f"/admin/instalments/payments/{p.id}/waive-late-fee",
        headers=admin_h,
        json={"reason": "Paid most of it quickly"},
    )
    assert res.status_code == 200 and res.get_json()["status"] == "paid"
    customer = User.query.filter_by(role="customer").one()
    facts = risk.facts_for(customer)
    # Paid within the window with the fee 'settled', but part of it was waived: not a cleared late payment
    assert facts.late_payments_cured == 0 and facts.late_payments_total == 1


def test_mark_paid_cant_reuse_a_receipt(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    p2, p3 = (
        InstalmentPayment.query.filter(
            InstalmentPayment.plan_id == plan.id,
            InstalmentPayment.installment_number.in_((2, 3)),
        )
        .order_by(InstalmentPayment.installment_number)
        .all()
    )
    url = "/admin/instalments/payments/{}/mark-paid"
    assert (
        client.put(
            url.format(p2.id), headers=admin_h, json={"payment_reference": "MM-ONCE"}
        ).status_code
        == 200
    )
    res = client.put(
        url.format(p3.id), headers=admin_h, json={"payment_reference": "MM-ONCE"}
    )
    assert res.status_code == 400 and "already been recorded" in res.get_json()["error"]


def test_a_stranger_cant_use_up_the_owners_reset_allowance(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000708", business_email="ama.k@example.com")
    for _ in range(3):  # attacker: codes issued, wrong guesses from one IP
        user.reset_otp, user.reset_otp_expiry = _digest("135790"), datetime.utcnow() + timedelta(
            minutes=10
        )
        db.session.commit()
        for _ in range(5):
            client.post(
                "/api/verify-otp",
                json={"email": "ama.k@example.com", "otp": "000000"},
                headers={"X-Forwarded-For": "10.66.0.1"},
            )
    user.reset_otp, user.reset_otp_expiry = _digest("135790"), datetime.utcnow() + timedelta(
        minutes=10
    )
    db.session.commit()
    assert (
        client.post(
            "/api/verify-otp",
            json={"email": "ama.k@example.com", "otp": "000000"},
            headers={"X-Forwarded-For": "10.66.0.1"},
        ).status_code
        == 429
    )  # the attacker is stopped
    res = client.post(
        "/api/verify-otp",
        json={"email": "ama.k@example.com", "otp": "135790"},
        headers={"X-Forwarded-For": "10.1.2.3"},
    )  # the owner isn't
    assert res.status_code == 200


def test_merchants_only_get_in_through_kyb(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    admin_h = token(client, make_user("admin", "0200000709").phone)
    m = make_user("merchant", "0200000710")
    m.status, m.kyc_status = "pending", "pending"
    db.session.commit()
    assert (
        client.post(f"/admin/approve/{m.id}", headers=admin_h, json={}).status_code
        == 400
    )
    assert (
        client.put(
            f"/admin/merchants/{m.id}/kyc",
            headers=admin_h,
            json={"kyc_status": "verified"},
        ).status_code
        == 410
    )
    m = User.query.get(m.id)
    assert m.status == "pending" and m.kyc_status == "pending"


def test_the_owner_still_gets_a_reset_code_from_a_known_address(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000711", business_email="yaw@example.com")
    assert (
        client.post(
            "/login",
            json={"phone": user.phone, "password": "Secret123!"},
            headers={"X-Forwarded-For": "10.20.0.1"},
        ).status_code
        == 200
    )  # the owner's address
    from app.models.login_attempt import LoginAttempt

    for i in range(10):  # an attacker uses up today's allowance
        db.session.add(
            LoginAttempt(
                kind="reset",
                subject=f"user:{user.id}",
                identifier="yaw@example.com",
                ip=f"10.99.0.{i}",
                success=False,
            )
        )
    db.session.commit()
    client.post(
        "/forgot-password",
        json={"email": "yaw@example.com"},
        headers={"X-Forwarded-For": "10.99.0.50"},
    )
    assert User.query.get(user.id).reset_otp is None  # the attacker gets nothing
    client.post(
        "/forgot-password",
        json={"email": "yaw@example.com"},
        headers={"X-Forwarded-For": "10.20.0.1"},
    )
    assert User.query.get(user.id).reset_otp is not None  # the owner does


def test_deferment_fee_is_on_what_is_left(app_ctx):  # noqa: F811
    """Founder, 2026-09-27: 10% of what's still owed on the instalment, after part payments."""
    client, admin_h, plan = approved_plan(app_ctx)
    p = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    customer = User.query.filter_by(role="customer").one()
    ch = token(client, customer.phone)
    q = client.get(
        f"/customer/plans/{plan.id}/deferment?payment_id={p.id}", headers=ch
    ).get_json()
    assert q["fee"] == 80.0 and q["fee_base"] == 800.0
    payments.record_payment(p, 300, "mobile_money", "MM-DEF-300")
    db.session.commit()
    q = client.get(
        f"/customer/plans/{plan.id}/deferment?payment_id={p.id}", headers=ch
    ).get_json()
    assert q["allowed"] and q["fee_base"] == 500.0 and q["fee"] == 50.0


def test_suspension_takes_effect_on_the_next_request(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000712")
    h = token(client, user.phone)  # signed in before the suspension
    assert client.get("/customer/credit", headers=h).status_code == 200
    user.status = "suspended"
    db.session.commit()
    res = client.get("/customer/credit", headers=h)
    assert res.status_code == 401 and res.get_json()["code"] == "account_inactive"
    user.status = "active"  # reinstated: the same token works again
    db.session.commit()
    assert client.get("/customer/credit", headers=h).status_code == 200
