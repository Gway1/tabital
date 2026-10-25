"""SMS/WhatsApp providers behind one interface.

The provider is chosen with SMS_PROVIDER ('log', 'mnotify' or 'hubtel'). The same provider
sends both the SMS and WhatsApp reminder channels (reminders.dispatch_pending). The default
'log' provider only writes the message to the server log, so reminders can be tested end to
end without sending anything. To add a real provider,
implement `send(to, body) -> SendResult` and register it in PROVIDERS.
"""
from dataclasses import dataclass
from typing import Optional

from flask import current_app


@dataclass
class SendResult:
    ok: bool
    provider: str
    message_id: Optional[str] = None
    error: Optional[str] = None


def to_e164(phone: str) -> Optional[str]:
    """Ghana numbers: 0241234567 -> +233241234567. Returns None if it doesn't look valid."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if digits.startswith("233") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+233" + digits[1:]
    return None


class LogProvider:
    name = "log"

    def send(self, to, body):
        current_app.logger.info("SMS (log only) to %s: %s", to, body)
        return SendResult(ok=True, provider=self.name, message_id=None)


def to_local(phone: str) -> Optional[str]:
    """+233241234567 / 233241234567 / 0241234567 -> 0241234567 (the format mNotify documents)."""
    e164 = to_e164(phone)
    return "0" + e164[4:] if e164 else None


class MNotifyProvider:
    """mNotify BMS API v2 Quick SMS (https://readthedocs.mnotify.com/, founder choice 2026-09-26).

    POST https://api.mnotify.com/api/sms/quick?key=API_KEY
    {"recipient": ["0241234567"], "sender": "<registered sender id, max 11 chars>",
     "message": "...", "is_schedule": false, "schedule_date": ""}
    Success: {"status": "success", "code": "2000", "summary": {"_id": "<campaign id>", "total_rejected": 0}}
    """

    name = "mnotify"
    TIMEOUT_SECONDS = 20

    def __init__(self):
        cfg = current_app.config
        self.api_key = cfg.get("MNOTIFY_API_KEY")
        self.sender = (cfg.get("MNOTIFY_SENDER_ID") or "").strip()
        self.base_url = (
            cfg.get("MNOTIFY_BASE_URL") or "https://api.mnotify.com/api"
        ).rstrip("/")

    def send(self, to, body):
        import requests

        if not self.api_key:
            return SendResult(
                ok=False, provider=self.name, error="MNOTIFY_API_KEY is not set"
            )
        if not self.sender or len(self.sender) > 11:
            return SendResult(
                ok=False,
                provider=self.name,
                error="MNOTIFY_SENDER_ID must be set and at most 11 characters",
            )
        recipient = to_local(to)
        if not recipient:
            return SendResult(
                ok=False, provider=self.name, error=f"Invalid Ghana phone number: {to}"
            )

        payload = {
            "recipient": [recipient],
            "sender": self.sender,
            "message": body,
            "is_schedule": False,
            "schedule_date": "",
        }
        try:
            # The API key is a query parameter by mNotify's design; never log this URL
            res = requests.post(
                f"{self.base_url}/sms/quick",
                params={"key": self.api_key},
                json=payload,
                timeout=self.TIMEOUT_SECONDS,
            )
            data = res.json()
        except (requests.RequestException, ValueError) as e:
            return SendResult(
                ok=False,
                provider=self.name,
                error=f"mNotify unreachable: {type(e).__name__}",
            )

        summary = data.get("summary") or {}
        if (
            res.status_code >= 400
            or data.get("status") != "success"
            or str(data.get("code")) != "2000"
        ):
            return SendResult(
                ok=False,
                provider=self.name,
                error=f"mNotify {data.get('code') or res.status_code}: {data.get('message') or 'send failed'}"[
                    :255
                ],
            )
        if int(summary.get("total_rejected") or 0) > 0:
            return SendResult(
                ok=False,
                provider=self.name,
                message_id=summary.get("_id"),
                error="mNotify rejected the recipient number",
            )
        return SendResult(ok=True, provider=self.name, message_id=summary.get("_id"))


class HubtelProvider:
    """Hubtel Programmable SMS (https://developers.hubtel.com/).

    POST https://smsc.hubtel.com/v1/messages/send, HTTP Basic auth (client id : client secret)
    {"From": "<approved sender id, max 11 chars>", "To": "233241234567", "Content": "..."}
    Success (HTTP 201): {"rate": 1.0, "messageId": "<id>", "status": 0, "networkId": "62002"}
    Failure: status is non-zero and/or HTTP 4xx with a message.
    """

    name = "hubtel"
    TIMEOUT_SECONDS = 20

    def __init__(self):
        cfg = current_app.config
        self.client_id = cfg.get("HUBTEL_CLIENT_ID")
        self.client_secret = cfg.get("HUBTEL_CLIENT_SECRET")
        self.sender = (cfg.get("HUBTEL_SENDER_ID") or "").strip()
        self.base_url = (
            cfg.get("HUBTEL_BASE_URL") or "https://smsc.hubtel.com/v1"
        ).rstrip("/")

    def send(self, to, body):
        import requests

        if not self.client_id or not self.client_secret:
            return SendResult(
                ok=False,
                provider=self.name,
                error="HUBTEL_CLIENT_ID / HUBTEL_CLIENT_SECRET are not set",
            )
        if not self.sender or len(self.sender) > 11:
            return SendResult(
                ok=False,
                provider=self.name,
                error="HUBTEL_SENDER_ID must be set and at most 11 characters",
            )
        e164 = to_e164(to)
        if not e164:
            return SendResult(
                ok=False, provider=self.name, error=f"Invalid Ghana phone number: {to}"
            )

        payload = {
            "From": self.sender,
            "To": e164.lstrip("+"),  # Hubtel wants 233241234567, no plus
            "Content": body,
        }
        try:
            res = requests.post(
                f"{self.base_url}/messages/send",
                auth=(self.client_id, self.client_secret),
                json=payload,
                timeout=self.TIMEOUT_SECONDS,
            )
            data = res.json()
        except (requests.RequestException, ValueError) as e:
            return SendResult(
                ok=False,
                provider=self.name,
                error=f"Hubtel unreachable: {type(e).__name__}",
            )

        message_id = data.get("messageId") or data.get("MessageId")
        status = data.get("status", data.get("Status"))
        if res.status_code >= 400 or (status not in (None, 0, "0")):
            detail = data.get("message") or data.get("Message") or "send failed"
            return SendResult(
                ok=False,
                provider=self.name,
                message_id=message_id,
                error=f"Hubtel {status if status is not None else res.status_code}: {detail}"[
                    :255
                ],
            )
        return SendResult(ok=True, provider=self.name, message_id=message_id)


PROVIDERS = {
    "log": LogProvider,
    "mnotify": MNotifyProvider,
    "hubtel": HubtelProvider,
}


def provider():
    name = (current_app.config.get("SMS_PROVIDER") or "log").lower()
    cls = PROVIDERS.get(name)
    if cls is None:
        raise RuntimeError(
            f"Unknown SMS_PROVIDER '{name}'. Available: {', '.join(PROVIDERS)}"
        )
    return cls()
