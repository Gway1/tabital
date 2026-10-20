from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from app.models.user import User
from app.models.product import Product
from app.models.instalment import InstalmentPlan
from app.models.transaction import Transaction
from app.extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_
import json


class AdminProductStatsResource(Resource):
    @auth_required
    def get(self):
        """Get product statistics"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Total Products
        total_products = Product.query.count()

        # Active Products
        active_products = Product.query.filter_by(status="active").count()

        # Out of Stock Products
        out_of_stock = Product.query.filter(Product.stock_quantity <= 0).count()

        # Low Stock Products (less than 10 units)
        low_stock = Product.query.filter(Product.stock_quantity.between(1, 10)).count()

        # Featured Products
        featured_products = Product.query.filter_by(is_featured=True).count()

        # New Products (last 30 days)
        last_30_days = datetime.now() - timedelta(days=30)
        new_products = Product.query.filter(Product.created_at >= last_30_days).count()

        # Total Products Value
        total_value = (
            db.session.query(func.sum(Product.price * Product.stock_quantity)).scalar()
            or 0
        )

        return {
            "total_products": total_products,
            "active_products": active_products,
            "out_of_stock": out_of_stock,
            "low_stock": low_stock,
            "featured_products": featured_products,
            "new_products": new_products,
            "total_value": float(total_value),
        }, 200


class AdminGetProductsResource(Resource):
    @auth_required
    def get(self):
        """Get all products with filters and pagination"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        search = request.args.get("search", "", type=str)
        category = request.args.get("category", "", type=str)
        status = request.args.get("status", "", type=str)
        merchant_id = request.args.get("merchant_id", "", type=int)
        sort_by = request.args.get("sort_by", "created_at", type=str)
        sort_order = request.args.get("sort_order", "desc", type=str)

        # Build query
        query = Product.query

        # Apply search filter
        if search:
            query = query.filter(
                or_(
                    Product.name.ilike(f"%{search}%"),
                    Product.product_id.ilike(f"%{search}%"),
                    Product.brand.ilike(f"%{search}%"),
                    Product.model.ilike(f"%{search}%"),
                )
            )

        # Apply category filter
        if category:
            query = query.filter(Product.category == category)

        # Apply status filter
        if status:
            query = query.filter(Product.status == status)

        # Apply merchant filter
        if merchant_id:
            query = query.filter(Product.merchant_id == merchant_id)

        # Apply sorting
        if sort_order == "desc":
            query = query.order_by(getattr(Product, sort_by).desc())
        else:
            query = query.order_by(getattr(Product, sort_by).asc())

        # Pagination
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        products = []
        for product in paginated.items:
            merchant = User.query.get(product.merchant_id)

            # Parse gallery images
            gallery_images = []
            if product.gallery_images:
                try:
                    gallery_images = json.loads(product.gallery_images)
                except:
                    gallery_images = []

            products.append(
                {
                    "id": product.id,
                    "product_id": product.product_id,
                    "name": product.name,
                    "description": product.description,
                    "category": product.category,
                    "brand": product.brand or "N/A",
                    "model": product.model or "N/A",
                    "year": product.year,
                    "price": float(product.price),
                    "stock_quantity": product.stock_quantity,
                    "status": product.status,
                    "is_featured": product.is_featured,
                    "is_new": product.is_new,
                    "main_image": product.main_image,
                    "gallery_images": gallery_images,
                    "merchant_id": product.merchant_id,
                    "merchant_name": merchant.business_name if merchant else "N/A",
                    "created_at": product.created_at.isoformat()
                    if product.created_at
                    else None,
                }
            )

        return {
            "products": products,
            "total": paginated.total,
            "page": page,
            "per_page": per_page,
            "total_pages": paginated.pages if paginated.pages > 0 else 1,
        }, 200


class AdminGetProductDetailResource(Resource):
    @auth_required
    def get(self, product_id):
        """Get detailed product information"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        product = Product.query.get(product_id)
        if not product:
            return {"error": "Product not found"}, 404

        merchant = User.query.get(product.merchant_id)

        # Get related instalment plans for this product
        instalment_plans = InstalmentPlan.query.filter_by(
            transaction_id=product.id
        ).all()

        # Parse gallery images
        gallery_images = []
        if product.gallery_images:
            try:
                gallery_images = json.loads(product.gallery_images)
            except:
                gallery_images = []

        return {
            "product": {
                "id": product.id,
                "product_id": product.product_id,
                "name": product.name,
                "description": product.description,
                "category": product.category,
                "brand": product.brand,
                "model": product.model,
                "year": product.year,
                "price": float(product.price),
                "stock_quantity": product.stock_quantity,
                "sku": product.sku,
                "barcode": product.barcode,
                "status": product.status,
                "is_featured": product.is_featured,
                "is_new": product.is_new,
                "main_image": product.main_image,
                "gallery_images": gallery_images,
                "created_at": product.created_at.isoformat()
                if product.created_at
                else None,
                "updated_at": product.updated_at.isoformat()
                if product.updated_at
                else None,
            },
            "merchant": {
                "id": merchant.id if merchant else None,
                "business_name": merchant.business_name if merchant else "N/A",
                "owner_name": merchant.owner_name if merchant else "N/A",
                "phone": merchant.phone if merchant else "N/A",
                "email": merchant.business_email or merchant.email
                if merchant
                else "N/A",
            },
            "instalment_plans": [
                {
                    "id": p.id,
                    "plan_id": p.plan_id,
                    "total_amount": float(p.total_amount),
                    "remaining_amount": p.outstanding_balance,
                    "number_of_installments": p.number_of_installments,
                    "installment_amount": float(p.installment_amount),
                    "paid_installments": p.paid_installments,
                    "status": p.status,
                }
                for p in instalment_plans
            ],
        }, 200


class AdminUpdateProductStatusResource(Resource):
    @auth_required
    def put(self, product_id):
        """Update product status (active, inactive, out_of_stock)"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        new_status = data.get("status")
        reason = data.get("reason", "")

        if new_status not in ["active", "inactive", "out_of_stock"]:
            return {"error": "Invalid status"}, 400

        product = Product.query.get(product_id)
        if not product:
            return {"error": "Product not found"}, 404

        old_status = product.status
        product.status = new_status

        db.session.commit()

        return {
            "message": f"Product status updated from {old_status} to {new_status}",
            "product_id": product.product_id,
            "status": new_status,
        }, 200


class AdminUpdateProductFeaturedResource(Resource):
    @auth_required
    def put(self, product_id):
        """Toggle product featured status"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        is_featured = data.get("is_featured", False)

        product = Product.query.get(product_id)
        if not product:
            return {"error": "Product not found"}, 404

        product.is_featured = is_featured

        db.session.commit()

        return {
            "message": f"Product featured status updated to {is_featured}",
            "product_id": product.product_id,
            "is_featured": is_featured,
        }, 200


class AdminUpdateProductStockResource(Resource):
    @auth_required
    def put(self, product_id):
        """Update product stock quantity"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        stock_quantity = data.get("stock_quantity", 0)
        reason = data.get("reason", "")

        if stock_quantity < 0:
            return {"error": "Stock quantity cannot be negative"}, 400

        product = Product.query.get(product_id)
        if not product:
            return {"error": "Product not found"}, 404

        old_stock = product.stock_quantity
        product.stock_quantity = stock_quantity

        # Update status based on stock
        if stock_quantity <= 0:
            product.status = "out_of_stock"
        elif product.status == "out_of_stock":
            product.status = "active"

        db.session.commit()

        return {
            "message": f"Stock updated from {old_stock} to {stock_quantity}",
            "product_id": product.product_id,
            "stock_quantity": stock_quantity,
            "status": product.status,
        }, 200


class AdminExportProductsResource(Resource):
    @auth_required
    def get(self):
        """Export products to CSV"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        category = request.args.get("category", "", type=str)
        status = request.args.get("status", "", type=str)

        # Build query
        query = Product.query

        if category:
            query = query.filter(Product.category == category)

        if status:
            query = query.filter(Product.status == status)

        products = query.all()

        # Create CSV content
        import csv
        from io import StringIO
        from flask import make_response

        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(
            [
                "Product ID",
                "Name",
                "Category",
                "Brand",
                "Model",
                "Price",
                "Stock",
                "Status",
                "Featured",
                "Merchant",
                "Created At",
            ]
        )

        # Write data
        for product in products:
            merchant = User.query.get(product.merchant_id)

            writer.writerow(
                [
                    product.product_id,
                    product.name,
                    product.category,
                    product.brand or "N/A",
                    product.model or "N/A",
                    product.price,
                    product.stock_quantity,
                    product.status,
                    "Yes" if product.is_featured else "No",
                    merchant.business_name if merchant else "N/A",
                    product.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    if product.created_at
                    else "",
                ]
            )

        output.seek(0)
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers[
            "Content-Disposition"
        ] = f'attachment; filename=products_{datetime.now().strftime("%Y%m%d")}.csv'
        return response
