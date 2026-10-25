"""Thin Smile ID v3 API client (https://docs.usesmileid.com/).

Flow (Biometric KYC):
1. The backend mints a short-lived token (POST /v3/token) bound to the customer's details
   (Ghana Card number, names, phone, consent, callback URL).
2. The browser captures the selfie + liveness images with Smile ID's web components and sends
   them straight to Smile ID with that token. Images never pass through our servers.
3. Smile ID posts the result to our webhook (signed). We re-check the status with
   GET /v3/status/{job_id} before acting on it.
"""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone

import requests
from flask import current_app

TIMEOUT_SECONDS = 20
WEBHOOK_MAX_AGE_SECONDS = 600


class SmileIDError(Exception):
    pass


def is_configured() -> bool:
    return bool(
        current_app.config.get("SMILEID_PARTNER_ID")
        and current_app.config.get("SMILEID_API_KEY")
    )


def _partner_id():
    value = current_app.config.get("SMILEID_PARTNER_ID")
    if not value:
        raise SmileIDError("Smile ID is not configured (SMILEID_PARTNER_ID missing)")
    return str(value)


def _api_key():
    value = current_app.config.get("SMILEID_API_KEY")
    if not value:
        raise SmileIDError("Smile ID is not configured (SMILEID_API_KEY missing)")
    return value


def base_url():
    return (
        current_app.config.get("SMILEID_BASE_URL")
        or "https://testapi.smileidentity.com"
    ).rstrip("/")


def is_sandbox() -> bool:
    return "testapi" in base_url()


def config_warnings() -> list:
    """Problems with a configured Smile ID that would only show up with real customers."""
    if not is_configured():
        return []
    problems = []
    if is_sandbox():
        problems.append(
            "SMILEID_BASE_URL is the Smile ID sandbox: no real identity is being checked"
        )
    if not current_app.config.get("SMILEID_CALLBACK_URL"):
        problems.append(
            "SMILEID_CALLBACK_URL is not set: Smile ID has nowhere to post results"
        )
    return problems


def mint_token(*, user_ref, product, payload, partner_params=None):
    """A 15-minute token the browser uses to submit one job. Returns the token string.

    `payload` binds the job to the details we already hold (country, id_type, id_number,
    given_names, last_name, phone_number, callback_url, consent).
    """
    form = {
        "user_id": user_ref,
        "product": product,
        "payload": json.dumps(payload),
    }
    if partner_params:
        form["partner_params"] = json.dumps(
            {k: str(v) for k, v in partner_params.items()}
        )
    headers = {"smileid-partner-id": _partner_id(), "smileid-api-key": _api_key()}
    try:
        # multipart/form-data, as the token endpoint requires
        res = requests.post(
            f"{base_url()}/v3/token",
            files={k: (None, v) for k, v in form.items()},
            headers=headers,
            timeout=TIMEOUT_SECONDS,
        )
        body = res.json()
    except (requests.RequestException, ValueError) as e:
        raise SmileIDError(f"Could not reach Smile ID: {e}")
    if res.status_code >= 400 or not body.get("token"):
        raise SmileIDError(body.get("message") or f"Smile ID error {res.status_code}")
    return body["token"]


def job_status(job_id):
    """Smile ID's own view of a job: status is clear / attention / block / error / processing / not_found."""
    token = mint_token(user_ref="status-check", product="biometric_kyc", payload={})
    headers = {"SmileID-Partner-ID": _partner_id(), "SmileID-Token": token}
    try:
        res = requests.get(
            f"{base_url()}/v3/status/{job_id}", headers=headers, timeout=TIMEOUT_SECONDS
        )
        body = res.json()
    except (requests.RequestException, ValueError) as e:
        raise SmileIDError(f"Could not reach Smile ID: {e}")
    if res.status_code >= 400:
        raise SmileIDError(body.get("message") or f"Smile ID error {res.status_code}")
    return body


def expected_signature(timestamp: str) -> str:
    """base64(HMAC-SHA256(api_key, timestamp + partner_id + "sid_request"))."""
    message = f"{timestamp}{_partner_id()}sid_request".encode()
    return base64.b64encode(
        hmac.new(_api_key().encode(), message, hashlib.sha256).digest()
    ).decode()


def verify_webhook(timestamp, signature, now=None) -> bool:
    """Check Smile ID's Response-Signature header, and that the timestamp is recent."""
    if not timestamp or not signature or not is_configured():
        return False
    try:
        sent = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    if abs((now - sent).total_seconds()) > WEBHOOK_MAX_AGE_SECONDS:
        return False
    return hmac.compare_digest(expected_signature(timestamp), signature)
