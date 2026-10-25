"""Guessing limits (services/attempts.py): per account (existing or not), password spraying per IP,
reset codes, sign-ups; a password reset clears a lock; no public "does this account exist" lookup."""
from datetime import datetime, timedelta

from app.services.auth_service import _digest
from app.extensions import db, guard
from app.models.login_attempt import LoginAttempt
from app.models.user import User

from tests.test_ledger import app_ctx, make_user  # noqa: F401  (fixture)


def _login(client, phone, password, ip="10.0.0.1"):
    return client.post(
        "/login",
        json={"phone": phone, "password": password},
        headers={"X-Forwarded-For": ip},
    )


def _age_attempts(minutes=16):
    LoginAttempt.query.update(
        {LoginAttempt.created_at: datetime.utcnow() - timedelta(minutes=minutes)}
    )
    db.session.commit()


def test_account_locks_after_five_failures_even_with_the_right_password(
    app_ctx,
):  # noqa: F811
    client = app_ctx.test_client()
    make_user("customer", "0200000301")
    for _ in range(5):
        assert _login(client, "0200000301", "wrong-pass").status_code == 401
    res = _login(client, "0200000301", "Secret123!")
    assert res.status_code == 429 and "Too many" in res.get_json()["error"]

    _age_attempts()
    assert _login(client, "0200000301", "Secret123!").status_code == 200
    assert (
        _login(client, "0200000301", "wrong-pass").status_code == 401
    )  # counting starts again


def test_phone_and_email_share_one_limit(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    make_user("customer", "0200000305", business_email="ama@example.com")
    for i in range(5):
        _login(client, "0200000305" if i % 2 else "AMA@example.com", "wrong-pass")
    assert _login(client, "ama@example.com", "Secret123!").status_code == 429
    assert _login(client, "0200000305", "Secret123!").status_code == 429


def test_unknown_accounts_look_the_same(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    for _ in range(5):
        res = _login(client, "0200009999", "whatever1")
        assert (
            res.status_code == 401
            and res.get_json()["error"] == "Invalid phone or password"
        )
    assert _login(client, "0200009999", "whatever1").status_code == 429


def test_success_resets_the_count(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    make_user("customer", "0200000302")
    for _ in range(4):
        _login(client, "0200000302", "wrong-pass")
    assert _login(client, "0200000302", "Secret123!").status_code == 200
    for _ in range(4):
        assert _login(client, "0200000302", "wrong-pass").status_code == 401
    assert _login(client, "0200000302", "Secret123!").status_code == 200


def test_spraying_from_one_address_is_stopped_but_known_users_there_still_sign_in(
    app_ctx,
):  # noqa: F811
    client = app_ctx.test_client()
    app_ctx.config["LOGIN_MAX_ACCOUNTS_PER_IP"] = 6
    make_user("customer", "0200000303")
    make_user("customer", "0200000304")
    assert (
        _login(client, "0200000304", "Secret123!", ip="10.9.9.9").status_code == 200
    )  # a regular here
    for i in range(6):
        _login(client, f"02000070{i:02d}", "guess-pass", ip="10.9.9.9")
    assert (
        _login(client, "0200000303", "Secret123!", ip="10.9.9.9").status_code == 429
    )  # new here: blocked
    assert (
        _login(client, "0200000304", "Secret123!", ip="10.9.9.9").status_code == 200
    )  # known: fine
    assert _login(client, "0200000303", "Secret123!", ip="10.1.1.1").status_code == 200


def test_client_chosen_forwarded_hops_are_ignored(app_ctx):  # noqa: F811
    """With one trusted proxy only the last hop counts, so a made-up first entry changes nothing."""
    client = app_ctx.test_client()
    make_user("customer", "0200000306")
    for i in range(5):
        _login(client, "0200000306", "wrong-pass", ip=f"1.2.3.{i}, 10.0.0.7")
    assert LoginAttempt.query.filter_by(ip="10.0.0.7").count() == 5


def test_password_reset_clears_the_lock(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000307")
    for _ in range(6):
        _login(client, "0200000307", "wrong-pass")
    assert _login(client, "0200000307", "Secret123!").status_code == 429
    (
        user.reset_token,
        user.reset_token_expiry,
    ) = _digest("tok-123"), datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    assert (
        client.post(
            "/reset-password",
            json={"reset_token": "tok-123", "new_password": "Brand-new-9"},
        ).status_code
        == 200
    )
    assert _login(client, "0200000307", "Brand-new-9").status_code == 200


def test_reset_codes_give_one_answer_and_a_daily_limit(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    user = make_user("customer", "0200000308", business_email="kwame@example.com")
    url = "/api/verify-otp"
    # Unknown email, no code, wrong code: the same answer
    answers = {
        client.post(
            url, json={"email": "nobody@example.com", "otp": "111111"}
        ).get_json()["error"],
        client.post(
            url, json={"email": "kwame@example.com", "otp": "111111"}
        ).get_json()["error"],
    }
    user.reset_otp, user.reset_otp_expiry = "654321", datetime.utcnow() + timedelta(
        minutes=10
    )
    db.session.commit()
    answers.add(
        client.post(
            url, json={"email": "kwame@example.com", "otp": "111111"}
        ).get_json()["error"]
    )
    user.reset_otp_expiry = datetime.utcnow() - timedelta(minutes=1)
    db.session.commit()
    answers.add(
        client.post(
            url, json={"email": "kwame@example.com", "otp": "654321"}
        ).get_json()["error"]
    )
    assert len(answers) == 1

    # Across fresh codes, 10 wrong guesses against real codes a day is the limit
    for _ in range(10):
        user.reset_otp, user.reset_otp_expiry = "654321", datetime.utcnow() + timedelta(
            minutes=10
        )
        db.session.commit()
        client.post(url, json={"email": "kwame@example.com", "otp": "000000"})
    user.reset_otp, user.reset_otp_expiry = "654321", datetime.utcnow() + timedelta(
        minutes=10
    )
    db.session.commit()
    assert (
        client.post(
            url, json={"email": "kwame@example.com", "otp": "654321"}
        ).status_code
        == 429
    )


def test_no_public_account_lookup_and_signups_are_limited(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    assert (
        client.post("/api/check-user-exists", json={"phone": "0200000309"}).status_code
        == 410
    )
    app_ctx.config["SIGNUP_MAX_FAILURES_PER_IP"] = 3
    make_user("customer", "0200000309")
    body = {
        "role": "customer",
        "phone": "0200000309",
        "password": "Secret123!",
        "full_name": "X",
    }
    for _ in range(3):
        assert (
            client.post(
                "/register", json=body, headers={"X-Forwarded-For": "10.5.5.5"}
            ).status_code
            == 409
        )
    assert (
        client.post(
            "/register", json=body, headers={"X-Forwarded-For": "10.5.5.5"}
        ).status_code
        == 429
    )
