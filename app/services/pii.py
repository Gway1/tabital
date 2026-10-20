"""Personal data on admin screens is masked by default (Vault admin queues, Ghana Data Protection).

Every JSON response under /admin/ passes through mask_payload(): Ghana Card numbers, phone and
Mobile Money numbers and account numbers keep only their last digits. An admin who needs a full
value (e.g. to call a customer in collections) reveals it through POST /admin/pii/reveal with a
reason; each reveal is logged in pii_access_log.

Masked values contain MASK_CHAR, so code that saves data refuses them (see is_masked()).
"""
import re

MASK_CHAR = "•"  # •

# Response keys that hold personal identifiers
CARD_KEYS = {"national_id", "id_number", "ghana_card", "ghana_card_number"}
PHONE_KEYS = {
    "phone",
    "customer_phone",
    "merchant_phone",
    "business_phone",
    "momo_number",
    "ref_phone",
    "phone_number",
    "user_phone",
    "mobile_money_number",
    "to_address",
}
ACCOUNT_KEYS = {"account_number", "bank_account", "bank_account_number"}

# What an admin may reveal, per user field
REVEALABLE = {
    "national_id": "Ghana Card",
    "phone": "Phone",
    "momo_number": "Mobile Money number",
    "account_number": "Bank account number",
    "business_phone": "Business phone",
    "ref_phone": "Referee phone",
}


def is_masked(value) -> bool:
    return isinstance(value, str) and MASK_CHAR in value


def mask_card(value: str) -> str:
    m = re.fullmatch(r"GHA-?(\d{9})-?(\d)", value.strip().upper())
    if m:
        return f"GHA-{MASK_CHAR * 5}{m.group(1)[-4:]}-{m.group(2)}"
    return mask_generic(value, keep=4)


def mask_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 6:
        return MASK_CHAR * len(value) if value else value
    # keep the network prefix and the last 2 digits: 024•••••67
    head = value[:3] if value.startswith("0") else value[:4]
    return head + MASK_CHAR * 5 + digits[-2:]


def mask_generic(value: str, keep: int = 4) -> str:
    s = value.strip()
    if len(s) <= keep:
        return MASK_CHAR * len(s)
    return MASK_CHAR * 6 + s[-keep:]


def _norm(key) -> str:
    """Match keys however they're written: national_id, IDNumber, PhoneNumber (Smile ID), ..."""
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


_CARD = {_norm(k) for k in CARD_KEYS}
_PHONE = {_norm(k) for k in PHONE_KEYS}
_ACCOUNT = {_norm(k) for k in ACCOUNT_KEYS}


def mask_value(key: str, value):
    if not isinstance(value, str) or not value.strip() or is_masked(value):
        return value
    k = _norm(key)
    if k in _CARD:
        return mask_card(value)
    if k in _PHONE or k.startswith("phonenumber"):
        return mask_phone(value)
    if k in _ACCOUNT:
        return mask_generic(value, keep=4)
    return value


def mask_payload(obj):
    """Walk JSON-like data and mask personal identifiers by key."""
    if isinstance(obj, dict):
        return {
            k: (mask_value(k, v) if isinstance(v, str) else mask_payload(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [mask_payload(v) for v in obj]
    return obj
