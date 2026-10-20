from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from app.models.user import User
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_

from app.services.payments import mark_instalment_paid


class AdminInstalmentStatsResource(Resource):
    @auth_required
    def get(self):
        """Get instalment statistics"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Date calculations
        today = datetime.now().date()
        last_30_days = today - timedelta(days=30)

        # Total Active Plans
        total_active_plans = InstalmentPlan.query.filter_by(status="active").count()

        # Total Completed Plans
        total_completed_plans = InstalmentPlan.query.filter_by(
            status="completed"
        ).count()

        # Total Defaulted Plans
        total_defaulted_plans = InstalmentPlan.query.filter_by(
            status="defaulted"
        ).count()

        # Total Outstanding Amount
        # Outstanding and paid come from the ledger
        from app.services import ledger

        portfolio = ledger.portfolio_totals()
        total_outstanding = portfolio["outstanding"]
        total_paid = portfolio["paid"]

        # Total Financed
        total_financed = (
            db.session.query(func.sum(InstalmentPlan.total_amount)).scalar() or 0
        )

        # Overdue Payments Count
        overdue_payments = InstalmentPayment.query.filter(
            InstalmentPayment.status == "overdue",
            InstalmentPayment.due_date < datetime.now(),
        ).count()

        # Upcoming Payments (next 7 days)
        upcoming_payments = InstalmentPayment.query.filter(
            InstalmentPayment.status == "pending",
            InstalmentPayment.due_date.between(
                datetime.now(), datetime.now() + timedelta(days=7)
            ),
        ).count()

        # Collection Rate
        collection_rate = (
            (total_paid / total_financed * 100) if total_financed > 0 else 0
        )

        return {
            "total_active_plans": total_active_plans,
            "total_completed_plans": total_completed_plans,
            "total_defaulted_plans": total_defaulted_plans,
            "total_outstanding": float(total_outstanding),
            "total_paid": float(total_paid),
            "total_financed": float(total_financed),
            "overdue_payments": overdue_payments,
            "upcoming_payments": upcoming_payments,
            "collection_rate": round(collection_rate, 1),
        }, 200


class AdminGetInstalmentPlansResource(Resource):
    @auth_required
    def get(self):
        """Get all instalment plans with filters and pagination"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        search = request.args.get("search", "", type=str)
        status = request.args.get("status", "", type=str)
        customer_id = request.args.get("customer_id", "", type=int)
        merchant_id = request.args.get("merchant_id", "", type=int)
        sort_by = request.args.get("sort_by", "created_at", type=str)
        sort_order = request.args.get("sort_order", "desc", type=str)

        # Build query
        query = InstalmentPlan.query

        # Apply search filter
        if search:
            query = query.join(User, InstalmentPlan.customer_id == User.id).filter(
                or_(
                    InstalmentPlan.plan_id.ilike(f"%{search}%"),
                    InstalmentPlan.plan_name.ilike(f"%{search}%"),
                    User.full_name.ilike(f"%{search}%"),
                    User.business_name.ilike(f"%{search}%"),
                )
            )

        # Apply status filter
        if status:
            query = query.filter(InstalmentPlan.status == status)

        # Apply customer filter
        if customer_id:
            query = query.filter(InstalmentPlan.customer_id == customer_id)

        # Apply merchant filter
        if merchant_id:
            query = query.filter(InstalmentPlan.merchant_id == merchant_id)

        # Apply sorting
        if sort_order == "desc":
            query = query.order_by(getattr(InstalmentPlan, sort_by).desc())
        else:
            query = query.order_by(getattr(InstalmentPlan, sort_by).asc())

        # Pagination
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        plans = []
        for plan in paginated.items:
            customer = User.query.get(plan.customer_id)
            merchant = User.query.get(plan.merchant_id)

            plans.append(
                {
                    "id": plan.id,
                    "plan_id": plan.plan_id,
                    "customer_name": customer.full_name if customer else "N/A",
                    "merchant_name": merchant.business_name if merchant else "N/A",
                    "plan_name": plan.plan_name,
                    "total_amount": float(plan.total_amount),
                    "down_payment": float(plan.down_payment),
                    "remaining_amount": plan.outstanding_balance,
                    "paid_amount": float(plan.paid_to_date),
                    "number_of_installments": plan.number_of_installments,
                    "installment_amount": float(plan.installment_amount),
                    "paid_installments": plan.paid_installments,
                    "missed_payments": plan.missed_payments,
                    "status": plan.status,
                    "start_date": plan.start_date.isoformat()
                    if plan.start_date
                    else None,
                    "end_date": plan.end_date.isoformat() if plan.end_date else None,
                    "created_at": plan.created_at.isoformat()
                    if plan.created_at
                    else None,
                }
            )

        return {
            "plans": plans,
            "total": paginated.total,
            "page": page,
            "per_page": per_page,
            "total_pages": paginated.pages if paginated.pages > 0 else 1,
        }, 200


class AdminGetInstalmentPlanDetailResource(Resource):
    @auth_required
    def get(self, plan_id):
        """Get detailed instalment plan information including payment schedule"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        plan = InstalmentPlan.query.get(plan_id)
        if not plan:
            return {"error": "Instalment plan not found"}, 404

        customer = User.query.get(plan.customer_id)
        merchant = User.query.get(plan.merchant_id)

        # Get payment schedule
        payments = (
            InstalmentPayment.query.filter_by(plan_id=plan.id)
            .order_by(InstalmentPayment.installment_number)
            .all()
        )

        payment_schedule = []
        for payment in payments:
            payment_schedule.append(
                {
                    "id": payment.id,
                    "payment_id": payment.payment_id,
                    "installment_number": payment.installment_number,
                    "due_date": payment.due_date.isoformat()
                    if payment.due_date
                    else None,
                    "paid_date": payment.paid_date.isoformat()
                    if payment.paid_date
                    else None,
                    "amount": float(payment.amount),
                    "paid_amount": float(payment.paid_amount),
                    "late_fee": float(payment.late_fee),
                    "late_fee_paid": payment.late_fee_paid,
                    "status": payment.status,
                    "payment_method": payment.payment_method,
                    "payment_reference": payment.payment_reference,
                }
            )

        from app.services import ledger
        from app.models.ledger import LedgerEntry

        entries = plan.ledger_entries.order_by(
            LedgerEntry.created_at, LedgerEntry.id
        ).all()

        return {
            "ledger": {
                "customer_balance": float(ledger.customer_balance(plan)),
                "entries": [
                    {
                        "id": e.id,
                        "account": e.account,
                        "entry_type": e.entry_type,
                        "amount": float(ledger.to_cedis(e.amount_pesewas)),
                        "payment_id": e.payment_id,
                        "reference": e.reference,
                        "note": e.note,
                        "created_by": e.created_by,
                        "created_at": e.created_at.isoformat()
                        if e.created_at
                        else None,
                    }
                    for e in entries
                ],
            },
            "plan": {
                "id": plan.id,
                "plan_id": plan.plan_id,
                "plan_name": plan.plan_name,
                "description": plan.description,
                "total_amount": float(plan.total_amount),
                "down_payment": float(plan.down_payment),
                "remaining_amount": plan.outstanding_balance,
                "paid_amount": float(plan.paid_to_date),
                "number_of_installments": plan.number_of_installments,
                "installment_amount": float(plan.installment_amount),
                "frequency": plan.frequency,
                "paid_installments": plan.paid_installments,
                "missed_payments": plan.missed_payments,
                "status": plan.status,
                "start_date": plan.start_date.isoformat() if plan.start_date else None,
                "end_date": plan.end_date.isoformat() if plan.end_date else None,
                "created_at": plan.created_at.isoformat() if plan.created_at else None,
                "completed_at": plan.completed_at.isoformat()
                if plan.completed_at
                else None,
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
                "email": merchant.business_email or merchant.email
                if merchant
                else "N/A",
            },
            "payment_schedule": payment_schedule,
        }, 200


class AdminUpdateInstalmentStatusResource(Resource):
    """Turned off (go-live review): A plan's status can't be set by hand."""

    @auth_required
    def put(self, *args, **kwargs):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        return {
            "error": "A plan's status can't be set by hand: it would write off debt or hide a default without a ledger entry. Plans complete when paid, and are charged off by the daily servicing job at 90 days past due."
        }, 410


class AdminApplyLateFeeResource(Resource):
    @auth_required
    def post(self, payment_id):
        """Manually apply late fee to a payment"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        payment = InstalmentPayment.query.get(payment_id)
        if not payment:
            return {"error": "Payment not found"}, 404

        if payment.status == "paid":
            return {"error": "Payment already paid"}, 400

        if payment.late_fee > 0:
            return {"error": "Late fee already applied"}, 400

        if not payment.apply_late_fee():
            return {"error": "Late fees apply from the day after the due date"}, 400

        return {
            "message": "Late fee applied successfully",
            "payment_id": payment.payment_id,
            "late_fee": payment.late_fee,
            "total_due": payment.get_total_due(),
        }, 200


class AdminWaiveLateFeeResource(Resource):
    @auth_required
    def post(self, payment_id):
        """Waive late fee for a payment"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        from app.services import payments

        data = request.get_json() or {}
        reason = (data.get("reason") or "").strip()

        payment = InstalmentPayment.query.get(payment_id)
        if not payment:
            return {"error": "Payment not found"}, 404
        if not reason:
            return {"error": "A reason is required to waive a late fee"}, 400

        # Only the part of the fee not already covered by part payments (services/payments.py)
        try:
            waived = payments.waive_late_fee(payment, reason, user=current_admin)
        except payments.PaymentError as e:
            db.session.rollback()
            return {"error": str(e)}, 400
        db.session.commit()

        return {
            "message": f"Late fee of {float(waived):.2f} waived. Reason: {reason}",
            "payment_id": payment.payment_id,
            "status": payment.status,
            "still_owed": payment.get_total_due(),
        }, 200


class AdminMarkPaymentAsPaidResource(Resource):
    @auth_required
    def put(self, payment_id):
        """Manually mark a payment as paid"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json() or {}
        payment_method = data.get("payment_method") or "manual"
        # Keep the customer's submitted reference unless the admin supplies the confirmed one
        payment_reference = (data.get("payment_reference") or "").strip()

        payment = InstalmentPayment.query.get(payment_id)
        if not payment:
            return {"error": "Payment not found"}, 404

        if payment.status == "paid":
            return {"error": "Payment already paid"}, 400

        from app.services import payments

        if payment.installment_number == 1 and payment.status == "pending_verification":
            # Verifying a down payment recorded without Paystack (its reference is already stored)
            payment_reference = payment_reference or payment.payment_reference
            if not payment_reference:
                return {
                    "error": "Enter the MoMo/bank reference of the money received"
                }, 400
            if (
                payment_reference != payment.payment_reference
                and payments.reference_in_use(payment_reference)
            ):
                return {"error": "That reference has already been recorded"}, 400
            mark_instalment_paid(
                payment, payment_method, payment_reference, user=current_admin
            )
        else:
            # Any other instalment: the same checks as collections (unique reference, part or full)
            try:
                payments.record_payment(
                    payment,
                    data.get("amount_received", payment.get_total_due()),
                    payment_method,
                    payment_reference,
                    user=current_admin,
                )
            except payments.PaymentError as e:
                db.session.rollback()
                return {"error": str(e)}, 400
        db.session.commit()

        return {
            "message": "Payment marked as paid",
            "payment_id": payment.payment_id,
            "status": "paid",
        }, 200


class AdminExportInstalmentsResource(Resource):
    @auth_required
    def get(self):
        """Export instalment plans to CSV"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        status = request.args.get("status", "", type=str)

        # Build query
        query = InstalmentPlan.query

        if status:
            query = query.filter(InstalmentPlan.status == status)

        plans = query.all()

        # Create CSV content
        import csv
        from io import StringIO
        from flask import make_response

        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(
            [
                "Plan ID",
                "Customer",
                "Merchant",
                "Plan Name",
                "Total Amount",
                "Remaining Amount",
                "Installments",
                "Installment Amount",
                "Paid Installments",
                "Missed Payments",
                "Status",
                "Start Date",
                "End Date",
                "Created At",
            ]
        )

        # Write data
        for plan in plans:
            customer = User.query.get(plan.customer_id)
            merchant = User.query.get(plan.merchant_id)

            writer.writerow(
                [
                    plan.plan_id,
                    customer.full_name if customer else "N/A",
                    merchant.business_name if merchant else "N/A",
                    plan.plan_name,
                    plan.total_amount,
                    plan.outstanding_balance,
                    plan.number_of_installments,
                    plan.installment_amount,
                    plan.paid_installments,
                    plan.missed_payments,
                    plan.status,
                    plan.start_date.strftime("%Y-%m-%d") if plan.start_date else "",
                    plan.end_date.strftime("%Y-%m-%d") if plan.end_date else "",
                    plan.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    if plan.created_at
                    else "",
                ]
            )

        output.seek(0)
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers[
            "Content-Disposition"
        ] = f'attachment; filename=instalments_{datetime.now().strftime("%Y%m%d")}.csv'
        return response
