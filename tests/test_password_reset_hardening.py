"""Password reset: codes and tokens stored hashed, lookup by email or phone, SMS channel, and a
reset or password change signs out older sessions."""
import re
import time
from datetime import datetime, timedelta

from app.extensions import db, guard
from app.models.user import User
from app.services.auth_service import _digest

from tests.test_ledger import app_ctx, make_user  # noqa: F401  (fixture)


def _code_from_log(caplog):
    return re.findall(r"reset code is (\d{6})", caplog.text)[-1]


def _reset(client, **who):
    """forgot -> verify -> reset, with the code read from the database side (it is only a hash there)."""
    res = client.post("/forgot-password", json=who)
    assert res.status_code == 200
    return res


def test_code_and_token_are_stored_hashed(app_ctx, monkeypatch):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000801", business_email="hash@example.com")
    sent = []
    monkeypatch.setattr(
        "app.services.auth_service._send_in_background", lambda m: sent.append(m)
    )
    _reset(client, email="hash@example.com")
    code = re.search(r'class="otp-code">\s*(\d{6})', sent[0].html).group(1)
    stored = User.query.get(user.id).reset_otp
    assert stored != code and stored == _digest(code)

    res = client.post(
        "/api/verify-otp", json={"email": "hash@example.com", "otp": code}
    )
    assert res.status_code == 200
    token = res.get_json()["reset_token"]
    assert User.query.get(user.id).reset_token == _digest(token) != token
    assert (
        client.post(
            "/reset-password", json={"reset_token": token, "new_password": "Fresh-pass-1"}
        ).status_code
        == 200
    )


def test_reset_finds_an_account_by_plain_email_field(app_ctx, monkeypatch):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000802")
    user.email = "Plain@Example.com"
    db.session.commit()
    sent = []
    monkeypatch.setattr(
        "app.services.auth_service._send_in_background", lambda m: sent.append(m)
    )
    _reset(client, email="plain@example.com")
    assert len(sent) == 1 and User.query.get(user.id).reset_otp


def test_shared_email_gets_no_code(app_ctx, monkeypatch):  # noqa: F811
    client = app_ctx.test_client()
    a = make_user("customer", "0200000803")
    b = make_user("customer", "0200000804")
    a.email = b.email = "shared@example.com"
    db.session.commit()
    sent = []
    monkeypatch.setattr(
        "app.services.auth_service._send_in_background", lambda m: sent.append(m)
    )
    res = _reset(client, email="shared@example.com")
    assert not sent and "reset code" in res.get_json()["message"]


def test_reset_by_sms(app_ctx, caplog):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000805")
    with caplog.at_level("INFO"):
        # Written differently from how it is stored
        _reset(client, phone="+233200000805")
    code = _code_from_log(caplog)
    assert User.query.get(user.id).reset_otp == _digest(code)
    res = client.post("/api/verify-otp", json={"phone": "0200000805", "otp": code})
    assert res.status_code == 200
    assert (
        client.post(
            "/reset-password",
            json={"reset_token": res.get_json()["reset_token"], "new_password": "Sms-pass-12"},
        ).status_code
        == 200
    )
    assert (
        client.post("/login", json={"phone": "0200000805", "password": "Sms-pass-12"}).status_code
        == 200
    )


def test_unknown_phone_looks_the_same(app_ctx, caplog):  # noqa: F811
    client = app_ctx.test_client()
    make_user("customer", "0200000806")
    with caplog.at_level("INFO"):
        known = _reset(client, phone="0200000806").get_json()
        unknown = _reset(client, phone="0209999999").get_json()
    assert known == unknown
    assert client.post("/forgot-password", json={}).status_code == 400


def test_password_change_signs_out_older_sessions(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000807")
    old = guard.encode_jwt_token(user)
    # Tokens carry whole-second times: keep the old one clearly in the past
    time.sleep(1.1)
    assert (
        client.put(
            "/customer/password",
            headers={"Authorization": f"Bearer {old}"},
            json={"current_password": "Secret123!", "new_password": "Changed-pass-1"},
        ).status_code
        == 200
    )
    res = client.get("/customer/profile", headers={"Authorization": f"Bearer {old}"})
    assert res.status_code == 401 and res.get_json()["code"] == "session_expired"

    fresh = client.post(
        "/login", json={"phone": "0200000807", "password": "Changed-pass-1"}
    ).get_json()["access_token"]
    assert (
        client.get("/customer/profile", headers={"Authorization": f"Bearer {fresh}"}).status_code
        != 401
    )
