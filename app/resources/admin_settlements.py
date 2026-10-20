from flask_restful import Resource, request
from ..services import merchant_fees
from flask_praetorian import auth_required, current_user
from app.models.user import User
from app.models.transaction import Transaction
from app.models.instalment import InstalmentPlan
from app.extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_


class AdminSettlementStatsResource(Resource):
    @auth_required
    def get(self):
        """Get settlement statistics"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Date calculations
        today = datetime.now().date()
        last_30_days = today - timedelta(days=30)

        # Total Pending Payouts
        total_pending = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter(
                Transaction.payment_status == "completed",
                Transaction.status == "completed",
            )
            .scalar()
            or 0
        )

        # Total Processed (last 30 days)
        total_processed = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter(
                Transaction.payment_status == "completed",
                Transaction.status == "completed",
                Transaction.completion_date >= last_30_days,
            )
            .scalar()
            or 0
        )

        # Upcoming Settlements (next 7 days)
        upcoming_settlements = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter(
                Transaction.status == "completed",
                Transaction.payment_status == "pending",
                Transaction.completion_date.isnot(None),
                Transaction.completion_date <= datetime.now() + timedelta(days=7),
            )
            .scalar()
            or 0
        )

        # Total Settled (All Time)
        total_settled = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter(Transaction.payment_status == "settled")
            .scalar()
            or 0
        )

        # Average Settlement Time (days)
        avg_settlement_time = 3.2  # Sample value - calculate based on actual data
        avg_settlement_previous = 4.1
        settlement_time_change = avg_settlement_time - avg_settlement_previous

        return {
            "total_pending": float(total_pending),
            "total_processed": float(total_processed),
            "upcoming_settlements": float(upcoming_settlements),
            "total_settled": float(total_settled),
            "avg_settlement_time": avg_settlement_time,
            "settlement_time_change": round(settlement_time_change, 1),
        }, 200


class AdminGetSettlementsResource(Resource):
    @auth_required
    def get(self):
        """Get all settlements with filters and pagination"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        search = request.args.get("search", "", type=str)
        status = request.args.get("status", "", type=str)  # pending, completed, settled
        merchant_id = request.args.get("merchant_id", "", type=int)
        date_from = request.args.get("date_from", "", type=str)
        date_to = request.args.get("date_to", "", type=str)
        sort_by = request.args.get("sort_by", "created_at", type=str)
        sort_order = request.args.get("sort_order", "desc", type=str)

        # Build query for completed transactions that need settlement
        query = Transaction.query.filter(Transaction.status == "completed")

        # Apply payment status filter
        if status:
            if status == "pending":
                query = query.filter(Transaction.payment_status == "pending")
            elif status == "completed":
                query = query.filter(Transaction.payment_status == "completed")
            elif status == "settled":
                query = query.filter(Transaction.payment_status == "settled")

        # Apply search filter
        if search:
            query = query.join(User, Transaction.merchant_id == User.id).filter(
                or_(
                    Transaction.transaction_id.ilike(f"%{search}%"),
                    User.business_name.ilike(f"%{search}%"),
                    User.owner_name.ilike(f"%{search}%"),
                    Transaction.product_name.ilike(f"%{search}%"),
                )
            )

        # Apply merchant filter
        if merchant_id:
            query = query.filter(Transaction.merchant_id == merchant_id)

        # Apply date range filter
        if date_from:
            query = query.filter(Transaction.completion_date >= date_from)
        if date_to:
            query = query.filter(Transaction.completion_date <= date_to)

        # Apply sorting
        if sort_order == "desc":
            query = query.order_by(getattr(Transaction, sort_by).desc())
        else:
            query = query.order_by(getattr(Transaction, sort_by).asc())

        # Pagination
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        settlements = []
        for transaction in paginated.items:
            merchant = User.query.get(transaction.merchant_id)

            # Calculate days to settle
            days_to_settle = None
            if transaction.completion_date:
                days_to_settle = (datetime.now() - transaction.completion_date).days

            settlements.append(
                {
                    "id": transaction.id,
                    "transaction_id": transaction.transaction_id,
                    "merchant_id": merchant.id if merchant else None,
                    "merchant_name": merchant.business_name if merchant else "N/A",
                    "merchant_phone": merchant.phone if merchant else "N/A",
                    "merchant_email": merchant.business_email or merchant.email
                    if merchant
                    else "N/A",
                    "product_name": transaction.product_name,
                    "amount": float(transaction.amount),
                    "commission": merchant_fees.transaction_split(
                        transaction, merchant
                    )[
                        0
                    ],  # tier fee (§6.1)
                    "payout_amount": merchant_fees.transaction_split(
                        transaction, merchant
                    )[1],
                    "payment_status": transaction.payment_status,
                    "transaction_date": transaction.transaction_date.isoformat()
                    if transaction.transaction_date
                    else None,
                    "completion_date": transaction.completion_date.isoformat()
                    if transaction.completion_date
                    else None,
                    "days_to_settle": days_to_settle,
                    "payment_method": transaction.payment_method,
                    "payment_reference": transaction.payment_reference,
                }
            )

        return {
            "settlements": settlements,
            "total": paginated.total,
            "page": page,
            "per_page": per_page,
            "total_pages": paginated.pages if paginated.pages > 0 else 1,
        }, 200


class AdminGetSettlementDetailResource(Resource):
    @auth_required
    def get(self, settlement_id):
        """Get detailed settlement information"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        transaction = Transaction.query.get(settlement_id)
        if not transaction or transaction.status != "completed":
            return {"error": "Settlement not found"}, 404

        merchant = User.query.get(transaction.merchant_id)
        customer = User.query.get(transaction.customer_id)

        # Get bank details
        bank_details = {
            "bank_name": merchant.bank_name if merchant else None,
            "account_name": merchant.account_name if merchant else None,
            "account_number": merchant.account_number if merchant else None,
            "branch_name": merchant.branch_name if merchant else None,
            "swift_code": merchant.swift_code if merchant else None,
        }

        # Get mobile money details
        momo_details = {
            "momo_name": merchant.momo_name if merchant else None,
            "momo_number": merchant.momo_number if merchant else None,
        }

        return {
            "settlement": {
                "id": transaction.id,
                "transaction_id": transaction.transaction_id,
                "amount": float(transaction.amount),
                "commission": merchant_fees.transaction_split(transaction)[0],
                "payout_amount": merchant_fees.transaction_split(transaction)[1],
                "payment_status": transaction.payment_status,
                "transaction_date": transaction.transaction_date.isoformat()
                if transaction.transaction_date
                else None,
                "completion_date": transaction.completion_date.isoformat()
                if transaction.completion_date
                else None,
                "product_name": transaction.product_name,
                "product_description": transaction.product_description,
            },
            "merchant": {
                "id": merchant.id if merchant else None,
                "business_name": merchant.business_name if merchant else "N/A",
                "owner_name": merchant.owner_name if merchant else "N/A",
                "phone": merchant.phone if merchant else "N/A",
                "email": merchant.business_email or merchant.email
                if merchant
                else "N/A",
                "bank_details": bank_details,
                "momo_details": momo_details,
            },
            "customer": {
                "id": customer.id if customer else None,
                "name": customer.full_name if customer else "N/A",
                "phone": customer.phone if customer else "N/A",
                "email": customer.business_email or customer.email
                if customer
                else "N/A",
            },
        }, 200


_RETIRED = (
    "Settlements are paid through settlement batches now: review and approve them under "
    "/admin/settlement-batches. Marking transactions as settled by hand is no longer allowed."
)


class AdminProcessSettlementResource(Resource):
    @auth_required
    def post(self):
        """Retired in Phase 5: this marked transactions 'settled' without sending any money."""
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        return {"error": _RETIRED}, 410


class AdminProcessSingleSettlementResource(Resource):
    @auth_required
    def put(self, settlement_id):
        """Retired in Phase 5: this marked a transaction 'settled' (and overwrote its customer
        payment reference) without sending any money."""
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        return {"error": _RETIRED}, 410


class AdminExportSettlementsResource(Resource):
    @auth_required
    def get(self):
        """Export settlements to CSV"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        status = request.args.get("status", "", type=str)
        date_from = request.args.get("date_from", "", type=str)
        date_to = request.args.get("date_to", "", type=str)

        # Build query
        query = Transaction.query.filter(Transaction.status == "completed")

        if status:
            if status == "pending":
                query = query.filter(Transaction.payment_status == "pending")
            elif status == "settled":
                query = query.filter(Transaction.payment_status == "settled")

        if date_from:
            query = query.filter(Transaction.completion_date >= date_from)
        if date_to:
            query = query.filter(Transaction.completion_date <= date_to)

        settlements = query.all()

        # Create CSV content
        import csv
        from io import StringIO
        from flask import make_response

        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(
            [
                "Transaction ID",
                "Merchant",
                "Product",
                "Amount",
                "Commission",
                "Payout Amount",
                "Status",
                "Transaction Date",
                "Completion Date",
            ]
        )

        # Write data
        for settlement in settlements:
            merchant = User.query.get(settlement.merchant_id)

            writer.writerow(
                [
                    settlement.transaction_id,
                    merchant.business_name if merchant else "N/A",
                    settlement.product_name,
                    settlement.amount,
                    merchant_fees.transaction_split(settlement, merchant)[0],
                    merchant_fees.transaction_split(settlement, merchant)[1],
                    settlement.payment_status,
                    settlement.transaction_date.strftime("%Y-%m-%d %H:%M:%S")
                    if settlement.transaction_date
                    else "",
                    settlement.completion_date.strftime("%Y-%m-%d %H:%M:%S")
                    if settlement.completion_date
                    else "",
                ]
            )

        output.seek(0)
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers[
            "Content-Disposition"
        ] = f'attachment; filename=settlements_{datetime.now().strftime("%Y%m%d")}.csv'
        return response
