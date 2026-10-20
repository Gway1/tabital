"""A customer saying "I've paid this instalment outside the app" (MoMo/bank reference).

A claim never changes the instalment: it stays pending/overdue (late fees, days past due and
collections carry on) until staff confirm the money arrived, which records it through
services/payments.record_payment, or reject the claim.
"""
from datetime import datetime

from ..extensions import db


class PaymentClaim(db.Model):
    __tablename__ = "payment_claims"

    PENDING, CONFIRMED, REJECTED = "pending", "confirmed", "rejected"

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.Integer, db.ForeignKey("instalment_payments.id"), nullable=False, index=True
    )
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=False, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    method = db.Column(db.String(30), nullable=False)
    reference = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=PENDING, index=True)
    review_note = db.Column(db.String(500))
    reviewed_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    payment = db.relationship("InstalmentPayment", foreign_keys=[payment_id])
    customer = db.relationship("User", foreign_keys=[customer_id])

    def to_dict(self):
        p = self.payment
        return {
            "id": self.id,
            "payment_id": self.payment_id,
            "plan_id": self.plan_id,
            "installment_number": p.installment_number if p else None,
            "customer_id": self.customer_id,
            "customer_name": (self.customer.full_name or self.customer.phone)
            if self.customer
            else None,
            "customer_phone": self.customer.phone if self.customer else None,
            "method": self.method,
            "reference": self.reference,
            "status": self.status,
            "still_owed": p.get_total_due() if p else None,
            "due_date": p.due_date.isoformat() if p and p.due_date else None,
            "review_note": self.review_note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
