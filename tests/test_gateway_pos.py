"""Checkout API, POS medium: the customer is found by phone and sent the checkout link."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app import create_app
from app.extensions import db
from app.models.gateway import CheckoutSession, WebhookDelivery
from app.models.message_outbox import MessageOutbox
from app.models.notifications import Notifications
from app.models.purchase_order import PurchaseOrder
from app.services import gateway, payments, webhooks
from tests.test_gateway import SHOP, bearer, new_key
from tests.test_ledger import make_user, token


@pytest.fixture()
def env():
    app = create_app()
    app.config["TESTING"] = True
    app.config["PAYSTACK_SECRET_KEY"] = None
    with app.app_context():
        db.drop_all()
        db.create_all()
        client = app.test_client()
        merchant = make_user("merchant", "0200000302", shop_url=SHOP, business_name="Kofi Shop")
        merchant.webhook_url = "https://hooks.example.com/tabital"
        merchant.webhook_secret = "whsec_test"
        customer = make_user("customer", "0200000303", kyc_status="verified", email="ama@example.com")
        db.session.commit()
        yield client, merchant, customer, new_key(client, merchant)
        db.session.remove()
        db.drop_all()


def pos(**over):
    data = {
        "medium": "pos",
        "reference": "TILL-1",
        "amount": 800,
        "customer_phone": "0200000303",
        "terminal_id": "T-7",
    }
    data.update(over)
    return data


def create(client, secret, **over):
    return client.post("/gateway/sessions", headers=bearer(secret), json=pos(**over))


def test_pos_session_needs_no_redirect_urls_and_notifies_customer(env):
    client, merchant, customer, secret = env
    res = create(client, secret)
    assert res.status_code == 201, res.get_json()
    view = res.get_json()
    assert view["medium"] == "pos" and view["terminal_id"] == "T-7"
    assert view["customer_phone"] == "020****303"  # masked
    assert view["notified"] == {"sms": "sent", "in_app": "sent"}
    session = CheckoutSession.query.one()
    assert session.customer_id == customer.id and session.success_url is None
    assert (session.expires_at - datetime.utcnow()) < timedelta(minutes=16)

    sms = MessageOutbox.query.filter_by(channel="sms").one()
    assert sms.to_address == "+233200000303" and f"/customer/checkout/{session.token}" in sms.body
    note = Notifications.query.filter_by(user_id=customer.id).one()
    assert note.link == f"/customer/checkout/{session.token}" and note.type == "order"


def test_phone_formats_find_the_same_customer(env):
    client, _, customer, secret = env
    res = create(client, secret, customer_phone="+233 20 000 0303")
    assert res.status_code == 201 and CheckoutSession.query.one().customer_id == customer.id


def test_repeating_a_reference_does_not_message_the_customer_twice(env):
    client, _, _, secret = env
    assert create(client, secret).status_code == 201
    again = create(client, secret)
    assert again.status_code == 200
    assert MessageOutbox.query.filter_by(template="pos_checkout").count() == 2  # sms + in_app


def test_email_channel_and_missing_email(env):
    client, _, customer, secret = env
    res = create(client, secret, channels=["email"])
    assert res.get_json()["notified"] == {"email": "sent"}
    assert MessageOutbox.query.filter_by(channel="email").one().to_address == "ama@example.com"
    customer.email = None
    db.session.commit()
    res = create(client, secret, reference="TILL-2", channels=["email", "sms"])
    assert res.get_json()["notified"] == {"email": "skipped", "sms": "sent"}


def test_bad_channels_cancel_the_session(env):
    client, _, _, secret = env
    res = create(client, secret, channels=["pigeon"])
    assert res.status_code == 400
    assert CheckoutSession.query.one().status == "cancelled"


def test_unknown_or_ineligible_customer_creates_nothing(env):
    client, _, customer, secret = env
    res = create(client, secret, customer_phone="0244444444")
    assert res.status_code == 404 and res.get_json()["code"] == "customer_not_found"
    customer.kyc_status = "pending"
    db.session.commit()
    res = create(client, secret)
    assert res.status_code == 409 and res.get_json()["code"] == "customer_not_eligible"
    assert create(client, secret, customer_phone="").status_code == 400
    assert CheckoutSession.query.count() == 0 and MessageOutbox.query.count() == 0


def test_medium_is_validated_and_ecommerce_still_needs_redirects(env):
    client, _, _, secret = env
    assert create(client, secret, medium="kiosk").status_code == 400
    res = create(client, secret, medium="ecommerce")  # no success/cancel url
    assert res.status_code == 400
    ok = client.post(
        "/gateway/sessions",
        headers=bearer(secret),
        json={"reference": "W-1", "amount": 50, "success_url": f"{SHOP}/a", "cancel_url": f"{SHOP}/b"},
    )
    assert ok.status_code == 201 and ok.get_json()["medium"] == "ecommerce"


def test_pos_redirect_urls_if_given_must_be_on_the_storefront(env):
    client, _, _, secret = env
    assert create(client, secret, success_url="https://evil.example.com/x").status_code == 400


def test_lookup_rate_limit(env, monkeypatch):
    client, _, _, secret = env
    monkeypatch.setattr(gateway, "POS_LOOKUPS_PER_HOUR", 3)
    codes = [create(client, secret, reference=f"R{i}", customer_phone="0244444444").status_code for i in range(5)]
    assert codes[:3] == [404, 404, 404] and codes[3:] == [429, 429]


def test_only_the_phone_owner_can_open_or_pay(env):
    client, _, customer, secret = env
    create(client, secret)
    tok = CheckoutSession.query.one().token
    other = make_user("customer", "0200000304", kyc_status="verified")
    other_headers = token(client, other.phone)
    assert client.get(f"/customer/gateway/sessions/{tok}", headers=other_headers).status_code == 403
    pay = client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=other_headers,
        json={"accept_terms": True, "delivery_address": "Osu"},
    )
    assert pay.status_code == 403 and PurchaseOrder.query.count() == 0

    headers = token(client, customer.phone)
    assert client.get(f"/customer/gateway/sessions/{tok}", headers=headers).status_code == 200
    pay = client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=headers,
        json={"accept_terms": True, "number_of_installments": 4, "delivery_address": "Osu"},
    )
    assert pay.status_code == 201, pay.get_json()
    assert PurchaseOrder.query.one().merchant_reference == "TILL-1"


def test_down_payment_sends_order_paid_with_pos_details(env):
    client, merchant, customer, secret = env
    create(client, secret)
    tok = CheckoutSession.query.one().token
    client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=token(client, customer.phone),
        json={"accept_terms": True, "number_of_installments": 4, "delivery_address": "Osu"},
    )
    order = PurchaseOrder.query.one()
    order.status, order.down_payment_status = "awaiting_payment", "unpaid"
    WebhookDelivery.query.delete()
    db.session.commit()

    intent = SimpleNamespace(order=order, channel="mobile_money", reference="PS-1", gateway_response=None, status=None)
    payments._apply_down_payment(intent)
    db.session.commit()
    events = [w.event for w in WebhookDelivery.query.order_by(WebhookDelivery.id)]
    assert events == ["order.paid", "order.pending"]
    data = json.loads(WebhookDelivery.query.first().payload)["data"]
    assert data["medium"] == "pos" and data["terminal_id"] == "T-7"
    assert data["customer_phone"] == "020****303" and data["reference"] == "TILL-1"


def test_cancel_and_resend(env):
    client, _, _, secret = env
    sid = create(client, secret).get_json()["session_id"]
    resend = client.post(f"/gateway/sessions/{sid}/resend", headers=bearer(secret), json={"channels": ["sms"]})
    assert resend.status_code == 200 and resend.get_json()["notified"]["sms"] == "sent"
    assert MessageOutbox.query.filter_by(channel="sms").count() == 2
    for _ in range(3):  # capped at 3 messages per channel
        client.post(f"/gateway/sessions/{sid}/resend", headers=bearer(secret), json={"channels": ["sms"]})
    assert MessageOutbox.query.filter_by(channel="sms").count() == 3

    cancel = client.post(f"/gateway/sessions/{sid}/cancel", headers=bearer(secret))
    assert cancel.status_code == 200 and cancel.get_json()["status"] == "cancelled"
    assert client.post(f"/gateway/sessions/{sid}/resend", headers=bearer(secret), json={}).status_code == 409
    tok = CheckoutSession.query.one().token
    assert client.get(f"/customer/gateway/sessions/{tok}", headers=token(client, "0200000303")).get_json()["status"] == "cancelled"


def test_unpaid_pos_session_expires_and_tells_the_till(env):
    client, _, _, secret = env
    create(client, secret)
    session = CheckoutSession.query.one()
    session.expires_at = datetime.utcnow() - timedelta(minutes=1)
    WebhookDelivery.query.delete()
    db.session.commit()
    assert webhooks.expire_stale_sessions() == 1
    assert CheckoutSession.query.one().status == "expired"
    row = WebhookDelivery.query.one()
    assert row.event == "session.expired" and json.loads(row.payload)["data"]["reference"] == "TILL-1"
    assert webhooks.expire_stale_sessions() == 0
