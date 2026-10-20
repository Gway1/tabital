from flask_restful import Resource, request
from ..docs import INT, NUM, STR, api_doc, obj
from ..services import merchant_fees
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_

_TRANSACTION = obj(
    {
        "id": INT,
        "transaction_id": STR,
        "customer_id": INT,
        "customer_name": STR,
        "customer_phone": STR,
        "merchant_id": INT,
        "merchant_name": STR,
        "merchant_phone": STR,
        "amount": NUM,
        "product_name": STR,
        "product_description": STR,
        "quantity": INT,
        "payment_method": STR,
        "payment_status": STR,
        "payment_reference": STR,
        "payment_plan": STR,
        "status": STR,
        "transaction_date": STR,
        "completion_date": STR,
        "delivery_address": STR,
        "delivery_status": STR,
        "tracking_number": STR,
        "notes": STR,
        "created_at": STR,
        "commission_rate": {**NUM, "description": "Percent"},
        "commission_amount": NUM,
        "payout_amount": NUM,
        "payout_estimated": {
            "type": "boolean",
            "description": "True for older sales with no stored payout",
        },
        "is_instalment": {"type": "boolean"},
    },
    description="Dates are ISO 8601; missing values are empty strings. The merchant listing leaves out some fields.",
)
_TXN_QUERY = {
    "status": (STR, "Transaction status"),
    "payment_status": (STR, "Payment status"),
    "start_date": (STR, "From this date"),
    "end_date": (STR, "To this date"),
    "search": (STR, "Matches transaction id, product name or payment reference"),
    "type": (
        {"type": "string", "enum": ["sent", "received", "all"]},
        "Customers only: own purchases or sales",
    ),
}
_FULFILMENT_BODY = obj({"tracking_number": STR, "notes": STR})
_TXN_UPDATED = obj(
    {
        "message": STR,
        "transaction_id": STR,
        "amount": NUM,
        "commission_amount": NUM,
        "payout_amount": NUM,
    }
)


def safe_str(value):
    return value if value is not None else ""


def safe_float(value):
    return value if value is not None else 0.0


def safe_int(value):
    return value if value is not None else 0


class GetTransactionsResource(Resource):
    @api_doc(
        "List transactions (admin: all, customer: own, merchant: own sales)",
        query=_TXN_QUERY,
        responses={
            200: (
                "Transactions, newest first. Not paginated.",
                {"type": "array", "items": _TRANSACTION},
            ),
            403: "Not an admin, customer or merchant",
        },
    )
    @auth_required
    def get(self):
        """Get all transactions with filters"""
        current_user_obj = current_user()

        # Get query parameters
        status = request.args.get("status", "").strip()
        payment_status = request.args.get("payment_status", "").strip()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        search = request.args.get("search", "").strip()
        transaction_type = request.args.get("type", "").strip()  # sent, received, all

        # Base query based on user role
        if current_user_obj.role == "admin":
            query = Transaction.query
        elif current_user_obj.role == "customer":
            query = Transaction.query.filter_by(customer_id=current_user_obj.id)
            if transaction_type == "sent":
                query = Transaction.query.filter_by(customer_id=current_user_obj.id)
            elif transaction_type == "received":
                query = Transaction.query.filter_by(merchant_id=current_user_obj.id)
        elif current_user_obj.role == "merchant":
            query = Transaction.query.filter_by(merchant_id=current_user_obj.id)
        else:
            return {"error": "Unauthorized"}, 403

        # Apply filters
        if status:
            query = query.filter(Transaction.status == status)
        if payment_status:
            query = query.filter(Transaction.payment_status == payment_status)
        if start_date:
            query = query.filter(Transaction.transaction_date >= start_date)
        if end_date:
            query = query.filter(Transaction.transaction_date <= end_date)
        if search:
            query = query.filter(
                db.or_(
                    Transaction.transaction_id.ilike(f"%{search}%"),
                    Transaction.product_name.ilike(f"%{search}%"),
                    Transaction.payment_reference.ilike(f"%{search}%"),
                )
            )

        transactions = query.order_by(Transaction.created_at.desc()).all()

        return [
            {
                "id": t.id,
                "transaction_id": safe_str(t.transaction_id),
                "customer_id": t.customer_id,
                "customer_name": safe_str(
                    t.customer.full_name or t.customer.business_name or t.customer.phone
                ),
                "customer_phone": safe_str(t.customer.phone),
                "merchant_id": t.merchant_id,
                "merchant_name": safe_str(
                    t.merchant.business_name or t.merchant.full_name or t.merchant.phone
                ),
                "merchant_phone": safe_str(t.merchant.phone),
                "amount": safe_float(t.amount),
                "product_name": safe_str(t.product_name),
                "product_description": safe_str(t.product_description),
                "quantity": safe_int(t.quantity),
                "payment_method": safe_str(t.payment_method),
                "payment_status": safe_str(t.payment_status),
                "payment_reference": safe_str(t.payment_reference),
                "status": safe_str(t.status),
                "transaction_date": t.transaction_date.isoformat()
                if t.transaction_date
                else "",
                "completion_date": t.completion_date.isoformat()
                if t.completion_date
                else "",
                "delivery_address": safe_str(t.delivery_address),
                "delivery_status": safe_str(t.delivery_status),
                "tracking_number": safe_str(t.tracking_number),
                "notes": safe_str(t.notes),
                "created_at": t.created_at.isoformat() if t.created_at else "",
                # Payout information
                **merchant_fees.transaction_fields(
                    t
                ),  # fee from the stored payout (tier, §6.1)
                "is_instalment": getattr(t, "payment_plan", None) is not None,
            }
            for t in transactions
        ]


class CreateTransactionResource(Resource):
    @api_doc(
        "Turned off: transactions come from approved purchase orders",
        responses={410: "Use /customer/purchase"},
    )
    @auth_required
    def post(self):
        """Disabled: transactions are only created when an admin approves a purchase order.

        This endpoint let a customer create a transaction for any merchant and amount,
        and it crashed on unknown columns anyway. Purchases go through /customer/purchase.
        """
        return {
            "error": "Transactions are created from approved purchase orders. Use /customer/purchase."
        }, 410


class UpdateTransactionStatusResource(Resource):
    @api_doc(
        "Set the tracking number and notes on a transaction (admin, or the merchant who owns it)",
        description="Money and delivery states can't be set here; sending `status`, `payment_status`, "
        "`payment_reference` or `delivery_status` gives 400.",
        body=_FULFILMENT_BODY,
        responses={
            200: ("Updated", _TXN_UPDATED),
            403: "Not allowed to edit this transaction",
            404: "Transaction not found",
        },
    )
    @auth_required
    def put(self, transaction_id):
        """Update transaction status"""
        current_user_obj = current_user()

        transaction = Transaction.query.get(transaction_id)
        if not transaction:
            return {"error": "Transaction not found"}, 404

        # Only fulfilment details can be edited here. Money states (status, payment_status,
        # payment_reference) follow the payments and the ledger; delivery is confirmed by the merchant
        # through /merchant/orders/<id>/delivery, which makes the sale payable (settlements).
        if current_user_obj.role == "admin" or (
            current_user_obj.role == "merchant"
            and transaction.merchant_id == current_user_obj.id
        ):
            allowed_fields = ["tracking_number", "notes"]
        else:
            return {"error": "Unauthorized"}, 403

        data = request.get_json() or {}
        refused = [
            f
            for f in (
                "status",
                "payment_status",
                "payment_reference",
                "delivery_status",
            )
            if f in data
        ]
        if refused:
            return {
                "error": "Only the tracking number and notes can be changed here. Delivery is confirmed on "
                "the merchant's Orders screen; payment states follow the payments."
            }, 400

        for field in allowed_fields:
            if field in data:
                setattr(transaction, field, data[field])
        db.session.commit()

        fee, payout = merchant_fees.transaction_split(transaction)
        return {
            "message": "Transaction updated successfully",
            "transaction_id": transaction.transaction_id,
            "amount": transaction.amount,
            "commission_amount": fee,
            "payout_amount": payout,
        }, 200


class GetTransactionStatsResource(Resource):
    @api_doc("Transaction totals and counts for the signed-in user's role")
    @auth_required
    def get(self):
        """Get transaction statistics"""
        current_user_obj = current_user()

        # Base query based on role
        if current_user_obj.role == "admin":
            query = Transaction.query
        elif current_user_obj.role == "customer":
            query = Transaction.query.filter_by(customer_id=current_user_obj.id)
        elif current_user_obj.role == "merchant":
            query = Transaction.query.filter_by(merchant_id=current_user_obj.id)
        else:
            return {"error": "Unauthorized"}, 403

        # Calculate statistics
        total_transactions = query.count()
        total_amount = (
            db.session.query(db.func.sum(Transaction.amount))
            .filter(Transaction.id.in_([t.id for t in query.all()]))
            .scalar()
            or 0
        )
        total_payout = (
            db.session.query(db.func.sum(Transaction.payout_amount))
            .filter(Transaction.id.in_([t.id for t in query.all()]))
            .scalar()
            or 0
        )
        total_commission = sum(
            merchant_fees.transaction_split(t)[0] for t in query.all()
        )

        pending = query.filter_by(status="pending").count()
        completed = query.filter_by(status="completed").count()
        cancelled = query.filter_by(status="cancelled").count()

        # Monthly breakdown
        from sqlalchemy import extract

        monthly_stats = (
            db.session.query(
                extract("year", Transaction.transaction_date).label("year"),
                extract("month", Transaction.transaction_date).label("month"),
                db.func.count(Transaction.id).label("count"),
                db.func.sum(Transaction.amount).label("amount"),
                db.func.sum(Transaction.payout_amount).label("payout"),
            )
            .filter(Transaction.id.in_([t.id for t in query.all()]))
            .group_by("year", "month")
            .order_by("year", "month")
            .limit(6)
            .all()
        )

        return {
            "total_transactions": total_transactions,
            "total_amount": float(total_amount),
            "total_payout": float(total_payout),
            "total_commission": float(total_commission),
            "pending": pending,
            "completed": completed,
            "cancelled": cancelled,
            "monthly_stats": [
                {
                    "month": f"{int(m[1])}/{int(m[0])}",
                    "count": m[2],
                    "amount": float(m[3]),
                    "payout": float(m[4]) if m[4] else 0,
                }
                for m in monthly_stats
            ],
        }, 200


class DeleteTransactionResource(Resource):
    """Turned off (go-live review): Transactions are financial records and can't be deleted."""

    @api_doc(
        "Turned off: transactions are financial records",
        responses={410: "Can't be deleted"},
    )
    @auth_required
    def delete(self, *args, **kwargs):
        if current_user().role not in ("admin", "merchant", "customer"):
            return {"error": "Unauthorized"}, 403
        return {
            "error": "Transactions are financial records and can't be deleted."
        }, 410


class MerchantGetTransactionsResource(Resource):
    @api_doc(
        "A merchant's sales, paginated, with payout and commission totals",
        query={
            **_TXN_QUERY,
            "limit": (INT, "Page size, default 50"),
            "page": (INT, "Page number, default 1"),
        },
        responses={
            200: (
                "Page of transactions",
                obj(
                    {
                        "transactions": {"type": "array", "items": _TRANSACTION},
                        "total": INT,
                        "page": INT,
                        "limit": INT,
                        "total_pages": INT,
                        "total_payout": NUM,
                        "total_commission": NUM,
                    }
                ),
            ),
            403: "Not a merchant",
        },
    )
    @auth_required
    def get(self):
        """Get all transactions for the merchant with filters"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        status = request.args.get("status", "").strip()
        payment_status = request.args.get("payment_status", "").strip()
        search = request.args.get("search", "").strip()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        transaction_type = request.args.get("type", "").strip()
        limit = request.args.get("limit", 50, type=int)
        page = request.args.get("page", 1, type=int)

        # Base query
        query = Transaction.query.filter_by(merchant_id=current_merchant.id)

        # Apply filters
        if status:
            query = query.filter(Transaction.status == status)

        if payment_status:
            query = query.filter(Transaction.payment_status == payment_status)

        if transaction_type == "sale":
            query = query.filter(Transaction.payment_plan.is_(None))
        elif transaction_type == "instalment":
            query = query.filter(Transaction.payment_plan.isnot(None))

        if search:
            query = query.filter(
                or_(
                    Transaction.transaction_id.ilike(f"%{search}%"),
                    Transaction.product_name.ilike(f"%{search}%"),
                    Transaction.customer.has(User.full_name.ilike(f"%{search}%")),
                    Transaction.customer.has(User.phone.ilike(f"%{search}%")),
                )
            )

        if start_date:
            query = query.filter(Transaction.transaction_date >= start_date)

        if end_date:
            query = query.filter(Transaction.transaction_date <= end_date)

        # Pagination
        total = query.count()
        transactions = (
            query.order_by(Transaction.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        # Calculate total payout for filtered transactions
        splits = [merchant_fees.transaction_split(t) for t in transactions]
        total_payout = sum(p for _f, p in splits)
        total_commission = sum(f for f, _p in splits)

        return {
            "transactions": [
                {
                    "id": t.id,
                    "transaction_id": safe_str(t.transaction_id),
                    "customer_name": safe_str(
                        t.customer.full_name
                        or t.customer.business_name
                        or t.customer.phone
                    ),
                    "customer_phone": safe_str(t.customer.phone),
                    "customer_email": safe_str(
                        t.customer.business_email or t.customer.email
                    ),
                    "amount": safe_float(t.amount),
                    "product_name": safe_str(t.product_name),
                    "product_description": safe_str(t.product_description),
                    "quantity": safe_int(t.quantity),
                    "payment_method": safe_str(t.payment_method),
                    "payment_status": safe_str(t.payment_status),
                    "payment_reference": safe_str(t.payment_reference),
                    "payment_plan": safe_str(t.payment_plan),
                    "status": safe_str(t.status),
                    "transaction_date": t.transaction_date.isoformat()
                    if t.transaction_date
                    else "",
                    "completion_date": t.completion_date.isoformat()
                    if t.completion_date
                    else "",
                    "delivery_status": safe_str(t.delivery_status),
                    "created_at": t.created_at.isoformat() if t.created_at else "",
                    "is_instalment": t.payment_plan is not None
                    and t.payment_plan != "",
                    # Payout information for merchants
                    **merchant_fees.transaction_fields(
                        t
                    ),  # fee from the stored payout (tier, §6.1)
                }
                for t in transactions
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            "total_payout": float(total_payout),
            "total_commission": float(total_commission),
        }


class MerchantGetTransactionStatsResource(Resource):
    @api_doc(
        "Sales totals and counts for the merchant", responses={403: "Not a merchant"}
    )
    @auth_required
    def get(self):
        """Get transaction statistics for the merchant including payout info"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Date ranges
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        # Get all completed transactions for this merchant
        all_completed_transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
        ).all()

        # Calculate total payout (all time)
        splits = [
            merchant_fees.transaction_split(t, current_merchant)
            for t in all_completed_transactions
        ]
        total_payout = sum(p for _f, p in splits)
        total_commission = sum(f for f, _p in splits)

        # Calculate pending payout (completed but not paid)
        pending_transactions = [
            t
            for t in all_completed_transactions
            if getattr(t, "payment_status", "pending") == "pending"
        ]
        pending_payout = sum(
            merchant_fees.transaction_split(t)[1] for t in pending_transactions
        )

        # Calculate paid payout
        paid_transactions = [
            t
            for t in all_completed_transactions
            if getattr(t, "payment_status", "") == "paid"
        ]
        paid_payout = sum(
            merchant_fees.transaction_split(t)[1] for t in paid_transactions
        )

        # This month's payout
        this_month_transactions = [
            t
            for t in all_completed_transactions
            if t.completion_date and t.completion_date >= month_ago
        ]
        this_month_payout = sum(
            merchant_fees.transaction_split(t)[1] for t in this_month_transactions
        )

        # Last month's payout (for growth calculation)
        last_month_start = (month_ago - timedelta(days=30)).replace(day=1)
        last_month_end = month_ago - timedelta(days=1)
        last_month_transactions = [
            t
            for t in all_completed_transactions
            if t.completion_date
            and last_month_start <= t.completion_date <= last_month_end
        ]
        last_month_payout = sum(
            merchant_fees.transaction_split(t)[1] for t in last_month_transactions
        )

        # Calculate payout growth
        if last_month_payout > 0:
            payout_growth = (
                (this_month_payout - last_month_payout) / last_month_payout
            ) * 100
        else:
            payout_growth = 100 if this_month_payout > 0 else 0

        # Return the format the frontend expects
        return {
            "total_payout": float(total_payout),
            "total_commission": float(total_commission),
            "pending_payout": float(pending_payout),
            "paid_payout": float(paid_payout),
            "this_month_payout": float(this_month_payout),
            "last_month_payout": float(last_month_payout),
            "payout_growth": float(payout_growth),
        }, 200


class MerchantUpdateTransactionStatusResource(Resource):
    @api_doc(
        "Set the tracking number and notes on one of the merchant's sales",
        description="Delivery is confirmed on the Orders screen (`/merchant/orders/<id>/delivery`), not here.",
        body=_FULFILMENT_BODY,
        responses={
            200: ("Updated", _TXN_UPDATED),
            400: "Tried to change status or payment fields",
            403: "Not a merchant",
            404: "Transaction not found",
        },
    )
    @auth_required
    def put(self, transaction_id):
        """Update transaction status"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        transaction = Transaction.query.get(transaction_id)

        if not transaction or transaction.merchant_id != current_merchant.id:
            return {"error": "Transaction not found"}, 404

        data = request.get_json() or {}

        # Merchants can't set status/payment_status: those drive settlements. Delivery is
        # confirmed through /merchant/orders/<id>/delivery, which creates the settlement line.
        if any(
            f in data
            for f in (
                "status",
                "payment_status",
                "payment_reference",
                "delivery_status",
            )
        ):
            return {
                "error": "Confirm delivery on your Orders screen. Only the tracking number and notes "
                "can be changed here."
            }, 400
        allowed_fields = ["tracking_number", "notes"]

        for field in allowed_fields:
            if field in data:
                setattr(transaction, field, data[field])

        db.session.commit()

        fee, payout = merchant_fees.transaction_split(transaction, current_merchant)
        return {
            "message": "Transaction updated successfully",
            "transaction_id": transaction.transaction_id,
            "amount": transaction.amount,
            "commission_amount": fee,
            "payout_amount": payout,
        }, 200


class MerchantUpdateTransactionResource(Resource):
    @api_doc(
        "Edit the delivery address, tracking number and notes of one of the merchant's sales",
        body=obj({"delivery_address": STR, "tracking_number": STR, "notes": STR}),
        responses={
            200: (
                "Updated",
                obj({"message": STR, "transaction_id": STR, "payout_amount": NUM}),
            ),
            400: "Tried to change status or payment fields",
            403: "Not a merchant",
            404: "Transaction not found",
        },
    )
    @auth_required
    def put(self, transaction_id):
        """Update transaction details"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        transaction = Transaction.query.get(transaction_id)

        if not transaction or transaction.merchant_id != current_merchant.id:
            return {"error": "Transaction not found"}, 404

        data = request.get_json() or {}

        # Fulfilment details only; the payment reference belongs to the payment records
        if any(
            f in data
            for f in (
                "status",
                "payment_status",
                "payment_reference",
                "delivery_status",
            )
        ):
            return {
                "error": "Only the delivery address, tracking number and notes can be changed here."
            }, 400
        allowed_fields = ["delivery_address", "tracking_number", "notes"]

        for field in allowed_fields:
            if field in data:
                setattr(transaction, field, data[field])

        db.session.commit()

        return {
            "message": "Transaction updated successfully",
            "transaction_id": transaction.transaction_id,
            "payout_amount": merchant_fees.transaction_split(
                transaction, current_merchant
            )[1],
        }, 200


class MerchantRefundTransactionResource(Resource):
    """Turned off (go-live review): Refunds go through a dispute (the customer opens it and Tabital resolves it) or an order rejection; marking a sale refunded here didn't move any money or stop the customer's plan."""

    @api_doc(
        "Turned off: refunds go through a dispute or an order rejection",
        responses={410: "Not available"},
    )
    @auth_required
    def post(self, *args, **kwargs):
        if current_user().role != "merchant":
            return {"error": "Unauthorized"}, 403
        return {
            "error": "Refunds go through a dispute (the customer opens it and Tabital resolves it) or an order rejection; marking a sale refunded here didn't move any money or stop the customer's plan."
        }, 410


class MerchantExportTransactionsResource(Resource):
    @api_doc(
        "Download the merchant's sales as CSV (with payout columns)",
        query={
            "start_date": (STR, "From this date"),
            "end_date": (STR, "To this date"),
        },
        produces="text/csv",
    )
    @auth_required
    def get(self):
        """Export transactions to CSV with payout info"""
        from flask import Response
        import csv
        from io import StringIO

        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        query = Transaction.query.filter_by(merchant_id=current_merchant.id)

        if start_date:
            query = query.filter(Transaction.transaction_date >= start_date)
        if end_date:
            query = query.filter(Transaction.transaction_date <= end_date)

        transactions = query.order_by(Transaction.created_at.desc()).all()

        output = StringIO()
        writer = csv.writer(output)

        # Write headers with payout columns
        writer.writerow(
            [
                "Transaction ID",
                "Date",
                "Customer Name",
                "Customer Phone",
                "Product",
                "Quantity",
                "Amount",
                "Commission Rate (%)",
                "Commission Amount",
                "Payout Amount",
                "Status",
                "Payment Status",
                "Payment Method",
                "Delivery Status",
            ]
        )

        # Write data
        for t in transactions:
            writer.writerow(
                [
                    t.transaction_id,
                    t.transaction_date.strftime("%Y-%m-%d %H:%M")
                    if t.transaction_date
                    else "",
                    t.customer.full_name
                    or t.customer.business_name
                    or t.customer.phone,
                    t.customer.phone,
                    t.product_name,
                    t.quantity,
                    t.amount,
                    merchant_fees.transaction_fields(t)["commission_rate"],
                    merchant_fees.transaction_fields(t)["commission_amount"],
                    merchant_fees.transaction_fields(t)["payout_amount"],
                    t.status,
                    t.payment_status,
                    t.payment_method,
                    t.delivery_status,
                ]
            )

        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename=transactions_{datetime.now().strftime("%Y%m%d")}.csv'
            },
        )


# Add these to your Flask API resources


class MerchantPayoutStatsResource(Resource):
    @auth_required
    def get(self):
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Calculate payout stats
        from ..models.transaction import Transaction

        # Total payout (all time)
        total_payout = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter_by(merchant_id=current_merchant.id, status="completed")
            .scalar()
            or 0
        )

        total_commission = sum(
            merchant_fees.transaction_split(t, current_merchant)[0]
            for t in Transaction.query.filter_by(
                merchant_id=current_merchant.id, status="completed"
            )
        )

        # Pending payout (completed but not paid)
        pending_payout = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter_by(
                merchant_id=current_merchant.id,
                status="completed",
                payment_status="pending",
            )
            .scalar()
            or 0
        )

        # Paid payout
        paid_payout = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter_by(
                merchant_id=current_merchant.id,
                status="completed",
                payment_status="paid",
            )
            .scalar()
            or 0
        )

        # This month's payout
        today = datetime.now()
        first_day_of_month = today.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        this_month_payout = (
            db.session.query(func.sum(Transaction.payout_amount))
            .filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.status == "completed",
                Transaction.completion_date >= first_day_of_month,
            )
            .scalar()
            or 0
        )

        # Available for withdrawal (minimum payout is 100)
        min_payout = 100
        available_for_withdrawal = pending_payout if pending_payout >= min_payout else 0

        # Next payout date (next settlement date)
        next_payout_date = (today + timedelta(days=7)).strftime("%Y-%m-%d")

        return {
            "total_payout": float(total_payout),
            "total_commission": float(total_commission),
            "pending_payout": float(pending_payout),
            "paid_payout": float(paid_payout),
            "this_month_payout": float(this_month_payout),
            "last_month_payout": 0,
            "payout_growth": 0,
            "available_for_withdrawal": float(available_for_withdrawal),
            "next_payout_date": next_payout_date,
            "payout_history": [],
        }, 200


class MerchantRecentPayoutsResource(Resource):
    @auth_required
    def get(self):
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        limit = request.args.get("limit", 5, type=int)

        # Get recent completed transactions
        transactions = (
            Transaction.query.filter_by(
                merchant_id=current_merchant.id, status="completed"
            )
            .order_by(Transaction.completion_date.desc())
            .limit(limit)
            .all()
        )

        result = []
        for t in transactions:
            result.append(
                {
                    "id": t.id,
                    "payout_id": t.transaction_id,
                    "amount": float(t.amount),
                    "payout_amount": merchant_fees.transaction_split(t)[1],
                    "commission_amount": merchant_fees.transaction_split(t)[0],
                    "status": t.payment_status or "pending",
                    "date": t.completion_date.isoformat()
                    if t.completion_date
                    else t.created_at.isoformat(),
                    "product_name": t.product_name,
                }
            )

        return result, 200
