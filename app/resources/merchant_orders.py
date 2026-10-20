# resources/merchant_orders.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.purchase_order import PurchaseOrder
from ..models.transaction import Transaction
from ..extensions import db
from datetime import datetime


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def _payout(order):
    """What the merchant gets for this order and where the money is (Vault orders screen).

    From the ledger once the order is approved; before that an estimate at today's MDR.
    """
    from decimal import Decimal
    from ..models.instalment import InstalmentPlan
    from ..models.ledger import LedgerEntry
    from ..models.settlement import Settlement, SettlementLine
    from ..models.system_settings import SystemSetting
    from ..services import ledger

    product_total = Decimal(str(order.product_price or 0)) * (order.quantity or 1)
    plan = (
        InstalmentPlan.query.filter_by(transaction_id=order.transaction_id).first()
        if order.transaction_id
        else None
    )
    fee = payable = None
    if plan:
        rows = dict(
            db.session.query(
                LedgerEntry.entry_type, db.func.sum(LedgerEntry.amount_pesewas)
            )
            .filter(
                LedgerEntry.plan_id == plan.id,
                LedgerEntry.entry_type.in_(
                    [LedgerEntry.MERCHANT_FEE, LedgerEntry.MERCHANT_PAYABLE]
                ),
            )
            .group_by(LedgerEntry.entry_type)
            .all()
        )
        if rows:
            fee = ledger.to_cedis(rows.get(LedgerEntry.MERCHANT_FEE, 0))
            payable = ledger.to_cedis(rows.get(LedgerEntry.MERCHANT_PAYABLE, 0))
    estimated = payable is None
    if estimated:
        from ..services import merchant_fees

        mdr = merchant_fees.rate_for(
            order.merchant
        )  # the merchant's tier; fixed on approval
        fee = (product_total * mdr).quantize(Decimal("0.01"))
        payable = product_total - fee

    payout_status = (
        "after_approval"
        if order.status in ("awaiting_payment", "pending")
        else "after_delivery"
    )
    if order.status in ("rejected", "cancelled"):
        payout_status = "none"
    if plan:
        line = SettlementLine.query.filter_by(
            plan_id=plan.id, line_type=SettlementLine.SALE
        ).first()
        if line and line.settlement_id:
            batch = Settlement.query.get(line.settlement_id)
            payout_status = (
                "paid" if batch and batch.status == Settlement.PAID else "in_settlement"
            )
        elif line:
            payout_status = "next_settlement"
    return {
        "product_total": float(product_total),
        "merchant_fee": float(fee),
        "merchant_payout": float(payable),
        "payout_estimated": estimated,
        "payout_status": payout_status,
    }


class MerchantGetOrdersResource(Resource):
    @auth_required
    def get(self):
        """Get orders for merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        status = request.args.get("status", "")
        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 20, type=int)

        query = PurchaseOrder.query.filter_by(merchant_id=current_merchant.id)

        if status:
            query = query.filter(PurchaseOrder.status == status)
        search = (request.args.get("search") or "").strip()
        if search:
            from ..models.user import User

            like = f"%{search}%"
            query = query.join(User, User.id == PurchaseOrder.customer_id).filter(
                db.or_(
                    PurchaseOrder.order_id.ilike(like),
                    PurchaseOrder.product_name.ilike(like),
                    User.full_name.ilike(like),
                    User.phone.ilike(like),
                )
            )

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
                    "product_name": o.product_name,
                    "product_price": o.product_price,
                    "quantity": o.quantity,
                    "total_payable": o.total_payable,
                    "down_payment_amount": o.down_payment_amount,
                    "installment_amount": o.installment_amount,
                    "number_of_installments": o.number_of_installments,
                    "status": o.status,
                    "created_at": o.created_at.isoformat() if o.created_at else "",
                    "delivery_address": o.delivery_address,
                    "delivery_status": o.delivery_status,
                    **_payout(o),
                }
                for o in orders
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
            # Counts across all of this merchant's orders, whatever tab is shown
            "counts": dict(
                db.session.query(PurchaseOrder.status, db.func.count(PurchaseOrder.id))
                .filter(PurchaseOrder.merchant_id == current_merchant.id)
                .group_by(PurchaseOrder.status)
                .all()
            ),
        }


class MerchantUpdateDeliveryStatusResource(Resource):
    @auth_required
    def put(self, order_id):
        """Merchant updates delivery status of approved order"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        order = PurchaseOrder.query.filter_by(
            id=order_id, merchant_id=current_merchant.id
        ).first()

        if not order:
            return {"error": "Order not found"}, 404

        if order.status != "approved":
            return {"error": "Only approved orders can be updated"}, 400

        data = request.get_json()
        delivery_status = data.get("delivery_status")

        if delivery_status:
            order.delivery_status = delivery_status

            if delivery_status == "delivered":
                from ..models.instalment import InstalmentPlan
                from ..services import settlements

                order.status = "completed"
                order.completed_at = datetime.now()

                # The order's own transaction (older orders fall back to the old match)
                transaction = (
                    Transaction.query.get(order.transaction_id)
                    if order.transaction_id
                    else Transaction.query.filter_by(
                        customer_id=order.customer_id,
                        merchant_id=order.merchant_id,
                        product_name=order.product_name,
                    ).first()
                )

                if transaction:
                    transaction.status = "completed"
                    transaction.payment_status = "completed"
                    transaction.completion_date = datetime.now()
                    # Delivered: the merchant's share goes into their next settlement (Phase 5)
                    plan = InstalmentPlan.query.filter_by(
                        transaction_id=transaction.id
                    ).first()
                    if plan and plan.status not in ("cancelled",):
                        settlements.record_delivery(plan, transaction)

        db.session.commit()

        return {"message": f"Delivery status updated to {delivery_status}"}, 200
