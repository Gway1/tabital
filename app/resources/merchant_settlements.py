from flask_restful import Resource, request
from ..services import merchant_fees
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class MerchantGetSettlementsResource(Resource):
    @auth_required
    def get(self):
        """Get settlement history for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        status = request.args.get("status", "").strip()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        limit = request.args.get("limit", 50, type=int)
        page = request.args.get("page", 1, type=int)

        # Base query - get all completed transactions
        query = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
            Transaction.payment_status == "completed",
        )

        if start_date:
            query = query.filter(Transaction.completion_date >= start_date)
        if end_date:
            query = query.filter(Transaction.completion_date <= end_date)

        # Group by settlement periods (you can customize this logic)
        # For now, we'll group by date and calculate settlements

        total = query.count()
        transactions = (
            query.order_by(Transaction.completion_date.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        # Group transactions by settlement batches
        settlements = {}
        for t in transactions:
            # Group by week (you can change this to daily, monthly, etc.)
            if t.completion_date:
                # Monday-start week, the same one start_date/end_date below show and the
                # detail endpoint (/merchant/settlements/<period_id>) rebuilds
                week_key = t.completion_date.strftime("%Y-%W")  # Year-Week
                if week_key not in settlements:
                    settlements[week_key] = {
                        "period_id": week_key,
                        "period": f"Week of {t.completion_date.strftime('%d %b %Y')}",
                        "start_date": (
                            t.completion_date
                            - timedelta(days=t.completion_date.weekday())
                        ).strftime("%Y-%m-%d"),
                        "end_date": (
                            t.completion_date
                            + timedelta(days=6 - t.completion_date.weekday())
                        ).strftime("%Y-%m-%d"),
                        "transactions": [],
                        "total_amount": 0,
                        "commission": 0,
                        "net_amount": 0,
                        "status": "pending",
                    }

                commission = merchant_fees.transaction_split(t, current_merchant)[
                    0
                ]  # fee fixed on the sale
                net = t.amount - commission

                settlements[week_key]["transactions"].append(
                    {
                        "id": t.id,
                        "transaction_id": t.transaction_id,
                        "amount": t.amount,
                        "commission": commission,
                        "net": net,
                        "date": t.completion_date.isoformat()
                        if t.completion_date
                        else "",
                        "customer_name": safe_str(
                            t.customer.full_name or t.customer.business_name
                        ),
                    }
                )
                settlements[week_key]["total_amount"] += t.amount
                settlements[week_key]["commission"] += commission
                settlements[week_key]["net_amount"] += net

        # Convert to list and add status based on date
        result = []
        for key, settlement in settlements.items():
            # Determine status (you can add logic for paid/unpaid)
            settlement_date = datetime.strptime(settlement["end_date"], "%Y-%m-%d")
            if settlement_date + timedelta(days=7) < datetime.utcnow():
                settlement["status"] = "paid"
            elif settlement_date < datetime.utcnow():
                settlement["status"] = "processing"
            else:
                settlement["status"] = "pending"

            result.append(settlement)

        # Paginate results
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_result = result[start_idx:end_idx]

        return {
            "settlements": paginated_result,
            "total": len(result),
            "page": page,
            "limit": limit,
            "total_pages": (len(result) + limit - 1) // limit,
        }


class MerchantGetSettlementSummaryResource(Resource):
    @auth_required
    def get(self):
        """Get settlement summary for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get pending settlements (transactions not yet settled)
        pending_transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
            Transaction.payment_status == "completed",
        ).all()

        # Calculate pending amount
        pending_amount = sum(t.amount for t in pending_transactions)
        pending_commission = sum(
            merchant_fees.transaction_split(t, current_merchant)[0]
            for t in pending_transactions
        )
        pending_net = pending_amount - pending_commission

        # Get last settlement
        last_settlement_date = None
        # You can track last settlement in a separate table or calculate from completed settlements
        # For now, we'll assume settlements are processed weekly

        # Get monthly breakdown
        monthly_breakdown = []
        for i in range(6):
            month_date = datetime.utcnow() - timedelta(days=30 * i)
            month_start = month_date.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            if i == 0:
                month_end = datetime.utcnow()
            else:
                next_month = month_start + timedelta(days=32)
                month_end = next_month.replace(day=1) - timedelta(days=1)

            monthly_transactions = Transaction.query.filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.status == "completed",
                Transaction.payment_status == "completed",
                Transaction.completion_date >= month_start,
                Transaction.completion_date <= month_end,
            ).all()

            total = sum(t.amount for t in monthly_transactions)
            commission = sum(
                merchant_fees.transaction_split(t, current_merchant)[0]
                for t in monthly_transactions
            )
            net = total - commission

            monthly_breakdown.append(
                {
                    "month": month_date.strftime("%B %Y"),
                    "total": total,
                    "commission": commission,
                    "net": net,
                    "transaction_count": len(monthly_transactions),
                }
            )

        return {
            "pending_amount": pending_amount,
            "pending_commission": pending_commission,
            "pending_net": pending_net,
            "pending_transactions": len(pending_transactions),
            "commission_rate": merchant_fees.describe(current_merchant)[
                "fee_percentage"
            ],  # current tier (§6.1)
            "last_settlement": last_settlement_date,
            "next_settlement_estimate": (
                datetime.utcnow() + timedelta(days=7)
            ).strftime("%d %b %Y"),
            "monthly_breakdown": monthly_breakdown,
            "bank_name": safe_str(current_merchant.bank_name),
            "account_name": safe_str(current_merchant.account_name),
            "account_number": safe_str(current_merchant.account_number),
        }


class MerchantRequestPayoutResource(Resource):
    @auth_required
    def post(self):
        """Request a payout for pending settlements"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Retired in Phase 5: this used to report success without paying anything. Payouts now
        # run automatically on the merchant's settlement cycle (see /merchant/settlement-batches).
        return {
            "error": "Payouts are automatic now. Delivered sales are paid on your "
            "settlement cycle (3, 7 or 30 days)."
        }, 410


class MerchantUpdateSettlementSettingsResource(Resource):
    @auth_required
    def put(self):
        """Update settlement settings for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        from .merchant_payouts import PayoutError, apply_payout_details

        data = request.get_json() or {}
        payout_data = {
            k: data[k]
            for k in ("bank_name", "account_name", "account_number")
            if k in data
        }
        try:
            apply_payout_details(current_merchant, payout_data)
        except PayoutError as e:
            return {"error": str(e)}, e.status

        db.session.commit()

        return {"message": "Settlement settings updated successfully"}, 200


class MerchantGetSettlementDetailsResource(Resource):
    @auth_required
    def get(self, settlement_id):
        """Get detailed information for a specific settlement"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Parse settlement period (format: YYYY-WW, Monday-start weeks, as listed by /merchant/settlements)
        try:
            year, week = settlement_id.split("-")
            start_date = datetime.strptime(f"{year}-W{int(week)}-1", "%Y-W%W-%w")
        except ValueError:
            return {"error": "Settlement id must look like 2026-42 (year-week)"}, 400
        end_date = start_date + timedelta(days=6)

        # Get transactions in this period
        transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
            Transaction.payment_status == "completed",
            Transaction.completion_date >= start_date,
            # end_date is midnight at the start of Sunday, so the whole of Sunday needs the next Monday
            Transaction.completion_date < start_date + timedelta(days=7),
        ).all()

        total_amount = sum(t.amount for t in transactions)
        commission = sum(
            merchant_fees.transaction_split(t, current_merchant)[0]
            for t in transactions
        )
        net_amount = total_amount - commission

        return {
            "period_id": settlement_id,
            "period": f"Week of {start_date.strftime('%d %b %Y')}",
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "total_amount": total_amount,
            "commission_rate": merchant_fees.describe(current_merchant)[
                "fee_percentage"
            ],  # current tier (§6.1)
            "commission": commission,
            "net_amount": net_amount,
            "transaction_count": len(transactions),
            "transactions": [
                {
                    "id": t.id,
                    "transaction_id": t.transaction_id,
                    "amount": t.amount,
                    "customer_name": safe_str(
                        t.customer.full_name or t.customer.business_name
                    ),
                    "date": t.completion_date.isoformat() if t.completion_date else "",
                    "product_name": safe_str(t.product_name),
                }
                for t in transactions
            ],
            "status": "paid"
            if end_date + timedelta(days=7) < datetime.utcnow()
            else "pending",
        }
