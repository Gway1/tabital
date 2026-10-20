"""One attempt to collect an instalment through Paystack.

The amount is fixed by the server when the attempt starts. The instalment is only marked
paid when Paystack confirms a successful charge for this reference and amount.
"""
from datetime import datetime

from ..extensions import db


class PaymentIntent(db.Model):
    __tablename__ = "payment_intents"

    INITIALIZED = "initialized"
    SUCCESS = "success"
    FAILED = "failed"
    ABANDONED = "abandoned"
    AMOUNT_MISMATCH = (
        "amount_mismatch"  # Paystack says paid, but not the amount we asked for
    )

    # purpose values
    INSTALMENT = "instalment"  # an instalment on an approved plan
    DOWN_PAYMENT = "down_payment"  # Payment 1, collected at checkout before approval
    DEFERMENT_FEE = "deferment_fee"  # fee to push an instalment back a month (§4)

    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(64), unique=True, nullable=False, index=True)
    provider = db.Column(db.String(20), nullable=False, default="paystack")
    purpose = db.Column(db.String(20), nullable=False, default=INSTALMENT)
    # Instalment intents link to the plan/payment; down-payment intents link to the order
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=True, index=True
    )
    payment_id = db.Column(
        db.Integer, db.ForeignKey("instalment_payments.id"), nullable=True, index=True
    )
    order_id = db.Column(
        db.Integer, db.ForeignKey("purchase_orders.id"), nullable=True, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    amount_pesewas = db.Column(db.BigInteger, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default="GHS")
    status = db.Column(db.String(20), nullable=False, default=INITIALIZED)
    channel = db.Column(db.String(30))  # card, mobile_money, ...
    gateway_response = db.Column(db.String(255))
    paid_amount_pesewas = db.Column(db.BigInteger)
    paid_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    payment = db.relationship("InstalmentPayment", foreign_keys=[payment_id])
    plan = db.relationship("InstalmentPlan", foreign_keys=[plan_id])
    order = db.relationship("PurchaseOrder", foreign_keys=[order_id])
