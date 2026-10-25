"""Merchant payment gateway: API keys, storefront URL rules, checkout sessions.

A merchant's own website creates a checkout session with its secret API key, then sends the
customer to the Tabital-hosted checkout. The purchase runs through the normal order pipeline
(see resources/customer_purchase.place_order), so the customer must be a verified Tabital customer.
"""
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from urllib.parse import urlsplit

from flask import current_app, request

from ..extensions import db
from ..models.gateway import CheckoutSession, MerchantApiKey

KEY_PREFIX = "tb_sk_"
MAX_ITEMS = 100
MAX_ACTIVE_KEYS = 5
POS, ECOMMERCE = "pos", "ecommerce"
MEDIUMS = (ECOMMERCE, POS)
CHANNELS = ("sms", "email", "in_app")
POS_LOOKUPS_PER_HOUR = 60  # customer lookups by phone, per merchant


class GatewayError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = None


# ---------------------------------------------------------------- URLs


def _allow_insecure():
    return bool(current_app.config.get("GATEWAY_ALLOW_INSECURE_URLS"))


def normalise_origin(url):
    """'https://Shop.Example.com/path?x' -> 'https://shop.example.com'. Raises GatewayError."""
    url = (url or "").strip()
    parts = urlsplit(url)
    allowed = ("https", "http") if _allow_insecure() else ("https",)
    if parts.scheme not in allowed or not parts.hostname:
        raise GatewayError("Storefront URL must be a valid https:// address")
    if parts.username or parts.password:
        raise GatewayError("Storefront URL can't contain credentials")
    try:
        port = parts.port
    except ValueError:
        raise GatewayError("Storefront URL has an invalid port")
    host = parts.hostname.lower()
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def validate_storefront_url(url):
    return normalise_origin(url)


def check_redirect_url(url, merchant, field):
    """A success/cancel URL must be on the merchant's own storefront (stops open redirects)."""
    if not merchant.shop_url:
        raise GatewayError(
            "Add your storefront URL in your profile before using the gateway", 409
        )
    parts = urlsplit((url or "").strip())
    if not url or not parts.hostname:
        raise GatewayError(f"{field} is required")
    try:
        origin = normalise_origin(f"{parts.scheme}://{parts.netloc}")
        shop_origin = normalise_origin(merchant.shop_url)
    except GatewayError:
        raise GatewayError(f"{field} must be an https:// address on your storefront")
    if origin != shop_origin:
        raise GatewayError(f"{field} must be on your storefront ({shop_origin})")
    return url.strip()[:500]


def _is_public_ip(ip):
    ip = ipaddress.ip_address(ip)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def check_webhook_url(url, resolve=True):
    """Webhook target must be https and must not point at internal addresses (SSRF)."""
    origin = normalise_origin(url)
    parts = urlsplit(url.strip())
    if _allow_insecure() or not resolve:
        return url.strip()
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except socket.gaierror:
        raise GatewayError("Webhook URL host can't be resolved")
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise GatewayError("Webhook URL must be a public address")
    return url.strip()


# ---------------------------------------------------------------- API keys


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def create_api_key(merchant, label=None):
    """Returns (MerchantApiKey, secret). The secret is only ever shown here."""
    active = MerchantApiKey.query.filter_by(
        merchant_id=merchant.id, status="active"
    ).count()
    if active >= MAX_ACTIVE_KEYS:
        raise GatewayError(f"You can have at most {MAX_ACTIVE_KEYS} active API keys", 409)
    secret = KEY_PREFIX + secrets.token_urlsafe(32)
    key = MerchantApiKey(
        merchant_id=merchant.id,
        label=(label or "").strip()[:60] or None,
        key_prefix=secret[:14],
        key_hash=_hash(secret),
        last4=secret[-4:],
    )
    db.session.add(key)
    db.session.flush()
    return key, secret


def authenticate_key(secret):
    """The merchant (User) a secret key belongs to, or None."""
    from ..models.user import User

    if not secret or not secret.startswith(KEY_PREFIX):
        return None
    candidates = MerchantApiKey.query.filter_by(
        key_prefix=secret[:14], status="active"
    ).all()
    digest = _hash(secret)
    for key in candidates:
        if hmac.compare_digest(key.key_hash, digest):
            key.last_used_at = datetime.utcnow()
            return User.query.get(key.merchant_id)
    return None


def gateway_auth(func):
    """Server-to-server auth: `Authorization: Bearer tb_sk_...` -> merchant passed as first arg."""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        secret = header[7:].strip() if header.lower().startswith("bearer ") else ""
        merchant = authenticate_key(secret)
        if not merchant:
            return {"error": "Invalid API key"}, 401
        if merchant.role != "merchant" or merchant.status not in ("approved", "active"):
            return {"error": "Your merchant account is not active"}, 403
        if merchant.gateway_enabled is False:
            return {"error": "The payment gateway is disabled for this account"}, 403
        db.session.commit()  # keep last_used_at
        return func(self, merchant, *args, **kwargs)

    return wrapper


# ---------------------------------------------------------------- sessions


def _money(value):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise GatewayError("amount must be a number")
    if amount <= 0:
        raise GatewayError("amount must be greater than zero")
    return amount


def clean_items(items):
    """Cart snapshot: [{name, quantity, unit_price}], stored as sent and checked against the total."""
    if items in (None, ""):
        return []
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise GatewayError(f"items must be a list of at most {MAX_ITEMS} entries")
    cleaned = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            raise GatewayError("Every item needs a name")
        try:
            qty = int(item.get("quantity", 1))
        except (TypeError, ValueError):
            raise GatewayError("Item quantity must be a whole number")
        if qty < 1:
            raise GatewayError("Item quantity must be at least 1")
        entry = {"name": str(item["name"]).strip()[:150], "quantity": qty}
        if item.get("unit_price") is not None:
            entry["unit_price"] = float(_money(item["unit_price"]))
        cleaned.append(entry)
    return cleaned


def _pos_customer(merchant, raw_phone):
    """The registered, eligible customer for a POS checkout, found by phone. Raises GatewayError."""
    from datetime import timedelta

    from ..models.login_attempt import LoginAttempt
    from ..models.user import User
    from . import attempts
    from .sms import to_e164

    kind = "poslookup"
    subject = f"merchant:{merchant.id}"
    since = datetime.utcnow() - timedelta(hours=1)
    # Counted before the lookup, so the endpoint can't be used to scan for registered phones
    if attempts.failures(kind, subject, since) >= POS_LOOKUPS_PER_HOUR:
        raise GatewayError("Too many customer lookups. Try again later.", 429)
    attempts.begin(kind, subject, str(raw_phone or "")[:20], attempts.client_ip())

    e164 = to_e164(str(raw_phone or ""))
    if not e164:
        raise GatewayError("customer_phone is required for POS checkouts (e.g. 0241234567)")
    local = "0" + e164[4:]
    customer = User.query.filter(
        User.role == "customer", User.phone.in_([local, e164, e164[1:]])
    ).first()
    if not customer:
        err = GatewayError("No Tabital customer is registered with this phone number", 404)
        err.code = "customer_not_found"
        raise err
    if customer.status not in ("approved", "active") or customer.kyc_status != "verified":
        err = GatewayError("This customer isn't eligible to buy on Tabital Pay yet", 409)
        err.code = "customer_not_eligible"
        raise err
    return customer, local


def notify_customer(session, merchant, channels=None, resend=False):
    """Queue the checkout link for a POS customer and send it now. Returns {channel: status}.

    One message per channel per session (outbox dedupe key), so a repeated create call can't
    spam the customer. `resend` adds a new numbered message, capped at 3 per channel.
    """
    from ..models.message_outbox import MessageOutbox
    from ..models.user import User
    from . import reminders
    from .sms import to_e164

    customer = User.query.get(session.customer_id)
    wanted = [c for c in (channels or ["sms", "in_app"]) if c in CHANNELS]
    if not wanted:
        raise GatewayError('channels must be a list of "sms", "email" or "in_app"')
    url = checkout_url(session)
    shop = merchant.business_name or merchant.full_name
    minutes = max(1, int((session.expires_at - datetime.utcnow()).total_seconds() // 60))
    title = f"{shop} is requesting payment"
    body = (
        f"Tabital Pay: {shop} requests GHS {float(session.amount):,.2f}. "
        f"Choose your plan and pay your down payment: {url} (expires in {minutes} min)"
    )
    result, ids = {}, []
    for channel in wanted:
        n = 0
        if resend:
            n = MessageOutbox.query.filter(
                MessageOutbox.dedupe_key.like(f"pos_checkout:{session.id}:{channel}%")
            ).count()
            if n >= 3:
                result[channel] = "limit_reached"
                continue
        key = f"pos_checkout:{session.id}:{channel}" + (f":{n}" if n else "")
        if MessageOutbox.query.filter_by(dedupe_key=key).first():
            result[channel] = "already_sent"
            continue
        if channel == "sms":
            to = to_e164(customer.phone)
        elif channel == "email":
            to = (customer.email or "").strip() or None
        else:
            to = None
        if channel != "in_app" and not to:
            result[channel] = "skipped"
            continue
        row = MessageOutbox(
            user_id=customer.id,
            channel=channel,
            to_address=to,
            template="pos_checkout",
            title=title,
            body=body,
            link=f"/customer/checkout/{session.token}" if channel == "in_app" else None,
            dedupe_key=key,
            status=MessageOutbox.PENDING,
        )
        db.session.add(row)
        db.session.flush()
        ids.append((channel, row))
    db.session.commit()
    if ids:
        reminders.dispatch_pending(ids=[r.id for _, r in ids])
        for channel, row in ids:
            db.session.refresh(row)
            result[channel] = "sent" if row.status == MessageOutbox.SENT else "queued"
    session.notify_status = json.dumps(result)
    db.session.commit()
    return result


def create_session(merchant, data):
    from ..models.system_settings import SystemSetting

    if not isinstance(data, dict):
        raise GatewayError("Invalid request body")
    reference = str(data.get("reference") or "").strip()
    if not reference or len(reference) > 100:
        raise GatewayError("reference is required (your own order number, max 100 characters)")
    amount = _money(data.get("amount"))
    if (data.get("currency") or "GHS").upper() != "GHS":
        raise GatewayError("Only GHS is supported")
    low = Decimal(str(SystemSetting.get_value("gateway_min_amount", 1)))
    high = Decimal(str(SystemSetting.get_value("gateway_max_amount", 50000)))
    if amount < low or amount > high:
        raise GatewayError(f"amount must be between GHS {low:,.2f} and GHS {high:,.2f}")
    items = clean_items(data.get("items"))
    priced = [i for i in items if "unit_price" in i]
    if priced and len(priced) == len(items):
        total = sum(Decimal(str(i["unit_price"])) * i["quantity"] for i in items)
        if total.quantize(Decimal("0.01")) != amount:
            raise GatewayError("amount doesn't match the items' total")
    medium = str(data.get("medium") or "ecommerce").strip().lower()
    if medium not in MEDIUMS:
        raise GatewayError('medium must be "pos" or "ecommerce"')
    customer = phone = None
    if medium == POS:
        customer, phone = _pos_customer(merchant, data.get("customer_phone"))
    # A POS till has no browser to send back, so its redirect URLs are optional (but still on-storefront)
    success_url = cancel_url = None
    if medium == "ecommerce" or data.get("success_url"):
        success_url = check_redirect_url(data.get("success_url"), merchant, "success_url")
    if medium == "ecommerce" or data.get("cancel_url"):
        cancel_url = check_redirect_url(data.get("cancel_url"), merchant, "cancel_url")

    existing = CheckoutSession.query.filter_by(
        merchant_id=merchant.id, merchant_reference=reference
    ).first()
    if existing:
        # Same reference again: hand back the same session if nothing changed, else refuse
        if (
            existing.status == CheckoutSession.OPEN
            and existing.amount == amount
            and existing.medium == medium
            and (medium != POS or existing.customer_id == customer.id)
        ):
            return existing, False
        raise GatewayError(
            "A checkout with this reference already exists", 409
        )

    session = CheckoutSession(
        token=secrets.token_urlsafe(32),
        merchant_id=merchant.id,
        merchant_reference=reference,
        amount=amount,
        currency="GHS",
        description=(str(data.get("description") or "").strip()[:200] or None),
        items=json.dumps(items) if items else None,
        customer_hint=(str(data.get("customer_hint") or "").strip()[:200] or None),
        success_url=success_url,
        cancel_url=cancel_url,
        medium=medium,
        customer_id=customer.id if customer else None,
        customer_phone=phone,
        terminal_id=(str(data.get("terminal_id") or "").strip()[:60] or None),
        expires_at=CheckoutSession.new_expiry(
            int(
                current_app.config.get(
                    "GATEWAY_POS_SESSION_MINUTES" if medium == POS else "GATEWAY_SESSION_MINUTES",
                    15 if medium == POS else 30,
                )
            )
        ),
    )
    db.session.add(session)
    db.session.flush()
    return session, True


def checkout_url(session):
    base = (current_app.config.get("FRONTEND_URL") or "").rstrip("/")
    return f"{base}/customer/checkout/{session.token}"


def session_view(session, include_url=True):
    status = session.status
    if status == CheckoutSession.OPEN and session.expires_at <= datetime.utcnow():
        status = CheckoutSession.EXPIRED
    view = {
        "session_id": session.id,
        "reference": session.merchant_reference,
        "amount": float(session.amount),
        "currency": session.currency,
        "description": session.description,
        "items": json.loads(session.items) if session.items else [],
        "status": status,
        "medium": session.medium or ECOMMERCE,
        "expires_at": session.expires_at.isoformat(),
        "order_id": None,
    }
    if session.medium == POS:
        phone = session.customer_phone or ""
        view["customer_phone"] = phone[:3] + "****" + phone[-3:] if len(phone) >= 7 else None
        view["terminal_id"] = session.terminal_id
        view["notified"] = json.loads(session.notify_status) if session.notify_status else {}
    if session.order_id:
        from ..models.purchase_order import PurchaseOrder

        order = PurchaseOrder.query.get(session.order_id)
        view["order_id"] = order.order_id if order else None
    if include_url:
        view["checkout_url"] = checkout_url(session)
    return view
