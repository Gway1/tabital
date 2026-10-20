"""Merchant settlements, clawbacks and payout-account security (Phase 5).

Money flow (all amounts also recorded in the merchant account of the ledger):
  approval   -> merchant_payable  +(P - MDR)            what the merchant will be owed
  delivery   -> SettlementLine 'sale' +(P - MDR)         now eligible for payout
  clawback   -> merchant_clawback -(owed)                dispute lost by merchant / refund
               + SettlementLine 'clawback' -(amount) if the sale was already paid out
  cycle end  -> Settlement batch (3/7/30 days, D4) of all unbatched lines, net > 0 only
  approval   -> Paystack transfer; on success merchant_settled -(line net) per line
"""
import uuid
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import func

from ..extensions import db
from ..models.ledger import LedgerEntry
from ..models.settlement import Settlement, SettlementLine
from . import ledger

ALLOWED_PERIODS = (3, 7, 30)
DEFAULT_PERIOD = 7


# ---------------------------------------------------------------- ledger helpers


def merchant_owed_pesewas(plan):
    """What the merchant is still owed on a plan (payable - settled - clawed back), in pesewas."""
    total = (
        db.session.query(func.coalesce(func.sum(LedgerEntry.amount_pesewas), 0))
        .filter(
            LedgerEntry.plan_id == plan.id,
            LedgerEntry.account == LedgerEntry.MERCHANT,
            LedgerEntry.entry_type.in_(
                [
                    LedgerEntry.MERCHANT_PAYABLE,
                    LedgerEntry.MERCHANT_SETTLED,
                    LedgerEntry.MERCHANT_CLAWBACK,
                ]
            ),
        )
        .scalar()
    )
    return int(total or 0)


def _entry_sum(plan, entry_type):
    total = (
        db.session.query(func.coalesce(func.sum(LedgerEntry.amount_pesewas), 0))
        .filter(LedgerEntry.plan_id == plan.id, LedgerEntry.entry_type == entry_type)
        .scalar()
    )
    return int(total or 0)


# ---------------------------------------------------------------- delivery and clawbacks


def record_delivery(plan, transaction=None):
    """The merchant confirmed delivery: the sale becomes eligible for the next settlement.

    Idempotent (one sale line per plan). Nothing is created if the sale was already clawed back.
    Doesn't commit. Returns the SettlementLine or None.
    """
    existing = SettlementLine.query.filter_by(
        plan_id=plan.id, line_type=SettlementLine.SALE
    ).first()
    if existing:
        return existing
    owed = merchant_owed_pesewas(plan)
    if owed <= 0:
        return None
    fee = _entry_sum(plan, LedgerEntry.MERCHANT_FEE)
    line = SettlementLine(
        merchant_id=plan.merchant_id,
        plan_id=plan.id,
        transaction_id=transaction.id if transaction else plan.transaction_id,
        line_type=SettlementLine.SALE,
        gross_pesewas=owed + fee,
        fee_pesewas=fee,
        net_pesewas=owed,
        description=f"Sale {plan.plan_id}: {plan.plan_name}"[:255],
    )
    db.session.add(line)
    return line


def clawback_plan(plan, reason, user=None):
    """Take back the merchant's share of a plan (dispute won by the customer, refund).

    - Not delivered yet, or delivered but not in a paid/processing batch: the sale simply
      won't be paid (line removed / never created).
    - Already paid out (or being paid): a negative line comes off the next settlement.
    Doesn't commit. Returns the amount clawed back in pesewas.
    """
    owed = merchant_owed_pesewas(plan)
    sale = SettlementLine.query.filter_by(
        plan_id=plan.id, line_type=SettlementLine.SALE
    ).first()

    if sale is not None and sale.settlement_id is not None:
        batch = Settlement.query.get(sale.settlement_id)
        if batch and batch.status in (Settlement.PAID, Settlement.PROCESSING):
            # Money has gone (or is going) out: recover it from the next settlement
            amount = sale.net_pesewas
            if not SettlementLine.query.filter_by(
                plan_id=plan.id, line_type=SettlementLine.CLAWBACK
            ).first():
                db.session.add(
                    SettlementLine(
                        merchant_id=plan.merchant_id,
                        plan_id=plan.id,
                        transaction_id=sale.transaction_id,
                        line_type=SettlementLine.CLAWBACK,
                        gross_pesewas=0,
                        fee_pesewas=0,
                        net_pesewas=-amount,
                        description=f"Clawback {plan.plan_id}: {reason}"[:255],
                    )
                )
                ledger.record(
                    plan,
                    LedgerEntry.MERCHANT_CLAWBACK,
                    -(amount / 100),
                    account=LedgerEntry.MERCHANT,
                    note=reason[:255],
                    user=user,
                )
            return amount
        # Batched but not paid: take the line out of the batch
        sale.settlement_id = None
        if batch:
            _recalculate(batch)

    if sale is not None:
        db.session.delete(sale)
    if owed > 0:
        ledger.record(
            plan,
            LedgerEntry.MERCHANT_CLAWBACK,
            -(owed / 100),
            account=LedgerEntry.MERCHANT,
            note=reason[:255],
            user=user,
        )
    return max(owed, 0)


# ---------------------------------------------------------------- payout account security


def payout_details_changed(merchant, by_admin=False):
    """Call whenever a merchant's bank/MoMo payout details change.

    The Paystack recipient must be recreated. When the merchant changed them, payouts are
    held for `payout_hold_hours` (default 48) and the merchant is notified, so a hijacked
    account can't quietly redirect money. Doesn't commit.
    """
    from ..models.notifications import Notifications
    from ..models.system_settings import SystemSetting

    merchant.paystack_recipient_code = None
    merchant.payout_details_updated_at = datetime.utcnow()
    if not by_admin:
        hours = int(SystemSetting.get_value("payout_hold_hours", 48))
        merchant.payout_hold_until = datetime.utcnow() + timedelta(hours=hours)
        db.session.add(
            Notifications(
                notification_id=Notifications.generate_notification_id(),
                user_id=merchant.id,
                user_role="merchant",
                title="Payout details changed",
                message=f"Your payout account was changed. For your security, payouts are paused for "
                f"{hours} hours. If you didn't make this change, contact Tabital immediately.",
                type="system",
                link="/merchant/settings",
            )
        )


def payout_account(merchant):
    """(account_number, name) for the merchant's chosen payout method, or (None, reason)."""
    if merchant.payout_method == "mobile_money":
        if merchant.momo_number and merchant.payout_bank_code:
            return merchant.momo_number, (merchant.momo_name or merchant.business_name)
        return None, "Mobile Money number or provider missing"
    if merchant.payout_method == "bank":
        if merchant.account_number and merchant.payout_bank_code:
            return merchant.account_number, (
                merchant.account_name or merchant.business_name
            )
        return None, "Bank account number or bank missing"
    return None, "No payout method chosen"


def hold_reason(merchant, now=None):
    now = now or datetime.utcnow()
    if merchant.status not in ("approved", "active"):
        return "Merchant account isn't active"
    if merchant.payout_hold_until and merchant.payout_hold_until > now:
        return f"Payout details changed; on hold until {merchant.payout_hold_until:%d %b %Y %H:%M} UTC"
    from . import fraud

    flagged = fraud.merchant_hold_reason(merchant)
    if flagged:
        return flagged
    account, reason = payout_account(merchant)
    if not account:
        return reason
    return None


# ---------------------------------------------------------------- batches


def _recalculate(batch):
    lines = SettlementLine.query.filter_by(settlement_id=batch.id).all()
    batch.gross_pesewas = sum(
        l.gross_pesewas for l in lines if l.line_type == SettlementLine.SALE
    )
    batch.fees_pesewas = sum(
        l.fee_pesewas for l in lines if l.line_type == SettlementLine.SALE
    )
    batch.clawbacks_pesewas = sum(
        l.net_pesewas for l in lines if l.line_type == SettlementLine.CLAWBACK
    )
    batch.net_pesewas = sum(l.net_pesewas for l in lines)


def _next_due(merchant, oldest_line_date):
    period = (
        merchant.settlement_period_days
        if merchant.settlement_period_days in ALLOWED_PERIODS
        else DEFAULT_PERIOD
    )
    last = (
        Settlement.query.filter_by(merchant_id=merchant.id)
        .order_by(Settlement.period_end.desc())
        .first()
    )
    start = last.period_end if last else oldest_line_date
    return start + timedelta(days=period), (
        last.period_end + timedelta(days=1) if last else oldest_line_date
    )


def generate_batches(today=None):
    """Create settlement batches for every merchant whose billing cycle has ended. Commits.

    Returns the list of new Settlements. A merchant whose unbatched net is zero or negative
    (clawbacks larger than new sales) gets no batch; the lines carry forward.
    """
    from ..models.user import User

    today = today or datetime.utcnow().date()
    merchant_ids = [
        m
        for (m,) in db.session.query(SettlementLine.merchant_id)
        .filter(SettlementLine.settlement_id.is_(None))
        .distinct()
        .all()
    ]
    created = []
    for merchant_id in merchant_ids:
        merchant = User.query.get(merchant_id)
        lines = SettlementLine.query.filter(
            SettlementLine.merchant_id == merchant_id,
            SettlementLine.settlement_id.is_(None),
        ).all()
        lines = [l for l in lines if l.created_at.date() <= today]
        if not lines:
            continue
        oldest = min(l.created_at.date() for l in lines)
        due, period_start = _next_due(merchant, oldest)
        if today < due:
            continue
        net = sum(l.net_pesewas for l in lines)
        if net <= 0:
            continue
        reason = hold_reason(merchant)
        batch = Settlement(
            settlement_id=f"STL-{merchant.merchant_id or merchant.id}-{today:%Y%m%d}-{uuid.uuid4().hex[:4]}",
            merchant_id=merchant.id,
            period_start=period_start,
            period_end=today,
            status=Settlement.ON_HOLD if reason else Settlement.PENDING_APPROVAL,
            hold_reason=reason,
        )
        db.session.add(batch)
        db.session.flush()
        for l in lines:
            l.settlement_id = batch.id
        _recalculate(batch)
        created.append(batch)
    db.session.commit()
    return created


def _mark_paid(batch):
    batch.status = Settlement.PAID
    batch.paid_at = datetime.utcnow()
    batch.failure_reason = None
    for line in SettlementLine.query.filter_by(settlement_id=batch.id).all():
        ledger.record(
            line.plan,
            LedgerEntry.MERCHANT_SETTLED,
            -(line.net_pesewas / 100),
            account=LedgerEntry.MERCHANT,
            reference=batch.transfer_reference,
            note=f"Settlement {batch.settlement_id}",
        )


def approve_and_pay(batch, admin):
    """Admin approval: start the Paystack transfer. Commits. Returns an outcome string."""
    from . import paystack

    if batch.status not in (
        Settlement.PENDING_APPROVAL,
        Settlement.FAILED,
        Settlement.ON_HOLD,
    ):
        return f"not_payable:{batch.status}"
    merchant = batch.merchant
    reason = hold_reason(merchant)
    if reason:
        batch.status = Settlement.ON_HOLD
        batch.hold_reason = reason
        db.session.commit()
        return "on_hold"
    if batch.net_pesewas <= 0:
        return "nothing_to_pay"
    if not paystack.is_configured():
        return "paystack_not_configured"

    try:
        if not merchant.paystack_recipient_code:
            account, name = payout_account(merchant)
            merchant.paystack_recipient_code = paystack.create_transfer_recipient(
                payout_method=merchant.payout_method,
                name=name,
                account_number=account,
                bank_code=merchant.payout_bank_code,
            )
        batch.transfer_reference = f"TBS-{batch.id}-{uuid.uuid4().hex[:10]}"
        batch.approved_by = admin.id
        batch.approved_at = datetime.utcnow()
        batch.hold_reason = None
        data = paystack.initiate_transfer(
            amount_pesewas=batch.net_pesewas,
            recipient_code=merchant.paystack_recipient_code,
            reference=batch.transfer_reference,
            reason=f"Tabital settlement {batch.settlement_id}",
        )
    except paystack.PaystackError as e:
        batch.status = Settlement.FAILED
        batch.failure_reason = str(e)[:255]
        db.session.commit()
        current_app.logger.warning(
            "Settlement %s transfer failed: %s", batch.settlement_id, e
        )
        return "failed"

    batch.transfer_code = data.get("transfer_code")
    status = data.get("status")
    if status == "success":
        _mark_paid(batch)
        outcome = "paid"
    elif status in ("failed", "reversed"):
        batch.status = Settlement.FAILED
        batch.failure_reason = data.get("reason") or status
        outcome = "failed"
    else:  # pending / otp / queued: Paystack finishes it and sends a webhook
        batch.status = Settlement.PROCESSING
        outcome = "processing"
    db.session.commit()
    return outcome


def handle_transfer_event(event, reference):
    """transfer.success / transfer.failed / transfer.reversed webhooks. Re-verified with Paystack.

    Commits. Returns an outcome string.
    """
    from . import paystack

    batch = (
        Settlement.query.filter_by(transfer_reference=reference)
        .with_for_update()
        .first()
    )
    if not batch:
        return "unknown_reference"
    data = paystack.verify_transfer(reference)
    status = data.get("status")
    if status == "success" and batch.status != Settlement.PAID:
        _mark_paid(batch)
        outcome = "paid"
    elif status in ("failed", "reversed"):
        if batch.status == Settlement.PAID:
            # Money came back: undo the settled entries so the merchant is owed again
            for line in SettlementLine.query.filter_by(settlement_id=batch.id).all():
                ledger.record(
                    line.plan,
                    LedgerEntry.MERCHANT_SETTLED,
                    line.net_pesewas / 100,
                    account=LedgerEntry.MERCHANT,
                    reference=reference,
                    note=f"Transfer {status}: settlement {batch.settlement_id}",
                )
        batch.status = Settlement.FAILED
        batch.failure_reason = data.get("reason") or f"Transfer {status}"
        outcome = status
    else:
        outcome = "no_change"
    db.session.commit()
    return outcome
