"""Slowing down guessing: passwords, password-reset codes and sign-ups.

Each attempt is written (as a failure) **before** the password or code is checked, then counted, so
parallel requests can't slip past the limit; a correct answer flips it to success.

- Per account: counted on the account (user id) when it exists, so phone and email share one limit,
  and on the typed identifier when it doesn't (the answer is the same either way).
- Per IP: only when one address fails on many different accounts (password spraying). Accounts that
  have signed in successfully from that address before keep working, so people sharing a mobile
  network address (CGNAT) aren't locked out by someone else.
- A successful password reset clears an account's lock, so a lock can't be used to keep someone out.

The client IP comes from X-Forwarded-For with TRUSTED_PROXY_COUNT hops (Render: 1). Set it to 0 if
the app is ever reached without a proxy, or clients could choose their own address.
"""
import secrets
from datetime import datetime, timedelta

from flask import current_app, request

from ..extensions import db
from ..models.login_attempt import LoginAttempt

UNLOCK = "unlock"  # identifier of the success row a password reset writes


def client_ip() -> str:
    try:
        hops = [
            h.strip()
            for h in request.headers.get("X-Forwarded-For", "").split(",")
            if h.strip()
        ]
        trusted = int(current_app.config.get("TRUSTED_PROXY_COUNT", 1))
        if trusted > 0 and len(hops) >= trusted:
            return hops[-trusted][:64]
        return (request.remote_addr or "")[:64]
    except RuntimeError:  # outside a request (CLI, direct service calls)
        return ""


def subject_for(user, identifier) -> str:
    return (
        f"user:{user.id}"
        if user is not None
        else f"id:{(identifier or '').strip().lower()}"
    )


def begin(kind, subject, identifier, ip, now=None) -> LoginAttempt:
    """Write the attempt as a failure and commit, before anything is checked."""
    now = now or datetime.utcnow()
    row = LoginAttempt(
        kind=kind,
        subject=subject[:140],
        identifier=(identifier or "")[:120],
        ip=ip or None,
        success=False,
        created_at=now,
    )
    db.session.add(row)
    if secrets.randbelow(50) == 0:  # keep the table small
        LoginAttempt.query.filter(
            LoginAttempt.created_at < now - timedelta(days=30)
        ).delete()
    db.session.commit()
    return row


def succeed(row):
    row.success = True
    db.session.commit()


def discard(row):
    """Forget an attempt that was refused before anything was checked."""
    db.session.delete(row)
    db.session.commit()


def failures(kind, subject, since, ip=None) -> int:
    """Failures for this account since `since`, or since its last success / unlock if later.
    With `ip`, only failures from that address (so a stranger can't use up the owner's allowance).
    """
    last_ok = (
        db.session.query(db.func.max(LoginAttempt.created_at))
        .filter(
            LoginAttempt.kind == kind,
            LoginAttempt.subject == subject,
            LoginAttempt.success.is_(True),
        )
        .scalar()
    )
    start = max(since, last_ok) if last_ok else since
    # ">=": clocks tick coarsely (about 16 ms on Windows), so an attempt can share its timestamp with
    # the boundary (e.g. the moment a reset code was issued); it must still count
    q = LoginAttempt.query.filter(
        LoginAttempt.kind == kind,
        LoginAttempt.subject == subject,
        LoginAttempt.success.is_(False),
        LoginAttempt.created_at >= start,
    )
    if ip is not None:
        q = q.filter(LoginAttempt.ip == (ip or None))
    return q.count()


def ip_spraying(kind, ip, subject, since, limit) -> bool:
    """This address failed on `limit`+ different accounts, and this account hasn't signed in from it."""
    if not ip:
        return False
    accounts = (
        db.session.query(db.func.count(db.distinct(LoginAttempt.subject)))
        .filter(
            LoginAttempt.kind == kind,
            LoginAttempt.ip == ip,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at > since,
        )
        .scalar()
        or 0
    )
    if accounts < limit:
        return False
    known_here = LoginAttempt.query.filter(
        LoginAttempt.kind == kind,
        LoginAttempt.ip == ip,
        LoginAttempt.subject == subject,
        LoginAttempt.success.is_(True),
        LoginAttempt.identifier != UNLOCK,
        LoginAttempt.created_at > datetime.utcnow() - timedelta(days=30),
    ).first()
    return known_here is None


def clear(user, kinds=(LoginAttempt.LOGIN, LoginAttempt.OTP)):
    """After a successful password reset: the account's counts start again."""
    now = datetime.utcnow()
    for kind in kinds:
        db.session.add(
            LoginAttempt(
                kind=kind,
                subject=subject_for(user, None),
                identifier=UNLOCK,
                ip=None,
                success=True,
                created_at=now,
            )
        )
    db.session.commit()
