# resources/product.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.product import Product
from ..extensions import db
from datetime import datetime
import json
import re


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class MerchantProductsResource(Resource):
    @auth_required
    def get(self):
        """Get merchant's products"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 20, type=int)
        status = request.args.get("status", "")
        search = request.args.get("search", "")
        category = request.args.get("category", "")

        query = Product.query.filter_by(merchant_id=current_merchant.id)

        if status:
            query = query.filter(Product.status == status)
        if search:
            query = query.filter(Product.name.ilike(f"%{search}%"))
        if category:
            query = query.filter(Product.category == category)

        total = query.count()
        products = (
            query.order_by(Product.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        return {
            "products": [
                {
                    "id": p.id,
                    "product_id": p.product_id,
                    "name": p.name,
                    "description": p.description[:200] if p.description else "",
                    "category": p.category,
                    "brand": p.brand,
                    "model": p.model,
                    "year": p.year,
                    "price": p.price,
                    "stock_quantity": p.stock_quantity,
                    "sku": p.sku,
                    "barcode": p.barcode,
                    "main_image": p.main_image,  # Base64 string
                    "gallery_images": json.loads(p.gallery_images)
                    if p.gallery_images
                    else [],
                    "status": p.status,
                    "is_featured": p.is_featured,
                    "is_new": p.is_new,
                    "created_at": p.created_at.isoformat() if p.created_at else "",
                }
                for p in products
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }, 200

    @auth_required
    def post(self):
        """Create a new product with Base64 images"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # Validate required fields
        required_fields = ["name", "price", "category", "description"]
        for field in required_fields:
            if not data.get(field):
                return {"error": f"{field} is required"}, 400

        # Create product
        product = Product(
            product_id=Product.generate_product_id(Product),
            merchant_id=current_merchant.id,
            name=data["name"],
            description=data.get("description", ""),
            category=data.get("category", ""),
            brand=data.get("brand", ""),
            model=data.get("model", ""),
            year=data.get("year"),
            price=float(data["price"]),
            stock_quantity=int(data.get("stock_quantity", 0)),
            sku=data.get("sku", ""),
            barcode=data.get("barcode", ""),
            main_image=data.get("main_image", ""),  # Base64 string
            gallery_images=json.dumps(data.get("gallery_images", []))
            if data.get("gallery_images")
            else None,
            status=data.get("status", "active"),
            is_featured=data.get("is_featured", False),
            is_new=data.get("is_new", False),
        )

        db.session.add(product)
        db.session.commit()

        return {
            "message": "Product created successfully",
            "product": {
                "id": product.id,
                "product_id": product.product_id,
                "name": product.name,
            },
        }, 201


class MerchantProductDetailResource(Resource):
    @auth_required
    def get(self, product_id):
        """Get single product details"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        product = Product.query.filter_by(
            id=product_id, merchant_id=current_merchant.id
        ).first()

        if not product:
            return {"error": "Product not found"}, 404

        return {
            "id": product.id,
            "product_id": product.product_id,
            "name": product.name,
            "description": product.description,
            "category": product.category,
            "brand": product.brand,
            "model": product.model,
            "year": product.year,
            "price": product.price,
            "stock_quantity": product.stock_quantity,
            "sku": product.sku,
            "barcode": product.barcode,
            "main_image": product.main_image,
            "gallery_images": json.loads(product.gallery_images)
            if product.gallery_images
            else [],
            "status": product.status,
            "is_featured": product.is_featured,
            "is_new": product.is_new,
            "created_at": product.created_at.isoformat() if product.created_at else "",
            "updated_at": product.updated_at.isoformat() if product.updated_at else "",
        }, 200

    @auth_required
    def put(self, product_id):
        """Update product with Base64 images"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        product = Product.query.filter_by(
            id=product_id, merchant_id=current_merchant.id
        ).first()

        if not product:
            return {"error": "Product not found"}, 404

        data = request.get_json()

        # Update fields
        updatable_fields = [
            "name",
            "description",
            "category",
            "brand",
            "model",
            "year",
            "price",
            "stock_quantity",
            "sku",
            "barcode",
            "main_image",
            "status",
            "is_featured",
            "is_new",
        ]

        for field in updatable_fields:
            if field in data:
                if field == "price":
                    setattr(product, field, float(data[field]))
                elif field in ["stock_quantity", "year"]:
                    setattr(product, field, int(data[field]))
                else:
                    setattr(product, field, data[field])

        if "gallery_images" in data:
            product.gallery_images = json.dumps(data["gallery_images"])

        db.session.commit()

        return {"message": "Product updated successfully"}, 200

    @auth_required
    def delete(self, product_id):
        """Delete product"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        product = Product.query.filter_by(
            id=product_id, merchant_id=current_merchant.id
        ).first()

        if not product:
            return {"error": "Product not found"}, 404

        db.session.delete(product)
        db.session.commit()

        return {"message": "Product deleted successfully"}, 200
