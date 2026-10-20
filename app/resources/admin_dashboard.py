from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from app.models.user import User
from app.models.transaction import Transaction
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.document import Document
from app.extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, and_


class AdminDashboardStatsResource(Resource):
    @auth_required
    def get(self):
        """Get admin dashboard statistics"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Date calculations
        today = datetime.now().date()
        last_30_days = today - timedelta(days=30)
        previous_30_days = last_30_days - timedelta(days=30)

        # Total Financed (All Time) - Using customer_id instead of user_id
        total_financed = (
            db.session.query(func.sum(InstalmentPlan.total_amount)).scalar() or 0
        )

        # Total Financed (Last 30 days)
        total_financed_last_30 = (
            db.session.query(func.sum(InstalmentPlan.total_amount))
            .filter(InstalmentPlan.created_at >= last_30_days)
            .scalar()
            or 0
        )

        # Previous 30 days for growth calculation
        total_financed_previous_30 = (
            db.session.query(func.sum(InstalmentPlan.total_amount))
            .filter(InstalmentPlan.created_at.between(previous_30_days, last_30_days))
            .scalar()
            or 0
        )

        financed_growth = 0
        if total_financed_previous_30 > 0:
            financed_growth = (
                (total_financed_last_30 - total_financed_previous_30)
                / total_financed_previous_30
            ) * 100

        # Active Customers (customers with at least one plan) - FIXED: use customer_id
        active_customers = (
            db.session.query(func.count(func.distinct(InstalmentPlan.customer_id)))
            .filter(InstalmentPlan.status.in_(["active", "completed"]))
            .scalar()
            or 0
        )

        active_customers_last_30 = (
            db.session.query(func.count(func.distinct(InstalmentPlan.customer_id)))
            .filter(InstalmentPlan.created_at >= last_30_days)
            .scalar()
            or 0
        )

        active_customers_previous_30 = (
            db.session.query(func.count(func.distinct(InstalmentPlan.customer_id)))
            .filter(InstalmentPlan.created_at.between(previous_30_days, last_30_days))
            .scalar()
            or 0
        )

        customers_growth = 0
        if active_customers_previous_30 > 0:
            customers_growth = (
                (active_customers_last_30 - active_customers_previous_30)
                / active_customers_previous_30
            ) * 100

        # Active Merchants (merchants with approved status)
        active_merchants = User.query.filter(
            User.role == "merchant", User.status == "approved"
        ).count()

        active_merchants_last_30 = User.query.filter(
            User.role == "merchant",
            User.status == "approved",
            User.created_at >= last_30_days,
        ).count()

        active_merchants_previous_30 = User.query.filter(
            User.role == "merchant",
            User.status == "approved",
            User.created_at.between(previous_30_days, last_30_days),
        ).count()

        merchants_growth = 0
        if active_merchants_previous_30 > 0:
            merchants_growth = (
                (active_merchants_last_30 - active_merchants_previous_30)
                / active_merchants_previous_30
            ) * 100

        # Repayment Rate (percentage of completed plans)
        total_plans = InstalmentPlan.query.count()
        completed_plans = InstalmentPlan.query.filter_by(status="completed").count()
        repayment_rate = (completed_plans / total_plans * 100) if total_plans > 0 else 0

        repayment_rate_last_30 = 0
        completed_last_30 = InstalmentPlan.query.filter(
            InstalmentPlan.status == "completed",
            InstalmentPlan.completed_at >= last_30_days,
        ).count()
        total_last_30 = InstalmentPlan.query.filter(
            InstalmentPlan.created_at >= last_30_days
        ).count()
        if total_last_30 > 0:
            repayment_rate_last_30 = (completed_last_30 / total_last_30) * 100

        # Default Rate
        defaulted_plans = InstalmentPlan.query.filter_by(status="defaulted").count()
        default_rate = (defaulted_plans / total_plans * 100) if total_plans > 0 else 0

        from app.services import ledger
        from app.models.purchase_order import PurchaseOrder

        # Revenue = MDR recognised in the ledger when plans are opened (§6.1)
        now = datetime.now()
        current_month_start = datetime(now.year, now.month, 1)
        last_month_start = (
            datetime(now.year - 1, 12, 1)
            if now.month == 1
            else datetime(now.year, now.month - 1, 1)
        )
        revenue_mtd = ledger.merchant_fees_between(current_month_start)
        revenue_last_month = ledger.merchant_fees_between(
            last_month_start, current_month_start
        )

        revenue_growth = 0
        if revenue_last_month > 0:
            revenue_growth = (
                (revenue_mtd - revenue_last_month) / revenue_last_month
            ) * 100

        # Portfolio at risk: unpaid overdue instalments by days past due (§8.4 buckets)
        total_exposure = ledger.portfolio_totals(InstalmentPlan.status == "active")[
            "outstanding"
        ]
        start_of_today = datetime(now.year, now.month, now.day)

        def overdue_between(min_days, max_days=None):
            # What's still owed on those instalments (unpaid late fees, minus part payments)
            q = InstalmentPayment.query.filter(
                InstalmentPayment.status == "overdue",
                InstalmentPayment.due_date <= start_of_today - timedelta(days=min_days),
            )
            if max_days is not None:
                q = q.filter(
                    InstalmentPayment.due_date
                    > start_of_today - timedelta(days=max_days + 1)
                )
            return float(sum(p.get_total_due() for p in q.all()))

        early_risk = overdue_between(1, 30)  # DPD 1-30: early delinquency
        late_risk = overdue_between(31, 60)  # DPD 31-60: high risk
        default_risk = overdue_between(61)  # DPD 61+: default watch / charge-off

        # Alerts: only counts the system actually tracks. Features that don't exist yet report 0.
        alerts = {
            "high_risk_transactions": 0,
            "failed_payments": 0,
            "overdue_installments": InstalmentPayment.query.filter(
                InstalmentPayment.status == "overdue"
            ).count(),
            "payments_awaiting_verification": InstalmentPayment.query.filter(
                InstalmentPayment.status == "pending_verification"
            ).count(),
            "chargebacks": 0,
            "system_notifications": 0,
        }

        # Pending Approvals
        pending_approvals = {
            "kyc_verifications": Document.query.filter_by(status="pending").count(),
            "merchant_onboarding": User.query.filter_by(
                role="merchant", status="pending"
            ).count(),
            "transaction_approvals": PurchaseOrder.query.filter_by(
                status="pending"
            ).count(),
            "refund_requests": 0,
            "limit_increase_requests": 0,
        }

        # Installment Status - Using InstalmentPayment for more accurate stats
        # A payment is late if a late fee was ever applied to it (even if later waived)
        paid_late = InstalmentPayment.query.filter(
            InstalmentPayment.status == "paid",
            InstalmentPayment.late_fee_applied_date.isnot(None),
        ).count()
        paid_on_time = (
            InstalmentPayment.query.filter_by(status="paid").count() - paid_late
        )
        upcoming = (
            InstalmentPayment.query.filter_by(status="pending")
            .filter(InstalmentPayment.due_date >= datetime.now())
            .count()
        )
        overdue = InstalmentPayment.query.filter_by(status="overdue").count()

        total_payments = paid_on_time + paid_late + upcoming + overdue

        # Top Merchants by GMV
        top_merchants = (
            db.session.query(
                User.business_name, func.sum(InstalmentPlan.total_amount).label("gmv")
            )
            .join(InstalmentPlan, User.id == InstalmentPlan.merchant_id)
            .filter(User.role == "merchant")
            .group_by(User.id)
            .order_by(func.sum(InstalmentPlan.total_amount).desc())
            .limit(5)
            .all()
        )

        merchants_list = [
            {"name": m[0] or "Unknown", "gmv": float(m[1])} for m in top_merchants
        ]

        # Recent Transactions - Fixed to use customer relationship
        recent_transactions = (
            db.session.query(
                InstalmentPlan.plan_id.label("txn_id"),
                User.full_name.label("customer"),
                User.business_name.label("merchant"),
                InstalmentPlan.total_amount.label("amount"),
                InstalmentPlan.number_of_installments.label("plan"),
                InstalmentPlan.status.label("status"),
                InstalmentPlan.created_at.label("time"),
            )
            .join(User, InstalmentPlan.customer_id == User.id)
            .order_by(InstalmentPlan.created_at.desc())
            .limit(5)
            .all()
        )

        transactions = []
        for t in recent_transactions:
            status_display = (
                "Completed"
                if t.status == "completed"
                else "Approved"
                if t.status == "active"
                else "Pending"
                if t.status == "pending"
                else "Failed"
            )
            transactions.append(
                {
                    "txn_id": t.txn_id,
                    "customer": t.customer or "N/A",
                    "merchant": t.merchant or "N/A",
                    "amount": float(t.amount),
                    "plan": f"Pay in {t.plan}",
                    "status": status_display,
                    "time": t.time.strftime("%d %b, %I:%M %p") if t.time else "",
                }
            )

        return {
            "stats": {
                "total_financed": float(total_financed),
                "total_financed_growth": round(financed_growth, 1),
                "active_customers": active_customers,
                "active_customers_growth": round(customers_growth, 1),
                "active_merchants": active_merchants,
                "active_merchants_growth": round(merchants_growth, 1),
                "repayment_rate": round(repayment_rate, 1),
                "repayment_rate_growth": round(
                    repayment_rate_last_30 - repayment_rate, 1
                ),
                "default_rate": round(default_rate, 1),
                "revenue_mtd": float(revenue_mtd),
                "revenue_growth": round(revenue_growth, 1),
            },
            "portfolio": {
                "total_exposure": float(total_exposure),
                "early_risk": float(early_risk),
                "early_percentage": round(
                    (early_risk / total_exposure * 100) if total_exposure > 0 else 0, 1
                ),
                "late_risk": float(late_risk),
                "late_percentage": round(
                    (late_risk / total_exposure * 100) if total_exposure > 0 else 0, 1
                ),
                "default_risk": float(default_risk),
                "default_percentage": round(
                    (default_risk / total_exposure * 100) if total_exposure > 0 else 0,
                    1,
                ),
            },
            "alerts": alerts,
            "pending_approvals": pending_approvals,
            "installment_status": {
                "paid_on_time": paid_on_time,
                "paid_on_time_percentage": round(
                    (paid_on_time / total_payments * 100) if total_payments > 0 else 0,
                    1,
                ),
                "paid_late": paid_late,
                "paid_late_percentage": round(
                    (paid_late / total_payments * 100) if total_payments > 0 else 0, 1
                ),
                "upcoming": upcoming,
                "upcoming_percentage": round(
                    (upcoming / total_payments * 100) if total_payments > 0 else 0, 1
                ),
                "overdue": overdue,
                "overdue_percentage": round(
                    (overdue / total_payments * 100) if total_payments > 0 else 0, 1
                ),
            },
            "top_merchants": merchants_list,
            "recent_transactions": transactions,
        }, 200


class AdminRecentTransactionsResource(Resource):
    @auth_required
    def get(self):
        """Get recent transactions for admin dashboard"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        limit = request.args.get("limit", 10, type=int)

        recent_transactions = (
            db.session.query(
                InstalmentPlan.plan_id.label("txn_id"),
                User.full_name.label("customer"),
                User.business_name.label("merchant"),
                InstalmentPlan.total_amount.label("amount"),
                InstalmentPlan.number_of_installments.label("plan"),
                InstalmentPlan.status.label("status"),
                InstalmentPlan.created_at.label("time"),
            )
            .join(User, InstalmentPlan.customer_id == User.id)
            .order_by(InstalmentPlan.created_at.desc())
            .limit(limit)
            .all()
        )

        transactions = []
        for t in recent_transactions:
            status_display = (
                "Completed"
                if t.status == "completed"
                else "Approved"
                if t.status == "active"
                else "Pending"
                if t.status == "pending"
                else "Failed"
            )
            transactions.append(
                {
                    "txn_id": t.txn_id,
                    "customer": t.customer or "N/A",
                    "merchant": t.merchant or "N/A",
                    "amount": float(t.amount),
                    "plan": f"Pay in {t.plan}",
                    "status": status_display,
                    "time": t.time.strftime("%d %b, %I:%M %p") if t.time else "",
                }
            )

        return {"transactions": transactions}, 200
