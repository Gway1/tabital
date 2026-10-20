# resources/customer_dashboard.py
from ..docs import INT, NUM, STR, api_doc, obj
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user

from app.models.purchase_order import PurchaseOrder
from ..models.user import User
from ..models.transaction import Transaction
from ..models.instalment import InstalmentPlan
from ..models.instalment_payment import InstalmentPayment
from ..extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, and_
from ..extensions import *
from flask_mail import Message


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


def _credit_summary(customer):
    from ..services import risk

    decision, _ = risk.decide(customer)
    return risk.decision_view(decision)


_SCHEDULE_ROW = obj(
    {
        "id": INT,
        "installment_number": INT,
        "instalment_number": {
            **INT,
            "description": "Older spelling of installment_number",
        },
        "due_date": STR,
        "original_due_date": {
            **STR,
            "nullable": True,
            "description": "Set when the payment was deferred",
        },
        "amount": NUM,
        "late_fee": {**NUM, "description": "Unpaid late fee"},
        "amount_due": {
            **NUM,
            "description": "Instalment + unpaid late fee - part payments received",
        },
        "part_paid": NUM,
        "part_payments": {"type": "array", "items": obj({"amount": NUM, "at": STR})},
        "status": {"type": "string", "description": "pending, overdue, paid, ..."},
        "paid_date": STR,
        "payment_reference": STR,
    }
)
_PLAN_FIELDS = {
    "id": INT,
    "plan_id": STR,
    "transaction_id": STR,
    "product_name": STR,
    "product_description": STR,
    "merchant_name": STR,
    "merchant_phone": STR,
    "total_amount": NUM,
    "amount_paid": NUM,
    "amount_outstanding": NUM,
    "instalment_term": INT,
    "instalment_frequency": STR,
    "instalment_amount": NUM,
    "status": STR,
    "created_at": STR,
    "payment_schedule": {"type": "array", "items": _SCHEDULE_ROW},
}
_PLAN_SUMMARY = obj(
    {
        **_PLAN_FIELDS,
        "next_payment_date": STR,
        "next_payment_amount": NUM,
        "due_date": STR,
        "completed_at": STR,
    }
)
_PLAN_DETAIL = obj(
    {
        **_PLAN_FIELDS,
        "down_payment": NUM,
        "start_date": STR,
        "end_date": STR,
        "payment_status": STR,
        "paid_installments": INT,
    }
)


class CustomerDashboardStatsResource(Resource):
    @auth_required
    def get(self):
        """Get customer dashboard statistics"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Get all active instalment plans for this customer
        active_plans = InstalmentPlan.query.filter(
            InstalmentPlan.customer_id == current_customer.id,
            InstalmentPlan.status == "active",
        ).all()

        # Calculate total outstanding
        total_outstanding = sum(p.outstanding_balance for p in active_plans)
        total_outstanding_plans_count = len(active_plans)

        # Get next payment
        next_payment = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.customer_id == current_customer.id,
                InstalmentPayment.status == "pending",
                InstalmentPayment.due_date >= datetime.now(),
            )
            .order_by(InstalmentPayment.due_date.asc())
            .first()
        )

        next_payment_amount = (
            next_payment.get_total_due() if next_payment else 0
        )  # what's still owed
        next_payment_date = next_payment.due_date.isoformat() if next_payment else ""
        next_payment_plan_name = next_payment.plan.plan_name if next_payment else ""

        # Calculate paid to date (this year)
        year_start = datetime.now().replace(month=1, day=1, hour=0, minute=0, second=0)
        paid_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.customer_id == current_customer.id,
                InstalmentPayment.status == "paid",
                InstalmentPayment.paid_date >= year_start,
            )
            .all()
        )

        paid_to_date = _received(
            current_customer.id, year_start
        )  # ledger: includes part payments

        # Account status
        account_status = "good_standing"
        account_status_message = "Good Standing"

        # Check for overdue payments
        overdue_count = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.customer_id == current_customer.id,
                InstalmentPayment.status == "overdue",
                InstalmentPayment.due_date < datetime.now(),
            )
            .count()
        )

        if overdue_count > 0:
            account_status = "overdue"
            account_status_message = f"Overdue ({overdue_count} payments)"

        return {
            "total_outstanding": safe_float(total_outstanding),
            # Spending limit from underwriting (computed now, not stored)
            "credit": _credit_summary(current_customer),
            "total_outstanding_plans_count": safe_int(total_outstanding_plans_count),
            "next_payment_amount": safe_float(next_payment_amount),
            "next_payment_date": next_payment_date,
            "next_payment_plan_name": safe_str(next_payment_plan_name),
            "paid_to_date": safe_float(paid_to_date),
            "paid_to_date_period": str(datetime.now().year),
            "active_plans_count": safe_int(len(active_plans)),
            "account_status": account_status,
            "account_status_message": account_status_message,
            "customer_name": safe_str(
                current_customer.full_name or current_customer.business_name
            ),
            "customer_id": safe_str(current_customer.customer_id),
            "email": safe_str(
                current_customer.business_email or current_customer.email
            ),
            "phone": safe_str(current_customer.phone),
        }


class CustomerPaymentOverviewResource(Resource):
    @auth_required
    def get(self):
        """Get payment overview chart data for the last 12 months"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        monthly_data = []
        total_paid = 0
        total_outstanding = 0

        # Get last 12 months from current date
        today = datetime.now()

        for i in range(11, -1, -1):
            # Calculate the target month
            target_month = today.month - i
            target_year = today.year

            if target_month <= 0:
                target_month += 12
                target_year -= 1

            month_start = datetime(target_year, target_month, 1, 0, 0, 0)

            # Calculate next month
            if target_month == 12:
                next_month = datetime(target_year + 1, 1, 1, 0, 0, 0)
            else:
                next_month = datetime(target_year, target_month + 1, 1, 0, 0, 0)

            # Get paid payments for this month
            paid_payments = (
                InstalmentPayment.query.join(InstalmentPlan)
                .filter(
                    InstalmentPlan.customer_id == current_customer.id,
                    InstalmentPayment.status == "paid",
                    InstalmentPayment.paid_date >= month_start,
                    InstalmentPayment.paid_date < next_month,
                )
                .all()
            )

            paid_amount = _received(current_customer.id, month_start, next_month)

            # Get pending payments due this month
            pending_payments = (
                InstalmentPayment.query.join(InstalmentPlan)
                .filter(
                    InstalmentPlan.customer_id == current_customer.id,
                    InstalmentPayment.status == "pending",
                    InstalmentPayment.due_date >= month_start,
                    InstalmentPayment.due_date < next_month,
                )
                .all()
            )

            pending_amount = sum(p.get_total_due() for p in pending_payments)

            # Only add months that have data or are recent months
            # Use consistent month format without year for chart
            monthly_data.append(
                {
                    "month": month_start.strftime("%b"),
                    "paid": float(paid_amount),
                    "outstanding": float(pending_amount),
                }
            )

            total_paid += paid_amount
            total_outstanding += pending_amount

        return {
            "monthly_data": monthly_data,
            "total_paid": float(total_paid),
            "total_outstanding": float(total_outstanding),
        }, 200


class CustomerUpcomingPaymentsResource(Resource):
    @auth_required
    def get(self):
        """Get upcoming payments for customer"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        limit = request.args.get("limit", 5, type=int)

        upcoming_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.customer_id == current_customer.id,
                InstalmentPayment.status.in_(["pending", "overdue"]),
                InstalmentPayment.due_date >= datetime.now(),
            )
            .order_by(InstalmentPayment.due_date.asc())
            .limit(limit)
            .all()
        )

        result = []
        for payment in upcoming_payments:
            plan = payment.plan
            result.append(
                {
                    "id": plan.id,
                    "plan_id": safe_str(plan.plan_id),
                    "transaction_id": safe_str(plan.transaction_id),
                    "product_name": safe_str(plan.plan_name),
                    "product_description": safe_str(plan.description),
                    "merchant_name": safe_str(
                        plan.merchant.business_name or plan.merchant.full_name
                    ),
                    "merchant_phone": safe_str(plan.merchant.phone),
                    "total_amount": safe_float(plan.total_amount),
                    "amount_paid": safe_float(plan.paid_to_date),
                    "amount_outstanding": safe_float(plan.outstanding_balance),
                    "instalment_term": safe_int(plan.number_of_installments),
                    "instalment_frequency": safe_str(plan.frequency),
                    "instalment_amount": safe_float(plan.installment_amount),
                    "next_payment_date": payment.due_date.isoformat()
                    if payment.due_date
                    else "",
                    "next_payment_amount": safe_float(payment.get_total_due()),
                    "due_date": payment.due_date.isoformat()
                    if payment.due_date
                    else "",
                    "status": safe_str(plan.status),
                    "created_at": plan.created_at.isoformat()
                    if plan.created_at
                    else "",
                }
            )

        return result


class CustomerRecentTransactionsResource(Resource):
    @auth_required
    def get(self):
        """Get recent transactions for customer"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        limit = request.args.get("limit", 5, type=int)

        # Get completed instalment payments
        payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.customer_id == current_customer.id,
                InstalmentPayment.status == "paid",
            )
            .order_by(InstalmentPayment.paid_date.desc())
            .limit(limit)
            .all()
        )

        result = []
        for payment in payments:
            plan = payment.plan
            result.append(
                {
                    "id": payment.id,
                    "transaction_id": safe_str(payment.payment_id),
                    "merchant_name": safe_str(
                        plan.merchant.business_name or plan.merchant.full_name
                    ),
                    "product_name": safe_str(plan.plan_name),
                    "product_description": safe_str(plan.description),
                    "amount": safe_float(payment.paid_amount or payment.amount),
                    "payment_method": safe_str(payment.payment_method),
                    "payment_plan": "Instalment",
                    "status": "completed",
                    "transaction_date": payment.paid_date.isoformat()
                    if payment.paid_date
                    else "",
                    "delivery_status": "completed",
                }
            )

        return result


class CustomerInstalmentsResource(Resource):
    @api_doc(
        "The customer's instalment plans, paginated",
        query={
            "status": (STR, "Plan status, or `all` (default)"),
            "limit": (INT, "Page size, default 50"),
            "page": (INT, "Page number, default 1"),
        },
        responses={
            200: (
                "Plans, newest first, each with its payment schedule",
                obj(
                    {
                        "plans": {"type": "array", "items": _PLAN_SUMMARY},
                        "total": INT,
                        "page": INT,
                        "limit": INT,
                    }
                ),
            ),
            403: "Not a customer",
        },
    )
    @auth_required
    def get(self):
        """Get customer's instalment plans"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        status = request.args.get("status", "all")
        limit = request.args.get("limit", 50, type=int)
        page = request.args.get("page", 1, type=int)

        query = InstalmentPlan.query.filter(
            InstalmentPlan.customer_id == current_customer.id
        )

        if status != "all":
            query = query.filter(InstalmentPlan.status == status)

        total = query.count()
        plans = (
            query.order_by(InstalmentPlan.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        result = []
        for plan in plans:
            # Get next payment
            next_payment = (
                InstalmentPayment.query.filter(
                    InstalmentPayment.plan_id == plan.id,
                    InstalmentPayment.status.in_(["pending", "overdue"]),
                )
                .order_by(InstalmentPayment.due_date.asc())
                .first()
            )

            result.append(
                {
                    "id": plan.id,
                    "payment_schedule": _schedule(plan),
                    "plan_id": safe_str(plan.plan_id),
                    "transaction_id": safe_str(plan.transaction_id),
                    "product_name": safe_str(plan.plan_name),
                    "product_description": safe_str(plan.description),
                    "merchant_name": safe_str(
                        plan.merchant.business_name or plan.merchant.full_name
                    ),
                    "merchant_phone": safe_str(plan.merchant.phone),
                    "total_amount": safe_float(plan.total_amount),
                    "amount_paid": safe_float(plan.paid_to_date),
                    "amount_outstanding": safe_float(plan.outstanding_balance),
                    "instalment_term": safe_int(plan.number_of_installments),
                    "instalment_frequency": safe_str(plan.frequency),
                    "instalment_amount": safe_float(plan.installment_amount),
                    "next_payment_date": next_payment.due_date.isoformat()
                    if next_payment
                    else "",
                    "next_payment_amount": safe_float(next_payment.get_total_due())
                    if next_payment
                    else 0,
                    "due_date": plan.end_date.isoformat() if plan.end_date else "",
                    "status": safe_str(plan.status),
                    "created_at": plan.created_at.isoformat()
                    if plan.created_at
                    else "",
                    "completed_at": plan.completed_at.isoformat()
                    if plan.completed_at
                    else "",
                }
            )

        return {"plans": result, "total": total, "page": page, "limit": limit}


def _received(customer_id, start=None, end=None):
    """Money the customer actually paid (ledger PAYMENT_RECEIVED, part payments included), in GHS."""
    from ..models.ledger import LedgerEntry
    from ..services import ledger

    q = (
        db.session.query(db.func.coalesce(db.func.sum(LedgerEntry.amount_pesewas), 0))
        .join(InstalmentPlan, InstalmentPlan.id == LedgerEntry.plan_id)
        .filter(
            InstalmentPlan.customer_id == customer_id,
            LedgerEntry.entry_type == LedgerEntry.PAYMENT_RECEIVED,
        )
    )
    if start is not None:
        q = q.filter(LedgerEntry.created_at >= start)
    if end is not None:
        q = q.filter(LedgerEntry.created_at < end)
    return float(-ledger.to_cedis(q.scalar()))


def _schedule(plan, payments=None):
    """A plan's payments for the customer, including any unpaid late fee and the total now due."""
    if payments is None:
        payments = (
            InstalmentPayment.query.filter_by(plan_id=plan.id)
            .order_by(InstalmentPayment.installment_number)
            .all()
        )
    rows = []
    for p in payments:
        unpaid_fee = (
            safe_float(p.late_fee)
            if p.late_fee and not p.late_fee_paid and p.status != "paid"
            else 0.0
        )
        part_paid = float(p.part_paid()) if p.status != "paid" else 0.0
        rows.append(
            {
                "id": p.id,
                "installment_number": p.installment_number,
                "instalment_number": p.installment_number,  # older spelling, kept for existing screens
                "due_date": p.due_date.isoformat() if p.due_date else "",
                "original_due_date": p.original_due_date.isoformat()
                if p.original_due_date
                else None,
                "amount": safe_float(p.amount),
                "late_fee": unpaid_fee,
                # What's still owed now (instalment + unpaid late fee - part payments already received)
                "amount_due": safe_float(p.get_total_due())
                if p.status != "paid"
                else 0.0,
                "part_paid": part_paid,
                "part_payments": [
                    {"amount": pp.amount_pesewas / 100, "at": pp.created_at.isoformat()}
                    for pp in p.part_payments()
                ]
                if part_paid
                else [],
                "status": safe_str(p.status),
                "paid_date": p.paid_date.isoformat() if p.paid_date else "",
                "payment_reference": safe_str(p.payment_reference),
            }
        )
    return rows


class CustomerPlanDetailsResource(Resource):
    @api_doc(
        "One instalment plan with its payment schedule",
        responses={
            200: ("The plan", _PLAN_DETAIL),
            403: "Not a customer",
            404: "Plan not found",
        },
    )
    @auth_required
    def get(self, plan_id):
        """Get detailed instalment plan information"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        plan = InstalmentPlan.query.filter_by(
            id=plan_id, customer_id=current_customer.id
        ).first()

        if not plan:
            return {"error": "Plan not found"}, 404

        # Get payment schedule
        payments = (
            InstalmentPayment.query.filter_by(plan_id=plan.id)
            .order_by(InstalmentPayment.installment_number)
            .all()
        )

        payment_schedule = _schedule(plan, payments)

        return {
            "id": plan.id,
            "plan_id": safe_str(plan.plan_id),
            "transaction_id": safe_str(plan.transaction_id),
            "product_name": safe_str(plan.plan_name),
            "product_description": safe_str(plan.description),
            "merchant_name": safe_str(
                plan.merchant.business_name or plan.merchant.full_name
            ),
            "merchant_phone": safe_str(plan.merchant.phone),
            "total_amount": safe_float(plan.total_amount),
            "amount_paid": safe_float(plan.paid_to_date),
            "amount_outstanding": safe_float(plan.outstanding_balance),
            "instalment_term": safe_int(plan.number_of_installments),
            "instalment_frequency": safe_str(plan.frequency),
            "instalment_amount": safe_float(plan.installment_amount),
            "down_payment": safe_float(plan.down_payment),
            "start_date": plan.start_date.isoformat() if plan.start_date else "",
            "end_date": plan.end_date.isoformat() if plan.end_date else "",
            "status": safe_str(plan.status),
            "payment_status": safe_str(plan.payment_status),
            "paid_installments": safe_int(plan.paid_installments),
            "created_at": plan.created_at.isoformat() if plan.created_at else "",
            "payment_schedule": payment_schedule,
        }
