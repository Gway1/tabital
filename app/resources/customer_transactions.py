# resources/customer_transactions.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..models.instalment import InstalmentPlan
from ..models.instalment_payment import InstalmentPayment
from ..extensions import db
from datetime import datetime, timedelta
from sqlalchemy import or_, and_


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class CustomerGetTransactionsResource(Resource):
    @auth_required
    def get(self):
        """Get customer's transactions - only shows payments made by customer"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 20, type=int)
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "")
        payment_status = request.args.get("payment_status", "")
        start_date = request.args.get("start_date", "")
        end_date = request.args.get("end_date", "")

        # Build query - only get transaction records (individual payments made)
        # These are created when customer makes a payment (down payment or installment)
        query = Transaction.query.filter(Transaction.customer_id == current_customer.id)

        # Apply filters
        if search:
            query = query.filter(
                or_(
                    Transaction.transaction_id.ilike(f"%{search}%"),
                    Transaction.product_name.ilike(f"%{search}%"),
                )
            )

        if status:
            query = query.filter(Transaction.status == status)

        if payment_status:
            query = query.filter(Transaction.payment_status == payment_status)

        if start_date:
            query = query.filter(Transaction.transaction_date >= start_date)

        if end_date:
            query = query.filter(Transaction.transaction_date <= end_date)

        # Get total count
        total = query.count()

        # Get paginated results
        transactions = (
            query.order_by(Transaction.transaction_date.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        # Format response
        result = []
        for tx in transactions:
            # Get merchant details
            merchant = User.query.get(tx.merchant_id)

            result.append(
                {
                    "id": tx.id,
                    "transaction_id": safe_str(tx.transaction_id),
                    "merchant_name": safe_str(
                        merchant.business_name or merchant.full_name or merchant.phone
                    ),
                    "merchant_phone": safe_str(merchant.phone),
                    "product_name": safe_str(tx.product_name),
                    "product_description": safe_str(tx.product_description),
                    "amount": safe_float(tx.amount),
                    "quantity": safe_int(tx.quantity),
                    "payment_method": safe_str(tx.payment_method),
                    "payment_plan": safe_str(tx.payment_plan),
                    "payment_status": safe_str(tx.payment_status),
                    "payment_reference": safe_str(tx.payment_reference),
                    "status": safe_str(tx.status),
                    "transaction_date": tx.transaction_date.isoformat()
                    if tx.transaction_date
                    else "",
                    "completion_date": tx.completion_date.isoformat()
                    if tx.completion_date
                    else "",
                    "delivery_address": safe_str(tx.delivery_address),
                    "delivery_status": safe_str(tx.delivery_status),
                    "tracking_number": safe_str(tx.tracking_number),
                    "notes": safe_str(tx.notes),
                    "is_instalment_payment": "instalment"
                    in str(tx.product_description).lower()
                    or "Installment" in str(tx.product_description),
                    "installment_number": self._extract_installment_number(
                        tx.product_description
                    ),
                }
            )

        return {
            "transactions": result,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }

    def _extract_installment_number(self, description):
        """Extract installment number from description"""
        if not description:
            return None
        import re

        match = re.search(r"Installment (\d+) of", description)
        if match:
            return int(match.group(1))
        return None


class CustomerGetTransactionDetailsResource(Resource):
    @auth_required
    def get(self, transaction_id):
        """Get specific transaction details"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        transaction = Transaction.query.filter_by(
            id=transaction_id, customer_id=current_customer.id
        ).first()

        if not transaction:
            return {"error": "Transaction not found"}, 404

        # Get merchant details
        merchant = User.query.get(transaction.merchant_id)

        return {
            "id": transaction.id,
            "transaction_id": safe_str(transaction.transaction_id),
            "merchant_name": safe_str(
                merchant.business_name or merchant.full_name or merchant.phone
            ),
            "merchant_phone": safe_str(merchant.phone),
            "merchant_email": safe_str(merchant.business_email or merchant.email),
            "product_name": safe_str(transaction.product_name),
            "product_description": safe_str(transaction.product_description),
            "amount": safe_float(transaction.amount),
            "quantity": safe_int(transaction.quantity),
            "payment_method": safe_str(transaction.payment_method),
            "payment_plan": safe_str(transaction.payment_plan),
            "payment_status": safe_str(transaction.payment_status),
            "payment_reference": safe_str(transaction.payment_reference),
            "status": safe_str(transaction.status),
            "transaction_date": transaction.transaction_date.isoformat()
            if transaction.transaction_date
            else "",
            "completion_date": transaction.completion_date.isoformat()
            if transaction.completion_date
            else "",
            "delivery_address": safe_str(transaction.delivery_address),
            "delivery_status": safe_str(transaction.delivery_status),
            "tracking_number": safe_str(transaction.tracking_number),
            "notes": safe_str(transaction.notes),
        }


class CustomerGetTransactionStatsResource(Resource):
    @auth_required
    def get(self):
        """Get transaction statistics for customer - based on payments made"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Today's transactions (payments made)
        today_tx = Transaction.query.filter(
            Transaction.customer_id == current_customer.id,
            Transaction.transaction_date >= today_start,
        ).all()

        # This week's transactions
        week_tx = Transaction.query.filter(
            Transaction.customer_id == current_customer.id,
            Transaction.transaction_date >= week_start,
        ).all()

        # This month's transactions
        month_tx = Transaction.query.filter(
            Transaction.customer_id == current_customer.id,
            Transaction.transaction_date >= month_start,
        ).all()

        # Get total spent across all payments
        all_transactions = Transaction.query.filter_by(
            customer_id=current_customer.id
        ).all()
        total_spent = sum(t.amount for t in all_transactions if t.status == "completed")

        def calculate_stats(transactions):
            total = sum(t.amount for t in transactions if t.status == "completed")
            count = len(transactions)
            completed = len([t for t in transactions if t.status == "completed"])
            return {"total": total, "count": count, "completed": completed}

        return {
            "today": calculate_stats(today_tx),
            "this_week": calculate_stats(week_tx),
            "this_month": calculate_stats(month_tx),
            "total_spent": total_spent,
            "total_transactions": len(all_transactions),
        }


class CustomerExportTransactionsResource(Resource):
    @auth_required
    def get(self):
        """Export customer transactions to CSV"""
        from flask import Response
        import csv
        from io import StringIO

        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Get filters
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "")
        payment_status = request.args.get("payment_status", "")
        start_date = request.args.get("start_date", "")
        end_date = request.args.get("end_date", "")

        # Build query
        query = Transaction.query.filter(Transaction.customer_id == current_customer.id)

        if search:
            query = query.filter(
                or_(
                    Transaction.transaction_id.ilike(f"%{search}%"),
                    Transaction.product_name.ilike(f"%{search}%"),
                )
            )

        if status:
            query = query.filter(Transaction.status == status)

        if payment_status:
            query = query.filter(Transaction.payment_status == payment_status)

        if start_date:
            query = query.filter(Transaction.transaction_date >= start_date)

        if end_date:
            query = query.filter(Transaction.transaction_date <= end_date)

        transactions = query.order_by(Transaction.transaction_date.desc()).all()

        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(
            [
                "Transaction ID",
                "Date",
                "Merchant",
                "Product",
                "Amount",
                "Status",
                "Payment Status",
                "Payment Method",
                "Delivery Status",
                "Notes",
            ]
        )

        # Write data
        for tx in transactions:
            merchant = User.query.get(tx.merchant_id)
            writer.writerow(
                [
                    tx.transaction_id,
                    tx.transaction_date.strftime("%Y-%m-%d %H:%M")
                    if tx.transaction_date
                    else "",
                    merchant.business_name or merchant.full_name or merchant.phone,
                    tx.product_name,
                    tx.amount,
                    tx.status,
                    tx.payment_status,
                    tx.payment_method,
                    tx.delivery_status,
                    tx.notes or "",
                ]
            )

        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename=my_payments_{datetime.now().strftime("%Y%m%d")}.csv'
            },
        )


class CustomerGetPaymentSummaryResource(Resource):
    @auth_required
    def get(self):
        """Get payment summary for customer (total paid, pending, etc.)"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Get all instalment plans for this customer
        instalment_plans = InstalmentPlan.query.filter_by(
            customer_id=current_customer.id
        ).all()

        total_commitment = 0
        total_paid = 0
        total_remaining = 0
        active_plans = 0
        completed_plans = 0

        for plan in instalment_plans:
            total_commitment += plan.total_amount
            total_paid += plan.paid_to_date
            total_remaining += plan.outstanding_balance

            if plan.status == "active":
                active_plans += 1
            elif plan.status == "completed":
                completed_plans += 1

        # Get one-time transactions (non-instalment)
        one_time_transactions = Transaction.query.filter(
            Transaction.customer_id == current_customer.id,
            Transaction.payment_plan.is_(None),
        ).all()

        one_time_total = sum(t.amount for t in one_time_transactions)

        return {
            "total_commitment": total_commitment,
            "total_paid": total_paid + one_time_total,
            "total_remaining": total_remaining,
            "active_plans": active_plans,
            "completed_plans": completed_plans,
            "one_time_purchases": len(one_time_transactions),
            "one_time_total": one_time_total,
        }, 200
