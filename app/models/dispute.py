from ..extensions import db
from datetime import datetime
import sqlalchemy as sa


class Dispute(db.Model):
    __tablename__ = "disputes"

    id = db.Column(db.Integer, primary_key=True)
    dispute_id = db.Column(db.String(50), unique=True, nullable=False, index=True)

    # Foreign Keys
    transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=False
    )
    merchant_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Dispute Details
    reason = db.Column(
        db.String(100), nullable=False
    )  # product_not_received, defective, not_as_described, unauthorized, other
    description = db.Column(db.Text, nullable=False)
    amount = db.Column(db.Float, nullable=False)

    # Evidence
    evidence_notes = db.Column(db.Text)
    evidence_files = db.Column(db.Text)  # JSON array of file URLs

    # Status
    status = db.Column(
        db.String(50), default="open"
    )  # open, under_review, resolved, closed, escalated
    resolution = db.Column(
        db.String(50)
    )  # refunded, partial_refund, rejected, customer_won, merchant_won

    # Resolution Details
    resolution_notes = db.Column(db.Text)
    refund_amount = db.Column(db.Float, default=0)
    resolved_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    resolved_at = db.Column(db.DateTime)

    # Communication
    merchant_notes = db.Column(db.Text)
    customer_notes = db.Column(db.Text)
    admin_notes = db.Column(db.Text)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime)

    # Relationships
    transaction = db.relationship("Transaction", backref="disputes")
    merchant = db.relationship(
        "User", foreign_keys=[merchant_id], backref="merchant_disputes"
    )
    customer = db.relationship(
        "User", foreign_keys=[customer_id], backref="customer_disputes"
    )
    resolver = db.relationship(
        "User", foreign_keys=[resolved_by], backref="resolved_disputes"
    )

    def generate_dispute_id(self):
        """Generate a dispute ID in format DSP001, DSP002, etc."""
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(Dispute.dispute_id, 4).cast(sa.Integer))
            )
            .filter(Dispute.dispute_id.isnot(None))
            .scalar()
        )
        return f"DSP{(result + 1) if result else 1:03d}"
