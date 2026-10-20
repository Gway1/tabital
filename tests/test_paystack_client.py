"""The Paystack HTTP client sends the right request (requests is faked, no network)."""
import hashlib
import hmac

import pytest

from app import create_app
from app.services import paystack

FAKE_SECRET = "sk_test_fake_key_for_unit_tests"


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def app_ctx():
    app = create_app()
    app.config["PAYSTACK_SECRET_KEY"] = FAKE_SECRET
    with app.app_context():
        yield app


def test_initialize_sends_pesewas_ghs_and_channels(app_ctx, monkeypatch):
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.update(url=url, json=json, headers=headers, timeout=timeout)
        return FakeResponse(
            200,
            {
                "status": True,
                "data": {
                    "authorization_url": "https://x",
                    "reference": json["reference"],
                },
            },
        )

    monkeypatch.setattr(paystack.requests, "post", fake_post)
    data = paystack.initialize_transaction(
        email="a@b.com",
        amount_pesewas=165000,
        reference="TBP-1",
        callback_url="http://localhost:4200/customer/payment-callback",
    )
    assert calls["url"] == "https://api.paystack.co/transaction/initialize"
    assert calls["json"]["amount"] == 165000
    assert calls["json"]["currency"] == "GHS"
    assert calls["json"]["channels"] == ["card", "mobile_money"]
    assert calls["headers"]["Authorization"] == f"Bearer {FAKE_SECRET}"
    assert calls["timeout"]
    assert data["reference"] == "TBP-1"


def test_errors_raise_paystack_error(app_ctx, monkeypatch):
    monkeypatch.setattr(
        paystack.requests,
        "get",
        lambda *a, **k: FakeResponse(
            400, {"status": False, "message": "Transaction reference not found"}
        ),
    )
    with pytest.raises(paystack.PaystackError, match="not found"):
        paystack.verify_transaction("nope")


def test_refund_posts_transaction_reference(app_ctx, monkeypatch):
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.update(url=url, json=json)
        return FakeResponse(
            200, {"status": True, "data": {"id": 42, "status": "pending"}}
        )

    monkeypatch.setattr(paystack.requests, "post", fake_post)
    data = paystack.refund_transaction("TBO-1-abc", reason="Order rejected")
    assert calls["url"] == "https://api.paystack.co/refund"
    assert calls["json"]["transaction"] == "TBO-1-abc"
    assert "amount" not in calls["json"]  # full refund
    assert data["id"] == 42


def test_signature_check(app_ctx):
    raw = b'{"event":"charge.success"}'
    good = hmac.new(FAKE_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    assert paystack.valid_webhook_signature(raw, good)
    assert not paystack.valid_webhook_signature(raw, "0" * 128)
    assert not paystack.valid_webhook_signature(raw + b" ", good)


def test_unconfigured_never_accepts_webhooks(app_ctx):
    app_ctx.config["PAYSTACK_SECRET_KEY"] = None
    assert not paystack.is_configured()
    assert not paystack.valid_webhook_signature(b"{}", "anything")
