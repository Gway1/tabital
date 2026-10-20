"""Money received towards an instalment before it's fully paid (services/payments.record_payment).

Each row is one receipt, written to the ledger as PAYMENT_RECEIVED when it's recorded. The reference
is unique, so the same MoMo/bank receipt can't be recorded twice.
"""
from datetime import datetime

from ..extensions import db


class InstalmentPartPayment(db.Model):
    __tablename__ = "instalment_part_payments"

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.Integer, db.ForeignKey("instalment_payments.id"), nullable=False, index=True
    )
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=False, index=True
    )
    amount_pesewas = db.Column(db.Integer, nullable=False)
    method = db.Column(db.String(50), nullable=False)
    reference = db.Column(db.String(100), nullable=False, unique=True)
    recorded_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    recorder = db.relationship("User", foreign_keys=[recorded_by])

    def to_dict(self):
        return {
            "id": self.id,
            "amount": self.amount_pesewas / 100,
            "method": self.method,
            "reference": self.reference,
            "recorded_by": (self.recorder.full_name or self.recorder.phone)
            if self.recorder
            else None,
            "at": self.created_at.isoformat() if self.created_at else None,
        }
