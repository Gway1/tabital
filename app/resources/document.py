from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.document import Document
from ..extensions import db
from datetime import datetime
import os


def safe_str(value):
    return value if value is not None else ""


class GetMerchantDocumentsResource(Resource):
    @auth_required
    def get(self, merchant_id):
        """Get all documents for a merchant"""
        current_user_obj = current_user()

        if current_user_obj.role != "admin":
            return {"error": "Unauthorized"}, 403

        merchant = User.query.get(merchant_id)
        if not merchant or merchant.role != "merchant":
            return {"error": "Merchant not found"}, 404

        # merchant_id in the URL is the merchant's users.id; documents are owned through user_id
        documents = Document.query.filter_by(user_id=merchant_id).all()

        return [
            {
                "id": d.id,
                "document_id": safe_str(d.document_id),
                "document_type": safe_str(d.document_type),
                "document_name": safe_str(d.document_name),
                "file_name": safe_str(d.file_name),
                "file_size": d.file_size,
                "mime_type": safe_str(d.mime_type),
                "status": safe_str(d.status),
                "uploaded_at": d.created_at.isoformat() if d.created_at else "",
                "verified_at": d.verified_at.isoformat() if d.verified_at else "",
                "rejection_reason": safe_str(d.rejection_reason),
                "expiry_date": d.expiry_date.isoformat() if d.expiry_date else "",
            }
            for d in documents
        ]


class UploadDocumentResource(Resource):
    @auth_required
    def post(self, merchant_id):
        """Upload a document for a merchant"""
        current_user_obj = current_user()

        if current_user_obj.role != "admin":
            return {"error": "Unauthorized"}, 403

        merchant = User.query.get(merchant_id)
        if not merchant or merchant.role != "merchant":
            return {"error": "Merchant not found"}, 404

        data = request.get_json()

        required_fields = ["document_type", "document_name"]
        for field in required_fields:
            if field not in data:
                return {"error": f"{field} is required"}, 400

        document = Document(
            user_id=merchant_id,
            document_type=data["document_type"],
            document_name=data["document_name"],
            file_name=data.get("file_name", ""),
            file_size=data.get("file_size", 0),
            mime_type=data.get("mime_type", ""),
            status="uploaded",
            uploaded_by=current_user_obj.id,
        )

        document.document_id = document.generate_document_id()

        db.session.add(document)
        db.session.commit()

        return {
            "message": "Document uploaded successfully",
            "document_id": document.document_id,
            "id": document.id,
        }, 201


class VerifyDocumentResource(Resource):
    @auth_required
    def put(self, document_id):
        """Verify a document"""
        current_user_obj = current_user()

        if current_user_obj.role != "admin":
            return {"error": "Unauthorized"}, 403

        document = Document.query.get(document_id)
        if not document:
            return {"error": "Document not found"}, 404

        data = request.get_json()

        # Parse the date first: a bad one is refused before anything on the document changes
        expiry_date = None
        if data.get("expiry_date"):
            try:
                expiry_date = datetime.fromisoformat(str(data["expiry_date"]))
            except ValueError:
                return {"error": "expiry_date must be a date like 2027-06-30"}, 400

        if "status" in data:
            document.status = data["status"]

        if data.get("status") == "verified":
            document.verified_by = current_user_obj.id
            document.verified_at = datetime.utcnow()

        if "rejection_reason" in data:
            document.rejection_reason = data["rejection_reason"]

        if "expiry_date" in data:
            document.expiry_date = expiry_date      # an empty value clears it

        db.session.commit()

        return {
            "message": f"Document {document.status} successfully",
            "document_id": document.document_id,
            "status": document.status,
        }, 200


class DeleteDocumentResource(Resource):
    @auth_required
    def delete(self, document_id):
        """Delete a document"""
        current_user_obj = current_user()

        if current_user_obj.role != "admin":
            return {"error": "Unauthorized"}, 403

        document = Document.query.get(document_id)
        if not document:
            return {"error": "Document not found"}, 404

        db.session.delete(document)
        db.session.commit()

        return {"message": "Document deleted successfully"}, 200
