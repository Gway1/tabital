"""End-to-end checks for the Phase 0 security fixes, run against a temporary SQLite DB."""
import json
import os

import pytest

from app import create_app
from app.extensions import db, guard
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.user import User
from tests.helpers import eligible_customer


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app.test_client()
        db.session.remove()
        db.drop_all()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


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


def login(client, phone):
    res = client.post("/login", json={"phone": phone, "password": "Secret123!"})
    assert res.status_code == 200, res.get_json()
    return res.get_json()["access_token"]


def test_signup_cannot_create_admin_or_set_privileged_fields(client):
    res = client.post(
        "/register",
        json={"role": "admin", "phone": "0241111111", "password": "Secret123!"},
    )
    assert res.status_code == 400

    res = client.post(
        "/register",
        json={
            "role": "customer",
            "phone": "0242222222",
            "password": "Secret123!",
            "full_name": "Ama",
            "status": "approved",
            "kyc_status": "verified",
            "verified": True,
        },
    )
    assert res.status_code == 201
    user = User.query.filter_by(phone="0242222222").one()
    assert user.role == "customer"
    assert user.status == "pending"
    assert user.kyc_status == "pending"

    # Pending accounts can't log in yet; wrong passwords get a real 401
    assert (
        client.post(
            "/login", json={"phone": "0242222222", "password": "Secret123!"}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/login", json={"phone": "0242222222", "password": "nope"}
        ).status_code
        == 401
    )


def test_duplicate_phone_returns_409(client):
    body = {"role": "customer", "phone": "0243333333", "password": "Secret123!"}
    assert client.post("/register", json=body).status_code == 201
    res = client.post("/register", json=body)
    assert res.status_code == 409
    assert res.get_json()["field"] == "phone"


def test_purchase_is_priced_by_server_and_payment_needs_verification(client):
    admin = make_user("admin", "0200000001", full_name="Admin")
    merchant = make_user("merchant", "0200000002", business_name="Phone Shop")
    customer = make_user(
        "customer", "0200000003", full_name="Kofi", kyc_status="verified"
    )
    product = Product(
        product_id="PRD0001",
        merchant_id=merchant.id,
        name="Smartphone",
        price=4000,
        stock_quantity=5,
        status="active",
    )
    db.session.add(product)
    db.session.commit()

    c_token = login(client, customer.phone)
    a_token = login(client, admin.phone)

    # Client-supplied amounts are ignored
    res = client.post(
        "/customer/purchase",
        headers=auth(c_token),
        json={
            "accept_terms": True,
            "product_id": product.id,
            "quantity": 1,
            "number_of_installments": 4,
            "delivery_address": "12 Ring Road, Accra",
            "product_price": 1,
            "down_payment_amount": 1,
            "total_payable": 1,
            "payment_schedule": [{"installment_number": 1, "amount": 1}],
        },
    )
    assert res.status_code == 201, res.get_json()
    order = PurchaseOrder.query.one()
    assert order.product_price == 4000
    assert order.down_payment_amount == 1600
    assert order.total_payable == 4050  # 4,000 + GHS 50 delivery (D3)
    schedule = json.loads(order.payment_schedule)
    assert [p["amount"] for p in schedule] == [1650, 800, 800, 800]

    # Approve without a down payment reference: Payment 1 waits for verification
    res = client.put(
        f"/admin/orders/{order.id}/approve", headers=auth(a_token), json={}
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["down_payment_status"] == "pending_verification"
    assert body["merchant_fee"] == 400 and body["payout_amount"] == 3600  # §5.3

    plan = InstalmentPlan.query.one()
    assert plan.remaining_amount == 2400
    assert plan.paid_installments == 0
    payments = InstalmentPayment.query.order_by(
        InstalmentPayment.installment_number
    ).all()
    assert [p.status for p in payments] == [
        "pending_verification",
        "pending",
        "pending",
        "pending",
    ]

    # Admin confirms the down payment with a reference
    res = client.put(
        f"/admin/instalments/payments/{payments[0].id}/mark-paid",
        headers=auth(a_token),
        json={"payment_reference": "MOMO-123"},
    )
    assert res.status_code == 200, res.get_json()
    db.session.refresh(plan)
    assert plan.paid_installments == 1
    assert (
        plan.remaining_amount == 2400
    )  # Payment 1 doesn't reduce the financed balance

    # Customer submitting a payment does NOT mark it paid
    res = client.post(
        "/customer/payments/make",
        headers=auth(c_token),
        json={
            "plan_id": plan.id,
            "amount": 999999,
            "payment_method": "mobile_money",
            "payment_reference": "MOMO-456",
        },
    )
    assert res.status_code == 202, res.get_json()
    claim_id = res.get_json()["claim_id"]
    db.session.refresh(payments[1])
    assert (
        payments[1].status == "pending"
    )  # only a claim: late fees and collections carry on
    db.session.refresh(plan)
    assert plan.remaining_amount == 2400

    # Tabital confirms the money arrived: recorded through the normal payment service
    res = client.post(
        f"/admin/payment-claims/{claim_id}/confirm", headers=auth(a_token), json={}
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["outcome"] == "paid"
    db.session.refresh(plan)
    assert plan.remaining_amount == 1600
    assert plan.paid_installments == 2


def test_purchase_requires_verified_kyc(client):
    merchant = make_user("merchant", "0200000012")
    customer = make_user("customer", "0200000013", kyc_status="pending")
    product = Product(
        product_id="PRD0002",
        merchant_id=merchant.id,
        name="Laptop",
        price=5000,
        stock_quantity=1,
        status="active",
    )
    db.session.add(product)
    db.session.commit()
    res = client.post(
        "/customer/purchase",
        headers=auth(login(client, customer.phone)),
        json={
            "accept_terms": True,
            "product_id": product.id,
            "number_of_installments": 4,
            "delivery_address": "Kumasi",
        },
    )
    assert res.status_code == 403


def test_merchant_cannot_self_verify_or_change_money_states(client):
    merchant = make_user("merchant", "0200000022", kyc_status="pending")
    token = login(client, merchant.phone)
    assert (
        client.put(
            "/merchant/settings/kyc",
            headers=auth(token),
            json={"kyc_status": "verified"},
        ).status_code
        == 403
    )
    db.session.refresh(merchant)
    assert merchant.kyc_status == "pending"


def test_customer_cannot_edit_income(client):
    customer = make_user("customer", "0200000031", income_range="2,000 - 3,000")
    token = login(client, customer.phone)
    client.put(
        "/customer/profile",
        headers=auth(token),
        json={"income_range": "5,000+", "city": "Tema"},
    )
    db.session.refresh(customer)
    assert customer.income_range == "2,000 - 3,000"
    assert customer.city == "Tema"


def test_change_password_works(client):
    customer = make_user("customer", "0200000041")
    token = login(client, customer.phone)
    res = client.put(
        "/customer/password",
        headers=auth(token),
        json={"current_password": "Secret123!", "new_password": "NewSecret456!"},
    )
    assert res.status_code == 200, res.get_json()
    assert (
        client.post(
            "/login", json={"phone": customer.phone, "password": "NewSecret456!"}
        ).status_code
        == 200
    )


def test_otp_attempts_are_limited(client):
    user = make_user("customer", "0200000051", business_email="kofi@example.com")
    user.reset_otp = "123456"
    from datetime import datetime, timedelta

    user.reset_otp_expiry = datetime.utcnow() + timedelta(minutes=10)
    user.reset_otp_attempts = 0
    db.session.commit()
    for _ in range(5):
        assert (
            client.post(
                "/api/verify-otp", json={"email": "kofi@example.com", "otp": "000000"}
            ).status_code
            == 401
        )
    # The 6th try uses the code up, even with the right digits (same generic answer)
    assert (
        client.post(
            "/api/verify-otp", json={"email": "kofi@example.com", "otp": "123456"}
        ).status_code
        == 401
    )
    assert (
        User.query.filter_by(business_email="kofi@example.com").one().reset_otp is None
    )
