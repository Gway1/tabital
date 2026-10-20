from ..extensions import db
from datetime import datetime
import sqlalchemy as sa


class Transaction(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.String(50), unique=True, nullable=False, index=True)

    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    merchant_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    amount = db.Column(db.Float, nullable=False)
    payout_amount = db.Column(db.Float)
    product_name = db.Column(db.String(200), nullable=False)
    product_description = db.Column(db.Text)
    quantity = db.Column(db.Integer, default=1)

    payment_method = db.Column(db.String(50))
    payment_status = db.Column(db.String(50), default="pending")
    payment_reference = db.Column(db.String(100))

    status = db.Column(db.String(50), default="pending", index=True)
    transaction_date = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    completion_date = db.Column(db.DateTime)

    delivery_address = db.Column(db.String(200))
    delivery_status = db.Column(db.String(50), default="pending")
    tracking_number = db.Column(db.String(100))
    notes = db.Column(db.Text)
    payment_plan = db.Column(db.String(50), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    customer = db.relationship(
        "User", foreign_keys=[customer_id], backref="customer_transactions"
    )
    merchant = db.relationship(
        "User", foreign_keys=[merchant_id], backref="merchant_transactions"
    )

    def generate_transaction_id(self):
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(Transaction.transaction_id, 4).cast(sa.Integer))
            )
            .filter(Transaction.transaction_id.isnot(None))
            .scalar()
        )
        return f"TRX{(result + 1) if result else 1:03d}"
