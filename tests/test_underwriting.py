"""Underwriting wired into the app: purchases, KYC approval, admin tools, customer views."""
from datetime import datetime, date, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db, guard
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.risk_assessment import RiskAssessment
from app.models.user import User
from tests.helpers import eligible_customer


@pytest.fixture()
def env():
    app = create_app()
    app.config["TESTING"] = True
    app.config[
        "PAYSTACK_SECRET_KEY"
    ] = None  # manual down-payment flow keeps these tests simple
    with app.app_context():
        db.drop_all()
        db.create_all()
        client = app.test_client()
        admin = make_user("admin", "0200000401")
        merchant = make_user("merchant", "0200000402")
        product = Product(
            product_id="PRD0401",
            merchant_id=merchant.id,
            name="Phone",
            price=4000,
            stock_quantity=10,
            status="active",
        )
        db.session.add(product)
        db.session.commit()
        yield {
            "client": client,
            "product": product,
            "admin": token(client, admin.phone),
        }
        db.session.remove()
        db.drop_all()


def make_user(role, phone, **extra):
    if role == "admin":
        extra.setdefault("admin_level", "management")  # services/access.py
    user = User(
        phone=phone,
        role=role,
        status="approved",
        password=guard.hash_password("Secret123!"),
        **eligible_customer(extra, phone),
    )
    db.session.add(user)
    db.session.commit()
    return user


def token(client, phone):
    res = client.post("/login", json={"phone": phone, "password": "Secret123!"})
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def buy(env, customer, n=4, price_product=None):
    return env["client"].post(
        "/customer/purchase",
        headers=token(env["client"], customer.phone),
        json={
            "accept_terms": True,
            "product_id": (price_product or env["product"]).id,
            "number_of_installments": n,
            "delivery_address": "Kumasi",
        },
    )


def test_ineligible_purchase_is_declined_with_reasons_and_recorded(env):
    c = make_user(
        "customer", "0200000410", kyc_status="verified", monthly_salary=Decimal("1500")
    )
    res = buy(env, c)
    assert res.status_code == 403
    assert any("GHS 2,000" in r for r in res.get_json()["reasons"])
    row = RiskAssessment.query.filter_by(user_id=c.id).one()
    assert row.source == "purchase" and row.eligible is False
    assert PurchaseOrder.query.count() == 0


def high_tier_below_12_months():
    """Raise the employment trigger through the risk_rules setting (tests the override path too)."""
    import json
    from app.models.system_settings import SystemSetting

    SystemSetting.set_value(
        "risk_rules", json.dumps({"high_tier_if_employment_months_below": 12}), "json"
    )


def test_high_tier_pays_50_percent_down_on_pay_in_4(env):
    high_tier_below_12_months()
    c = make_user(
        "customer",
        "0200000411",
        kyc_status="verified",
        employment_start_date=datetime.utcnow().date() - timedelta(days=240),
    )  # ~8 months
    res = buy(env, c)
    assert res.status_code == 201, res.get_json()
    order = PurchaseOrder.query.one()
    assert order.down_payment_amount == 2000  # 50% of 4,000
    assert order.risk_assessment_id is not None
    assert RiskAssessment.query.get(order.risk_assessment_id).tier == "high"


def test_purchase_above_limit_is_refused_but_full_payment_is_allowed(env):
    c = make_user(
        "customer", "0200000412", kyc_status="verified", monthly_salary=Decimal("3000")
    )
    res = buy(env, c)  # limit 3,000 < price 4,000
    assert res.status_code == 403 and res.get_json()["available_limit"] == 3000.0
    assert buy(env, c, n=1).status_code == 201  # no credit used


def test_outstanding_balance_reduces_available_limit(env):
    c = make_user(
        "customer", "0200000413", kyc_status="verified", monthly_salary=Decimal("5000")
    )
    assert buy(env, c).status_code == 201
    order = PurchaseOrder.query.one()
    assert (
        env["client"]
        .put(
            f"/admin/orders/{order.id}/approve",
            headers=env["admin"],
            json={"down_payment_reference": "MOMO-1"},
        )
        .status_code
        == 200
    )
    credit = (
        env["client"]
        .get("/customer/credit", headers=token(env["client"], c.phone))
        .get_json()
    )
    assert (
        credit["credit_limit"] == 5000.0 and credit["available_limit"] == 2600.0
    )  # owes 2,400
    assert buy(env, c).status_code == 403  # another 4,000 phone doesn't fit


def test_kyc_approval_runs_underwriting(env):
    c = make_user("customer", "0200000414", kyc_status="pending")
    c.dob, c.national_id, c.monthly_salary = (
        "1992-03-03",
        "GHA-000000414-1",
        Decimal("4000"),
    )
    c.employment_start_date, c.salary_paid_to_bank, c.salary_verified = (
        date(2021, 1, 1),
        True,
        True,
    )
    c.momo_number = "0200000414"
    db.session.commit()
    res = env["client"].put(f"/admin/kyc/customer/approve/{c.id}", headers=env["admin"])
    assert res.status_code == 200, res.get_json()
    row = RiskAssessment.query.filter_by(user_id=c.id, source="kyc_approval").one()
    assert row.eligible and row.tier == "medium" and float(row.credit_limit) == 4000.0
    assert User.query.get(c.id).credit_limit == Decimal("4000.00")


def test_customer_detail_change_needs_reverification(env):
    c = make_user("customer", "0200000415", kyc_status="verified")
    headers = token(env["client"], c.phone)
    res = env["client"].put(
        "/customer/underwriting", headers=headers, json={"monthly_salary": 20000}
    )
    assert res.status_code == 200
    assert res.get_json()["eligible"] is False  # salary now unverified
    assert User.query.get(c.id).salary_verified is False

    res = env["client"].put(
        f"/admin/customers/{c.id}/underwriting",
        headers=env["admin"],
        json={"salary_verified": True, "note": "Checked salary certificate"},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["current"]["credit_limit"] == 20000.0


def test_invalid_details_are_rejected(env):
    c = make_user("customer", "0200000416", kyc_status="verified")
    headers = token(env["client"], c.phone)
    assert (
        env["client"]
        .put("/customer/underwriting", headers=headers, json={"monthly_salary": "lots"})
        .status_code
        == 400
    )
    assert (
        env["client"]
        .put(
            "/customer/underwriting",
            headers=headers,
            json={"employment_start_date": "2999-01-01"},
        )
        .status_code
        == 400
    )
    assert (
        env["client"]
        .put("/customer/underwriting", headers=headers, json={"momo_number": "123"})
        .status_code
        == 400
    )


def test_admin_override_needs_reason_and_is_recorded(env):
    c = make_user(
        "customer", "0200000417", kyc_status="verified", monthly_salary=Decimal("3000")
    )
    url = f"/admin/customers/{c.id}/credit-limit"
    assert (
        env["client"]
        .put(url, headers=env["admin"], json={"credit_limit": 6000})
        .status_code
        == 400
    )
    res = env["client"].put(
        url,
        headers=env["admin"],
        json={"credit_limit": 6000, "reason": "Employer guarantee on file"},
    )
    assert res.status_code == 200 and res.get_json()["credit_limit"] == 6000.0
    row = RiskAssessment.query.filter_by(user_id=c.id, source="admin_override").one()
    assert row.note == "Employer guarantee on file"
    assert buy(env, c).status_code == 201  # 4,000 now fits

    res = env["client"].put(
        url,
        headers=env["admin"],
        json={"credit_limit": None, "reason": "Guarantee expired"},
    )
    assert res.get_json()["credit_limit"] == 3000.0


def test_admin_underwriting_view_shows_history(env):
    c = make_user("customer", "0200000418", kyc_status="verified")
    env["client"].post(
        f"/admin/customers/{c.id}/underwriting/rerun", headers=env["admin"], json={}
    )
    body = (
        env["client"]
        .get(f"/admin/customers/{c.id}/underwriting", headers=env["admin"])
        .get_json()
    )
    assert body["current"]["eligible"] and body["current"]["tier"] == "medium"
    assert len(body["history"]) == 1 and body["facts"]["monthly_salary"] == "10000.00"


def test_quote_and_dashboard_show_customer_terms(env):
    high_tier_below_12_months()
    c = make_user(
        "customer",
        "0200000419",
        kyc_status="verified",
        employment_start_date=datetime.utcnow().date() - timedelta(days=240),
    )
    headers = token(env["client"], c.phone)
    quote = (
        env["client"]
        .post(
            "/installment/calculate",
            headers=headers,
            json={"product_price": 4000, "number_of_installments": 4},
        )
        .get_json()
    )
    assert quote["down_payment"]["percentage"] == 50.0
    assert quote["credit"]["tier"] == "high"
    dash = env["client"].get("/customer/dashboard/stats", headers=headers).get_json()
    assert dash["credit"]["available_limit"] == 5000.0  # 0.5 x 10,000


def test_signup_stores_underwriting_details(env):
    res = env["client"].post(
        "/register",
        json={
            "role": "customer",
            "phone": "0241112223",
            "password": "Secret123!",
            "full_name": "Esi",
            "national_id": "gha-123456789-0",
            "monthly_salary": "4500",
            "employment_start_date": "2022-02-01",
            "salary_paid_to_bank": True,
            "momo_number": "0241112223",
            "salary_verified": True,
        },
    )
    assert res.status_code == 201, res.get_json()
    u = User.query.filter_by(phone="0241112223").one()
    assert u.national_id == "GHA-123456789-0" and u.monthly_salary == Decimal("4500.00")
    assert u.employment_start_date == date(2022, 2, 1)
    assert u.salary_verified is False  # can't self-verify
    bad = env["client"].post(
        "/register",
        json={
            "role": "customer",
            "phone": "0241112224",
            "password": "Secret123!",
            "monthly_salary": "-5",
        },
    )
    assert bad.status_code == 400
