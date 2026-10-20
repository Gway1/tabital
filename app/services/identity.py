"""Identity verification (Phase 6, CLAUDE.md §9A) with Smile ID Biometric KYC.

The customer's Ghana Card is checked against the national ID authority and their selfie
(with liveness) is compared to the ID photo. We then compare the name and date of birth the
authority returns with what the customer told us.

Outcome:
- clear:   Smile ID passed it and the name and date of birth match -> KYC verified
- review:  Smile ID said "attention", or our match failed -> an admin decides
- blocked: Smile ID said "block", or the Ghana Card is already verified on another account
- error:   Smile ID couldn't finish -> the customer can try again

When in doubt it goes to review; nothing borderline is auto-approved.
"""
import json
import re
import secrets
from datetime import datetime

from ..extensions import db
from ..models.identity import FraudSignal, IdentityCheck
from ..models.system_settings import SystemSetting
from ..models.user import User
from . import fraud, smileid
from .risk import parse_date
from .sms import to_e164

COUNTRY = "GH"
PRODUCT = "biometric_kyc"


class IdentityError(ValueError):
    pass


def id_type():
    # Smile ID's code for the Ghana Card. Confirm with GET /v3/services/supported_id_types?country=GH
    # once Ghana is enabled on the account (Smile ID enables Ghana on request).
    return SystemSetting.get_value("smileid_ghana_id_type", "GHANA_CARD")


def max_attempts():
    return int(SystemSetting.get_value("identity_max_attempts", 3))


def biometric_required():
    """Smile ID is the only way to verify a customer once it's configured (§9A)."""
    return smileid.is_configured() and bool(
        SystemSetting.get_value("kyc_require_biometric", True)
    )


def split_name(full_name):
    parts = (full_name or "").split()
    if len(parts) < 2:
        raise IdentityError(
            "Add your full name as it appears on your Ghana Card (first and last name) in your profile"
        )
    return " ".join(parts[:-1]), parts[-1]


def attempts_used(user):
    return IdentityCheck.query.filter(
        IdentityCheck.user_id == user.id,
        IdentityCheck.provider == "smileid",
        IdentityCheck.status != IdentityCheck.STARTED,
    ).count()


def latest(user):
    return (
        IdentityCheck.query.filter_by(user_id=user.id)
        .order_by(IdentityCheck.id.desc())
        .first()
    )


def status_view(user):
    check = latest(user)
    used = attempts_used(user)
    return {
        "configured": smileid.is_configured(),
        "required": biometric_required(),
        "kyc_status": user.kyc_status,
        "check": check.to_dict() if check else None,
        "attempts_used": used,
        "attempts_left": max(0, max_attempts() - used),
    }


def start(user, consent):
    """Mint a Smile ID token bound to this customer's details. Commits. Returns what the browser needs."""
    if user.role != "customer":
        raise IdentityError("Only customers verify with a selfie")
    if not smileid.is_configured():
        raise IdentityError("Identity checks aren't switched on yet")
    if not consent:
        raise IdentityError("Please agree to the identity check first")
    if user.kyc_status == "verified":
        raise IdentityError("Your identity is already verified")
    open_check = IdentityCheck.query.filter(
        IdentityCheck.user_id == user.id,
        IdentityCheck.status.in_([IdentityCheck.SUBMITTED, IdentityCheck.REVIEW]),
    ).first()
    if open_check:
        raise IdentityError("Your last check is still being processed")
    if attempts_used(user) >= max_attempts():
        raise IdentityError(
            "You've used all your attempts. Please contact Tabital support."
        )
    card = fraud.normalise_ghana_card(user.national_id)
    if not card:
        raise IdentityError(
            "Add a valid Ghana Card number (GHA-XXXXXXXXX-X) in your profile first"
        )
    phone = to_e164(user.phone)
    if not phone:
        raise IdentityError("Your phone number isn't valid")
    given_names, last_name = split_name(user.full_name)

    from flask import current_app

    reference = f"tp_{user.id}_{secrets.token_hex(6)}"
    now = datetime.utcnow()
    payload = {
        "country": COUNTRY,
        "id_type": id_type(),
        "id_number": card,
        "given_names": given_names,
        "last_name": last_name,
        "phone_number": phone,
        "consent": {
            "granted": True,
            "granted_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "notice_language": "EN",
            "notice_privacy_policy_url": current_app.config.get("PRIVACY_POLICY_URL"),
        },
    }
    if current_app.config.get("SMILEID_CALLBACK_URL"):
        payload["callback_url"] = current_app.config["SMILEID_CALLBACK_URL"]
    token = smileid.mint_token(
        user_ref=reference,
        product=PRODUCT,
        payload=payload,
        partner_params={"tabital_ref": reference},
    )

    # Any earlier unfinished start is superseded
    IdentityCheck.query.filter_by(
        user_id=user.id, status=IdentityCheck.STARTED
    ).delete()
    check = IdentityCheck(
        user_id=user.id,
        reference=reference,
        id_type=id_type(),
        id_number=card,
        product=PRODUCT,
        status=IdentityCheck.STARTED,
    )
    db.session.add(check)
    db.session.commit()
    return {
        "check_id": check.id,
        "token": token,
        "partner_id": str(current_app.config["SMILEID_PARTNER_ID"]),
        "api_url": smileid.base_url(),
        "submit_url": f"{smileid.base_url()}/v3/{PRODUCT}",
        # The browser must send exactly these; the token is bound to them
        "fields": {
            k: payload[k]
            for k in (
                "country",
                "id_type",
                "id_number",
                "given_names",
                "last_name",
                "phone_number",
            )
        }
        | (
            {"callback_url": payload["callback_url"]}
            if "callback_url" in payload
            else {}
        ),
        "consent": payload["consent"],
    }


def mark_submitted(user, check_id, job_id):
    """The browser reports Smile ID's job_id after the upload is accepted. Commits."""
    if not job_id or not re.fullmatch(r"job_[0-9a-z]{10,40}", str(job_id)):
        raise IdentityError("Invalid job id")
    check = IdentityCheck.query.filter_by(id=check_id, user_id=user.id).first()
    if not check:
        raise IdentityError("Check not found")
    if check.status != IdentityCheck.STARTED:
        return check
    if IdentityCheck.query.filter(
        IdentityCheck.job_id == job_id, IdentityCheck.id != check.id
    ).first():
        raise IdentityError("This job belongs to another check")
    check.job_id = job_id
    check.status = IdentityCheck.SUBMITTED
    db.session.commit()
    return check


# ------------------------------------------------------------------ results


def _tokens(name):
    return {
        t for t in re.sub(r"[^a-z ]", " ", (name or "").lower()).split() if len(t) > 1
    }


def names_match(ours, id_fields):
    theirs = _tokens(id_fields.get("full_name")) or _tokens(
        f"{id_fields.get('first_name', '')} {id_fields.get('middle_name', '')} {id_fields.get('last_name', '')}"
    )
    mine = _tokens(ours)
    if not theirs or not mine:
        return False
    small, big = (mine, theirs) if len(mine) <= len(theirs) else (theirs, mine)
    return len(small) >= 2 and small <= big  # allows a middle name missing on one side


def dob_match(ours, id_fields):
    a, b = parse_date(ours), parse_date(
        id_fields.get("date_of_birth") or id_fields.get("dob")
    )
    return bool(a and b and a == b)


def apply_result(check, provider_status, result=None, message=None):
    """Turn a (verified) Smile ID result into our decision. Commits. Idempotent for final checks."""
    if check.status in IdentityCheck.FINAL or (
        check.status == IdentityCheck.REVIEW and check.reviewed_at
    ):
        return check
    user = User.query.get(check.user_id)
    result = result or {}
    id_fields = result.get("id_fields") or {}
    check.provider_status = provider_status
    check.provider_message = (message or result.get("message") or "")[:255] or None
    # Keep what's needed to review, never images or links to them
    check.result_json = json.dumps(
        {
            "id_fields": {
                k: v
                for k, v in id_fields.items()
                if "photo" not in k and "image" not in k
            },
            "reason": result.get("reason"),
            "antifraud": result.get("antifraud"),
        }
    )
    check.completed_at = datetime.utcnow()
    reasons = []

    if provider_status == "processing":
        return check
    if provider_status == "error":
        check.status = IdentityCheck.ERROR
        reasons.append(check.provider_message or "Smile ID couldn't complete the check")
    elif provider_status == "block":
        check.status = IdentityCheck.BLOCKED
        reasons.append(
            result.get("reason")
            or check.provider_message
            or "Smile ID couldn't verify this identity"
        )
        user.kyc_status = "rejected"
    else:
        check.name_match = names_match(user.full_name, id_fields) if id_fields else None
        check.dob_match = dob_match(user.dob, id_fields) if id_fields else None
        if provider_status == "attention":
            reasons.append(result.get("reason") or "Smile ID asked for a manual review")
        if provider_status not in ("clear", "attention"):
            reasons.append(f"Unexpected result: {provider_status}")
        if not id_fields:
            reasons.append("No ID details came back to compare")
        else:
            if not check.name_match:
                reasons.append("The name on the Ghana Card doesn't match the profile")
            if not check.dob_match:
                reasons.append(
                    "The date of birth on the Ghana Card doesn't match the profile"
                )
        duplicate = _verified_elsewhere(user, check.id_number)
        if duplicate:
            check.status = IdentityCheck.BLOCKED
            reasons.append("This Ghana Card is already verified on another account")
            user.kyc_status = "rejected"
            fraud.raise_signal(
                user,
                "duplicate_ghana_card",
                "Ghana Card already verified on another account",
                severity=FraudSignal.BLOCK,
                related=duplicate,
                key=check.id_number,
            )
        elif not reasons and provider_status == "clear":
            check.status = IdentityCheck.CLEAR
            _verify(user)
        else:
            check.status = IdentityCheck.REVIEW
            user.kyc_status = "pending"
    check.reasons_json = json.dumps(reasons) if reasons else None
    db.session.commit()
    return check


def _verified_elsewhere(user, card):
    if not card:
        return None
    for other in User.query.filter(
        User.id != user.id, User.role == "customer", User.kyc_status == "verified"
    ).all():
        if fraud.normalise_ghana_card(other.national_id) == card:
            return other
    return None


def _verify(user):
    user.kyc_status = "verified"
    user.kyc_completed_on = datetime.utcnow()
    user.verification_level = "biometric"
    if user.status in (None, "pending"):
        user.status = "approved"
    # Underwriting: tier and limit now that identity is verified (as after a manual approval)
    from ..models.risk_assessment import RiskAssessment
    from . import risk

    risk.evaluate(
        user, RiskAssessment.KYC_APPROVAL, note="Identity verified by Smile ID"
    )


def refresh(check):
    """Ask Smile ID for the job's status and apply it (when the webhook hasn't arrived)."""
    if not check.job_id or check.status not in (IdentityCheck.SUBMITTED,):
        return check
    body = smileid.job_status(check.job_id)
    status = body.get("status")
    if status in (None, "processing", "not_found"):
        return check
    # The status endpoint doesn't return id_fields, so anything other than an error or block
    # can't be matched against the profile yet: wait for the signed webhook, or review.
    if status in ("error", "block"):
        return apply_result(check, status, body)
    return check


def handle_webhook(body, headers):
    """Signed Smile ID result. Returns (http_status, message)."""
    if not smileid.verify_webhook(
        headers.get("Response-Timestamp"), headers.get("Response-Signature")
    ):
        return 401, "bad signature"
    params = body.get("partner_params") or {}
    job_id = params.get("job_id") or headers.get("Job-ID")
    ref = params.get("user_id") or headers.get("User-ID") or params.get("tabital_ref")
    check = None
    if job_id:
        check = IdentityCheck.query.filter_by(job_id=job_id).first()
    if not check and ref:
        check = IdentityCheck.query.filter_by(reference=ref).first()
        if check and job_id and not check.job_id:
            check.job_id = job_id
    if not check:
        return 200, "unknown job"  # acknowledge so Smile ID stops retrying
    # Trust but verify: the status must agree with Smile ID's own record of the job
    if check.job_id:
        try:
            confirmed = smileid.job_status(check.job_id).get("status")
        except smileid.SmileIDError:
            return 503, "could not confirm with Smile ID"  # Smile ID will retry
        if (
            confirmed
            and confirmed not in ("processing",)
            and confirmed != body.get("status")
        ):
            return 200, "status mismatch ignored"
    apply_result(check, body.get("status"), body)
    return 200, "ok"


def admin_decide(check, admin, approve, note):
    """An admin clears or rejects a check in review (or overrides a block). Commits."""
    if not note or len(note.strip()) < 5:
        raise IdentityError("Add a note explaining the decision")
    if check.status not in (
        IdentityCheck.REVIEW,
        IdentityCheck.BLOCKED,
        IdentityCheck.ERROR,
    ):
        raise IdentityError("This check doesn't need a decision")
    user = User.query.get(check.user_id)
    if approve:
        duplicate = _verified_elsewhere(user, check.id_number)
        if duplicate:
            raise IdentityError(
                "This Ghana Card is already verified on another account"
            )
        check.status = IdentityCheck.CLEAR
        _verify(user)
    else:
        check.status = IdentityCheck.BLOCKED
        user.kyc_status = "rejected"
    check.reviewed_by, check.reviewed_at, check.review_note = (
        admin.id,
        datetime.utcnow(),
        note.strip()[:500],
    )
    db.session.commit()
    return check


def record_manual(user, admin, approve, note):
    """Manual document review, used only while Smile ID isn't configured. No commit."""
    check = IdentityCheck(
        user_id=user.id,
        provider="manual",
        product="document_review",
        reference=f"manual_{user.id}_{secrets.token_hex(6)}",
        id_number=fraud.normalise_ghana_card(user.national_id),
        status=IdentityCheck.CLEAR if approve else IdentityCheck.BLOCKED,
        reviewed_by=admin.id,
        reviewed_at=datetime.utcnow(),
        review_note=(note or "")[:500] or None,
        completed_at=datetime.utcnow(),
    )
    db.session.add(check)
    return check
