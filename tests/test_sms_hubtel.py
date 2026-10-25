"""Hubtel SMS provider (requests is faked, no network, no real credentials)."""
import pytest

from app import create_app
from app.services import sms


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def app_ctx():
    app = create_app()
    app.config.update(
        SMS_PROVIDER="hubtel",
        HUBTEL_CLIENT_ID="fake-id",
        HUBTEL_CLIENT_SECRET="fake-secret",
        HUBTEL_SENDER_ID="TabitalPay",
    )
    with app.app_context():
        yield app


def test_sends_with_basic_auth_and_international_number(app_ctx, monkeypatch):
    calls = {}

    def fake_post(url, auth=None, json=None, timeout=None):
        calls.update(url=url, auth=auth, json=json, timeout=timeout)
        return FakeResponse(201, {"rate": 1.0, "messageId": "H-1", "status": 0})

    monkeypatch.setattr("requests.post", fake_post)
    result = sms.provider().send("0241234567", "GHS 800.00 is due today.")
    assert result.ok and result.provider == "hubtel" and result.message_id == "H-1"
    assert calls["url"] == "https://smsc.hubtel.com/v1/messages/send"
    assert calls["auth"] == ("fake-id", "fake-secret")
    assert calls["json"] == {
        "From": "TabitalPay",
        "To": "233241234567",
        "Content": "GHS 800.00 is due today.",
    }
    assert calls["timeout"]


def test_api_error_is_reported_not_raised(app_ctx, monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: FakeResponse(400, {"status": 3, "message": "Invalid sender"}),
    )
    result = sms.provider().send("0241234567", "hi")
    assert not result.ok and "Invalid sender" in result.error


def test_missing_credentials_and_bad_number(app_ctx):
    assert not sms.provider().send("12345", "hi").ok
    app_ctx.config["HUBTEL_CLIENT_SECRET"] = None
    result = sms.provider().send("0241234567", "hi")
    assert not result.ok and "HUBTEL_CLIENT_ID" in result.error


def test_network_failure_is_a_result(app_ctx, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("requests.post", boom)
    result = sms.provider().send("0241234567", "hi")
    assert not result.ok and "unreachable" in result.error
