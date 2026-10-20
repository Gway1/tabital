"""Payment reminders (Phase 4, §8.5 step 1: automated reminders).

Schedule, relative to an instalment's due date (all configurable in `reminder_schedule`):
  -3, -1, 0   before and on the due date
  +1          late fee charged; pay within the 7-day grace window to keep the limit
  +6          last day tomorrow to keep the limit
  +31         second late fee
Messages go to SMS and in-app by default (`reminder_channels`). Paused plans (open dispute)
and instalments awaiting verification get nothing.
"""
from datetime import datetime, timedelta

from ..extensions import db
from ..models.message_outbox import MessageOutbox

DEFAULT_SCHEDULE = [
    {"offset": -3, "template": "due_in_3"},
    {"offset": -1, "template": "due_tomorrow"},
    {"offset": 0, "template": "due_today"},
    {"offset": 1, "template": "late_fee_charged"},
    {"offset": 6, "template": "grace_ending"},
    {"offset": 31, "template": "second_late_fee"},
]
DEFAULT_CHANNELS = ["sms", "in_app"]


def _ghs(amount):
    return f"GHS {float(amount):,.2f}"


def render(template, payment, plan):
    """(title, body) for a reminder. Amounts come from the stored instalment."""
    from .risk import current_rules

    cure_days = int(current_rules().get("late_payment_cure_days", 7))
    due = payment.due_date.strftime("%d %b %Y")
    amount = _ghs(payment.get_total_due())
    item = plan.plan_name
    n = payment.installment_number
    texts = {
        "due_in_3": (
            "Payment due in 3 days",
            f"Tabital Pay: your payment {n} of {amount} for {item} is due on {due}. "
            f"Pay in the app with MoMo or card.",
        ),
        "due_tomorrow": (
            "Payment due tomorrow",
            f"Tabital Pay: reminder, {amount} for {item} is due tomorrow ({due}).",
        ),
        "due_today": (
            "Payment due today",
            f"Tabital Pay: {amount} for {item} is due today. Pay now to avoid a late fee.",
        ),
        "late_fee_charged": (
            "Payment overdue",
            f"Tabital Pay: your payment for {item} was due {due}. A late fee has been added; "
            f"{amount} is now due. Pay within {cure_days} days of the due date to keep your spending limit.",
        ),
        "grace_ending": (
            "Last day tomorrow to keep your limit",
            f"Tabital Pay: pay {amount} for {item} by tomorrow to keep your spending limit.",
        ),
        "second_late_fee": (
            "Payment 31 days overdue",
            f"Tabital Pay: your payment for {item} is 31 days overdue and a further late fee "
            f"has been added. {amount} is due. Please pay or contact us today.",
        ),
    }
    return texts[template]


def queue_due_reminders(today=None):
    """Queue every reminder due today. Returns how many new messages were queued. Commits."""
    from ..models.instalment import InstalmentPlan
    from ..models.instalment_payment import InstalmentPayment
    from ..models.system_settings import SystemSetting
    from .sms import to_e164

    today = today or datetime.utcnow().date()
    schedule = SystemSetting.get_value("reminder_schedule", None) or DEFAULT_SCHEDULE
    channels = SystemSetting.get_value("reminder_channels", None) or DEFAULT_CHANNELS
    by_offset = {int(s["offset"]): s["template"] for s in schedule}

    # Only instalments whose due date is at one of the schedule offsets from today
    candidate_dates = [today - timedelta(days=o) for o in by_offset]
    start = datetime.combine(min(candidate_dates), datetime.min.time())
    end = datetime.combine(max(candidate_dates), datetime.max.time())
    payments = (
        InstalmentPayment.query.join(InstalmentPlan)
        .filter(
            InstalmentPlan.status == "active",
            InstalmentPlan.paused_at.is_(None),
            InstalmentPayment.status.in_(("pending", "overdue")),
            InstalmentPayment.due_date >= start,
            InstalmentPayment.due_date <= end,
        )
        .all()
    )

    queued = 0
    for payment in payments:
        offset = (today - payment.due_date.date()).days
        template = by_offset.get(offset)
        if not template:
            continue
        if (
            template in ("late_fee_charged", "second_late_fee")
            and not payment.late_fee_applied_date
        ):
            continue
        plan = payment.plan
        customer = plan.customer
        title, body = render(template, payment, plan)
        for channel in channels:
            key = f"{template}:{payment.id}:{channel}"
            if payment.original_due_date is not None:
                # Deferred (§4): the reminders run again for the new due date
                key = f"{template}:{payment.id}:{payment.due_date:%Y%m%d}:{channel}"
            if MessageOutbox.query.filter_by(dedupe_key=key).first():
                continue
            to = to_e164(customer.phone) if channel in ("sms", "whatsapp") else None
            db.session.add(
                MessageOutbox(
                    user_id=customer.id,
                    channel=channel,
                    to_address=to,
                    template=template,
                    title=title,
                    body=body,
                    dedupe_key=key,
                    plan_id=plan.id,
                    payment_id=payment.id,
                    status=MessageOutbox.PENDING
                    if (to or channel == "in_app")
                    else MessageOutbox.FAILED,
                    last_error=None
                    if (to or channel == "in_app")
                    else "No valid phone number",
                )
            )
            queued += 1
    db.session.commit()
    return queued


def dispatch_pending(limit=500):
    """Send pending messages. Failures are recorded and retried up to 3 times. Commits."""
    from ..models.notifications import Notifications
    from . import sms

    sender = None
    sent = 0
    rows = (
        MessageOutbox.query.filter(
            MessageOutbox.status == MessageOutbox.PENDING, MessageOutbox.attempts < 3
        )
        .limit(limit)
        .all()
    )
    for msg in rows:
        msg.attempts = (msg.attempts or 0) + 1
        try:
            if msg.channel == "in_app":
                n = Notifications(
                    notification_id=Notifications.generate_notification_id(),
                    user_id=msg.user_id,
                    user_role="customer",
                    title=msg.title or "Tabital Pay",
                    message=msg.body,
                    type="reminder",
                    link="/customer/instalments",
                    action_text="Pay now",
                )
                db.session.add(n)
                msg.provider = "in_app"
                ok, error, message_id = True, None, n.notification_id
            else:
                sender = sender or sms.provider()
                result = sender.send(msg.to_address, msg.body)
                msg.provider = result.provider
                ok, error, message_id = result.ok, result.error, result.message_id
        except Exception as e:  # a provider failure must not stop the other messages
            ok, error, message_id = False, str(e)[:255], None
        if ok:
            msg.status = MessageOutbox.SENT
            msg.sent_at = datetime.utcnow()
            msg.provider_message_id = message_id
            sent += 1
        else:
            msg.last_error = (error or "send failed")[:255]
            if msg.attempts >= 3:
                msg.status = MessageOutbox.FAILED
    db.session.commit()
    return sent
