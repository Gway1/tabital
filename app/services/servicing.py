"""Daily loan servicing (Phase 4): late fees, delinquency buckets, collections stages.

Run once a day (`flask run-daily`, e.g. a Render cron job at 06:00 Africa/Accra). It's
idempotent: running it twice on the same day doesn't charge anything twice.
"""
from dataclasses import dataclass, field
from datetime import datetime

from ..extensions import db

UNPAID_STATUSES = (
    "pending",
    "overdue",
)  # pending_verification = customer says paid; admin checking


def dpd_bucket(dpd: int) -> str:
    """§8.4 delinquency buckets."""
    if dpd <= 0:
        return "current"
    if dpd <= 30:
        return "dpd_1_30"
    if dpd <= 60:
        return "dpd_31_60"
    if dpd <= 90:
        return "dpd_61_90"
    return "dpd_90_plus"


def collection_stage(dpd: int, order_total, high_ticket_threshold) -> str:
    """§8.5 recovery ladder. Legal recovery only for high-ticket orders (§13.1 D6)."""
    if dpd <= 0:
        return None
    if dpd <= 30:
        return "reminders"
    if dpd <= 60:
        return "call_centre"
    if dpd <= 90:
        return "employer_contact"
    if order_total is not None and float(order_total) >= float(high_ticket_threshold):
        return "legal_recovery"
    return "bureau_reporting"


@dataclass
class DailyRunSummary:
    date: str
    plans_checked: int = 0
    first_fees: int = 0
    second_fees: int = 0
    newly_overdue_plans: int = 0
    charged_off: int = 0
    paused_skipped: int = 0
    buckets: dict = field(default_factory=dict)
    reminders_queued: int = 0
    autopay_attempts: int = 0
    settlements_created: int = 0

    def to_dict(self):
        return dict(self.__dict__)


def run_daily(today=None, send_reminders=True, run_autopay=True):
    """Service every active plan for `today` (a date). Commits. Returns DailyRunSummary."""
    from ..models.instalment import InstalmentPlan
    from ..models.instalment_payment import InstalmentPayment
    from ..models.system_settings import SystemSetting

    today = today or datetime.utcnow().date()
    summary = DailyRunSummary(date=today.isoformat())
    high_ticket = SystemSetting.get_value("high_ticket_threshold", 10000)
    charge_off_after = int(SystemSetting.get_value("charge_off_after_days", 90))

    plans = InstalmentPlan.query.filter(InstalmentPlan.status == "active").all()
    for plan in plans:
        summary.plans_checked += 1
        if plan.paused_at is not None:
            summary.paused_skipped += 1
            continue

        unpaid = (
            InstalmentPayment.query.filter(
                InstalmentPayment.plan_id == plan.id,
                InstalmentPayment.status.in_(UNPAID_STATUSES),
            )
            .order_by(InstalmentPayment.installment_number)
            .all()
        )

        worst = 0
        for p in unpaid:
            if not p.due_date or not today > p.due_date.date():
                continue
            if p.apply_late_fee(today=today, commit=False):
                summary.first_fees += 1
            if p.apply_second_late_fee(today=today, commit=False):
                summary.second_fees += 1
            worst = max(worst, (today - p.due_date.date()).days)

        was_current = (plan.days_past_due or 0) == 0
        plan.days_past_due = worst
        plan.dpd_bucket = dpd_bucket(worst)
        plan.collection_stage = collection_stage(worst, plan.total_amount, high_ticket)
        plan.missed_payments = len(
            [p for p in unpaid if p.due_date and today > p.due_date.date()]
        )
        if worst > 0 and was_current:
            summary.newly_overdue_plans += 1

        # §8.4: 90+ DPD is charge-off. Collections continue; the plan leaves the active book.
        if worst > charge_off_after and plan.charged_off_at is None:
            plan.status = "defaulted"
            plan.charged_off_at = datetime.combine(today, datetime.min.time())
            summary.charged_off += 1

        summary.buckets[plan.dpd_bucket] = summary.buckets.get(plan.dpd_bucket, 0) + 1

    db.session.commit()

    if send_reminders:
        from . import reminders

        summary.reminders_queued = reminders.queue_due_reminders(today)
        reminders.dispatch_pending()
    if run_autopay:
        from . import autopay

        summary.autopay_attempts = autopay.run(today)

    # Merchant settlement batches for billing cycles that ended today (Phase 5). Payouts still
    # need an admin's approval before any money moves.
    from . import settlements

    summary.settlements_created = len(settlements.generate_batches(today))

    return summary
