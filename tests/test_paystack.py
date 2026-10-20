"""Paystack flow with the Paystack API replaced by a fake (no network, no real keys).

Checkout: the customer pays Payment 1 (down payment + delivery) on Paystack when ordering;
the order only reaches the admin once that's confirmed. Instalments 2..N are paid later.
"""
import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db, guard
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.payment_intent import PaymentIntent
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.user import User
from tests.helpers import eligible_customer
from app.services import ledger, paystack

FAKE_SECRET = "sk_test_fake_key_for_unit_tests"


class FakePaystack:
    """Stands in for the Paystack API. Tests set what verify() will report."""

    def __init__(self):
        self.initialized = []
        self.refunds = []
        self.verify_result = {}
        self.refund_error = None

    def initialize(self, **kwargs):
        self.initialized.append(kwargs)
        return {
            "authorization_url": f"https://checkout.paystack.test/{kwargs['reference']}",
            "access_code": "ac_test",
            "reference": kwargs["reference"],
        }

    def verify(self, reference):
        data = {
            "reference": reference,
            "currency": "GHS",
            "channel": "mobile_money",
            "gateway_response": "Approved",
        }
        data.update(self.verify_result)
        return data

    def refund(self, reference, amount_pesewas=None, reason=None):
        if self.refund_error:
            raise paystack.PaystackError(self.refund_error)
        self.refunds.append(reference)
        return {"id": 9001, "status": "pending"}


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


@pytest.fixture()
def env(monkeypatch):
    app = create_app()
    app.config["TESTING"] = True
    app.config["PAYSTACK_SECRET_KEY"] = FAKE_SECRET
    fake = FakePaystack()
    monkeypatch.setattr(paystack, "initialize_transaction", fake.initialize)
    monkeypatch.setattr(paystack, "verify_transaction", fake.verify)
    monkeypatch.setattr(paystack, "refund_transaction", fake.refund)
    with app.app_context():
        db.drop_all()
        db.create_all()
        client = app.test_client()
        admin = make_user("admin", "0200000301")
        merchant = make_user("merchant", "0200000302")
        customer = make_user("customer", "0200000303", kyc_status="verified")
        product = Product(
            product_id="PRD0301",
            merchant_id=merchant.id,
            name="Phone",
            price=4000,
            stock_quantity=3,
            status="active",
        )
        db.session.add(product)
        db.session.commit()
        yield {
            "client": client,
            "fake": fake,
            "product": product,
            "customer": token(client, customer.phone),
            "admin": token(client, admin.phone),
        }
        db.session.remove()
        db.drop_all()


def signed(body: dict):
    raw = json.dumps(body).encode()
    sig = hmac.new(FAKE_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    return raw, {"x-paystack-signature": sig, "Content-Type": "application/json"}


def checkout(env):
    res = env["client"].post(
        "/customer/purchase",
        headers=env["customer"],
        json={
            "accept_terms": True,
            "product_id": env["product"].id,
            "number_of_installments": 4,
            "delivery_address": "Osu, Accra",
            "down_payment_amount": 1,
        },
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()


def verify(env, reference):
    return env["client"].get(
        f"/customer/payments/paystack/verify/{reference}", headers=env["customer"]
    )


def paid_checkout(env):
    body = checkout(env)
    env["fake"].verify_result = {"status": "success", "amount": 165000}
    assert verify(env, body["reference"]).get_json()["outcome"] == "applied"
    return body


def approve(env, order_pk):
    return env["client"].put(
        f"/admin/orders/{order_pk}/approve", headers=env["admin"], json={}
    )


# ---------------- checkout: down payment ----------------


def test_checkout_starts_paystack_for_payment_1(env):
    body = checkout(env)
    assert body["status"] == "awaiting_payment"
    assert body["amount"] == 1650.0  # server amount, not the client's 1
    assert body["authorization_url"].endswith(body["reference"])
    assert env["fake"].initialized[-1]["amount_pesewas"] == 165000
    intent = PaymentIntent.query.filter_by(reference=body["reference"]).one()
    assert (
        intent.purpose == PaymentIntent.DOWN_PAYMENT and intent.order_id == body["id"]
    )


def test_admin_cannot_approve_before_down_payment(env):
    body = checkout(env)
    res = approve(env, body["id"])
    assert res.status_code == 400
    assert PurchaseOrder.query.get(body["id"]).status == "awaiting_payment"


def test_paid_checkout_goes_to_approval_and_plan_starts_paid(env):
    body = paid_checkout(env)
    order = PurchaseOrder.query.get(body["id"])
    assert order.status == "pending" and order.down_payment_status == "paid"
    assert order.down_payment_method == "paystack_mobile_money"

    res = approve(env, order.id)
    assert res.status_code == 200 and res.get_json()["down_payment_status"] == "paid"
    plan = InstalmentPlan.query.one()
    p1 = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=1).one()
    assert p1.status == "paid" and p1.payment_reference == body["reference"]
    assert plan.paid_installments == 1
    assert ledger.customer_balance(plan) == Decimal("2400.00")


def test_signed_webhook_confirms_down_payment_once(env):
    body = checkout(env)
    env["fake"].verify_result = {"status": "success", "amount": 165000}
    raw, headers = signed(
        {"event": "charge.success", "data": {"reference": body["reference"]}}
    )
    assert (
        env["client"]
        .post("/webhooks/paystack", data=raw, headers=headers)
        .get_json()["status"]
        == "applied"
    )
    assert (
        env["client"]
        .post("/webhooks/paystack", data=raw, headers=headers)
        .get_json()["status"]
        == "already_applied"
    )
    assert PurchaseOrder.query.get(body["id"]).status == "pending"


def test_webhook_rejects_bad_signature(env):
    body = checkout(env)
    raw = json.dumps(
        {"event": "charge.success", "data": {"reference": body["reference"]}}
    ).encode()
    res = env["client"].post(
        "/webhooks/paystack",
        data=raw,
        headers={"x-paystack-signature": "forged", "Content-Type": "application/json"},
    )
    assert res.status_code == 401
    assert PurchaseOrder.query.get(body["id"]).status == "awaiting_payment"


def test_underpaid_down_payment_is_not_accepted(env):
    body = checkout(env)
    env["fake"].verify_result = {"status": "success", "amount": 100}
    assert verify(env, body["reference"]).get_json()["outcome"] == "amount_mismatch"
    order = PurchaseOrder.query.get(body["id"])
    assert order.status == "awaiting_payment" and order.down_payment_status == "unpaid"


def test_failed_down_payment_can_be_retried(env):
    body = checkout(env)
    env["fake"].verify_result = {"status": "failed", "amount": 165000}
    assert verify(env, body["reference"]).get_json()["status"] == PaymentIntent.FAILED
    retry = env["client"].post(
        f"/customer/orders/{body['id']}/pay", headers=env["customer"]
    )
    assert (
        retry.status_code == 201 and retry.get_json()["reference"] != body["reference"]
    )
    env["fake"].verify_result = {"status": "success", "amount": 165000}
    assert verify(env, retry.get_json()["reference"]).get_json()["outcome"] == "applied"
    assert PurchaseOrder.query.get(body["id"]).status == "pending"


def test_second_down_payment_is_flagged_not_double_counted(env):
    body = checkout(env)
    second = (
        env["client"]
        .post(f"/customer/orders/{body['id']}/pay", headers=env["customer"])
        .get_json()
    )
    env["fake"].verify_result = {"status": "success", "amount": 165000}
    assert verify(env, body["reference"]).get_json()["outcome"] == "applied"
    assert verify(env, second["reference"]).get_json()["outcome"] == "duplicate_payment"
    order = PurchaseOrder.query.get(body["id"])
    assert order.down_payment_reference == body["reference"]


def test_rejecting_a_paid_order_refunds_the_down_payment(env):
    body = paid_checkout(env)
    res = env["client"].put(
        f"/admin/orders/{body['id']}/reject",
        headers=env["admin"],
        json={"reason": "Customer outside service area"},
    )
    assert res.status_code == 200 and res.get_json()["refund_status"] == "refunded"
    assert env["fake"].refunds == [body["reference"]]
    order = PurchaseOrder.query.get(body["id"])
    assert order.status == "rejected" and order.refund_status == "refunded"


def test_failed_refund_is_flagged_for_manual_action(env):
    body = paid_checkout(env)
    env["fake"].refund_error = "Refund not possible"
    res = env["client"].put(
        f"/admin/orders/{body['id']}/reject",
        headers=env["admin"],
        json={"reason": "Out of stock at merchant"},
    )
    assert res.get_json()["refund_status"] == "refund_failed"
    assert "manually" in res.get_json()["message"]


def test_payment_after_rejection_is_marked_for_refund(env):
    body = checkout(env)
    env["client"].put(
        f"/admin/orders/{body['id']}/reject",
        headers=env["admin"],
        json={"reason": "Duplicate order"},
    )
    env["fake"].verify_result = {"status": "success", "amount": 165000}
    assert verify(env, body["reference"]).get_json()["outcome"] == "refund_required"
    assert PurchaseOrder.query.get(body["id"]).refund_status == "refund_required"


# ---------------- instalments after approval ----------------


def test_instalment_paid_through_paystack(env):
    body = paid_checkout(env)
    approve(env, body["id"])
    plan = InstalmentPlan.query.one()
    res = env["client"].post(
        "/customer/payments/paystack/initialize",
        headers=env["customer"],
        json={"plan_id": plan.id, "amount": 1},
    )
    assert res.status_code == 201
    start = res.get_json()
    assert start["installment_number"] == 2 and start["amount"] == 800.0
    env["fake"].verify_result = {"status": "success", "amount": 80000}
    assert verify(env, start["reference"]).get_json()["outcome"] == "applied"
    assert ledger.customer_balance(plan) == Decimal("1600.00")


def test_customer_cannot_verify_someone_elses_payment(env):
    body = checkout(env)
    other = make_user("customer", "0209999999")
    res = env["client"].get(
        f"/customer/payments/paystack/verify/{body['reference']}",
        headers=token(env["client"], other.phone),
    )
    assert res.status_code == 404


def test_without_paystack_orders_go_straight_to_approval(env):
    from flask import current_app

    current_app.config["PAYSTACK_SECRET_KEY"] = None
    body = checkout(env)
    assert body["status"] == "pending" and "authorization_url" not in body
    assert env["client"].get(
        "/customer/payments/config", headers=env["customer"]
    ).get_json() == {"paystack_enabled": False}
