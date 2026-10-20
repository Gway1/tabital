"""Fraud checks (Phase 6, CLAUDE.md §9 D and E).

Each check raises a FraudSignal (deduplicated) for an admin to review:
- block:  stops the customer's new purchases, or the merchant's payouts, until an admin clears it
- review: shows up in the queue and on the order, but doesn't stop anything by itself
- info:   recorded only

Nothing here decides fraud on its own; it makes sure a person looks. When in doubt the
signal is `review`, because shared phones and shared devices are common in Ghana.
"""
import json
import re
from datetime import datetime, timedelta

from flask import request
from sqlalchemy import func

from ..extensions import db
from ..models.identity import DeviceSeen, FraudSignal
from ..models.system_settings import SystemSetting
from ..models.user import User

MAX_DEVICE_ID = 64
_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _setting(key, default):
    return SystemSetting.get_value(key, default)


def normalise_ghana_card(value):
    """'gha 123456789 0' -> 'GHA-123456789-0'. Returns None if it isn't a Ghana Card number."""
    if not value:
        return None
    digits = re.sub(r"[^0-9A-Za-z]", "", str(value)).upper()
    m = re.fullmatch(r"GHA(\d{9})(\d)", digits)
    return f"GHA-{m.group(1)}-{m.group(2)}" if m else None


def _digits(value):
    return (
        re.sub(r"\D", "", str(value or ""))[-9:] or None
    )  # last 9 digits: 024.. == +23324..


def raise_signal(
    user,
    code,
    message,
    severity=FraudSignal.REVIEW,
    related=None,
    details=None,
    key=None,
):
    """Create a signal once per (code, user, related, key). Doesn't commit. Returns the signal."""
    dedupe = f"{code}:{user.id}:{related.id if related else '-'}:{key or '-'}"[:160]
    existing = FraudSignal.query.filter_by(dedupe_key=dedupe).first()
    if existing:
        return existing
    signal = FraudSignal(
        user_id=user.id,
        related_user_id=related.id if related else None,
        code=code,
        severity=severity,
        message=message[:300],
        dedupe_key=dedupe,
        details_json=json.dumps(details) if details else None,
    )
    db.session.add(signal)
    db.session.flush()
    return signal


def blocking_signals(user):
    """Open or confirmed block-level signals on this account."""
    return FraudSignal.query.filter(
        FraudSignal.user_id == user.id,
        FraudSignal.severity == FraudSignal.BLOCK,
        FraudSignal.status.in_([FraudSignal.OPEN, FraudSignal.CONFIRMED]),
    ).all()


def open_signals(user):
    return FraudSignal.query.filter(
        FraudSignal.user_id == user.id,
        FraudSignal.status.in_([FraudSignal.OPEN, FraudSignal.CONFIRMED]),
    ).all()


def merchant_hold_reason(merchant):
    """Why this merchant's payouts must wait for a fraud review, or None."""
    if blocking_signals(merchant):
        return "Fraud review in progress. Tabital will contact you."
    return None


# ------------------------------------------------------------------ devices (§9D)


def device_from_request():
    """(device_id, flags) sent by the app in X-Device-Id / X-Device-Flags, if valid."""
    try:
        device_id = (request.headers.get("X-Device-Id") or "").strip()
        flags = (request.headers.get("X-Device-Flags") or "").strip()[:200]
    except RuntimeError:  # outside a request
        return None, ""
    if not _DEVICE_ID_RE.match(device_id):
        return None, flags
    return device_id, flags


def record_device(user, device_id=None, flags=None):
    """Remember that this account used this device, and flag suspicious patterns. No commit."""
    if device_id is None:
        device_id, flags = device_from_request()
    if not device_id or user.role == "admin":
        return
    try:
        ip = (
            (request.headers.get("X-Forwarded-For") or request.remote_addr or "")
            .split(",")[0]
            .strip()[:64]
        )
        ua = (request.headers.get("User-Agent") or "")[:300]
    except RuntimeError:
        ip, ua = None, None
    now = datetime.utcnow()
    seen = DeviceSeen.query.filter_by(device_id=device_id, user_id=user.id).first()
    if seen:
        seen.last_seen, seen.last_ip, seen.user_agent = now, ip, ua
        if flags:
            seen.flags = flags
    else:
        seen = DeviceSeen(
            device_id=device_id,
            user_id=user.id,
            last_ip=ip,
            user_agent=ua,
            flags=flags or None,
            first_seen=now,
            last_seen=now,
        )
        db.session.add(seen)
    db.session.flush()

    if flags and ("webdriver" in flags or "emulator" in flags):
        raise_signal(
            user,
            "automated_browser",
            "Signed in from an automated browser or emulator",
            details={"flags": flags, "device_id": device_id},
            key=device_id,
        )

    others = (
        User.query.join(DeviceSeen, DeviceSeen.user_id == User.id)
        .filter(DeviceSeen.device_id == device_id, User.id != user.id)
        .all()
    )
    customers = [u for u in others if u.role == "customer"]
    merchants = [u for u in others if u.role == "merchant"]
    limit = int(_setting("fraud_max_customer_accounts_per_device", 2))
    if user.role == "customer" and len(customers) + 1 > limit:
        raise_signal(
            user,
            "shared_device",
            f"This device has been used by {len(customers) + 1} customer accounts",
            details={
                "device_id": device_id,
                "other_customers": [u.id for u in customers],
            },
            key=device_id,
        )
    # A customer and a merchant on the same device may be selling to themselves (§9E)
    pairs = (
        [(user, m) for m in merchants]
        if user.role == "customer"
        else [(c, user) for c in customers]
        if user.role == "merchant"
        else []
    )
    for customer, merchant in pairs:
        raise_signal(
            merchant,
            "merchant_customer_same_device",
            "A merchant and a customer account used the same device",
            related=customer,
            details={"device_id": device_id},
            key=device_id,
        )


# ------------------------------------------------------------------ duplicate accounts (§9D)


def check_duplicates(user):
    """Same Ghana Card, MoMo number or payout account on another account. No commit."""
    if user.role == "customer":
        card = normalise_ghana_card(user.national_id)
        if card:
            for other in User.query.filter(
                User.id != user.id,
                User.role == "customer",
                User.national_id.isnot(None),
            ).all():
                if normalise_ghana_card(other.national_id) == card:
                    for a, b in ((user, other), (other, user)):
                        raise_signal(
                            a,
                            "duplicate_ghana_card",
                            "Another customer account uses the same Ghana Card",
                            severity=FraudSignal.BLOCK,
                            related=b,
                            key=card,
                        )
        momo = _digits(user.momo_number)
        if momo:
            for other in _users_with_number(momo, exclude=user):
                if other.role == "customer":
                    raise_signal(
                        user,
                        "shared_momo",
                        "Another customer uses the same Mobile Money number",
                        related=other,
                        key=momo,
                    )
                elif other.role == "merchant":
                    raise_signal(
                        other,
                        "merchant_customer_shared_account",
                        "A customer's Mobile Money number is also a merchant's payout or contact number",
                        related=user,
                        key=momo,
                    )
    elif user.role == "merchant":
        for number in {_digits(user.momo_number), _digits(user.account_number)} - {
            None
        }:
            for other in _users_with_number(number, exclude=user):
                if other.role == "customer":
                    raise_signal(
                        user,
                        "merchant_customer_shared_account",
                        "The merchant's payout account is also a customer's Mobile Money number",
                        related=other,
                        key=number,
                    )
                elif other.role == "merchant":
                    raise_signal(
                        user,
                        "shared_payout_account",
                        "Another merchant uses the same payout account",
                        related=other,
                        key=number,
                    )


def _users_with_number(last9, exclude):
    like = f"%{last9}"
    return User.query.filter(
        User.id != exclude.id,
        db.or_(
            User.momo_number.like(like),
            User.phone.like(like),
            User.account_number.like(like),
        ),
    ).all()


# ------------------------------------------------------------------ merchant fraud (§9E)


def check_purchase(customer, merchant):
    """Before an order is created. Returns an error message if the order must not go ahead.

    Also raises review signals that the admin sees when approving the order. Doesn't commit.
    """
    if blocking_signals(customer):
        return "Your account is being reviewed. Please contact Tabital support."
    if merchant is None:
        return None
    if blocking_signals(merchant):
        return "This merchant can't take new orders right now."
    # Customer buying from a merchant they control: the same number on both accounts
    customer_numbers = {_digits(customer.momo_number), _digits(customer.phone)} - {None}
    merchant_numbers = {
        _digits(merchant.momo_number),
        _digits(merchant.account_number),
        _digits(merchant.phone),
    } - {None}
    shared = customer_numbers & merchant_numbers
    if shared:
        raise_signal(
            merchant,
            "self_dealing",
            "Customer and merchant share a phone or payout number",
            severity=FraudSignal.BLOCK,
            related=customer,
            key=sorted(shared)[0],
        )
        return "This order can't be placed. Please contact Tabital support."
    same_device = (
        db.session.query(DeviceSeen.device_id)
        .filter(DeviceSeen.user_id == customer.id)
        .filter(
            DeviceSeen.device_id.in_(
                db.session.query(DeviceSeen.device_id).filter(
                    DeviceSeen.user_id == merchant.id
                )
            )
        )
        .first()
    )
    if same_device:
        raise_signal(
            merchant,
            "merchant_customer_same_device",
            "A merchant and a customer account used the same device",
            related=customer,
            details={"device_id": same_device[0]},
            key=same_device[0],
        )
    # Many orders from one merchant to one customer in a short time
    from ..models.purchase_order import PurchaseOrder

    window = datetime.utcnow() - timedelta(
        days=int(_setting("fraud_repeat_order_window_days", 30))
    )
    repeat = PurchaseOrder.query.filter(
        PurchaseOrder.customer_id == customer.id,
        PurchaseOrder.merchant_id == merchant.id,
        PurchaseOrder.created_at >= window,
    ).count()
    if repeat + 1 >= int(_setting("fraud_repeat_orders_review", 3)):
        raise_signal(
            merchant,
            "repeat_orders_same_customer",
            f"{repeat + 1} orders from the same customer in a short time",
            related=customer,
            key=window.strftime("%Y%m"),
        )
    return None


def check_quick_dispute(plan, dispute):
    """A dispute opened soon after delivery may be a fake sale. No commit."""
    from ..models.settlement import SettlementLine

    sale = SettlementLine.query.filter_by(
        plan_id=plan.id, line_type=SettlementLine.SALE
    ).first()
    delivered = (
        sale.created_at if sale else None
    )  # the sale line is written on delivery
    days = int(_setting("fraud_quick_dispute_days", 3))
    opened = dispute.created_at or datetime.utcnow()
    if delivered and opened - delivered <= timedelta(days=days):
        merchant = User.query.get(plan.merchant_id) if plan.merchant_id else None
        customer = User.query.get(plan.customer_id) if plan.customer_id else None
        if merchant and customer:
            raise_signal(
                merchant,
                "quick_dispute",
                f"Customer disputed within {days} days of delivery",
                related=customer,
                key=str(plan.id),
            )


def review_flags(customer, merchant):
    """Open signals on either side, for the admin's order approval screen."""
    rows = open_signals(customer) + (open_signals(merchant) if merchant else [])
    return [
        {"code": s.code, "severity": s.severity, "message": s.message, "id": s.id}
        for s in rows
    ]


def summary():
    rows = (
        db.session.query(FraudSignal.severity, func.count())
        .filter(FraudSignal.status == FraudSignal.OPEN)
        .group_by(FraudSignal.severity)
        .all()
    )
    return {sev: n for sev, n in rows}
