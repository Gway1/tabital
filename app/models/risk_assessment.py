"""Every underwriting decision, with the facts and rules it was made from (append-only)."""
import json
from datetime import datetime

from ..extensions import db


class RiskAssessment(db.Model):
    __tablename__ = "risk_assessments"

    # source values
    KYC_APPROVAL = "kyc_approval"
    PURCHASE = "purchase"
    CUSTOMER_VIEW = "customer_view"
    ADMIN_RERUN = "admin_rerun"
    ADMIN_OVERRIDE = "admin_override"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    source = db.Column(db.String(30), nullable=False)
    eligible = db.Column(db.Boolean, nullable=False)
    tier = db.Column(db.String(10))
    pay_in_4_dp_rate = db.Column(db.Numeric(5, 4))
    limit_multiplier = db.Column(db.Numeric(6, 3))
    credit_limit = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    outstanding = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    available_limit = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    extended_plans_eligible = db.Column(db.Boolean, default=False)
    reasons = db.Column(db.Text)  # JSON list of strings
    inputs = db.Column(db.Text)  # JSON of the Facts used
    rules_version = db.Column(db.String(40), nullable=False)
    note = db.Column(db.String(255))  # e.g. the admin's reason for an override
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def reasons_list(self):
        return json.loads(self.reasons) if self.reasons else []

    def to_dict(self):
        f = lambda v: float(v) if v is not None else None
        return {
            "id": self.id,
            "source": self.source,
            "eligible": self.eligible,
            "tier": self.tier,
            "pay_in_4_down_payment_percent": f(self.pay_in_4_dp_rate * 100)
            if self.pay_in_4_dp_rate is not None
            else None,
            "limit_multiplier": f(self.limit_multiplier),
            "credit_limit": f(self.credit_limit),
            "outstanding": f(self.outstanding),
            "available_limit": f(self.available_limit),
            "extended_plans_eligible": bool(self.extended_plans_eligible),
            "reasons": self.reasons_list(),
            "rules_version": self.rules_version,
            "note": self.note,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
