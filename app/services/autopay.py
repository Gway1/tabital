"""Autopay (Phase 4): charge a customer's saved card on the due date, with retries.

A reusable Paystack authorization is saved automatically after a successful card payment
(save_authorization). The customer has to switch autopay on. The daily job then charges
the next unpaid instalment on the due date and on each retry day (`autopay_retry_days`,
default due date, +1 and +3), once per day per instalment. Paused plans are skipped.
"""
import uuid
from datetime import datetime, timedelta

from flask import current_app

from ..extensions import db

DEFAULT_RETRY_DAYS = [0, 1, 3]


def save_authorization(customer_id, email, data):
    """Store/refresh a reusable authorization from a successful Paystack transaction. No commit."""
    from ..models.payment_method import PaymentMethod

    auth = (data or {}).get("authorization") or {}
    if not auth.get("authorization_code") or not auth.get("reusable"):
        return None
    signature = auth.get("signature")
    existing = None
    if signature:
        existing = PaymentMethod.query.filter_by(
            customer_id=customer_id, signature=signature, revoked_at=None
        ).first()
    method = existing or PaymentMethod(customer_id=customer_id, signature=signature)
    method.authorization_code = auth["authorization_code"]
    method.email = email
    method.channel = auth.get("channel")
    method.card_type = auth.get("card_type")
    method.bank = auth.get("bank")
    method.last4 = auth.get("last4")
    method.exp_month = auth.get("exp_month")
    method.exp_year = auth.get("exp_year")
    method.reusable = True
    if existing is None:
        # The newest card becomes the default; autopay stays off until the customer opts in
        for other in PaymentMethod.query.filter_by(
            customer_id=customer_id, revoked_at=None
        ).all():
            other.is_default = False
        method.is_default = True
        method.autopay_enabled = False
        db.session.add(method)
    return method


def run(today=None):
    """Attempt autopay for every eligible instalment due today or on a retry day. Commits."""
    from ..models.instalment import InstalmentPlan
    from ..models.instalment_payment import InstalmentPayment
    from ..models.payment_intent import PaymentIntent
    from ..models.payment_method import PaymentMethod
    from ..models.system_settings import SystemSetting
    from . import paystack
    from .ledger import to_pesewas
    from .payments import apply_verified_transaction, next_payable_instalment

    if not paystack.is_configured():
        return 0
    today = today or datetime.utcnow().date()
    retry_days = (
        SystemSetting.get_value("autopay_retry_days", None) or DEFAULT_RETRY_DAYS
    )
    day_start = datetime.combine(today, datetime.min.time())

    methods = PaymentMethod.query.filter_by(
        autopay_enabled=True, is_default=True, reusable=True, revoked_at=None
    ).all()
    attempts = 0
    for method in methods:
        plans = InstalmentPlan.query.filter_by(
            customer_id=method.customer_id, status="active", paused_at=None
        ).all()
        for plan in plans:
            payment = next_payable_instalment(plan)
            if (
                not payment
                or payment.status == "pending_verification"
                or not payment.due_date
            ):
                continue
            if (today - payment.due_date.date()).days not in [
                int(d) for d in retry_days
            ]:
                continue
            # Once per instalment per day, and never while another attempt is in flight
            already = PaymentIntent.query.filter(
                PaymentIntent.payment_id == payment.id,
                PaymentIntent.created_at >= day_start,
            ).first()
            if already:
                continue

            amount_pesewas = to_pesewas(payment.get_total_due())
            intent = PaymentIntent(
                reference=f"TBA-{plan.id}-{payment.installment_number}-{uuid.uuid4().hex[:10]}",
                purpose=PaymentIntent.INSTALMENT,
                plan_id=plan.id,
                payment_id=payment.id,
                customer_id=method.customer_id,
                amount_pesewas=amount_pesewas,
                currency="GHS",
                channel="autopay",
            )
            db.session.add(intent)
            db.session.flush()
            attempts += 1
            try:
                data = paystack.charge_authorization(
                    email=method.email,
                    amount_pesewas=amount_pesewas,
                    authorization_code=method.authorization_code,
                    reference=intent.reference,
                    metadata={
                        "plan_id": plan.id,
                        "installment_number": payment.installment_number,
                        "autopay": True,
                    },
                )
            except paystack.PaystackError as e:
                intent.status = PaymentIntent.FAILED
                intent.gateway_response = str(e)[:255]
                current_app.logger.warning(
                    "Autopay failed for %s: %s", intent.reference, e
                )
                continue
            # A successful charge comes straight from Paystack's API, so it can be applied now;
            # anything else is finished later by the webhook.
            apply_verified_transaction(intent, data)
    db.session.commit()
    return attempts
