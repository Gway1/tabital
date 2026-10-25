"""Outbound webhooks to merchants who use the payment gateway.

Events are queued in the same transaction as the change they report (so a rolled-back change
never produces one) and delivered by `flask deliver-webhooks` (also part of `flask run-daily`).
Each request is signed: X-Tabital-Signature: t=<unix>,v1=<hex HMAC-SHA256 of "<t>.<body>">.
"""
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta

import requests
from flask import current_app

from ..extensions import db
from ..models.gateway import WebhookDelivery

MAX_ATTEMPTS = 8
BACKOFF_MINUTES = (1, 5, 15, 60, 180, 360, 720)  # after attempt 1, 2, ...


def new_secret():
    return "whsec_" + secrets.token_urlsafe(24)


def sign(secret, timestamp, body):
    return hmac.new(
        secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256
    ).hexdigest()


def _session_extras(order):
    """medium / terminal for orders that came from a POS checkout."""
    from ..models.gateway import CheckoutSession

    session = CheckoutSession.query.get(order.checkout_session_id) if order.checkout_session_id else None
    extras = {"medium": (session.medium if session else None) or "ecommerce"}
    if session is not None and session.medium == "pos":
        extras["terminal_id"] = session.terminal_id
        phone = session.customer_phone or ""
        extras["customer_phone"] = phone[:3] + "****" + phone[-3:] if len(phone) >= 7 else None
    return extras


def order_payload(order, event):
    return {
        "event": event,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "data": {
            "order_id": order.order_id,
            "reference": order.merchant_reference,
            "status": order.status,
            "delivery_status": order.delivery_status,
            "down_payment_status": order.down_payment_status or "unpaid",
            "amount": order.product_price,
            "total_payable": order.total_payable,
            "number_of_installments": order.number_of_installments,
            "currency": "GHS",
            **_session_extras(order),
        },
    }


def queue_order_event(order, event):
    """Queue `event` for a gateway order's merchant. Does nothing for other orders. No commit."""
    from ..models.user import User

    if not order.checkout_session_id:
        return None
    merchant = User.query.get(order.merchant_id)
    if not merchant or not merchant.webhook_url or not merchant.webhook_secret:
        return None
    row = WebhookDelivery(
        merchant_id=merchant.id,
        order_id=order.id,
        event=event,
        payload=json.dumps(order_payload(order, event)),
    )
    db.session.add(row)
    return row


def queue_session_event(session, event):
    """Tell the merchant a checkout session ended without an order (e.g. `session.expired`). No commit."""
    from ..models.user import User

    merchant = User.query.get(session.merchant_id)
    if not merchant or not merchant.webhook_url or not merchant.webhook_secret:
        return None
    row = WebhookDelivery(
        merchant_id=merchant.id,
        event=event,
        payload=json.dumps(
            {
                "event": event,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "data": {
                    "session_id": session.id,
                    "reference": session.merchant_reference,
                    "status": session.status,
                    "medium": session.medium,
                    "terminal_id": session.terminal_id,
                    "amount": float(session.amount),
                    "currency": "GHS",
                },
            }
        ),
    )
    db.session.add(row)
    return row


def expire_stale_sessions(now=None):
    """Close open POS sessions that ran out of time and tell the till. Commits; returns the count."""
    from ..models.gateway import CheckoutSession

    now = now or datetime.utcnow()
    rows = CheckoutSession.query.filter(
        CheckoutSession.status == CheckoutSession.OPEN,
        CheckoutSession.medium == "pos",
        CheckoutSession.expires_at <= now,
    ).all()
    for session in rows:
        session.status = CheckoutSession.EXPIRED
        queue_session_event(session, "session.expired")
    db.session.commit()
    return len(rows)


def deliver(row, merchant=None):
    """One delivery attempt. Updates the row; the caller commits."""
    from ..models.user import User
    from . import gateway

    merchant = merchant or User.query.get(row.merchant_id)
    row.attempts = (row.attempts or 0) + 1
    try:
        if not merchant or not merchant.webhook_url or not merchant.webhook_secret:
            raise ValueError("webhook not configured")
        gateway.check_webhook_url(merchant.webhook_url)  # re-checked: DNS can change
        timestamp = int(time.time())
        body = row.payload
        res = requests.post(
            merchant.webhook_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Tabital-Event": row.event,
                "X-Tabital-Delivery": str(row.id),
                "X-Tabital-Signature": f"t={timestamp},v1={sign(merchant.webhook_secret, timestamp, body)}",
            },
            timeout=float(current_app.config.get("GATEWAY_WEBHOOK_TIMEOUT", 5)),
            allow_redirects=False,
        )
        row.last_status_code = res.status_code
        if 200 <= res.status_code < 300:
            row.status = WebhookDelivery.DELIVERED
            row.delivered_at = datetime.utcnow()
            row.last_error = None
            return True
        row.last_error = f"HTTP {res.status_code}"
    except (requests.RequestException, ValueError, gateway.GatewayError) as e:
        row.last_error = str(e)[:255]

    if row.attempts >= MAX_ATTEMPTS:
        row.status = WebhookDelivery.FAILED
    else:
        wait = BACKOFF_MINUTES[min(row.attempts, len(BACKOFF_MINUTES)) - 1]
        row.next_attempt_at = datetime.utcnow() + timedelta(minutes=wait)
    return False


def deliver_due(limit=100):
    """Send every pending webhook that's due. Commits. Returns (sent, failed_or_retrying)."""
    expire_stale_sessions()
    rows = (
        WebhookDelivery.query.filter(
            WebhookDelivery.status == WebhookDelivery.PENDING,
            WebhookDelivery.next_attempt_at <= datetime.utcnow(),
        )
        .order_by(WebhookDelivery.id)
        .limit(limit)
        .all()
    )
    sent = failed = 0
    for row in rows:
        if deliver(row):
            sent += 1
        else:
            failed += 1
        db.session.commit()
    return sent, failed
