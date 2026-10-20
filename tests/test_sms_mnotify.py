"""mNotify SMS provider (requests is faked, no network, no real key)."""
import pytest

from app import create_app
from app.services import sms

FAKE_KEY = "fake-mnotify-key-for-tests"


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
        SMS_PROVIDER="mnotify", MNOTIFY_API_KEY=FAKE_KEY, MNOTIFY_SENDER_ID="TabitalPay"
    )
    with app.app_context():
        yield app


def test_sends_quick_sms_in_documented_format(app_ctx, monkeypatch):
    calls = {}

    def fake_post(url, params=None, json=None, timeout=None):
        calls.update(url=url, params=params, json=json, timeout=timeout)
        return FakeResponse(
            200,
            {
                "status": "success",
                "code": "2000",
                "message": "messages sent successfully",
                "summary": {"_id": "A59CCB70", "total_sent": 1, "total_rejected": 0},
            },
        )

    monkeypatch.setattr("requests.post", fake_post)
    result = sms.provider().send(
        "+233241234567", "Tabital Pay: GHS 800.00 is due today."
    )
    assert (
        result.ok and result.provider == "mnotify" and result.message_id == "A59CCB70"
    )
    assert calls["url"] == "https://api.mnotify.com/api/sms/quick"
    assert calls["params"] == {"key": FAKE_KEY}
    assert calls["json"]["recipient"] == ["0241234567"]  # local format per mNotify docs
    assert calls["json"]["sender"] == "TabitalPay"
    assert calls["json"]["is_schedule"] is False
    assert calls["timeout"]


def test_api_error_is_reported_not_raised(app_ctx, monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: FakeResponse(
            200,
            {"status": "error", "code": "1004", "message": "Sender ID not approved"},
        ),
    )
    result = sms.provider().send("0241234567", "hi")
    assert not result.ok and "1004" in result.error and "Sender ID" in result.error


def test_rejected_number_is_a_failure(app_ctx, monkeypatch):
    monkeypatch.setattr(
        "requests.post",
        lambda *a, **k: FakeResponse(
            200,
            {
                "status": "success",
                "code": "2000",
                "summary": {"_id": "X", "total_rejected": 1},
            },
        ),
    )
    assert not sms.provider().send("0241234567", "hi").ok


def test_network_error_does_not_leak_the_key(app_ctx, monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError(
            f"failed https://api.mnotify.com/api/sms/quick?key={FAKE_KEY}"
        )

    monkeypatch.setattr("requests.post", boom)
    result = sms.provider().send("0241234567", "hi")
    assert not result.ok and FAKE_KEY not in result.error


def test_missing_key_or_bad_sender(app_ctx):
    app_ctx.config["MNOTIFY_API_KEY"] = None
    assert "MNOTIFY_API_KEY" in sms.provider().send("0241234567", "hi").error
    app_ctx.config.update(
        MNOTIFY_API_KEY=FAKE_KEY, MNOTIFY_SENDER_ID="TabitalPayGhana"
    )  # 15 chars
    assert "11 characters" in sms.provider().send("0241234567", "hi").error


def test_phone_formats():
    assert sms.to_local("+233241234567") == "0241234567"
    assert sms.to_local("233241234567") == "0241234567"
    assert sms.to_local("0241234567") == "0241234567"
    assert sms.to_local("12345") is None
