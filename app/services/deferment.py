"""Instalment deferment (CLAUDE.md §4, interim rule §13 #8).

A customer may pay a deferment fee (10% of what's still owed on the instalment, founder 2026-09-27)
to push an upcoming instalment back
a month. The deferred instalment and every unpaid instalment after it move by the same amount,
so no two payments land in the same month. The deferment isn't a late payment, so it doesn't
lower the limit or tier.

Rules (all settings, pending founder confirmation):
- deferment_fee_percentage (10), deferment_max_per_plan (1), deferment_months (1)
- only an upcoming instalment (not Payment 1), on or before its due date, with no late fee
- the plan must be active, not paused by a dispute, with nothing overdue
- the fee is paid up front through Paystack; the deferment applies once Paystack confirms it
"""
import json
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from ..extensions import db
from ..models.deferment import Deferment
from ..models.instalment_payment import InstalmentPayment
from ..models.ledger import LedgerEntry
from ..models.system_settings import SystemSetting
from . import ledger
from .plan_engine import add_months

CENT = Decimal("0.01")


def _setting(key, default):
    return SystemSetting.get_value(key, default)


def fee_base(payment) -> Decimal:
    """What's left to pay on the instalment (founder, 2026-09-27): the instalment minus any part
    payments already received. Deferment is only allowed before a late fee, so none is included.
    """
    left = Decimal(str(payment.amount)).quantize(CENT) - payment.part_paid()
    return max(Decimal("0.00"), left)


def fee_for(payment) -> Decimal:
    pct = Decimal(str(_setting("deferment_fee_percentage", 10)))
    return (fee_base(payment) * pct / 100).quantize(CENT, ROUND_HALF_UP)


def months():
    return max(1, int(_setting("deferment_months", 1)))


def used(plan):
    return Deferment.query.filter_by(plan_id=plan.id, status=Deferment.APPLIED).count()


def remaining(plan):
    return max(0, int(_setting("deferment_max_per_plan", 1)) - used(plan))


def _unpaid_from(plan, payment):
    return (
        InstalmentPayment.query.filter(
            InstalmentPayment.plan_id == plan.id,
            InstalmentPayment.installment_number >= payment.installment_number,
            InstalmentPayment.status.in_(
                ("pending", "overdue", "pending_verification")
            ),
        )
        .order_by(InstalmentPayment.installment_number)
        .all()
    )


def reasons_not_allowed(plan, payment, today=None):
    """Why this instalment can't be deferred now; an empty list means it can."""
    today = today or datetime.utcnow().date()
    reasons = []
    if not _setting("deferment_enabled", True):
        return ["Deferment isn't available right now"]
    if plan.status != "active":
        reasons.append("Only active plans can be deferred")
    if plan.paused_at is not None:
        reasons.append("Payments on this plan are paused while a dispute is open")
    if payment is None or payment.plan_id != plan.id:
        return reasons + ["No instalment to defer"]
    if payment.installment_number <= 1:
        reasons.append("The down payment can't be deferred")
    if payment.status != "pending":
        reasons.append("Only an upcoming unpaid instalment can be deferred")
    if (
        not payment.due_date
        or payment.due_date.date() < today
        or payment.late_fee_applied_date
    ):
        reasons.append(
            "This instalment is already overdue. Deferments must be requested by the due date"
        )
    overdue = InstalmentPayment.query.filter(
        InstalmentPayment.plan_id == plan.id,
        InstalmentPayment.id != payment.id,
        InstalmentPayment.status.in_(("pending", "overdue")),
        InstalmentPayment.due_date < datetime.combine(today, datetime.min.time()),
    ).first()
    if overdue:
        reasons.append("Pay your overdue instalment first")
    if remaining(plan) <= 0:
        reasons.append("You've already used the deferment allowed on this plan")
    return reasons


def quote(plan, payment, today=None):
    """What the customer sees before agreeing: fee, the new dates, and the new total."""
    fee = fee_for(payment) if payment else Decimal("0.00")
    n = months()
    moved = (
        [
            {
                "payment_id": p.id,
                "installment_number": p.installment_number,
                "amount": float(p.amount),
                "due_date": p.due_date.date().isoformat(),
                "new_due_date": add_months(p.due_date, n).date().isoformat(),
            }
            for p in _unpaid_from(plan, payment)
        ]
        if payment
        else []
    )
    charged = (
        db.session.query(db.func.coalesce(db.func.sum(LedgerEntry.amount_pesewas), 0))
        .filter(
            LedgerEntry.plan_id == plan.id,
            LedgerEntry.account == LedgerEntry.CUSTOMER,
            LedgerEntry.amount_pesewas > 0,
        )
        .scalar()
    )
    total_before = (
        ledger.to_cedis(charged)
        if charged
        else Decimal(str(plan.total_amount or 0)).quantize(CENT)
    )
    reasons = reasons_not_allowed(plan, payment, today)
    return {
        "allowed": not reasons,
        "reasons": reasons,
        "plan_id": plan.id,
        "payment_id": payment.id if payment else None,
        "installment_number": payment.installment_number if payment else None,
        "instalment_amount": float(payment.amount) if payment else None,
        # The fee is a % of what's still owed on the instalment (after any part payments)
        "fee_base": float(fee_base(payment)) if payment else None,
        "fee": float(fee),
        "fee_percentage": float(_setting("deferment_fee_percentage", 10)),
        "months": n,
        "deferments_left": remaining(plan),
        "schedule": moved,
        "total_payable_before": float(total_before),
        "total_payable_after": float(total_before + fee),
    }


def apply_paid(intent):
    """The deferment fee was confirmed by Paystack: move the dates. Doesn't commit.

    Checked again now, because time has passed since the quote. If the deferment can no
    longer be applied, the fee is kept on record as received and flagged for a refund.
    """
    from ..models.instalment import InstalmentPlan
    from ..models.payment_intent import PaymentIntent

    intent.status = PaymentIntent.SUCCESS
    payment = intent.payment
    plan = InstalmentPlan.query.get(intent.plan_id)
    fee = ledger.to_cedis(intent.amount_pesewas)
    original = payment.due_date

    # Money arrived either way: record it (charged and paid, so the balance doesn't change)
    ledger.record(
        plan,
        LedgerEntry.DEFERMENT_FEE,
        fee,
        payment=payment,
        reference=intent.reference,
        note=f"Deferment fee for payment {payment.installment_number}",
    )
    ledger.record(
        plan,
        LedgerEntry.PAYMENT_RECEIVED,
        -fee,
        payment=payment,
        reference=intent.reference,
        note=f"Deferment fee for payment {payment.installment_number} paid",
    )

    problem = None
    if payment.status not in ("pending", "overdue"):
        problem = "The instalment was paid before the deferment went through"
    elif remaining(plan) <= 0:
        problem = "The deferment allowed on this plan was already used"
    elif plan.status != "active":
        problem = "The plan is no longer active"
    if problem:
        db.session.add(
            Deferment(
                plan_id=plan.id,
                payment_id=payment.id,
                customer_id=intent.customer_id,
                intent_id=intent.id,
                fee_pesewas=intent.amount_pesewas,
                months=months(),
                original_due_date=original,
                status=Deferment.REFUND_REQUIRED,
                note=f"Refund the fee: {problem}"[:255],
            )
        )
        intent.gateway_response = f"DEFERMENT NOT APPLIED: {problem}; refund the fee"[
            :255
        ]
        return "refund_required"

    # A late fee charged while the payment was going through is reversed: the new due date is later
    if payment.late_fee and not payment.late_fee_paid and payment.late_fee_applied_date:
        ledger.late_fee_waived(
            plan, payment, payment.late_fee, reason="Deferred before the due date"
        )
        payment.late_fee, payment.late_fee_applied_date, payment.late_fee_stage = (
            0,
            None,
            None,
        )

    n = months()
    moved = []
    for p in _unpaid_from(plan, payment):
        new_date = add_months(p.due_date, n)
        moved.append(
            {
                "payment_id": p.id,
                "installment_number": p.installment_number,
                "from": p.due_date.date().isoformat(),
                "to": new_date.date().isoformat(),
            }
        )
        if p.original_due_date is None:
            p.original_due_date = p.due_date
        p.due_date = new_date
        if p.status == "overdue":
            p.status = "pending"
    if plan.end_date:
        plan.end_date = add_months(plan.end_date, n)
    db.session.add(
        Deferment(
            plan_id=plan.id,
            payment_id=payment.id,
            customer_id=intent.customer_id,
            intent_id=intent.id,
            fee_pesewas=intent.amount_pesewas,
            months=n,
            original_due_date=original,
            new_due_date=payment.due_date,
            moved_json=json.dumps(moved),
            status=Deferment.APPLIED,
        )
    )
    return "deferred"
