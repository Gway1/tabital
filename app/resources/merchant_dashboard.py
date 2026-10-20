from flask_restful import Resource, request
from ..services import merchant_fees
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class MerchantDashboardStatsResource(Resource):
    @auth_required
    def get(self):
        """Get merchant dashboard statistics"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get today's date range
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = datetime.now().replace(
            hour=23, minute=59, second=59, microsecond=999999
        )

        # Yesterday's date range for comparison
        yesterday_start = (datetime.now() - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        yesterday_end = (datetime.now() - timedelta(days=1)).replace(
            hour=23, minute=59, second=59, microsecond=999999
        )

        # Today's transactions
        today_transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.transaction_date >= today_start,
            Transaction.transaction_date <= today_end,
        ).all()

        # Yesterday's transactions
        yesterday_transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.transaction_date >= yesterday_start,
            Transaction.transaction_date <= yesterday_end,
        ).all()

        # Calculate today's stats
        today_sales = sum(
            t.amount for t in today_transactions if t.status == "completed"
        )
        today_transactions_count = len(
            [t for t in today_transactions if t.status == "completed"]
        )

        # Calculate yesterday's stats for comparison
        yesterday_sales = sum(
            t.amount for t in yesterday_transactions if t.status == "completed"
        )
        yesterday_transactions_count = len(
            [t for t in yesterday_transactions if t.status == "completed"]
        )

        # Calculate growth percentages
        sales_growth = (
            ((today_sales - yesterday_sales) / yesterday_sales * 100)
            if yesterday_sales > 0
            else 0
        )
        transactions_growth = (
            (
                (today_transactions_count - yesterday_transactions_count)
                / yesterday_transactions_count
                * 100
            )
            if yesterday_transactions_count > 0
            else 0
        )

        # Get unique customers (distinct customer_id)
        unique_customers = set()
        for t in today_transactions:
            if t.customer_id:
                unique_customers.add(t.customer_id)
        new_customers = len(unique_customers)

        # Instalment sales (transactions with payment_plan)
        instalment_sales = sum(
            t.amount
            for t in today_transactions
            if t.payment_plan
            and t.payment_plan != "Standard"
            and t.status == "completed"
        )

        # Successful rate
        successful = len([t for t in today_transactions if t.status == "completed"])
        failed = len(
            [t for t in today_transactions if t.status in ["cancelled", "failed"]]
        )
        total = successful + failed
        success_rate = (successful / total * 100) if total > 0 else 100

        return {
            "today_sales": safe_float(today_sales),
            "today_transactions": safe_int(today_transactions_count),
            "new_customers": safe_int(new_customers),
            "instalment_sales": safe_float(instalment_sales),
            "success_rate": safe_float(success_rate),
            "sales_growth": safe_float(sales_growth),
            "transactions_growth": safe_float(transactions_growth),
            "customers_growth": safe_float(0),
            "instalment_growth": safe_float(0),
            "success_rate_growth": safe_float(0),
        }


class MerchantSalesChartResource(Resource):
    @auth_required
    def get(self):
        """Get sales chart data for the last 7 days"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        chart_data = []

        for i in range(6, -1, -1):
            date = datetime.now() - timedelta(days=i)
            date_start = date.replace(hour=0, minute=0, second=0, microsecond=0)
            date_end = date.replace(hour=23, minute=59, second=59, microsecond=999999)

            daily_transactions = Transaction.query.filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.status == "completed",
                Transaction.transaction_date >= date_start,
                Transaction.transaction_date <= date_end,
            ).all()

            daily_total = sum(t.amount for t in daily_transactions)

            chart_data.append(
                {
                    "name": date.strftime("%d %b"),
                    "value": safe_float(daily_total),
                    "full_date": date.strftime("%Y-%m-%d"),
                }
            )

        return chart_data


class MerchantRecentTransactionsResource(Resource):
    @auth_required
    def get(self):
        """Get recent transactions for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        limit = request.args.get("limit", 10, type=int)

        transactions = (
            Transaction.query.filter_by(merchant_id=current_merchant.id)
            .order_by(Transaction.created_at.desc())
            .limit(limit)
            .all()
        )

        return [
            {
                "id": t.id,
                "transaction_id": safe_str(t.transaction_id),
                "customer_name": safe_str(
                    t.customer.full_name or t.customer.business_name or t.customer.phone
                ),
                "amount": safe_float(t.amount),
                "payment_plan": safe_str(t.payment_plan),
                "product_name": safe_str(t.product_name),
                "date": t.transaction_date.isoformat() if t.transaction_date else "",
                "status": safe_str(t.status),
                "payment_method": safe_str(t.payment_method),
                "type": "Sale" if not t.payment_plan else "Installment Plan",
            }
            for t in transactions
        ]


class MerchantInstalmentsOverviewResource(Resource):
    @auth_required
    def get(self):
        """Get instalments overview for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        instalments = Transaction.query.filter(
            Transaction.merchant_id
            == current_merchant.id
            # Transaction.payment_plan.isnot(None),
            # Transaction.payment_plan != 'Standard'
        ).all()

        today = datetime.now()

        active_plans = len([i for i in instalments if i.status == "pending"])
        paid = len(
            [
                i
                for i in instalments
                if i.status == "completed" and i.payment_status == "completed"
            ]
        )
        overdue = len(
            [
                i
                for i in instalments
                if i.status == "pending"
                and i.completion_date
                and i.completion_date < today
            ]
        )
        completed = len([i for i in instalments if i.status == "completed"])

        total_active_amount = sum(
            i.amount for i in instalments if i.status == "pending"
        )

        return {
            "active_plans": safe_int(active_plans),
            "paid": safe_int(paid),
            "overdue": safe_int(overdue),
            "completed": safe_int(completed),
            "total_active_amount": safe_float(total_active_amount),
        }


class MerchantSettlementInfoResource(Resource):
    @auth_required
    def get(self):
        """Get settlement information for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get pending transactions for next settlement
        pending_transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
            Transaction.payment_status == "completed",
        ).all()

        # Get next settlement date (assuming 3 days from now)
        next_settlement_date = datetime.now() + timedelta(days=3)

        # Calculate estimated amount from pending payouts
        estimated_amount = current_merchant.pending_payout or sum(
            t.amount for t in pending_transactions
        )

        return {
            "next_settlement": next_settlement_date.strftime("%d %b %Y"),
            "estimated_amount": safe_float(estimated_amount),
            "pending_payout": safe_float(current_merchant.pending_payout or 0),
            "last_settlement": "20 May 2024",  # You can calculate this from settlement history
        }


class MerchantAccountStatusResource(Resource):
    @auth_required
    def get(self):
        """Get merchant account status"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Mask bank account number
        bank_account = current_merchant.account_number or ""
        masked_account = (
            f"*******{bank_account[-4:]}" if len(bank_account) >= 4 else "Not set"
        )

        return {
            "kyc_verified": current_merchant.kyc_status == "verified",
            "kyc_status": safe_str(current_merchant.kyc_status),
            "payout_account": masked_account,
            "plan": safe_str(current_merchant.payment_plan or "Standard"),
            "member_since": current_merchant.created_at.strftime("%d %b %Y")
            if current_merchant.created_at
            else "N/A",
            "business_name": safe_str(current_merchant.business_name),
            "business_email": safe_str(current_merchant.business_email),
            "business_phone": safe_str(current_merchant.business_phone),
            "verified": current_merchant.verified,
            "commission_rate": merchant_fees.describe(current_merchant)[
                "fee_percentage"
            ],  # current tier (§6.1)
        }


class MerchantQuickActionsResource(Resource):
    @auth_required
    def post(self):
        """Handle quick actions"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        action = data.get("action")

        if action == "create_payment_link":
            # Logic to create payment link
            return {
                "message": "Payment link created",
                "link": "https://pay.tabital.com/link123",
            }, 200

        elif action == "create_instalment_plan":
            # Logic to create instalment plan
            return {"message": "Instalment plan created"}, 200

        elif action == "download_reports":
            # Logic to download reports
            return {"message": "Reports are being generated"}, 200

        elif action == "contact_support":
            # Logic to contact support
            return {"message": "Support ticket created"}, 200

        return {"error": "Invalid action"}, 400
