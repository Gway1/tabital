# resources/customer_document.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.document import Document
from ..extensions import db
from datetime import datetime
import json
import os
import uuid
import base64
from pathlib import Path

# ============================================
# CONFIGURATION
# ============================================

# Get absolute base directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Define upload folders with absolute paths
UPLOAD_FOLDER = BASE_DIR / "uploads" / "kyc"
ALT_UPLOAD_FOLDER = Path("/app/uploads/kyc")
CWD_UPLOAD_FOLDER = Path.cwd() / "uploads" / "kyc"

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "pdf"}
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def safe_str(v):
    return v if v is not None else ""


def find_file_and_load_base64(file_name):
    """Helper function to find and load file from multiple possible locations"""
    if not file_name:
        return None

    # List of possible paths to check
    possible_paths = [
        str(UPLOAD_FOLDER / file_name),  # Absolute path: BASE_DIR/uploads/kyc/filename
        str(ALT_UPLOAD_FOLDER / file_name),  # Docker path
        str(CWD_UPLOAD_FOLDER / file_name),  # Current working directory path
        f"uploads/kyc/{file_name}",  # Relative path
        f"/app/uploads/kyc/{file_name}",  # Docker absolute path
        file_name,  # Just the filename (if in current directory)
    ]

    # Also check if file_path is stored in DB
    for path in possible_paths:
        if path and os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    file_data = base64.b64encode(f.read()).decode("utf-8")
                    print(
                        f"✅ Loaded file: {file_name} from {path}, size: {len(file_data)} chars"
                    )
                    return file_data
            except Exception as e:
                print(f"Error reading {path}: {str(e)}")
                continue

    print(f"❌ File not found: {file_name}")
    return None


def create_upload_directory():
    """Create upload directory if it doesn't exist"""
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)


# ============================================
# CUSTOMER RESOURCES
# ============================================


class CustomerGetDocumentsResource(Resource):
    @auth_required
    def get(self):
        """Get customer's uploaded documents"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        documents = (
            Document.query.filter_by(user_id=current_customer.id)
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


class CustomerUploadDocumentsResource(Resource):
    @auth_required
    def post(self):
        """Upload KYC documents (Ghana Card, Salary Certificate, Bank Statement)"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Create upload directory
        create_upload_directory()

        # Required files
        required_files = [
            "front_image",
            "back_image",
            "salary_certificate",
            "bank_statement",
        ]
        for file_key in required_files:
            if file_key not in request.files:
                return {"error": f"Missing required file: {file_key}"}, 400
            file = request.files[file_key]
            if file.filename == "":
                return {"error": f"Empty file for: {file_key}"}, 400

        uploaded_documents = []

        # Document configurations
        doc_configs = {
            "front_image": {"name": "Ghana Card (Front)", "type": "kyc_front"},
            "back_image": {"name": "Ghana Card (Back)", "type": "kyc_back"},
            "salary_certificate": {
                "name": "Salary Certificate",
                "type": "salary_certificate",
            },
            "bank_statement": {
                "name": "3 Months Bank Statement",
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
            customer_id = current_customer.customer_id or f"C{current_customer.id:03d}"
            filename = f"{file_key}_{customer_id}_{uuid.uuid4().hex}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
            filepath = str(UPLOAD_FOLDER / filename)

            # Save file
            file.save(filepath)

            # Create document record
            document = Document(
                document_id=Document.generate_document_id(Document),
                user_id=current_customer.id,
                document_type=config["type"],
                document_name=config["name"],
                file_path=filepath,
                file_name=filename,
                file_size=os.path.getsize(filepath),
                mime_type=file.content_type,
                status="pending",
                uploaded_by=current_customer.id,
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

        # Update customer KYC status
        current_customer.kyc_status = "pending"

        db.session.commit()

        return {
            "message": "Documents uploaded successfully. Verification in progress.",
            "documents": uploaded_documents,
        }, 201


class CustomerUploadOptionalDocumentResource(Resource):
    @auth_required
    def post(self):
        """Upload optional KYC document"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        if "document" not in request.files:
            return {"error": "No file provided"}, 400

        file = request.files["document"]
        document_type = request.form.get("document_type", "")
        document_name = request.form.get("document_name", "Optional Document")

        if file.filename == "":
            return {"error": "No file selected"}, 400

        if not allowed_file(file.filename):
            return {
                "error": f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
            }, 400

        create_upload_directory()

        ext = file.filename.rsplit(".", 1)[1].lower()
        customer_id = current_customer.customer_id or f"C{current_customer.id:03d}"
        filename = f"{document_type}_{customer_id}_{uuid.uuid4().hex}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
        filepath = str(UPLOAD_FOLDER / filename)
        file.save(filepath)

        document = Document(
            document_id=Document.generate_document_id(Document),
            user_id=current_customer.id,
            document_type=document_type,
            document_name=document_name,
            file_path=filepath,
            file_name=filename,
            file_size=os.path.getsize(filepath),
            mime_type=file.content_type,
            status="pending",
            uploaded_by=current_customer.id,
        )
        db.session.add(document)
        db.session.commit()

        return {
            "message": f"{document_name} uploaded successfully",
            "document": {
                "id": document.id,
                "document_id": document.document_id,
                "document_name": document.document_name,
                "status": document.status,
            },
        }, 201


class CustomerGetKYCStatusResource(Resource):
    @auth_required
    def get(self):
        """Get customer's KYC verification status"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Get all KYC documents
        kyc_documents = (
            Document.query.filter_by(user_id=current_customer.id)
            .filter(
                Document.document_type.in_(
                    ["kyc_front", "kyc_back", "salary_certificate", "bank_statement"]
                )
            )
            .all()
        )

        # Determine overall KYC status
        status = current_customer.kyc_status or "not_submitted"

        # Check if any document is rejected
        rejected_docs = [d for d in kyc_documents if d.status == "rejected"]
        if rejected_docs:
            status = "rejected"
            rejection_reason = rejected_docs[0].rejection_reason
        else:
            rejection_reason = None

        # Check if all required documents are verified
        required_types = [
            "kyc_front",
            "kyc_back",
            "salary_certificate",
            "bank_statement",
        ]
        existing_types = [d.document_type for d in kyc_documents]
        all_required_present = all(t in existing_types for t in required_types)

        if all_required_present and all(
            d.status == "verified"
            for d in kyc_documents
            if d.document_type in required_types
        ):
            status = "verified"

        # Calculate submitted_at
        submitted_at = None
        if kyc_documents:
            earliest_date = min(
                [d.created_at for d in kyc_documents if d.created_at], default=None
            )
            submitted_at = earliest_date.isoformat() if earliest_date else None

        return {
            "status": status,
            "level": current_customer.verification_level or "basic",
            "submitted_at": submitted_at,
            "verified_at": current_customer.kyc_completed_on.isoformat()
            if current_customer.kyc_completed_on
            else None,
            "rejection_reason": rejection_reason,
        }, 200


# ============================================
# ADMIN KYC MANAGEMENT RESOURCES
# ============================================


class AdminGetPendingCustomerKYCResource(Resource):
    @auth_required
    def get(self):
        """Admin gets all pending customer KYC verification requests"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get customers with pending KYC
        pending_customers = User.query.filter(
            User.role == "customer", User.kyc_status == "pending"
        ).all()

        result = []
        for customer in pending_customers:
            # Get all documents for this customer
            documents = (
                Document.query.filter_by(user_id=customer.id)
                .order_by(Document.created_at.desc())
                .all()
            )

            if documents:
                documents_data = []
                for doc in documents:
                    # Load file data using helper function
                    file_data = find_file_and_load_base64(doc.file_name)

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
                            "verified_at": doc.verified_at.isoformat()
                            if doc.verified_at
                            else None,
                        }
                    )

                # Calculate submitted_at
                submitted_at = None
                if documents:
                    earliest_date = min(
                        [d.created_at for d in documents if d.created_at], default=None
                    )
                    submitted_at = earliest_date.isoformat() if earliest_date else None

                result.append(
                    {
                        "customer_id": customer.id,
                        "customer_name": customer.full_name
                        or customer.business_name
                        or f"Customer {customer.id}",
                        "phone": customer.phone or "N/A",
                        "email": customer.business_email or customer.email or "N/A",
                        "kyc_status": customer.kyc_status,
                        "verification_level": customer.verification_level or "basic",
                        "submitted_at": submitted_at,
                        "documents": documents_data,
                    }
                )

        return {"pending_verifications": result, "total": len(result)}, 200


class AdminGetVerifiedCustomerKYCResource(Resource):
    @auth_required
    def get(self):
        """Admin gets all verified customer KYC"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        verified_customers = User.query.filter(
            User.role == "customer", User.kyc_status == "verified"
        ).all()

        result = []
        for customer in verified_customers:
            result.append(
                {
                    "customer_id": customer.id,
                    "customer_name": customer.full_name
                    or customer.business_name
                    or f"Customer {customer.id}",
                    "phone": customer.phone or "N/A",
                    "email": customer.business_email or customer.email or "N/A",
                    "kyc_status": customer.kyc_status,
                    "verified_at": customer.kyc_completed_on.isoformat()
                    if customer.kyc_completed_on
                    else None,
                    "verification_level": customer.verification_level,
                }
            )

        return {"verified_customers": result, "total": len(result)}, 200


class AdminGetRejectedCustomerKYCResource(Resource):
    @auth_required
    def get(self):
        """Admin gets all rejected customer KYC"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        rejected_customers = User.query.filter(
            User.role == "customer", User.kyc_status == "rejected"
        ).all()

        result = []
        for customer in rejected_customers:
            rejected_docs = Document.query.filter_by(
                user_id=customer.id, status="rejected"
            ).first()

            result.append(
                {
                    "customer_id": customer.id,
                    "customer_name": customer.full_name
                    or customer.business_name
                    or f"Customer {customer.id}",
                    "phone": customer.phone or "N/A",
                    "email": customer.business_email or customer.email or "N/A",
                    "kyc_status": customer.kyc_status,
                    "rejection_reason": rejected_docs.rejection_reason
                    if rejected_docs
                    else None,
                    "rejected_at": rejected_docs.verified_at.isoformat()
                    if rejected_docs and rejected_docs.verified_at
                    else None,
                }
            )

        return {"rejected_customers": result, "total": len(result)}, 200


class AdminGetCustomerKYCDetailResource(Resource):
    @auth_required
    def get(self, customer_id):
        """Admin gets specific customer's KYC details with file previews"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        customer = User.query.get(customer_id)
        if not customer or customer.role != "customer":
            return {"error": "Customer not found"}, 404

        documents = (
            Document.query.filter_by(user_id=customer.id)
            .order_by(Document.created_at.desc())
            .all()
        )

        documents_data = []
        for doc in documents:
            file_data = find_file_and_load_base64(doc.file_name)

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
                    "verified_at": doc.verified_at.isoformat()
                    if doc.verified_at
                    else None,
                    "verified_by": doc.verified_by,
                }
            )

        return {
            "customer": {
                "id": customer.id,
                "full_name": customer.full_name,
                "phone": customer.phone,
                "email": customer.business_email or customer.email,
                "kyc_status": customer.kyc_status,
                "verification_level": customer.verification_level,
                "kyc_completed_on": customer.kyc_completed_on.isoformat()
                if customer.kyc_completed_on
                else None,
            },
            "documents": documents_data,
        }, 200


def _manual_kyc_blocked(customer):
    """Why an admin can't mark this customer verified from documents, or None."""
    from ..services import fraud, identity

    fraud.check_duplicates(customer)
    if fraud.blocking_signals(customer):
        db.session.commit()  # keep the signal
        return "This customer has an open fraud flag (e.g. a duplicate Ghana Card). Resolve it in Fraud review first."
    if identity.biometric_required():
        return (
            "Identity is verified with the Smile ID selfie check now. Ask the customer to complete it, "
            "or decide their check in Identity review."
        )
    return None


class AdminApproveCustomerKYCResource(Resource):
    @auth_required
    def put(self, customer_id):
        """Admin approves a customer's KYC verification"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        customer = User.query.get(customer_id)
        if not customer or customer.role != "customer":
            return {"error": "Customer not found"}, 404

        # Phase 6: once Smile ID is on, identity is only verified by the selfie + Ghana Card check
        # (or an admin decision on that check in the identity review queue), never by documents alone
        blocked = _manual_kyc_blocked(customer)
        if blocked:
            return {"error": blocked}, 409

        try:
            documents = Document.query.filter_by(user_id=customer.id).all()
            for doc in documents:
                doc.status = "verified"
                doc.verified_by = current_admin.id
                doc.verified_at = datetime.now()

            from ..services import identity

            identity.record_manual(
                customer,
                current_admin,
                True,
                (request.get_json(silent=True) or {}).get("note"),
            )
            customer.kyc_status = "verified"
            customer.verification_level = "verified"
            customer.kyc_completed_on = datetime.now()
            from ..services import accounts as _accounts

            _accounts.approve_after_checks(customer)  # never lifts a restriction

            # Underwriting: decide tier and limit now that identity is verified (Phase 3)
            from ..services import risk
            from ..models.risk_assessment import RiskAssessment

            risk.evaluate(
                customer, RiskAssessment.KYC_APPROVAL, created_by=current_admin
            )

            db.session.commit()

            return {
                "message": "Customer KYC approved successfully",
                "customer_id": customer.id,
                "status": "verified",
            }, 200

        except Exception as e:
            db.session.rollback()
            print(f"Error approving customer KYC: {str(e)}")
            return {"error": f"Failed to approve KYC: {str(e)}"}, 500


class AdminRejectCustomerKYCResource(Resource):
    @auth_required
    def put(self, customer_id):
        """Admin rejects a customer's KYC verification"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        rejection_reason = data.get("rejection_reason", "")

        if not rejection_reason:
            return {"error": "Rejection reason is required"}, 400

        customer = User.query.get(customer_id)
        if not customer or customer.role != "customer":
            return {"error": "Customer not found"}, 404

        try:
            documents = Document.query.filter_by(user_id=customer.id).all()
            for doc in documents:
                doc.status = "rejected"
                doc.rejection_reason = rejection_reason
                doc.verified_by = current_admin.id
                doc.verified_at = datetime.now()

            customer.kyc_status = "rejected"

            db.session.commit()

            return {
                "message": "Customer KYC rejected",
                "customer_id": customer.id,
                "status": "rejected",
                "rejection_reason": rejection_reason,
            }, 200

        except Exception as e:
            db.session.rollback()
            print(f"Error rejecting customer KYC: {str(e)}")
            return {"error": f"Failed to reject KYC: {str(e)}"}, 500


class AdminApproveCustomerDocumentResource(Resource):
    @auth_required
    def put(self, document_id):
        """Admin approves a customer document"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        document = Document.query.get(document_id)
        if not document:
            return {"error": "Document not found"}, 404

        try:
            document.status = "verified"
            document.verified_by = current_admin.id
            document.verified_at = datetime.now()

            customer_docs = Document.query.filter_by(user_id=document.user_id).all()
            all_verified = all(d.status == "verified" for d in customer_docs)

            if all_verified:
                customer = User.query.get(document.user_id)
                if (
                    customer
                    and customer.role == "customer"
                    and _manual_kyc_blocked(customer)
                ):
                    customer = (
                        None  # documents are checked, but identity still needs Smile ID
                    )
                if customer:
                    from ..services import identity

                    identity.record_manual(
                        customer, current_admin, True, "All documents approved"
                    )
                    customer.kyc_status = "verified"
                    customer.verification_level = "verified"
                    customer.kyc_completed_on = datetime.now()
                    from ..services import accounts as _accounts

                    _accounts.approve_after_checks(
                        customer
                    )  # never lifts a restriction
                    from ..services import risk
                    from ..models.risk_assessment import RiskAssessment

                    risk.evaluate(
                        customer, RiskAssessment.KYC_APPROVAL, created_by=current_admin
                    )

            db.session.commit()

            return {
                "message": "Document approved successfully",
                "document_id": document.document_id,
                "status": "verified",
                "all_verified": all_verified,
            }, 200

        except Exception as e:
            db.session.rollback()
            print(f"Error approving document: {str(e)}")
            return {"error": f"Failed to approve document: {str(e)}"}, 500


class AdminRejectCustomerDocumentResource(Resource):
    @auth_required
    def put(self, document_id):
        """Admin rejects a customer document"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        rejection_reason = data.get("rejection_reason", "")

        if not rejection_reason:
            return {"error": "Rejection reason is required"}, 400

        document = Document.query.get(document_id)
        if not document:
            return {"error": "Document not found"}, 404

        try:
            document.status = "rejected"
            document.rejection_reason = rejection_reason
            document.verified_by = current_admin.id
            document.verified_at = datetime.now()

            customer = User.query.get(document.user_id)
            if customer:
                customer.kyc_status = "rejected"

            db.session.commit()

            return {
                "message": "Document rejected",
                "document_id": document.document_id,
                "status": "rejected",
                "rejection_reason": rejection_reason,
            }, 200

        except Exception as e:
            db.session.rollback()
            print(f"Error rejecting document: {str(e)}")
            return {"error": f"Failed to reject document: {str(e)}"}, 500


# ============================================
# DEBUG RESOURCE
# ============================================


class DebugFileLocationResource(Resource):
    @auth_required
    def get(self):
        """Debug endpoint to check file locations"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get all documents with pending status
        pending_docs = Document.query.filter_by(status="pending").limit(10).all()

        debug_info = {
            "base_dir": str(BASE_DIR),
            "upload_folder": str(UPLOAD_FOLDER),
            "upload_folder_exists": UPLOAD_FOLDER.exists(),
            "alt_upload_folder": str(ALT_UPLOAD_FOLDER),
            "alt_upload_folder_exists": ALT_UPLOAD_FOLDER.exists(),
            "cwd": str(Path.cwd()),
            "pending_documents_count": len(pending_docs),
            "documents": [],
        }

        for doc in pending_docs:
            file_info = {
                "id": doc.id,
                "file_name": doc.file_name,
                "file_path_db": doc.file_path,
                "exists_in_db_path": os.path.exists(doc.file_path)
                if doc.file_path
                else False,
                "checks": [],
            }

            # Check multiple locations
            locations_to_check = [
                str(UPLOAD_FOLDER / doc.file_name),
                str(ALT_UPLOAD_FOLDER / doc.file_name),
                str(CWD_UPLOAD_FOLDER / doc.file_name),
                f"uploads/kyc/{doc.file_name}",
                doc.file_name,
            ]

            for loc in locations_to_check:
                exists = os.path.exists(loc)
                file_info["checks"].append({"path": loc, "exists": exists})
                if exists:
                    file_info["found_at"] = loc
                    try:
                        with open(loc, "rb") as f:
                            size = len(f.read())
                        file_info["file_size_bytes"] = size
                    except:
                        pass

            debug_info["documents"].append(file_info)

        return debug_info, 200
