"""Merchant settlements (Phase 5).

SettlementLine: one amount the merchant is owed (+, a delivered sale) or owes back (-, a
clawback). Lines start unbatched (settlement_id is NULL) and are swept into the merchant's next
Settlement on their billing cycle (3, 7 or 30 days, §13.1 D4). A Settlement is paid out through
a Paystack transfer after an admin approves it.
"""
from datetime import datetime

from ..extensions import db


class Settlement(db.Model):
    __tablename__ = "settlements"

    PENDING_APPROVAL = "pending_approval"
    ON_HOLD = (
        "on_hold"  # payout details changed recently, or no verified payout account
    )
    PROCESSING = "processing"  # transfer started, waiting for Paystack
    PAID = "paid"
    FAILED = "failed"  # transfer failed or was reversed; can be retried

    id = db.Column(db.Integer, primary_key=True)
    settlement_id = db.Column(db.String(40), unique=True, nullable=False, index=True)
    merchant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date, nullable=False)
    gross_pesewas = db.Column(
        db.BigInteger, nullable=False, default=0
    )  # sales at product price
    fees_pesewas = db.Column(
        db.BigInteger, nullable=False, default=0
    )  # MDR on those sales
    clawbacks_pesewas = db.Column(
        db.BigInteger, nullable=False, default=0
    )  # negative lines
    net_pesewas = db.Column(
        db.BigInteger, nullable=False, default=0
    )  # what's transferred
    status = db.Column(
        db.String(20), nullable=False, default=PENDING_APPROVAL, index=True
    )
    hold_reason = db.Column(db.String(255))
    transfer_reference = db.Column(db.String(64), unique=True)
    transfer_code = db.Column(db.String(64))
    failure_reason = db.Column(db.String(255))
    approved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    approved_at = db.Column(db.DateTime)
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    merchant = db.relationship("User", foreign_keys=[merchant_id])
    lines = db.relationship("SettlementLine", backref="settlement", lazy="dynamic")

    def to_dict(self, with_lines=False):
        c = lambda p: round((p or 0) / 100, 2)
        body = {
            "id": self.id,
            "settlement_id": self.settlement_id,
            "merchant_id": self.merchant_id,
            "merchant_name": self.merchant.business_name if self.merchant else None,
            "period_start": self.period_start.isoformat()
            if self.period_start
            else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "gross": c(self.gross_pesewas),
            "fees": c(self.fees_pesewas),
            "clawbacks": c(self.clawbacks_pesewas),
            "net": c(self.net_pesewas),
            "status": self.status,
            "hold_reason": self.hold_reason,
            "failure_reason": self.failure_reason,
            "transfer_reference": self.transfer_reference,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if with_lines:
            body["lines"] = [
                l.to_dict() for l in self.lines.order_by(SettlementLine.id)
            ]
        return body


class SettlementLine(db.Model):
    __tablename__ = "settlement_lines"

    SALE = "sale"
    CLAWBACK = "clawback"

    id = db.Column(db.Integer, primary_key=True)
    merchant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    settlement_id = db.Column(
        db.Integer, db.ForeignKey("settlements.id"), nullable=True, index=True
    )
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=False, index=True
    )
    transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=True
    )
    line_type = db.Column(db.String(20), nullable=False)
    gross_pesewas = db.Column(db.BigInteger, nullable=False, default=0)
    fee_pesewas = db.Column(db.BigInteger, nullable=False, default=0)
    net_pesewas = db.Column(
        db.BigInteger, nullable=False
    )  # + owed to merchant, - owed back
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    plan = db.relationship("InstalmentPlan", foreign_keys=[plan_id])

    def to_dict(self):
        c = lambda p: round((p or 0) / 100, 2)
        return {
            "id": self.id,
            "type": self.line_type,
            "plan_id": self.plan_id,
            "plan_ref": self.plan.plan_id if self.plan else None,
            "product_name": self.plan.plan_name if self.plan else None,
            "gross": c(self.gross_pesewas),
            "fee": c(self.fee_pesewas),
            "net": c(self.net_pesewas),
            "description": self.description,
            "settlement_id": self.settlement_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PaymentLink(db.Model):
    """A shareable checkout link / QR code for one product (in-store and WhatsApp sales)."""

    __tablename__ = "payment_links"

    ACTIVE = "active"
    USED = "used"
    CANCELLED = "cancelled"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(40), unique=True, nullable=False, index=True)
    merchant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    note = db.Column(db.String(200))
    status = db.Column(db.String(20), nullable=False, default=ACTIVE)
    expires_at = db.Column(db.DateTime, nullable=False)
    # use_alter: purchase_orders also points here (payment_link_id), so the FK is added afterwards
    used_by_order_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "purchase_orders.id",
            use_alter=True,
            name="fk_payment_links_used_by_order_id",
        ),
        nullable=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    product = db.relationship("Product", foreign_keys=[product_id])
    merchant = db.relationship("User", foreign_keys=[merchant_id])

    def is_usable(self, now=None):
        now = now or datetime.utcnow()
        return self.status == self.ACTIVE and self.expires_at > now
