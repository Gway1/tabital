# models/product.py
from ..extensions import db
from datetime import datetime
import sqlalchemy as sa


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    merchant_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Basic Information
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    category = db.Column(db.String(100))
    brand = db.Column(db.String(100))
    model = db.Column(db.String(100))
    year = db.Column(db.Integer)

    # Pricing
    price = db.Column(db.Float, nullable=False)

    # Inventory
    stock_quantity = db.Column(db.Integer, default=0)
    sku = db.Column(db.String(100), unique=True)
    barcode = db.Column(db.String(100))

    # Images - Store Base64 strings
    main_image = db.Column(db.Text)  # Base64 string
    gallery_images = db.Column(db.Text)  # JSON array of Base64 strings

    # Status
    status = db.Column(db.String(50), default="active")
    is_featured = db.Column(db.Boolean, default=False)
    is_new = db.Column(db.Boolean, default=False)

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Relationships
    merchant = db.relationship("User", backref="products")

    def generate_product_id(self):
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(Product.product_id, 4).cast(sa.Integer))
            )
            .filter(Product.product_id.isnot(None))
            .scalar()
        )
        return f"PRD{(result + 1) if result else 1:04d}"
