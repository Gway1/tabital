"""A customer's saved Paystack authorization (reusable card), used for autopay.

Only Paystack's authorization code is stored, never card numbers. Autopay is opt-in.
"""
from datetime import datetime

from ..extensions import db


class PaymentMethod(db.Model):
    __tablename__ = "payment_methods"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    provider = db.Column(db.String(20), nullable=False, default="paystack")
    authorization_code = db.Column(db.String(100), nullable=False)
    signature = db.Column(
        db.String(100), index=True
    )  # Paystack's stable id for the same card
    email = db.Column(db.String(200), nullable=False)  # charges must use the same email
    channel = db.Column(db.String(30))
    card_type = db.Column(db.String(30))
    bank = db.Column(db.String(100))
    last4 = db.Column(db.String(4))
    exp_month = db.Column(db.String(2))
    exp_year = db.Column(db.String(4))
    reusable = db.Column(db.Boolean, default=False)
    autopay_enabled = db.Column(
        db.Boolean, default=False
    )  # the customer must switch it on
    is_default = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    revoked_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "channel": self.channel,
            "card_type": (self.card_type or "").strip(),
            "bank": self.bank,
            "last4": self.last4,
            "expiry": f"{self.exp_month}/{self.exp_year}"
            if self.exp_month and self.exp_year
            else None,
            "reusable": bool(self.reusable),
            "autopay_enabled": bool(self.autopay_enabled),
            "is_default": bool(self.is_default),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
