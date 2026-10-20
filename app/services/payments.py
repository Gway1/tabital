"""Applying confirmed money to instalments. Shared by admin confirmation, collections and Paystack."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..extensions import db
from ..models.instalment import InstalmentPlan
from . import ledger

CENT = Decimal("0.01")

# Instalments a customer can still pay
PAYABLE_STATUSES = ("pending", "overdue", "pending_verification")


def next_payable_instalment(plan):
    from ..models.instalment_payment import InstalmentPayment

    return (
        InstalmentPayment.query.filter(
            InstalmentPayment.plan_id == plan.id,
            InstalmentPayment.status.in_(PAYABLE_STATUSES),
        )
        .order_by(InstalmentPayment.installment_number)
        .first()
    )


class PaymentError(ValueError):
    pass


def _pesewas(amount) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"), ROUND_HALF_UP))


def reference_in_use(reference) -> bool:
    """A MoMo/bank reference already recorded against any instalment (full or part payment)."""
    from ..models.instalment_payment import InstalmentPayment
    from ..models.part_payment import InstalmentPartPayment

    return bool(
        InstalmentPartPayment.query.filter_by(reference=reference).first()
        or InstalmentPayment.query.filter_by(payment_reference=reference).first()
    )


def record_payment(payment, amount, payment_method, payment_reference, user=None):
    """Record money received for an instalment outside Paystack (collections). Doesn't commit.

    Less than what's owed is a part payment: saved as its own receipt, written to the ledger, and the
    instalment stays unpaid (and overdue if it is) until the rest arrives, so it never counts as paid
    on time (D14). Money goes to the instalment first, then late fees (interim rule, to confirm), so
    later late fees are charged on the smaller overdue amount. Exactly what's owed completes it; more
    is refused. Returns 'part' or 'paid'.
    """
    from ..models.part_payment import InstalmentPartPayment

    payment = lock(payment)
    if payment.status == "paid":
        raise PaymentError("This instalment is already paid")
    if payment.status == "pending_verification":
        raise PaymentError(
            "This down payment is waiting for verification; confirm it in Instalments"
        )
    reference = (payment_reference or "").strip()
    if len(reference) < 3:
        raise PaymentError("Enter the MoMo/bank reference of the money received")
    if reference_in_use(reference):
        raise PaymentError("That reference has already been recorded")
    try:
        received = _pesewas(amount)
    except (InvalidOperation, TypeError, ValueError):
        raise PaymentError("Enter the amount received")
    owed = _pesewas(payment.get_total_due())
    if received <= 0:
        raise PaymentError("Enter the amount received")
    if received > owed:
        raise PaymentError(
            f"That's more than the {owed / 100:.2f} still owed on this instalment"
        )
    if received == owed:
        mark_instalment_paid(
            payment,
            payment_method,
            reference,
            amount_received=received / 100,
            user=user,
        )
        return "paid"

    plan = InstalmentPlan.query.get(payment.plan_id)
    db.session.add(
        InstalmentPartPayment(
            payment_id=payment.id,
            plan_id=payment.plan_id,
            amount_pesewas=received,
            method=payment_method,
            reference=reference,
            recorded_by=user.id if user else None,
        )
    )
    ledger.payment_received(
        plan, payment, Decimal(received) / 100, reference, user=user
    )
    plan.payment_status = "partial"
    db.session.flush()
    return "part"


def lock(payment):
    """Re-read the instalment with a row lock (Postgres SELECT ... FOR UPDATE) so two payments,
    a webhook and a collections entry, can't both read the same 'still owed' and double-record.
    SQLite ignores the lock; tests run one request at a time."""
    from ..models.instalment_payment import InstalmentPayment

    if payment.id is None:
        return payment
    return (
        db.session.query(InstalmentPayment)
        .with_for_update()
        .populate_existing()
        .filter(InstalmentPayment.id == payment.id)
        .one()
    )


def waive_late_fee(payment, reason, user=None):
    """Waive what's left of an instalment's late fee. Doesn't commit. Returns the amount waived.

    Money goes to the instalment first, then the fee, so part payments above the instalment amount
    have already paid part of the fee: that part can't be waived (it would leave the customer in
    credit). If nothing is owed after the waiver, the instalment is complete.
    """
    payment = lock(payment)
    if not payment.late_fee:
        raise PaymentError("No late fee to waive")
    if payment.late_fee_paid:
        raise PaymentError("This late fee was already paid; issue a refund instead")
    amount = Decimal(str(payment.amount)).quantize(CENT)
    fee = Decimal(str(payment.late_fee)).quantize(CENT)
    covered = min(fee, max(Decimal("0.00"), payment.part_paid() - amount))
    waivable = fee - covered
    if waivable <= 0:
        raise PaymentError("The late fee has already been paid through part payments")
    plan = InstalmentPlan.query.get(payment.plan_id)
    ledger.late_fee_waived(plan, payment, waivable, reason=reason, user=user)
    # The instalment stays overdue; late_fee_applied_date stays set so the fee isn't charged again
    payment.late_fee = float(covered)
    db.session.flush()
    if Decimal(str(payment.get_total_due())) <= 0:
        # Part payments already cover what's left: the instalment is paid (no new money)
        last = payment.part_payments()[-1]
        mark_instalment_paid(
            payment, last.method, last.reference, amount_received=0, user=user
        )
    return waivable


def mark_instalment_paid(
    payment, payment_method, payment_reference, amount_received=None, user=None
):
    """Record the payment that completes an instalment, write it to the ledger, and update the plan.

    amount_received is this payment only (what's still owed when not given). Earlier part payments
    are already in the ledger, so paid_amount = part payments + this payment.
    Doesn't commit: the caller commits so everything is saved together.
    remaining_amount tracks the financed balance, which excludes Payment 1
    (down payment + delivery fee), so only instalments 2..N reduce it.
    """
    payment = lock(payment)
    owed = Decimal(str(payment.get_total_due())).quantize(CENT)
    before = payment.part_paid()
    this_payment = (
        Decimal(str(amount_received)).quantize(CENT)
        if amount_received is not None
        else owed
    )
    payment.status = "paid"
    payment.paid_date = datetime.utcnow()
    payment.paid_amount = float(before + this_payment)
    payment.payment_method = payment_method
    payment.payment_reference = payment_reference
    # A late fee charged after the customer started paying stays owed (and in the ledger)
    if payment.late_fee and this_payment + Decimal("0.005") >= owed:
        payment.late_fee_paid = True

    plan = InstalmentPlan.query.get(payment.plan_id)
    if plan:
        if this_payment > 0:  # a waiver can complete an instalment with no new money
            ledger.payment_received(
                plan, payment, this_payment, payment_reference, user=user
            )
        plan.paid_installments = (plan.paid_installments or 0) + 1
        if payment.installment_number > 1:
            plan.remaining_amount = round(
                (plan.remaining_amount or 0) - payment.amount, 2
            )
        plan.payment_status = "partial"
        if plan.paid_installments >= plan.number_of_installments:
            plan.status = "completed"
            plan.payment_status = "completed"
            plan.completed_at = datetime.utcnow()


def apply_verified_transaction(intent, data):
    """Apply a Paystack transaction that was verified with Paystack's API.

    Idempotent: calling it again for the same successful intent does nothing.
    Returns a short outcome string. Doesn't commit.
    """
    from ..models.payment_intent import PaymentIntent

    if intent.status == PaymentIntent.SUCCESS:
        return "already_applied"

    intent.channel = data.get("channel") or intent.channel
    intent.gateway_response = (data.get("gateway_response") or "")[
        :255
    ] or intent.gateway_response
    status = data.get("status")

    if status != "success":
        if status in ("failed", "reversed"):
            intent.status = PaymentIntent.FAILED
        elif status == "abandoned":
            intent.status = PaymentIntent.ABANDONED
        return status or "unknown"

    if data.get("reference") and data["reference"] != intent.reference:
        intent.status = PaymentIntent.AMOUNT_MISMATCH
        return "reference_mismatch"

    amount = int(data.get("amount") or 0)
    intent.paid_amount_pesewas = amount
    if data.get("currency") != intent.currency or amount < intent.amount_pesewas:
        # Money arrived but not what we asked for: never mark paid automatically
        intent.status = PaymentIntent.AMOUNT_MISMATCH
        return "amount_mismatch"

    intent.paid_at = datetime.utcnow()

    # Keep a reusable card for autopay (off until the customer switches it on)
    from . import autopay

    email = ((data.get("customer") or {}).get("email")) or None
    if email:
        autopay.save_authorization(intent.customer_id, email, data)

    if intent.purpose == PaymentIntent.DOWN_PAYMENT:
        return _apply_down_payment(intent)
    if intent.purpose == PaymentIntent.DEFERMENT_FEE:
        from . import deferment

        return deferment.apply_paid(intent)

    payment = lock(intent.payment)
    if payment.status == "paid":
        # Already settled another way (admin or another attempt): flag for refund review
        intent.status = PaymentIntent.SUCCESS
        intent.gateway_response = (
            "DUPLICATE: instalment was already paid; review for refund"
        )
        return "duplicate_payment"

    owed = _pesewas(payment.get_total_due())
    mark_instalment_paid(
        payment,
        f"paystack_{intent.channel or 'unknown'}",
        intent.reference,
        amount_received=amount / 100,
    )
    intent.status = PaymentIntent.SUCCESS
    if amount > owed:
        # A part payment was recorded after this checkout started: the extra must go back
        intent.gateway_response = (
            f"OVERPAID by {(amount - owed) / 100:.2f}: review for refund"
        )
        return "overpaid"
    return "applied"


def _apply_down_payment(intent):
    """Checkout down payment confirmed: the order moves to 'pending' (awaiting admin approval)."""
    from ..models.payment_intent import PaymentIntent

    order = intent.order
    intent.status = PaymentIntent.SUCCESS
    if order.down_payment_status == "paid":
        # Paid twice (e.g. two checkout tabs): keep the first, flag this one for refund
        intent.gateway_response = (
            "DUPLICATE: down payment already received; review for refund"
        )
        return "duplicate_payment"

    order.down_payment_status = "paid"
    order.down_payment_reference = intent.reference
    order.down_payment_method = f"paystack_{intent.channel or 'unknown'}"
    order.down_payment_paid_at = datetime.utcnow()
    if order.status == "awaiting_payment":
        order.status = "pending"
        return "applied"
    if order.status in ("rejected", "cancelled"):
        # Money arrived after the order was closed: it must go back to the customer
        order.refund_status = "refund_required"
        return "refund_required"
    return "applied"
