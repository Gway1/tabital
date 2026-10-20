"""Identity and fraud checks (Phase 6, CLAUDE.md §9).

IdentityCheck: one Smile ID Biometric KYC job (Ghana Card + selfie + liveness), or a manual
admin decision. FraudSignal: something that needs a person to look at it (duplicate account,
shared device, possible merchant collusion...). DeviceSeen: which accounts used which device.
"""
import json
from datetime import datetime

from ..extensions import db


class IdentityCheck(db.Model):
    __tablename__ = "identity_checks"

    STARTED = "started"  # token minted, customer is capturing the selfie
    SUBMITTED = "submitted"  # Smile ID accepted the job, waiting for the result
    CLEAR = "clear"  # passed and matched our records: KYC verified
    REVIEW = (
        "review"  # needs an admin (Smile ID "attention", or a mismatch on our side)
    )
    BLOCKED = "blocked"  # failed (Smile ID "block", or a duplicate Ghana Card)
    ERROR = "error"  # Smile ID couldn't complete it; the customer can try again
    FINAL = (CLEAR, BLOCKED)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    provider = db.Column(
        db.String(20), nullable=False, default="smileid"
    )  # smileid | manual
    product = db.Column(db.String(40), nullable=False, default="biometric_kyc")
    reference = db.Column(
        db.String(64), unique=True, nullable=False
    )  # our user_id sent to Smile ID
    job_id = db.Column(db.String(64), unique=True)
    id_type = db.Column(db.String(40))
    id_number = db.Column(db.String(40))
    status = db.Column(db.String(20), nullable=False, default=STARTED, index=True)
    provider_status = db.Column(db.String(20))  # clear / attention / block / error
    provider_message = db.Column(db.String(255))
    name_match = db.Column(db.Boolean)
    dob_match = db.Column(db.Boolean)
    reasons_json = db.Column(db.Text)  # why it went to review / was blocked
    result_json = db.Column(db.Text)  # trimmed provider result, never images
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    completed_at = db.Column(db.DateTime)

    user = db.relationship("User", foreign_keys=[user_id])

    @property
    def reasons(self):
        return json.loads(self.reasons_json) if self.reasons_json else []

    def to_dict(self, admin=False):
        body = {
            "id": self.id,
            "provider": self.provider,
            "status": self.status,
            "reasons": self.reasons,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
        }
        if admin:
            result = json.loads(self.result_json) if self.result_json else {}
            body.update(
                {
                    "user_id": self.user_id,
                    "customer_name": self.user.full_name if self.user else None,
                    "customer_phone": self.user.phone if self.user else None,
                    "job_id": self.job_id,
                    "id_type": self.id_type,
                    "id_number": self.id_number,
                    "provider_status": self.provider_status,
                    "provider_message": self.provider_message,
                    "name_match": self.name_match,
                    "dob_match": self.dob_match,
                    "id_fields": result.get("id_fields"),
                    "review_note": self.review_note,
                    "reviewed_at": self.reviewed_at.isoformat()
                    if self.reviewed_at
                    else None,
                }
            )
        return body


class FraudSignal(db.Model):
    __tablename__ = "fraud_signals"

    INFO = "info"  # recorded only
    REVIEW = "review"  # an admin should look; doesn't stop anything on its own
    BLOCK = (
        "block"  # stops new purchases (customer) or payouts (merchant) until cleared
    )

    OPEN = "open"
    CLEARED = "cleared"  # admin checked it: not fraud
    CONFIRMED = "confirmed"  # admin checked it: fraud (keeps blocking)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    related_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    code = db.Column(db.String(50), nullable=False, index=True)
    severity = db.Column(db.String(10), nullable=False, default=REVIEW)
    status = db.Column(db.String(12), nullable=False, default=OPEN, index=True)
    message = db.Column(db.String(300), nullable=False)
    details_json = db.Column(db.Text)
    dedupe_key = db.Column(db.String(160), unique=True, nullable=False)
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    review_note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user = db.relationship("User", foreign_keys=[user_id])
    related_user = db.relationship("User", foreign_keys=[related_user_id])

    def to_dict(self):
        def who(u):
            if not u:
                return None
            return {
                "id": u.id,
                "role": u.role,
                "phone": u.phone,
                "name": u.business_name if u.role == "merchant" else u.full_name,
            }

        return {
            "id": self.id,
            "code": self.code,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "details": json.loads(self.details_json) if self.details_json else {},
            "user": who(self.user),
            "related_user": who(self.related_user),
            "review_note": self.review_note,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DeviceSeen(db.Model):
    """A device (browser install id sent by the app) and the accounts that used it."""

    __tablename__ = "devices_seen"
    __table_args__ = (
        db.UniqueConstraint("device_id", "user_id", name="uq_devices_seen_device_user"),
    )

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(64), nullable=False, index=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    last_ip = db.Column(db.String(64))
    user_agent = db.Column(db.String(300))
    flags = db.Column(db.String(200))  # e.g. "webdriver" reported by the browser
    first_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
