# models/document.py
from ..extensions import db
from datetime import datetime


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.String(50), unique=True, nullable=False, index=True)

    # This column can be merchant_id for merchants OR customer_id for customers
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    # Document details
    document_type = db.Column(db.String(50), nullable=False)
    # For merchants: business_registration, trade_license, bank_proof, tax_certificate
    # For customers: kyc_front, kyc_back, passport_photo, proof_of_address

    document_name = db.Column(db.String(200), nullable=False)
    file_path = db.Column(db.String(500))
    file_name = db.Column(db.String(200))
    file_size = db.Column(db.Integer)  # in bytes
    mime_type = db.Column(db.String(100))

    # Status
    status = db.Column(
        db.String(50), default="pending"
    )  # pending, uploaded, verified, rejected
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id"))

    # Verification
    verified_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    verified_at = db.Column(db.DateTime)
    rejection_reason = db.Column(db.Text)

    # Expiry
    expiry_date = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Relationships
    user = db.relationship("User", foreign_keys=[user_id], backref="documents")
    uploader = db.relationship(
        "User", foreign_keys=[uploaded_by], backref="uploaded_documents"
    )
    verifier = db.relationship(
        "User", foreign_keys=[verified_by], backref="verified_documents"
    )

    def generate_document_id(self):
        """Generate a document ID in format DOC001, DOC002, etc."""
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(Document.document_id, 4).cast(db.Integer))
            )
            .filter(Document.document_id.isnot(None))
            .scalar()
        )
        return f"DOC{(result + 1) if result else 1:03d}"
