from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from app.models.user import User
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_

# §8.4 buckets: (key, label, min days past due, max days past due or None)
BUCKETS = [
    ("dpd_1_30", "1-30", 1, 30),
    ("dpd_31_60", "31-60", 31, 60),
    ("dpd_61_90", "61-90", 61, 90),
    ("dpd_90_plus", "90+", 91, None),
]


def _overdue_query(min_days, max_days, today):
    """Unpaid overdue instalments whose days past due fall in [min_days, max_days]."""
    start_of_today = datetime(today.year, today.month, today.day)
    q = InstalmentPayment.query.filter(
        InstalmentPayment.status == "overdue",
        InstalmentPayment.due_date <= start_of_today - timedelta(days=min_days),
    )
    if max_days is not None:
        q = q.filter(
            InstalmentPayment.due_date > start_of_today - timedelta(days=max_days + 1)
        )
    return q


class AdminCollectionStatsResource(Resource):
    @auth_required
    def get(self):
        """Collection statistics by §8.4 delinquency bucket (real figures only)."""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        today = datetime.now().date()
        overdue = InstalmentPayment.query.filter(
            InstalmentPayment.status == "overdue"
        ).all()
        total_overdue = sum(
            p.get_total_due() for p in overdue
        )  # still owed, after part payments
        accounts_overdue = len({p.plan.customer_id for p in overdue if p.plan})

        body = {
            "total_overdue": float(total_overdue),
            "accounts_overdue": accounts_overdue,
        }
        for key, label, lo, hi in BUCKETS:
            rows = _overdue_query(lo, hi, today).all()
            body[f"overdue_{key}"] = float(sum(p.get_total_due() for p in rows))
            body[f"count_{key}"] = len(rows)
        body["buckets"] = [
            {"key": k, "label": f"{label} days"} for k, label, _, _ in BUCKETS
        ]
        return body, 200


class AdminGetOverduePaymentsResource(Resource):
    @auth_required
    def get(self):
        """Get all overdue payments with filters"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        search = request.args.get("search", "", type=str)
        overdue_range = request.args.get(
            "overdue_range", "", type=str
        )  # 1-30, 31-60, 61-90, 90+ (§8.4)
        status = request.args.get("status", "", type=str)

        # Build query for overdue payments
        today = datetime.now()
        query = InstalmentPayment.query.filter(
            InstalmentPayment.status == "overdue", InstalmentPayment.due_date < today
        )

        # Apply overdue range filter
        if overdue_range:
            for _key, label, lo, hi in BUCKETS:
                if overdue_range == label:
                    start_of_today = datetime(today.year, today.month, today.day)
                    query = query.filter(
                        InstalmentPayment.due_date
                        <= start_of_today - timedelta(days=lo)
                    )
                    if hi is not None:
                        query = query.filter(
                            InstalmentPayment.due_date
                            > start_of_today - timedelta(days=hi + 1)
                        )

        # Apply search filter
        if search:
            query = (
                query.join(InstalmentPlan)
                .join(User, InstalmentPlan.customer_id == User.id)
                .filter(
                    or_(
                        User.full_name.ilike(f"%{search}%"),
                        User.phone.ilike(f"%{search}%"),
                        InstalmentPlan.plan_id.ilike(f"%{search}%"),
                    )
                )
            )

        # Apply status filter
        if status:
            query = query.filter(InstalmentPayment.status == status)

        # Order by due date
        query = query.order_by(InstalmentPayment.due_date.asc())

        # Pagination
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        overdue_payments = []
        for payment in paginated.items:
            plan = InstalmentPlan.query.get(payment.plan_id)
            customer = User.query.get(plan.customer_id) if plan else None
            merchant = User.query.get(plan.merchant_id) if plan else None

            days_overdue = (today.date() - payment.due_date.date()).days
            overdue_range_display = next(
                (
                    f"{label} Days"
                    for _k, label, lo, hi in BUCKETS
                    if days_overdue >= lo and (hi is None or days_overdue <= hi)
                ),
                "Current",
            )

            overdue_payments.append(
                {
                    "id": payment.id,
                    "payment_id": payment.payment_id,
                    "plan_id": plan.plan_id if plan else "N/A",
                    "customer_id": customer.id if customer else None,
                    "customer_name": customer.full_name if customer else "N/A",
                    "customer_phone": customer.phone if customer else "N/A",
                    "customer_email": customer.business_email or customer.email
                    if customer
                    else "N/A",
                    "merchant_name": merchant.business_name if merchant else "N/A",
                    "installment_number": payment.installment_number,
                    "amount": float(payment.amount),
                    "late_fee": float(payment.late_fee),
                    "total_due": payment.get_total_due(),  # still owed, after part payments
                    "part_paid": float(payment.part_paid()),
                    "due_date": payment.due_date.isoformat()
                    if payment.due_date
                    else None,
                    "days_overdue": days_overdue,
                    "overdue_range": overdue_range_display,
                    "status": payment.status,
                    "payment_method": payment.payment_method,
                    "payment_reference": payment.payment_reference,
                    "collection_stage": get_collection_stage(days_overdue, plan),
                }
            )

        return {
            "overdue_payments": overdue_payments,
            "total": paginated.total,
            "page": page,
            "per_page": per_page,
            "total_pages": paginated.pages if paginated.pages > 0 else 1,
        }, 200


STAGE_LABELS = {
    "reminders": "Automated reminders",
    "call_centre": "Collections call centre",
    "employer_contact": "Employer-assisted contact",
    "bureau_reporting": "Credit bureau reporting",
    "legal_recovery": "Legal recovery (high-ticket)",
}


def get_collection_stage(days_overdue, plan=None):
    """§8.5 recovery ladder for this many days past due."""
    from app.models.system_settings import SystemSetting
    from app.services.servicing import collection_stage

    stage = collection_stage(
        days_overdue,
        plan.total_amount if plan else None,
        SystemSetting.get_value("high_ticket_threshold", 10000),
    )
    return STAGE_LABELS.get(stage, "Current")


class AdminGetOverduePaymentDetailResource(Resource):
    @auth_required
    def get(self, payment_id):
        """Get detailed overdue payment information"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        payment = InstalmentPayment.query.get(payment_id)
        if not payment:
            return {"error": "Payment not found"}, 404

        plan = InstalmentPlan.query.get(payment.plan_id)
        customer = User.query.get(plan.customer_id) if plan else None
        merchant = User.query.get(plan.merchant_id) if plan else None

        today = datetime.now()
        days_overdue = (
            (today.date() - payment.due_date.date()).days if payment.due_date else 0
        )

        # Get all payments for this plan
        all_payments = (
            InstalmentPayment.query.filter_by(plan_id=plan.id)
            .order_by(InstalmentPayment.installment_number)
            .all()
            if plan
            else []
        )

        payment_schedule = []
        for p in all_payments:
            payment_schedule.append(
                {
                    "id": p.id,
                    "installment_number": p.installment_number,
                    "due_date": p.due_date.isoformat() if p.due_date else None,
                    "amount": float(p.amount),
                    "status": p.status,
                    "paid_date": p.paid_date.isoformat() if p.paid_date else None,
                    "late_fee": float(p.late_fee),
                    "part_paid": float(p.part_paid()),
                }
            )

        # Timeline of what actually happened: late fees charged and messages actually sent
        from app.models.message_outbox import MessageOutbox

        collection_timeline = []
        if payment.late_fee_applied_date:
            collection_timeline.append(
                {
                    "stage": "Late fee charged",
                    "completed": True,
                    "completed_date": payment.late_fee_applied_date.isoformat(),
                    "actions": ["First late fee"],
                }
            )
        if payment.second_late_fee_applied_date:
            collection_timeline.append(
                {
                    "stage": "Second late fee charged",
                    "completed": True,
                    "completed_date": payment.second_late_fee_applied_date.isoformat(),
                    "actions": ["Additional late fee (31+ days past due)"],
                }
            )
        for m in (
            MessageOutbox.query.filter_by(payment_id=payment.id)
            .order_by(MessageOutbox.created_at)
            .all()
        ):
            collection_timeline.append(
                {
                    "stage": f"Reminder: {m.template.replace('_', ' ')}",
                    "completed": m.status == MessageOutbox.SENT,
                    "completed_date": (m.sent_at or m.created_at).isoformat(),
                    "actions": [f"{m.channel.upper()} {m.status}"],
                }
            )
        collection_timeline.sort(key=lambda x: x["completed_date"] or "")
        current_stage = get_collection_stage(days_overdue, plan)

        return {
            "payment": {
                "id": payment.id,
                "payment_id": payment.payment_id,
                "installment_number": payment.installment_number,
                "amount": float(payment.amount),
                "late_fee": float(payment.late_fee),
                "total_due": payment.get_total_due(),  # still owed, after part payments
                "part_paid": float(payment.part_paid()),
                "part_payments": [p.to_dict() for p in payment.part_payments()],
                "due_date": payment.due_date.isoformat() if payment.due_date else None,
                "days_overdue": days_overdue,
                "status": payment.status,
                "payment_method": payment.payment_method,
                "payment_reference": payment.payment_reference,
            },
            "plan": {
                "id": plan.id,
                "plan_id": plan.plan_id,
                "plan_name": plan.plan_name,
                "total_amount": float(plan.total_amount),
                "remaining_amount": plan.outstanding_balance,
                "number_of_installments": plan.number_of_installments,
                "paid_installments": plan.paid_installments,
            },
            "customer": {
                "id": customer.id if customer else None,
                "name": customer.full_name if customer else "N/A",
                "phone": customer.phone if customer else "N/A",
                "email": customer.business_email or customer.email
                if customer
                else "N/A",
            },
            "merchant": {
                "id": merchant.id if merchant else None,
                "name": merchant.business_name if merchant else "N/A",
                "phone": merchant.phone if merchant else "N/A",
            },
            "payment_schedule": payment_schedule,
            "collection_timeline": collection_timeline,
            "collection_stage": current_stage,
        }, 200


class AdminSendPaymentReminderResource(Resource):
    @auth_required
    def post(self, payment_id):
        """Send payment reminder to customer"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        from app.extensions import db as _db
        from app.models.message_outbox import MessageOutbox
        from app.services import reminders
        from app.services.sms import to_e164

        data = request.get_json() or {}
        channel = data.get("reminder_type", "sms")
        # WhatsApp isn't connected yet (it would silently go out as SMS)
        if channel not in ("sms", "in_app"):
            return {"error": "reminder_type must be sms or in_app"}, 400

        payment = InstalmentPayment.query.get(payment_id)
        if not payment:
            return {"error": "Payment not found"}, 404
        if payment.status == "paid":
            return {"error": "This instalment is already paid"}, 400

        plan = InstalmentPlan.query.get(payment.plan_id)
        customer = User.query.get(plan.customer_id) if plan else None
        if not customer:
            return {"error": "Customer not found"}, 404

        template = "late_fee_charged" if payment.late_fee_applied_date else "due_today"
        title, body = reminders.render(template, payment, plan)
        to = to_e164(customer.phone) if channel != "in_app" else None
        if channel != "in_app" and not to:
            return {"error": "The customer has no valid phone number"}, 400
        msg = MessageOutbox(
            user_id=customer.id,
            channel=channel,
            to_address=to,
            template="manual_reminder",
            title=title,
            body=body,
            plan_id=plan.id,
            payment_id=payment.id,
            dedupe_key=f"manual:{payment.id}:{channel}:{datetime.utcnow().timestamp()}",
        )
        _db.session.add(msg)
        _db.session.commit()
        reminders.dispatch_pending()

        return {
            "message": f"Reminder {msg.status} via {channel}"
            + (f" ({msg.provider})" if msg.provider else ""),
            "payment_id": payment.payment_id,
            "reminder_type": channel,
            "status": msg.status,
            "provider": msg.provider,
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
        }, 200


class AdminMarkPaymentReceivedResource(Resource):
    @auth_required
    def put(self, payment_id):
        """Mark overdue payment as received"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        from app.services import payments

        data = request.get_json() or {}
        payment_method = (data.get("payment_method") or "manual").strip()[:50]
        if payment_method not in (
            "mobile_money",
            "bank_transfer",
            "cash",
            "card",
            "manual",
        ):
            return {"error": "Unknown payment method"}, 400

        payment = InstalmentPayment.query.get(payment_id)
        if not payment:
            return {"error": "Payment not found"}, 404

        # Less than what's owed is a part payment (services/payments.record_payment)
        try:
            outcome = payments.record_payment(
                payment,
                data.get("amount_received"),
                payment_method,
                data.get("payment_reference"),
                user=current_admin,
            )
        except payments.PaymentError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        db.session.commit()

        still_owed = payment.get_total_due()
        return {
            "message": "Payment recorded"
            if outcome == "paid"
            else f"Part payment recorded. {still_owed:.2f} is still owed on this instalment.",
            "payment_id": payment.payment_id,
            "status": payment.status,
            "outcome": outcome,
            "still_owed": still_owed,
            "part_payments": [p.to_dict() for p in payment.part_payments()],
        }, 200


class AdminSetPaymentPlanResource(Resource):
    """Turned off. It moved an overdue instalment's due date with no fee, record or ledger entry,
    which hides delinquency (CLAUDE.md §8.4, §12). Customers can defer one instalment for the 10%
    fee (deferments.py). An admin hardship arrangement needs a founder policy first."""

    @auth_required
    def post(self, payment_id):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        return {
            "error": "Changing a due date from Collections is turned off. The customer can defer one "
            "instalment (10% fee) from their app; other arrangements need a founder-approved policy."
        }, 410


class AdminExportOverduePaymentsResource(Resource):
    @auth_required
    def get(self):
        """Export overdue payments to CSV"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get overdue payments
        today = datetime.now()
        overdue_payments = InstalmentPayment.query.filter(
            InstalmentPayment.status == "overdue", InstalmentPayment.due_date < today
        ).all()

        # Create CSV content
        import csv
        from io import StringIO
        from flask import make_response

        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(
            [
                "Payment ID",
                "Customer",
                "Phone",
                "Merchant",
                "Plan ID",
                "Installment",
                "Amount",
                "Late Fee",
                "Total Due",
                "Due Date",
                "Days Overdue",
                "Status",
            ]
        )

        # Write data
        for payment in overdue_payments:
            plan = InstalmentPlan.query.get(payment.plan_id)
            customer = User.query.get(plan.customer_id) if plan else None
            merchant = User.query.get(plan.merchant_id) if plan else None
            days_overdue = (today - payment.due_date).days if payment.due_date else 0

            writer.writerow(
                [
                    payment.payment_id,
                    customer.full_name if customer else "N/A",
                    customer.phone if customer else "N/A",
                    merchant.business_name if merchant else "N/A",
                    plan.plan_id if plan else "N/A",
                    payment.installment_number,
                    payment.amount,
                    payment.late_fee,
                    payment.get_total_due(),
                    payment.due_date.strftime("%Y-%m-%d") if payment.due_date else "",
                    days_overdue,
                    payment.status,
                ]
            )

        output.seek(0)
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers[
            "Content-Disposition"
        ] = f'attachment; filename=overdue_payments_{datetime.now().strftime("%Y%m%d")}.csv'
        return response
