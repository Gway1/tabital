# resources/merchant_document.py
from ..docs import INT, MESSAGE, STR, api_doc, obj
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.document import Document
from ..extensions import db
from datetime import datetime
import os
import uuid
from werkzeug.utils import secure_filename

# Configuration
UPLOAD_FOLDER = "uploads/merchant_documents"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_str(v):
    return v if v is not None else ""


_BANK_DETAILS = obj(
    {
        "bank_name": STR,
        "account_name": STR,
        "account_number": STR,
        "branch_name": STR,
        "swift_code": STR,
        "momo_name": STR,
        "momo_number": STR,
    }
)


class MerchantGetDocumentsResource(Resource):
    @api_doc(
        "The merchant's uploaded KYC documents",
        responses={
            200: (
                "Documents, newest first",
                obj(
                    {
                        "documents": {
                            "type": "array",
                            "items": obj(
                                {
                                    "id": INT,
                                    "document_id": STR,
                                    "document_name": STR,
                                    "document_type": STR,
                                    "status": {
                                        "type": "string",
                                        "description": "pending, verified or rejected",
                                    },
                                    "uploaded_at": STR,
                                    "verified_at": STR,
                                    "rejection_reason": {**STR, "nullable": True},
                                }
                            ),
                        }
                    }
                ),
            ),
            403: "Not a merchant",
        },
    )
    @auth_required
    def get(self):
        """Get merchant's uploaded documents"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        documents = (
            Document.query.filter_by(user_id=current_merchant.id)
            .order_by(Document.created_at.desc())
            .all()
        )

        return {
            "documents": [
                {
                    "id": d.id,
                    "document_id": d.document_id,
                    "document_name": d.document_name,
                    "document_type": d.document_type,
                    "status": d.status,
                    "uploaded_at": d.created_at.isoformat() if d.created_at else "",
                    "verified_at": d.verified_at.isoformat() if d.verified_at else "",
                    "rejection_reason": d.rejection_reason,
                }
                for d in documents
            ]
        }, 200


# resources/merchant_document.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.document import Document
from ..extensions import db
from datetime import datetime
import os
import uuid
import base64
from pathlib import Path

# Get the absolute path - fix this based on your project structure
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # Goes up to project root
UPLOAD_FOLDER = BASE_DIR / "uploads" / "merchant_documents"

# Ensure the directory exists
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_str(v):
    return v if v is not None else ""


class MerchantUploadDocumentsResource(Resource):
    @api_doc(
        "Upload the three KYC documents (business registration, tax document, bank statement)",
        description="All three files are required. Allowed types: png, jpg, jpeg, webp, pdf. Sets KYC to "
        "`pending` until an admin verifies each document.",
        body_type="multipart/form-data",
        body=obj(
            {
                "business_registration": {"type": "string", "format": "binary"},
                "tax_document": {"type": "string", "format": "binary"},
                "bank_statement": {"type": "string", "format": "binary"},
                "notes": STR,
            },
            required=["business_registration", "tax_document", "bank_statement"],
        ),
        responses={
            201: (
                "Uploaded",
                obj(
                    {
                        "message": STR,
                        "documents": {
                            "type": "array",
                            "items": obj(
                                {
                                    "id": INT,
                                    "document_id": STR,
                                    "document_name": STR,
                                    "status": STR,
                                }
                            ),
                        },
                    }
                ),
            ),
            400: "A file is missing, empty, or of a type that isn't allowed",
            403: "Not a merchant",
        },
    )
    @auth_required
    def post(self):
        """Upload merchant verification documents"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Check required files
        required_files = ["business_registration", "tax_document", "bank_statement"]
        for file_key in required_files:
            if file_key not in request.files:
                return {"error": f"Missing required file: {file_key}"}, 400
            if request.files[file_key].filename == "":
                return {"error": f"Empty file for: {file_key}"}, 400

        # Create upload directory if not exists
        UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)

        notes = request.form.get("notes", "")
        uploaded_documents = []

        # Document configurations
        doc_configs = {
            "business_registration": {
                "name": "Business Registration Certificate",
                "type": "business_registration",
            },
            "tax_document": {"name": "Tax Document (TIN/VAT)", "type": "tax_document"},
            "bank_statement": {
                "name": "Bank Account Statement/Confirmation",
                "type": "bank_statement",
            },
        }

        for file_key, config in doc_configs.items():
            file = request.files[file_key]

            # Validate file type
            if not allowed_file(file.filename):
                return {
                    "error": f"File type not allowed for {config['name']}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
                }, 400

            # Generate unique filename
            ext = file.filename.rsplit(".", 1)[1].lower()
            filename = f"{file_key}_{current_merchant.id}_{uuid.uuid4().hex}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
            filepath = UPLOAD_FOLDER / filename

            # Save file
            file.save(str(filepath))

            # Create document record
            document = Document(
                document_id=Document.generate_document_id(Document),
                user_id=current_merchant.id,
                document_type=config["type"],
                document_name=config["name"],
                file_path=str(filepath),  # Store absolute path
                file_name=filename,
                file_size=filepath.stat().st_size,
                mime_type=file.content_type,
                status="pending",
                uploaded_by=current_merchant.id,
            )
            db.session.add(document)
            uploaded_documents.append(
                {
                    "id": document.id,
                    "document_id": document.document_id,
                    "document_name": document.document_name,
                    "status": document.status,
                }
            )

        # Update merchant KYC status
        current_merchant.kyc_status = "pending"

        db.session.commit()

        # Debug: Print where files were saved
        print(f"Files saved to: {UPLOAD_FOLDER}")
        print(f"Directory exists: {UPLOAD_FOLDER.exists()}")

        return {
            "message": "Documents uploaded successfully. Verification in progress.",
            "documents": uploaded_documents,
        }, 201


class AdminGetPendingKYCResource(Resource):
    @auth_required
    def get(self):
        """Get all pending KYC/KYB verification requests"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Debug: Print the upload folder path
        print(f"Looking for files in: {UPLOAD_FOLDER}")
        print(f"Directory exists: {UPLOAD_FOLDER.exists()}")

        if UPLOAD_FOLDER.exists():
            print(f"Files in directory: {list(UPLOAD_FOLDER.iterdir())}")

        # Get all merchants with pending KYC status
        pending_merchants = User.query.filter(
            User.role == "merchant",
            User.kyc_status.in_(["pending", "submitted", "not_submitted"]),
        ).all()

        result = []
        for merchant in pending_merchants:
            # Get all documents for this merchant
            documents = (
                Document.query.filter_by(user_id=merchant.id)
                .order_by(Document.created_at.desc())
                .all()
            )

            # Check if any documents exist
            if documents:
                documents_data = []
                for doc in documents:
                    file_data = None

                    # Debug: Print file path from database
                    print(f"Document {doc.id} - DB file_path: {doc.file_path}")

                    # Check if file exists at the stored path
                    if doc.file_path and Path(doc.file_path).exists():
                        print(f"File exists at: {doc.file_path}")
                        try:
                            with open(doc.file_path, "rb") as f:
                                file_data = base64.b64encode(f.read()).decode("utf-8")
                                print(
                                    f"Successfully loaded file: {doc.file_name}, size: {len(file_data)} chars"
                                )
                        except Exception as e:
                            print(f"Error reading file: {str(e)}")
                            file_data = None
                    else:
                        print(f"File NOT found at: {doc.file_path}")
                        # Try to find the file by name in the upload folder
                        if UPLOAD_FOLDER.exists():
                            possible_file = UPLOAD_FOLDER / doc.file_name
                            if possible_file.exists():
                                print(f"Found file by name: {possible_file}")
                                try:
                                    with open(possible_file, "rb") as f:
                                        file_data = base64.b64encode(f.read()).decode(
                                            "utf-8"
                                        )
                                        print(
                                            f"Successfully loaded file from name search"
                                        )
                                        # Update the document with correct path
                                        doc.file_path = str(possible_file)
                                        db.session.commit()
                                except Exception as e:
                                    print(f"Error reading found file: {str(e)}")

                    documents_data.append(
                        {
                            "id": doc.id,
                            "document_id": doc.document_id,
                            "document_name": doc.document_name,
                            "document_type": doc.document_type,
                            "status": doc.status,
                            "uploaded_at": doc.created_at.isoformat()
                            if doc.created_at
                            else None,
                            "file_data": file_data,
                            "file_name": doc.file_name,
                            "file_size": doc.file_size,
                            "mime_type": doc.mime_type,
                            "rejection_reason": doc.rejection_reason,
                        }
                    )

                result.append(
                    {
                        "merchant_id": merchant.id,
                        "merchant_name": merchant.business_name or merchant.full_name,
                        "owner_name": merchant.owner_name,
                        "phone": merchant.phone,
                        "business_email": merchant.business_email or merchant.email,
                        "city": merchant.city,
                        "address": merchant.address,
                        "kyc_status": merchant.kyc_status,
                        "verification_level": merchant.verification_level or "basic",
                        "submitted_at": min(
                            [d.created_at for d in documents]
                        ).isoformat()
                        if documents
                        else None,
                        "documents": documents_data,
                        "bank_details": {
                            "bank_name": merchant.bank_name,
                            "account_name": merchant.account_name,
                            "account_number": merchant.account_number,
                            "branch_name": merchant.branch_name,
                            "swift_code": merchant.swift_code,
                            "momo_name": merchant.momo_name,
                            "momo_number": merchant.momo_number,
                        },
                    }
                )

        return {"pending_verifications": result, "total": len(result)}, 200


class MerchantGetKYCStatusResource(Resource):
    @api_doc(
        "Where the merchant's KYC verification stands",
        responses={
            200: (
                "KYC status",
                obj(
                    {
                        "status": {
                            "type": "string",
                            "description": "not_submitted, pending, verified or rejected",
                        },
                        "level": STR,
                        "submitted_at": {**STR, "nullable": True},
                        "verified_at": {**STR, "nullable": True},
                        "rejection_reason": {**STR, "nullable": True},
                    }
                ),
            ),
            403: "Not a merchant",
        },
    )
    @auth_required
    def get(self):
        """Get merchant's KYC verification status"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get latest KYC documents
        kyc_documents = (
            Document.query.filter_by(user_id=current_merchant.id)
            .filter(
                Document.document_type.in_(
                    ["business_registration", "tax_document", "bank_statement"]
                )
            )
            .all()
        )

        # Determine overall KYC status
        status = current_merchant.kyc_status or "not_submitted"

        # Check if any document is rejected
        rejected_docs = [d for d in kyc_documents if d.status == "rejected"]
        if rejected_docs:
            status = "rejected"
            rejection_reason = rejected_docs[0].rejection_reason
        else:
            rejection_reason = None

        # Check if all documents are verified
        all_verified = (
            all(d.status == "verified" for d in kyc_documents)
            if kyc_documents
            else False
        )
        if all_verified and len(kyc_documents) >= 3:
            status = "verified"

        return {
            "status": status,
            "level": current_merchant.verification_level or "basic",
            "submitted_at": min([d.created_at for d in kyc_documents]).isoformat()
            if kyc_documents
            else None,
            "verified_at": current_merchant.kyc_completed_on.isoformat()
            if current_merchant.kyc_completed_on
            else None,
            "rejection_reason": rejection_reason,
        }, 200


class MerchantGetBankDetailsResource(Resource):
    @api_doc(
        "The merchant's bank and Mobile Money details",
        responses={
            200: ("Details; empty strings when not set", _BANK_DETAILS),
            403: "Not a merchant",
        },
    )
    @auth_required
    def get(self):
        """Get merchant's bank details"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        return {
            "bank_name": safe_str(current_merchant.bank_name),
            "account_name": safe_str(current_merchant.account_name),
            "account_number": safe_str(current_merchant.account_number),
            "branch_name": safe_str(current_merchant.branch_name),
            "swift_code": safe_str(current_merchant.swift_code),
            "momo_name": safe_str(current_merchant.momo_name),
            "momo_number": safe_str(current_merchant.momo_number),
        }, 200


class MerchantUpdateBankDetailsResource(Resource):
    @api_doc(
        "Change the merchant's bank or Mobile Money details",
        description="A real change pauses payouts for 48 hours. Same validation as `PUT /merchant/payout-account`.",
        body=obj(
            {
                "payout_method": {"type": "string", "enum": ["mobile_money", "bank"]},
                "payout_bank_code": STR,
                "bank_name": STR,
                "account_name": STR,
                "account_number": {**STR, "description": "6-20 digits"},
                "branch_name": STR,
                "swift_code": STR,
                "momo_name": STR,
                "momo_number": {**STR, "description": "10 digits, e.g. 0241234567"},
            }
        ),
        responses={
            200: ("Saved", MESSAGE),
            400: "Details failed validation",
            403: "Not a merchant",
        },
    )
    @auth_required
    def put(self):
        """Update merchant's bank details"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        from .merchant_payouts import PayoutError, apply_payout_details

        data = request.get_json() or {}

        # Payout fields go through the validated path (a change pauses payouts for 48 hours)
        try:
            changed = apply_payout_details(current_merchant, data)
        except PayoutError as e:
            return {"error": str(e)}, e.status
        if "swift_code" in data:
            taken = (
                data["swift_code"]
                and User.query.filter(
                    User.swift_code == data["swift_code"], User.id != current_merchant.id
                ).first()
            )
            if taken:
                db.session.rollback()
                return {"error": "This SWIFT code is already linked to another account."}, 409
            current_merchant.swift_code = data["swift_code"]

        db.session.commit()

        return {
            "message": "Bank details updated. For your security, payouts are paused for 48 hours."
            if changed
            else "Bank details updated successfully"
        }, 200


class AdminGetPendingMerchantKYCResource(Resource):
    @auth_required
    def get(self):
        """Admin gets all pending merchant KYC verification requests"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get merchants with pending KYC who have uploaded documents
        pending_merchants = User.query.filter(
            User.role == "merchant", User.kyc_status == "pending"
        ).all()

        result = []
        for merchant in pending_merchants:
            documents = Document.query.filter_by(user_id=merchant.id).all()

            if documents:
                result.append(
                    {
                        "merchant_id": merchant.id,
                        "merchant_name": merchant.business_name or merchant.full_name,
                        "merchant_phone": merchant.phone,
                        "merchant_email": merchant.business_email or merchant.email,
                        "documents": [
                            {
                                "id": d.id,
                                "document_id": d.document_id,
                                "document_name": d.document_name,
                                "document_type": d.document_type,
                                "file_path": d.file_path,
                                "status": d.status,
                                "uploaded_at": d.created_at.isoformat()
                                if d.created_at
                                else "",
                            }
                            for d in documents
                        ],
                        "bank_details": {
                            "bank_name": merchant.bank_name,
                            "account_name": merchant.account_name,
                            "account_number": merchant.account_number,
                        },
                    }
                )

        return {"pending_verifications": result, "total": len(result)}, 200


class AdminVerifyMerchantDocumentResource(Resource):
    @api_doc(
        "Verify or reject one merchant KYC document (admin)",
        description="When all three documents are verified the merchant's KYC becomes `verified`; rejecting "
        "one marks it `rejected`.",
        body=obj(
            {
                "action": {"type": "string", "enum": ["verify", "reject"]},
                "rejection_reason": STR,
            },
            required=["action"],
        ),
        responses={
            200: ("Done", obj({"message": STR, "document_id": STR, "status": STR})),
            400: "Invalid action",
            403: "Not an admin",
            404: "Document not found",
        },
    )
    @auth_required
    def put(self, document_id):
        """Admin verifies or rejects a merchant document"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        document = Document.query.get(document_id)
        if not document:
            return {"error": "Document not found"}, 404

        data = request.get_json()
        action = data.get("action")  # 'verify' or 'reject'
        rejection_reason = data.get("rejection_reason", "")

        if action == "verify":
            document.status = "verified"
            document.verified_by = current_admin.id
            document.verified_at = datetime.now()

            # Check if all merchant documents are verified
            merchant_docs = (
                Document.query.filter_by(user_id=document.user_id)
                .filter(
                    Document.document_type.in_(
                        ["business_registration", "tax_document", "bank_statement"]
                    )
                )
                .all()
            )

            all_verified = (
                all(d.status == "verified" for d in merchant_docs)
                if merchant_docs
                else False
            )

            if all_verified and len(merchant_docs) >= 3:
                merchant = User.query.get(document.user_id)
                if merchant:
                    merchant.kyc_status = "verified"
                    merchant.verification_level = "verified"
                    merchant.kyc_completed_on = datetime.now()
                    db.session.commit()

        elif action == "reject":
            document.status = "rejected"
            document.rejection_reason = rejection_reason
            document.verified_by = current_admin.id
            document.verified_at = datetime.now()

            # Update merchant KYC status
            merchant = User.query.get(document.user_id)
            if merchant:
                merchant.kyc_status = "rejected"
            db.session.commit()

        else:
            return {"error": "Invalid action. Use 'verify' or 'reject'"}, 400

        db.session.commit()

        return {
            "message": f"Document {action}ed successfully",
            "document_id": document.document_id,
            "status": document.status,
        }, 200


class AdminVerifyMerchantCompleteResource(Resource):
    @api_doc(
        "Verify or reject a merchant's onboarding as a whole (admin)",
        body=obj(
            {
                "action": {"type": "string", "enum": ["verify", "reject"]},
                "rejection_reason": STR,
            },
            required=["action"],
        ),
        responses={
            200: ("Done", MESSAGE),
            400: "Invalid action",
            403: "Not an admin",
            404: "Merchant not found",
        },
    )
    @auth_required
    def put(self, merchant_id):
        """Admin verifies a merchant completely (all documents at once)"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        merchant = User.query.get(merchant_id)
        if not merchant or merchant.role != "merchant":
            return {"error": "Merchant not found"}, 404

        data = request.get_json()
        action = data.get("action")  # 'verify' or 'reject'
        rejection_reason = data.get("rejection_reason", "")

        # Get all documents for this merchant
        documents = Document.query.filter_by(user_id=merchant.id).all()

        if action == "verify":
            for doc in documents:
                doc.status = "verified"
                doc.verified_by = current_admin.id
                doc.verified_at = datetime.now()

            merchant.kyc_status = "verified"
            merchant.verification_level = "verified"
            merchant.kyc_completed_on = datetime.now()
            db.session.commit()

            return {"message": "Merchant fully verified successfully"}, 200

        elif action == "reject":
            for doc in documents:
                doc.status = "rejected"
                doc.rejection_reason = rejection_reason
                doc.verified_by = current_admin.id
                doc.verified_at = datetime.now()

            merchant.kyc_status = "rejected"
            db.session.commit()

            return {
                "message": f"Merchant verification rejected: {rejection_reason}"
            }, 200

        else:
            return {"error": "Invalid action. Use 'verify' or 'reject'"}, 400
