"""Instalment deferment (CLAUDE.md §4): the customer pays a fee (10% of the instalment) to push
an instalment back, which protects their credit record. One per plan for now (§13 #8)."""
import json
from datetime import datetime

from ..extensions import db


class Deferment(db.Model):
    __tablename__ = "deferments"

    APPLIED = "applied"
    REFUND_REQUIRED = (
        "refund_required"  # fee was paid but the deferment couldn't be applied
    )

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=False, index=True
    )
    payment_id = db.Column(
        db.Integer, db.ForeignKey("instalment_payments.id"), nullable=False, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    intent_id = db.Column(db.Integer, db.ForeignKey("payment_intents.id"), unique=True)
    fee_pesewas = db.Column(db.BigInteger, nullable=False)
    months = db.Column(db.Integer, nullable=False, default=1)
    original_due_date = db.Column(db.DateTime, nullable=False)
    new_due_date = db.Column(db.DateTime)
    # [{payment_id, installment_number, from, to}] for every instalment that moved
    moved_json = db.Column(db.Text)
    status = db.Column(db.String(20), nullable=False, default=APPLIED)
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    payment = db.relationship("InstalmentPayment", foreign_keys=[payment_id])

    def to_dict(self):
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "installment_number": self.payment.installment_number
            if self.payment
            else None,
            "fee": round(self.fee_pesewas / 100, 2),
            "months": self.months,
            "original_due_date": self.original_due_date.date().isoformat()
            if self.original_due_date
            else None,
            "new_due_date": self.new_due_date.date().isoformat()
            if self.new_due_date
            else None,
            "moved": json.loads(self.moved_json) if self.moved_json else [],
            "status": self.status,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
