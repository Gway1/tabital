# resources/customer_products.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.product import Product
from ..models.user import User
from ..extensions import db
from datetime import datetime
import json


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class CustomerGetProductsResource(Resource):
    @auth_required
    def get(self):
        """Get all available products for customers to purchase"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 20, type=int)
        search = request.args.get("search", "").strip()
        category = request.args.get("category", "").strip()
        status = request.args.get("status", "active")
        min_price = request.args.get("min_price", 0, type=float)
        max_price = request.args.get("max_price", 100000, type=float)

        # Build query - only show active products with stock > 0
        query = Product.query.filter(
            Product.status == "active", Product.stock_quantity > 0
        )

        # Apply filters
        if search:
            query = query.filter(
                db.or_(
                    Product.name.ilike(f"%{search}%"),
                    Product.description.ilike(f"%{search}%"),
                    Product.brand.ilike(f"%{search}%"),
                    Product.model.ilike(f"%{search}%"),
                )
            )

        if category:
            query = query.filter(Product.category == category)

        if min_price > 0:
            query = query.filter(Product.price >= min_price)

        if max_price < 100000:
            query = query.filter(Product.price <= max_price)

        # Get total count
        total = query.count()

        # Get paginated results
        products = (
            query.order_by(Product.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        # Pay in 4 preview on every card ("GHS X today, then 3 x GHS Y"), priced by the server with
        # this customer's own down payment rate (Vault shop, §12: no amounts calculated in the UI)
        tier_rate = _customer_dp_rate(current_customer)

        # Get merchant names for each product
        result = []
        for product in products:
            merchant = User.query.get(product.merchant_id)
            result.append(
                {
                    "split_preview": _split_preview(product.price, tier_rate),
                    "id": product.id,
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "category": product.category,
                    "brand": product.brand,
                    "model": product.model,
                    "year": product.year,
                    "price": safe_float(product.price),
                    "stock_quantity": safe_int(product.stock_quantity),
                    "main_image": product.main_image,
                    "gallery_images": json.loads(product.gallery_images)
                    if product.gallery_images
                    else [],
                    "merchant_id": product.merchant_id,
                    "merchant_name": safe_str(
                        merchant.business_name or merchant.full_name or merchant.phone
                    ),
                    "status": product.status,
                    "created_at": product.created_at.isoformat()
                    if product.created_at
                    else "",
                }
            )

        return {
            "products": result,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }, 200


def _customer_dp_rate(customer):
    """The customer's Pay in 4 down payment rate, or None for the configured default."""
    from ..services import risk

    try:
        decision, _ = risk.decide(customer)
        return decision.pay_in_4_dp_rate if decision.eligible else None
    except Exception:  # noqa: BLE001 (a preview must never break the shop)
        return None


def _split_preview(price, tier_rate):
    from ..models.system_settings import SystemSetting
    from ..services import plan_engine

    try:
        plan = plan_engine.quote(
            price, 1, 4, SystemSetting.get_value, pay_in_4_dp_rate=tier_rate
        )
    except plan_engine.PlanError:
        return None
    return {
        "plan": "Pay in 4",
        "down_payment": float(plan["down_payment"]),
        "installments": plan["deferred_payments"],
        "installment_amount": float(plan["installment_amount"]),
        "delivery_fee": float(plan["delivery_fee"]),
    }


class CustomerPlanOptionsResource(Resource):
    @auth_required
    def post(self):
        """Every standard plan for one product, priced by the server, to show side by side.

        Each option has what's due today (down payment + delivery fee), the monthly amount, the
        full schedule and the total payable, plus the customer's credit check for that plan.
        """
        from ..models.system_settings import SystemSetting
        from ..services import plan_engine, risk
        from .system_settings import _key_facts

        customer = current_user()
        if customer.role != "customer":
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        product = Product.query.get(data.get("product_id"))
        if not product or product.status != "active":
            return {"error": "Product not available"}, 404
        try:
            quantity = int(data.get("quantity", 1))
        except (TypeError, ValueError):
            return {"error": "quantity must be a number"}, 400
        if quantity < 1 or quantity > (product.stock_quantity or 0):
            return {"error": "Requested quantity is not in stock"}, 400

        decision, _ = risk.decide(customer)
        credit = risk.decision_view(decision)
        tier_rate = decision.pay_in_4_dp_rate if decision.eligible else None
        options = []
        for n in plan_engine.SUPPORTED_PLANS:
            plan = plan_engine.quote(
                product.price,
                quantity,
                n,
                SystemSetting.get_value,
                pay_in_4_dp_rate=tier_rate,
                in_store=bool(data.get("in_store")),
            )
            price = float(plan["price"])
            if n == 1:
                blocked = None
            elif not decision.eligible:
                blocked = (
                    decision.reasons or ["You're not eligible for a payment plan yet"]
                )[0]
            elif price > float(credit.get("available_limit") or 0):
                blocked = "This is above your available limit"
            else:
                blocked = None
            options.append(
                {
                    "n_payments": n,
                    "label": "Pay in full" if n == 1 else f"Pay in {n}",
                    "due_now": float(plan["due_now"]),
                    "down_payment": float(plan["down_payment"]),
                    "down_payment_percentage": float(plan["down_payment_rate"] * 100),
                    "delivery_fee": float(plan["delivery_fee"]),
                    "installments": plan["deferred_payments"],
                    "installment_amount": float(plan["installment_amount"]),
                    "total_payable": float(plan["total_payable"]),
                    "payment_schedule": plan_engine.schedule_to_json(plan["schedule"]),
                    "available": blocked is None,
                    "blocked_reason": blocked,
                }
            )
        return {
            "product_id": product.id,
            "quantity": quantity,
            "product_price": float(plan_engine.money(product.price) * quantity),
            "options": options,
            "credit": credit,
            "key_facts": _key_facts(
                float(SystemSetting.get_value("late_fee_percentage", 10))
            ),
        }, 200


class CustomerGetProductDetailsResource(Resource):
    @auth_required
    def get(self, product_id):
        """Get single product details for customer"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        product = Product.query.get(product_id)

        if not product:
            return {"error": "Product not found"}, 404

        if product.status != "active":
            return {"error": "Product is not available"}, 400

        merchant = User.query.get(product.merchant_id)

        return {
            "id": product.id,
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "brand": product.brand,
            "model": product.model,
            "year": product.year,
            "price": safe_float(product.price),
            "stock_quantity": safe_int(product.stock_quantity),
            "main_image": product.main_image,
            "gallery_images": json.loads(product.gallery_images)
            if product.gallery_images
            else [],
            "merchant_id": product.merchant_id,
            "merchant_name": safe_str(
                merchant.business_name or merchant.full_name or merchant.phone
            ),
            "created_at": product.created_at.isoformat() if product.created_at else "",
        }, 200


class CustomerGetProductCategoriesResource(Resource):
    @auth_required
    def get(self):
        """Get all product categories with counts"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        from sqlalchemy import func

        categories = (
            db.session.query(Product.category, func.count(Product.id).label("count"))
            .filter(Product.status == "active", Product.stock_quantity > 0)
            .group_by(Product.category)
            .all()
        )

        return {
            "categories": [
                {"name": c[0] if c[0] else "Uncategorized", "count": c[1]}
                for c in categories
                if c[0]
            ]
        }, 200
