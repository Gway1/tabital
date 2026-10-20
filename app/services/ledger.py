"""Write money events to the ledger and read balances from it.

Callers add entries inside their own database transaction; this module never commits,
so the ledger row and the change it records are saved together or not at all.
"""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import case, func

from ..extensions import db
from ..models.ledger import LedgerEntry

CENT = Decimal("0.01")


def to_pesewas(amount) -> int:
    return int(
        (Decimal(str(amount)).quantize(CENT, ROUND_HALF_UP) * 100).to_integral_value()
    )


def to_cedis(pesewas) -> Decimal:
    return (Decimal(int(pesewas or 0)) / 100).quantize(CENT)


def record(
    plan,
    entry_type,
    amount,
    *,
    account=LedgerEntry.CUSTOMER,
    payment=None,
    reference=None,
    note=None,
    user=None,
    at=None,
):
    """Append one entry. `amount` is in GHS and carries its sign (see LedgerEntry).
    `at` backdates the entry; only the ledger-backfill command uses it (historical plans).
    """
    if plan.id is None:
        db.session.flush()
    entry = LedgerEntry(
        plan_id=plan.id,
        payment_id=payment.id if payment is not None else None,
        account=account,
        entry_type=entry_type,
        amount_pesewas=to_pesewas(amount),
        reference=reference,
        note=note,
        created_by=user.id if user is not None else None,
    )
    if at is not None:
        entry.created_at = at
    db.session.add(entry)
    return entry


def open_plan(
    plan, total_payable, merchant_fee, merchant_payable, user=None, fee_note=None
):
    """Record a new contract: what the customer owes and what the merchant is owed.

    fee_note records the merchant fee tier and rate used, so the contract shows how it was priced.
    """
    record(
        plan,
        LedgerEntry.PLAN_OPENED,
        total_payable,
        note="Contract total payable",
        user=user,
    )
    record(
        plan,
        LedgerEntry.MERCHANT_FEE,
        merchant_fee,
        account=LedgerEntry.MERCHANT,
        note=(fee_note or "Merchant discount (MDR)")[:255],
        user=user,
    )
    record(
        plan,
        LedgerEntry.MERCHANT_PAYABLE,
        merchant_payable,
        account=LedgerEntry.MERCHANT,
        note="Settlement owed to merchant",
        user=user,
    )


def payment_received(plan, payment, amount, reference, user=None):
    return record(
        plan,
        LedgerEntry.PAYMENT_RECEIVED,
        -Decimal(str(amount)),
        payment=payment,
        reference=reference,
        note=f"Payment {payment.installment_number}",
        user=user,
    )


def late_fee_charged(plan, payment, fee, user=None):
    return record(
        plan,
        LedgerEntry.LATE_FEE_CHARGED,
        fee,
        payment=payment,
        note=f"Late fee on payment {payment.installment_number}",
        user=user,
    )


def late_fee_waived(plan, payment, fee, reason=None, user=None):
    return record(
        plan,
        LedgerEntry.LATE_FEE_WAIVED,
        -Decimal(str(fee)),
        payment=payment,
        note=(reason or "Late fee waived")[:255],
        user=user,
    )


def late_fees_net(plan) -> Decimal:
    """Late fees charged on a plan minus those waived, in GHS."""
    total = (
        db.session.query(func.coalesce(func.sum(LedgerEntry.amount_pesewas), 0))
        .filter(
            LedgerEntry.plan_id == plan.id,
            LedgerEntry.entry_type.in_(
                [LedgerEntry.LATE_FEE_CHARGED, LedgerEntry.LATE_FEE_WAIVED]
            ),
        )
        .scalar()
    )
    return to_cedis(total)


def late_fee_cap_remaining(plan) -> Decimal:
    """How much more late fee this plan may be charged (cap: % of the order's total payable)."""
    from ..models.system_settings import SystemSetting

    cap_pct = Decimal(str(SystemSetting.get_value("late_fee_cap_percentage", 25)))
    cap = (Decimal(str(plan.total_amount or 0)) * cap_pct / 100).quantize(
        CENT, ROUND_HALF_UP
    )
    return max(cap - late_fees_net(plan), Decimal("0.00"))


def customer_balance(plan) -> Decimal:
    """What the customer still owes on this plan, in GHS, including unpaid late fees."""
    total = (
        db.session.query(func.coalesce(func.sum(LedgerEntry.amount_pesewas), 0))
        .filter(
            LedgerEntry.plan_id == plan.id, LedgerEntry.account == LedgerEntry.CUSTOMER
        )
        .scalar()
    )
    return to_cedis(total)


def has_entries(plan) -> bool:
    return (
        db.session.query(LedgerEntry.id).filter(LedgerEntry.plan_id == plan.id).first()
        is not None
    )


# ----------------------------------------------------------------------------
# Reads used by the dashboards and lists. All figures come from the ledger.
# Plans created before the ledger (not yet backfilled) fall back to the old
# stored numbers so screens keep working until `flask ledger-backfill` runs.
# ----------------------------------------------------------------------------


def _paid_expr():
    return func.coalesce(
        func.sum(
            case(
                (
                    LedgerEntry.entry_type == LedgerEntry.PAYMENT_RECEIVED,
                    -LedgerEntry.amount_pesewas,
                ),
                else_=0,
            )
        ),
        0,
    )


def _customer_sum_expr():
    return func.coalesce(
        func.sum(
            case(
                (
                    LedgerEntry.account == LedgerEntry.CUSTOMER,
                    LedgerEntry.amount_pesewas,
                ),
                else_=0,
            )
        ),
        0,
    )


def _merchant_fee_expr():
    return func.coalesce(
        func.sum(
            case(
                (
                    LedgerEntry.entry_type == LedgerEntry.MERCHANT_FEE,
                    LedgerEntry.amount_pesewas,
                ),
                else_=0,
            )
        ),
        0,
    )


def plan_balances(plans):
    """{plan_id: {"outstanding": Decimal, "paid": Decimal}} for a list of plans, in one query."""
    plans = [p for p in plans if p is not None]
    if not plans:
        return {}
    rows = (
        db.session.query(LedgerEntry.plan_id, _customer_sum_expr(), _paid_expr())
        .filter(LedgerEntry.plan_id.in_([p.id for p in plans]))
        .group_by(LedgerEntry.plan_id)
        .all()
    )
    found = {
        pid: {"outstanding": to_cedis(owed), "paid": to_cedis(paid)}
        for pid, owed, paid in rows
    }

    result = {}
    for p in plans:
        if p.id in found:
            result[p.id] = found[p.id]
        else:
            remaining = Decimal(str(p.remaining_amount or 0)).quantize(CENT)
            total = Decimal(str(p.total_amount or 0)).quantize(CENT)
            result[p.id] = {
                "outstanding": remaining,
                "paid": (total - remaining).quantize(CENT),
            }
    return result


def plan_balance(plan):
    return plan_balances([plan]).get(
        plan.id, {"outstanding": Decimal("0.00"), "paid": Decimal("0.00")}
    )


def merchant_fees_between(start, end=None) -> float:
    """MDR revenue recognised (plans opened) in [start, end)."""
    q = db.session.query(_merchant_fee_expr()).filter(LedgerEntry.created_at >= start)
    if end is not None:
        q = q.filter(LedgerEntry.created_at < end)
    return float(to_cedis(q.scalar()))


def portfolio_totals(*conditions):
    """Totals across all plans matching `conditions` (SQLAlchemy filters on InstalmentPlan).

    Returns {"outstanding", "paid", "merchant_fees", "plans"}. Sums are exact Decimals,
    returned as floats because they go straight into JSON dashboard responses.
    """
    from ..models.instalment import InstalmentPlan

    plans = InstalmentPlan.query.filter(*conditions).all()
    balances = plan_balances(plans)
    fees = (
        db.session.query(_merchant_fee_expr())
        .join(InstalmentPlan, InstalmentPlan.id == LedgerEntry.plan_id)
        .filter(*conditions)
        .scalar()
    )
    return {
        "outstanding": float(
            sum((b["outstanding"] for b in balances.values()), Decimal("0.00"))
        ),
        "paid": float(sum((b["paid"] for b in balances.values()), Decimal("0.00"))),
        "merchant_fees": float(to_cedis(fees)),
        "plans": len(plans),
    }
