# models/gateway.py - merchant payment gateway: API keys, checkout sessions, webhook queue
from datetime import datetime, timedelta

from ..extensions import db


class MerchantApiKey(db.Model):
    __tablename__ = "merchant_api_keys"

    id = db.Column(db.Integer, primary_key=True)
    merchant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    label = db.Column(db.String(60))
    key_prefix = db.Column(db.String(24), nullable=False, index=True)
    key_hash = db.Column(db.String(64), nullable=False)  # SHA-256 of the full secret
    last4 = db.Column(db.String(4))
    status = db.Column(db.String(20), default="active")  # active, revoked
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)

    merchant = db.relationship("User", foreign_keys=[merchant_id])


class CheckoutSession(db.Model):
    __tablename__ = "checkout_sessions"
    __table_args__ = (
        db.UniqueConstraint(
            "merchant_id", "merchant_reference", name="uq_checkout_merchant_reference"
        ),
    )

    OPEN, USED, EXPIRED, CANCELLED = "open", "used", "expired", "cancelled"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False, index=True)
    merchant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    merchant_reference = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(3), default="GHS")
    description = db.Column(db.String(200))
    items = db.Column(db.Text)  # JSON snapshot of the merchant's cart
    customer_hint = db.Column(db.String(200))  # optional email/phone shown to the customer
    success_url = db.Column(db.String(500))  # optional for POS sessions
    cancel_url = db.Column(db.String(500))
    medium = db.Column(db.String(12), nullable=False, default="ecommerce")  # ecommerce, pos
    customer_phone = db.Column(db.String(20))  # POS: who the checkout link was sent to
    terminal_id = db.Column(db.String(60))
    notify_status = db.Column(db.Text)  # JSON {channel: queued|sent|failed|skipped}
    status = db.Column(db.String(20), default=OPEN)
    expires_at = db.Column(db.DateTime, nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    # No foreign key: purchase_orders already points here, and a cycle breaks create_all
    order_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    merchant = db.relationship("User", foreign_keys=[merchant_id])

    def is_open(self):
        return self.status == self.OPEN and self.expires_at > datetime.utcnow()

    @staticmethod
    def new_expiry(minutes):
        return datetime.utcnow() + timedelta(minutes=minutes)


class WebhookDelivery(db.Model):
    """Outbound webhook to a merchant. Queued in the same transaction as the change it reports."""

    __tablename__ = "webhook_deliveries"

    PENDING, DELIVERED, FAILED = "pending", "delivered", "failed"

    id = db.Column(db.Integer, primary_key=True)
    merchant_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    order_id = db.Column(db.Integer, db.ForeignKey("purchase_orders.id"))
    event = db.Column(db.String(40), nullable=False)
    payload = db.Column(db.Text, nullable=False)  # JSON, frozen when queued
    status = db.Column(db.String(20), default=PENDING, index=True)
    attempts = db.Column(db.Integer, default=0)
    next_attempt_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_error = db.Column(db.String(255))
    last_status_code = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered_at = db.Column(db.DateTime)
