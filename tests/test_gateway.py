"""Merchant payment gateway: API keys, hosted checkout sessions, orders and webhooks."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models.gateway import CheckoutSession, MerchantApiKey, WebhookDelivery
from app.models.purchase_order import PurchaseOrder
from app.models.user import User
from app.services import webhooks
from tests.test_ledger import make_user, token

SHOP = "https://shop.example.com"


@pytest.fixture()
def env():
    app = create_app()
    app.config["TESTING"] = True
    app.config["PAYSTACK_SECRET_KEY"] = None  # manual down payment: order goes to 'pending'
    with app.app_context():
        db.drop_all()
        db.create_all()
        client = app.test_client()
        merchant = make_user("merchant", "0200000202", shop_url=SHOP, business_name="Kofi Shop")
        customer = make_user("customer", "0200000203", kyc_status="verified")
        yield client, merchant, customer
        db.session.remove()
        db.drop_all()


def new_key(client, merchant):
    res = client.post(
        "/merchant/gateway/keys",
        headers=token(client, merchant.phone),
        json={"label": "site"},
    )
    assert res.status_code == 201, res.get_json()
    return res.get_json()["api_key"]


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def body(**over):
    data = {
        "reference": "WEB-1001",
        "amount": 1200,
        "items": [{"name": "Shoes", "quantity": 2, "unit_price": 600}],
        "success_url": f"{SHOP}/thanks",
        "cancel_url": f"{SHOP}/cart",
    }
    data.update(over)
    return data


def test_key_secret_is_hashed_and_shown_once(env):
    client, merchant, _ = env
    secret = new_key(client, merchant)
    key = MerchantApiKey.query.one()
    assert secret.startswith("tb_sk_") and secret not in (key.key_hash, key.key_prefix)
    assert key.key_hash == hashlib.sha256(secret.encode()).hexdigest()
    listed = client.get("/merchant/gateway/keys", headers=token(client, merchant.phone)).get_json()
    assert secret not in json.dumps(listed)


def test_customers_cannot_create_keys(env):
    client, _, customer = env
    res = client.post("/merchant/gateway/keys", headers=token(client, customer.phone), json={})
    assert res.status_code == 403


def test_session_needs_valid_key(env):
    client, merchant, _ = env
    assert client.post("/gateway/sessions", json=body()).status_code == 401
    assert client.post("/gateway/sessions", headers=bearer("tb_sk_nope"), json=body()).status_code == 401


def test_session_created_and_idempotent(env):
    client, merchant, _ = env
    secret = new_key(client, merchant)
    res = client.post("/gateway/sessions", headers=bearer(secret), json=body())
    assert res.status_code == 201, res.get_json()
    first = res.get_json()
    assert first["checkout_url"].endswith(CheckoutSession.query.one().token)
    again = client.post("/gateway/sessions", headers=bearer(secret), json=body())
    assert again.status_code == 200 and again.get_json()["session_id"] == first["session_id"]
    changed = client.post("/gateway/sessions", headers=bearer(secret), json=body(amount=1300, items=[]))
    assert changed.status_code == 409


def test_redirects_must_stay_on_storefront(env):
    client, merchant, _ = env
    secret = new_key(client, merchant)
    res = client.post(
        "/gateway/sessions",
        headers=bearer(secret),
        json=body(success_url="https://evil.example.org/x"),
    )
    assert res.status_code == 400


def test_items_must_match_amount(env):
    client, merchant, _ = env
    secret = new_key(client, merchant)
    assert client.post("/gateway/sessions", headers=bearer(secret), json=body(amount=999)).status_code == 400


def test_revoked_key_and_inactive_merchant_rejected(env):
    client, merchant, _ = env
    secret = new_key(client, merchant)
    key_id = MerchantApiKey.query.one().id
    client.delete(f"/merchant/gateway/keys/{key_id}", headers=token(client, merchant.phone))
    assert client.post("/gateway/sessions", headers=bearer(secret), json=body()).status_code == 401
    secret = new_key(client, merchant)
    merchant.status = "suspended"
    db.session.commit()
    assert client.post("/gateway/sessions", headers=bearer(secret), json=body()).status_code == 403


def make_session(client, merchant):
    secret = new_key(client, merchant)
    res = client.post("/gateway/sessions", headers=bearer(secret), json=body())
    return secret, CheckoutSession.query.one().token, res.get_json()


def test_customer_checkout_creates_order_through_normal_pipeline(env):
    client, merchant, customer = env
    secret, tok, _ = make_session(client, merchant)
    headers = token(client, customer.phone)
    view = client.get(f"/customer/gateway/sessions/{tok}", headers=headers)
    assert view.status_code == 200 and view.get_json()["merchant_name"] == "Kofi Shop"

    res = client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=headers,
        json={"accept_terms": True, "number_of_installments": 4, "delivery_address": "Osu, Accra"},
    )
    assert res.status_code == 201, res.get_json()
    order = PurchaseOrder.query.one()
    assert order.customer_id == customer.id and order.merchant_id == merchant.id
    assert order.product_id is None and order.merchant_reference == "WEB-1001"
    assert order.product_price == 1200.0 and order.status == "pending"
    assert json.loads(order.items_json)[0]["name"] == "Shoes"
    assert CheckoutSession.query.one().status == "used"

    # The session can't be used twice
    again = client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=headers,
        json={"accept_terms": True, "delivery_address": "x"},
    )
    assert again.status_code == 410
    lookup = client.get("/gateway/orders/WEB-1001", headers=bearer(secret))
    assert lookup.status_code == 200 and lookup.get_json()["order_id"] == order.order_id


def test_checkout_enforces_customer_rules(env):
    client, merchant, customer = env
    _, tok, _ = make_session(client, merchant)
    url = f"/customer/gateway/sessions/{tok}/purchase"
    payload = {"accept_terms": True, "delivery_address": "Osu"}

    # a merchant can't pay
    assert client.post(url, headers=token(client, merchant.phone), json=payload).status_code == 403
    # an unverified customer can't pay
    customer.kyc_status = "pending"
    db.session.commit()
    assert client.post(url, headers=token(client, customer.phone), json=payload).status_code == 403
    customer.kyc_status = "verified"
    db.session.commit()
    # terms must be accepted
    res = client.post(url, headers=token(client, customer.phone), json={"delivery_address": "Osu"})
    assert res.status_code == 400
    # nothing was created by the failed attempts
    assert PurchaseOrder.query.count() == 0


def test_expired_session_refused(env):
    client, merchant, customer = env
    _, tok, _ = make_session(client, merchant)
    s = CheckoutSession.query.one()
    s.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    res = client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=token(client, customer.phone),
        json={"accept_terms": True, "delivery_address": "Osu"},
    )
    assert res.status_code == 410


def test_amount_above_credit_limit_declined(env):
    client, merchant, customer = env
    secret = new_key(client, merchant)
    client.post("/gateway/sessions", headers=bearer(secret), json=body(amount=30000, items=[]))
    tok = CheckoutSession.query.one().token
    res = client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=token(client, customer.phone),
        json={"accept_terms": True, "delivery_address": "Osu", "number_of_installments": 4},
    )
    assert res.status_code in (400, 403)
    assert PurchaseOrder.query.count() == 0
    assert CheckoutSession.query.one().status == "open"


def test_webhook_signed_queued_and_retried(env, monkeypatch):
    client, merchant, customer = env
    client.application.config["GATEWAY_ALLOW_INSECURE_URLS"] = True
    res = client.put(
        "/merchant/gateway/settings",
        headers=token(client, merchant.phone),
        json={"webhook_url": "http://hooks.example.com/tabital"},
    )
    assert res.status_code == 200, res.get_json()
    whsec = res.get_json()["webhook_secret"]

    secret, tok, _ = make_session(client, merchant)
    client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=token(client, customer.phone),
        json={"accept_terms": True, "delivery_address": "Osu", "number_of_installments": 4},
    )
    row = WebhookDelivery.query.one()
    assert row.event == "order.created" and row.status == "pending"

    calls = []

    class Resp:
        def __init__(self, code):
            self.status_code = code

    def fake_post(url, data=None, headers=None, **kw):
        calls.append((url, data, headers))
        return Resp(500 if len(calls) == 1 else 200)

    monkeypatch.setattr(webhooks.requests, "post", fake_post)
    assert webhooks.deliver_due() == (0, 1)
    row = WebhookDelivery.query.one()
    assert row.status == "pending" and row.attempts == 1 and row.next_attempt_at > datetime.utcnow()
    row.next_attempt_at = datetime.utcnow()
    db.session.commit()
    assert webhooks.deliver_due() == (1, 0)
    assert WebhookDelivery.query.one().status == "delivered"

    url, sent_body, headers = calls[-1]
    ts, sig = [p.split("=", 1)[1] for p in headers["X-Tabital-Signature"].split(",")]
    expected = hmac.new(whsec.encode(), f"{ts}.{sent_body}".encode(), hashlib.sha256).hexdigest()
    assert hmac.compare_digest(sig, expected)
    assert json.loads(sent_body)["data"]["reference"] == "WEB-1001"


def test_webhook_url_must_be_public_https(env):
    client, merchant, _ = env
    headers = token(client, merchant.phone)
    for bad in ("http://hooks.example.com/x", "https://127.0.0.1/x", "ftp://x.com"):
        res = client.put("/merchant/gateway/settings", headers=headers, json={"webhook_url": bad})
        assert res.status_code == 400, bad


def test_storefront_url_normalised_and_required_for_sessions(env):
    client, merchant, _ = env
    headers = token(client, merchant.phone)
    res = client.put("/merchant/gateway/settings", headers=headers, json={"storefront_url": "https://Shop.Example.com/a?b=1"})
    assert res.status_code == 200 and User.query.get(merchant.id).shop_url == SHOP
    assert client.put("/merchant/gateway/settings", headers=headers, json={"storefront_url": "javascript:alert(1)"}).status_code == 400
    client.put("/merchant/gateway/settings", headers=headers, json={"storefront_url": ""})
    secret = new_key(client, merchant)
    assert client.post("/gateway/sessions", headers=bearer(secret), json=body()).status_code == 409


def test_admin_can_switch_gateway_off_and_on(env):
    client, merchant, customer = env
    secret, tok, _ = make_session(client, merchant)
    admin = make_user("admin", "0200000299")
    ops = make_user("admin", "0200000298", admin_level="operations")
    url = f"/admin/merchants/{merchant.id}/gateway"

    assert client.put(url, headers=token(client, ops.phone), json={"enabled": False, "reason": "fraud check"}).status_code == 403
    headers = token(client, admin.phone)
    assert client.put(url, headers=headers, json={"enabled": False}).status_code == 400
    res = client.put(url, headers=headers, json={"enabled": False, "reason": "chargeback spike"})
    assert res.status_code == 200 and res.get_json()["enabled"] is False

    # Off: no new sessions, and an open session can't be paid
    assert client.post("/gateway/sessions", headers=bearer(secret), json=body(reference="X2", items=[])).status_code == 403
    pay = client.post(
        f"/customer/gateway/sessions/{tok}/purchase",
        headers=token(client, customer.phone),
        json={"accept_terms": True, "delivery_address": "Osu"},
    )
    assert pay.status_code == 404 and PurchaseOrder.query.count() == 0

    assert client.get(url, headers=headers).get_json()["sessions"] == 1
    client.put(url, headers=headers, json={"enabled": True, "reason": "cleared"})
    assert client.post("/gateway/sessions", headers=bearer(secret), json=body(reference="X3", items=[])).status_code == 201


def test_merchant_cannot_set_up_gateway_while_admin_has_it_off(env):
    client, merchant, _ = env
    headers = token(client, merchant.phone)
    merchant.gateway_enabled = False
    db.session.commit()
    settings = client.get("/merchant/gateway/settings", headers=headers).get_json()
    assert settings["enabled"] is False
    for res in (
        client.post("/merchant/gateway/keys", headers=headers, json={}),
        client.put("/merchant/gateway/settings", headers=headers, json={"storefront_url": SHOP}),
    ):
        assert res.status_code == 403 and res.get_json()["code"] == "gateway_disabled"
    assert MerchantApiKey.query.count() == 0
    merchant.gateway_enabled = True
    db.session.commit()
    assert client.post("/merchant/gateway/keys", headers=headers, json={}).status_code == 201
