from flask import Response
from flask_restful import Resource, request
from ..docs import STR, api_doc
from flask_praetorian import auth_required, current_user
from ..models.purchase_order import PurchaseOrder
from ..models.user import User
from ..models.transaction import Transaction
from ..extensions import db
from ..services import fraud
from datetime import datetime
from io import StringIO
import csv
import json


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def _search_orders_query(search):
    """PurchaseOrder query narrowed by an order/product/customer/merchant search term."""
    query = PurchaseOrder.query
    search = (search or "").strip()
    if search:
        like = f"%{search}%"
        user_ids = db.session.query(User.id).filter(
            db.or_(
                User.full_name.ilike(like),
                User.business_name.ilike(like),
                User.phone.ilike(like),
            )
        )
        query = query.filter(
            db.or_(
                PurchaseOrder.order_id.ilike(like),
                PurchaseOrder.product_name.ilike(like),
                PurchaseOrder.customer_id.in_(user_ids),
                PurchaseOrder.merchant_id.in_(user_ids),
            )
        )
    return query


def _csv_text(v):
    """Neutralise spreadsheet formulas in free-text cells."""
    v = safe_str(v)
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


class AdminGetOrdersResource(Resource):
    @auth_required
    def get(self):
        """Get all purchase orders for admin"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        status = request.args.get("status", "")
        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 20, type=int)

        query = _search_orders_query(request.args.get("search"))

        # Counts for the whole queue (not just this page), so the tabs show real numbers
        counts = dict(
            query.with_entities(PurchaseOrder.status, db.func.count(PurchaseOrder.id))
            .group_by(PurchaseOrder.status)
            .all()
        )

        if status:
            query = query.filter(PurchaseOrder.status == status)

        total = query.count()
        orders = (
            query.order_by(PurchaseOrder.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        return {
            "orders": [
                {
                    "id": o.id,
                    "order_id": o.order_id,
                    "customer_name": safe_str(
                        o.customer.full_name or o.customer.business_name
                    ),
                    "customer_phone": safe_str(o.customer.phone),
                    "customer_user_id": o.customer.id if o.customer else None,
                    "merchant_name": safe_str(
                        o.merchant.business_name or o.merchant.full_name
                    ),
                    "product_name": o.product_name,
                    "product_price": o.product_price,
                    "quantity": o.quantity,
                    "total_payable": o.total_payable,
                    "down_payment_amount": o.down_payment_amount,
                    "installment_amount": o.installment_amount,
                    "number_of_installments": o.number_of_installments,
                    "status": o.status,
                    "down_payment_status": o.down_payment_status or "unpaid",
                    "down_payment_reference": o.down_payment_reference,
                    "down_payment_paid_at": o.down_payment_paid_at.isoformat()
                    if o.down_payment_paid_at
                    else None,
                    "refund_status": o.refund_status,
                    "created_at": o.created_at.isoformat() if o.created_at else "",
                    "delivery_address": o.delivery_address,
                    # Phase 6: open fraud flags on the customer or merchant, and identity/employment checks
                    "fraud_flags": fraud.review_flags(o.customer, o.merchant)
                    if o.customer
                    else [],
                    "identity_verified_by": o.customer.verification_level
                    if o.customer
                    else None,
                    "employment_verified": bool(
                        o.customer and o.customer.employment_verified_at
                    ),
                }
                for o in orders
            ],
            "total": total,
            "counts": counts,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }


class AdminExportOrdersResource(Resource):
    @api_doc(
        "Download purchase orders as CSV (same filters as the orders list)",
        query={
            "status": (STR, "Only orders with this status"),
            "search": (STR, "Match order ID, product, customer or merchant"),
        },
        produces="text/csv",
    )
    @auth_required
    def get(self):
        """Export purchase orders to CSV"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        query = _search_orders_query(request.args.get("search"))
        status = request.args.get("status", "")
        if status:
            query = query.filter(PurchaseOrder.status == status)

        orders = query.order_by(PurchaseOrder.created_at.desc()).all()

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Order ID",
                "Customer Name",
                "Customer Phone",
                "Merchant",
                "Product",
                "Product Price",
                "Quantity",
                "Total Payable",
                "Down Payment",
                "Installment Amount",
                "Installments",
                "Status",
                "Down Payment Status",
                "Down Payment Reference",
                "Refund Status",
                "Delivery Address",
                "Created At",
            ]
        )
        for o in orders:
            customer, merchant = o.customer, o.merchant
            writer.writerow(
                [
                    o.order_id,
                    _csv_text(
                        (customer.full_name or customer.business_name)
                        if customer
                        else ""
                    ),
                    _csv_text(customer.phone if customer else ""),
                    _csv_text(
                        (merchant.business_name or merchant.full_name)
                        if merchant
                        else ""
                    ),
                    _csv_text(o.product_name),
                    safe_float(o.product_price),
                    o.quantity or 0,
                    safe_float(o.total_payable),
                    safe_float(o.down_payment_amount),
                    safe_float(o.installment_amount),
                    o.number_of_installments or 0,
                    o.status,
                    o.down_payment_status or "unpaid",
                    safe_str(o.down_payment_reference),
                    safe_str(o.refund_status),
                    _csv_text(o.delivery_address),
                    o.created_at.strftime("%Y-%m-%d %H:%M:%S") if o.created_at else "",
                ]
            )

        filename = f"orders_{datetime.utcnow().strftime('%Y%m%d')}.csv"
        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )


# class AdminApproveOrderResource(Resource):
#     @auth_required
#     def put(self, order_id):
#         """Admin approves an order"""
#         current_admin = current_user()

#         if current_admin.role != 'admin':
#             return {"error": "Unauthorized"}, 403

#         order = PurchaseOrder.query.get(order_id)
#         if not order:
#             return {"error": "Order not found"}, 404

#         if order.status != 'pending':
#             return {"error": f"Order already {order.status}"}, 400

#         data = request.get_json()

#         order.status = 'approved'
#         order.approved_at = datetime.now()
#         order.admin_notes = data.get('admin_notes', '')

#         # Create transaction for the merchant (full payment to merchant)
#         transaction = Transaction(
#             transaction_id=Transaction.generate_transaction_id(Transaction),
#             customer_id=order.customer_id,
#             merchant_id=order.merchant_id,
#             amount=order.total_payable,
#             product_name=order.product_name,
#             product_description=order.product_description,
#             quantity=order.quantity,
#             payment_plan=f"{order.number_of_installments} Months",
#             status='completed',
#             payment_status='processing',
#             delivery_address=order.delivery_address
#         )

#         db.session.add(transaction)
#         db.session.commit()

#         return {
#             "message": "Order approved successfully",
#             "transaction_id": transaction.transaction_id
#         }, 200


class AdminRejectOrderResource(Resource):
    @auth_required
    def put(self, order_id):
        """Admin rejects an order"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        order = PurchaseOrder.query.get(order_id)
        if not order:
            return {"error": "Order not found"}, 404

        if order.status not in ("pending", "awaiting_payment"):
            return {"error": f"Order already {order.status}"}, 400

        data = request.get_json() or {}
        reason = data.get("reason", "No reason provided")

        order.status = "rejected"
        order.rejected_at = datetime.now()
        order.admin_notes = reason
        from ..services import webhooks

        webhooks.queue_order_event(order, "order.rejected")

        # A down payment collected at checkout goes back to the customer
        refund = None
        if order.down_payment_status == "paid":
            refund = refund_down_payment(order, reason)

        db.session.commit()

        message = f"Order rejected: {reason}"
        if refund == "refunded":
            message += ". The down payment is being refunded to the customer."
        elif refund == "refund_failed":
            message += (
                ". The automatic refund failed: refund the down payment manually."
            )
        elif refund == "manual_refund_required":
            message += ". The down payment was paid manually: refund it manually."
        return {"message": message, "refund_status": order.refund_status}, 200


def refund_down_payment(order, reason):
    """Refund Payment 1 of a rejected order. Doesn't commit. Returns the refund status."""
    from flask import current_app
    from ..services import paystack

    reference = order.down_payment_reference or ""
    if not (order.down_payment_method or "").startswith("paystack"):
        order.refund_status = "manual_refund_required"
        return order.refund_status
    try:
        result = paystack.refund_transaction(
            reference, reason=f"Order {order.order_id} rejected: {reason}"
        )
        order.refund_status = "refunded"
        order.refund_reference = str(result.get("id") or reference)
    except paystack.PaystackError as e:
        current_app.logger.error("Refund failed for order %s: %s", order.order_id, e)
        order.refund_status = "refund_failed"
    return order.refund_status
