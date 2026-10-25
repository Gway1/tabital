# models/purchase_order.py
from ..extensions import db
from datetime import datetime
import sqlalchemy as sa

# models/purchase_order.py - Add missing fields if not present


class PurchaseOrder(db.Model):
    __tablename__ = "purchase_orders"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), unique=True, nullable=False, index=True)

    # Relationships
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    merchant_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=True)

    # Order Details
    product_name = db.Column(db.String(200), nullable=False)
    product_description = db.Column(db.Text)  # ✅ This exists
    product_price = db.Column(db.Float, nullable=False)
    product_image = db.Column(db.Text)
    quantity = db.Column(db.Integer, default=1)

    # Installment Details
    number_of_installments = db.Column(db.Integer, nullable=False)
    down_payment_amount = db.Column(db.Float, nullable=False)
    installment_amount = db.Column(db.Float, nullable=False)
    total_payable = db.Column(db.Float, nullable=False)
    # remaining_balance is calculated, not stored

    # Payment Schedule (JSON)
    payment_schedule = db.Column(db.Text)  # ✅ This stores the schedule

    # Status: awaiting_payment -> pending (down payment received, awaiting approval)
    #         -> approved / rejected -> completed
    status = db.Column(db.String(50), default="pending")
    admin_notes = db.Column(db.Text)

    # Down payment collected at checkout (Payment 1 = down payment + delivery fee)
    down_payment_status = db.Column(db.String(30), default="unpaid")  # unpaid, paid
    down_payment_reference = db.Column(db.String(100))
    down_payment_method = db.Column(db.String(50))
    down_payment_paid_at = db.Column(db.DateTime)
    # Set when a paid order is rejected: refunded, refund_failed
    refund_status = db.Column(db.String(30))
    refund_reference = db.Column(db.String(100))
    # Set on approval, so delivery updates the right transaction (was matched by product name)
    transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=True
    )
    # In-store / WhatsApp sale made through a merchant payment link
    payment_link_id = db.Column(
        db.Integer, db.ForeignKey("payment_links.id"), nullable=True
    )
    # Order placed through the merchant payment gateway (hosted checkout)
    checkout_session_id = db.Column(
        db.Integer, db.ForeignKey("checkout_sessions.id"), nullable=True, unique=True
    )
    merchant_reference = db.Column(db.String(100))  # the merchant's own order number
    items_json = db.Column(db.Text)  # cart snapshot sent by the merchant's site
    # The underwriting decision this order was accepted under
    risk_assessment_id = db.Column(
        db.Integer, db.ForeignKey("risk_assessments.id"), nullable=True
    )
    # Consent (§10): which Terms the customer agreed to, and when
    terms_version = db.Column(db.String(40))
    terms_accepted_at = db.Column(db.DateTime)

    # Delivery
    delivery_address = db.Column(db.String(500))
    delivery_status = db.Column(db.String(50), default="pending")

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)
    rejected_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)

    # Relationships
    customer = db.relationship(
        "User", foreign_keys=[customer_id], backref="customer_orders"
    )
    merchant = db.relationship(
        "User", foreign_keys=[merchant_id], backref="merchant_orders"
    )
    product = db.relationship("Product", foreign_keys=[product_id], backref="orders")

    def generate_order_id(self):
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(PurchaseOrder.order_id, 4).cast(sa.Integer))
            )
            .filter(PurchaseOrder.order_id.isnot(None))
            .scalar()
        )
        return f"ORD{(result + 1) if result else 1:04d}"
