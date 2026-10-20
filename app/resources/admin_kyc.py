# resources/admin_kyc.py

from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..services import merchant_fees
from ..models.document import Document
from app.models.notifications import Notifications
from ..extensions import db
from datetime import datetime
import os
import base64
import json
from ..extensions import db, mail


class AdminGetPendingKYCResource(Resource):
    @auth_required
    def get(self):
        """Get all pending KYC/KYB verification requests"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

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
                # Convert file paths to base64 for preview
                documents_data = []
                for doc in documents:
                    file_data = None
                    if doc.file_path and os.path.exists(doc.file_path):
                        try:
                            with open(doc.file_path, "rb") as f:
                                file_data = base64.b64encode(f.read()).decode("utf-8")
                                print(
                                    f"Loaded file: {doc.file_name}, size: {len(file_data)} chars"
                                )
                        except Exception as e:
                            print(f"Error reading file {doc.file_path}: {str(e)}")
                            file_data = None
                    else:
                        print(f"File not found: {doc.file_path}")

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
                        **merchant_fees.describe(merchant),  # fee tier (§6.1)
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


class AdminGetVerifiedKYCResource(Resource):
    @auth_required
    def get(self):
        """Get all verified merchants"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        verified_merchants = User.query.filter(
            User.role == "merchant", User.kyc_status == "verified"
        ).all()

        result = []
        for merchant in verified_merchants:
            result.append(
                {
                    "merchant_id": merchant.id,
                    **merchant_fees.describe(merchant),  # fee tier (§6.1)
                    "merchant_name": merchant.business_name or merchant.full_name,
                    "owner_name": merchant.owner_name,
                    "phone": merchant.phone,
                    "email": merchant.business_email or merchant.email,
                    "kyc_status": merchant.kyc_status,
                    "verified_at": merchant.kyc_completed_on.isoformat()
                    if merchant.kyc_completed_on
                    else None,
                    "verification_level": merchant.verification_level,
                }
            )

        return {"verified_merchants": result, "total": len(result)}, 200


class AdminGetRejectedKYCResource(Resource):
    @auth_required
    def get(self):
        """Get all rejected merchants"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        rejected_merchants = User.query.filter(
            User.role == "merchant", User.kyc_status == "rejected"
        ).all()

        result = []
        for merchant in rejected_merchants:
            # Get rejection reason from documents
            rejected_docs = Document.query.filter_by(
                user_id=merchant.id, status="rejected"
            ).first()

            result.append(
                {
                    "merchant_id": merchant.id,
                    **merchant_fees.describe(merchant),  # fee tier (§6.1)
                    "merchant_name": merchant.business_name or merchant.full_name,
                    "owner_name": merchant.owner_name,
                    "phone": merchant.phone,
                    "email": merchant.business_email or merchant.email,
                    "kyc_status": merchant.kyc_status,
                    "rejection_reason": rejected_docs.rejection_reason
                    if rejected_docs
                    else None,
                    "rejected_at": rejected_docs.verified_at.isoformat()
                    if rejected_docs and rejected_docs.verified_at
                    else None,
                }
            )

        return {"rejected_merchants": result, "total": len(result)}, 200


class AdminGetMerchantKYCResource(Resource):
    @auth_required
    def get(self, merchant_id):
        """Get specific merchant's KYC details"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        merchant = User.query.get(merchant_id)
        if not merchant or merchant.role != "merchant":
            return {"error": "Merchant not found"}, 404

        documents = (
            Document.query.filter_by(user_id=merchant.id)
            .order_by(Document.created_at.desc())
            .all()
        )

        documents_data = []
        for doc in documents:
            file_data = None
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    with open(doc.file_path, "rb") as f:
                        file_data = base64.b64encode(f.read()).decode("utf-8")
                except Exception as e:
                    print(f"Error reading file: {str(e)}")
                    file_data = None

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
            "merchant": {
                "id": merchant.id,
                **merchant_fees.describe(merchant),  # fee tier (§6.1)
                "business_name": merchant.business_name,
                "owner_name": merchant.owner_name,
                "phone": merchant.phone,
                "email": merchant.business_email or merchant.email,
                "city": merchant.city,
                "address": merchant.address,
                "kyc_status": merchant.kyc_status,
                "verification_level": merchant.verification_level,
                "kyc_completed_on": merchant.kyc_completed_on.isoformat()
                if merchant.kyc_completed_on
                else None,
            },
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
        }, 200


# Add this import at the top if not already present
from ..extensions import mail
from flask_mail import Message


class AdminApproveKYCResource(Resource):
    @auth_required
    def put(self, merchant_id):
        """Approve a merchant's KYC verification and update user status"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        merchant = User.query.get(merchant_id)
        if not merchant or merchant.role != "merchant":
            return {"error": "Merchant not found"}, 404

        # Optional merchant fee tier chosen by management at approval (§6.1)
        from ..services import merchant_fees

        try:
            merchant_fees.apply_at_approval(
                merchant, request.get_json(silent=True) or {}, current_admin
            )
        except merchant_fees.FeeTierError as e:
            db.session.rollback()
            return {"error": str(e)}, 400

        try:
            # Update all documents to verified
            documents = Document.query.filter_by(user_id=merchant.id).all()
            for doc in documents:
                doc.status = "verified"
                doc.verified_by = current_admin.id
                doc.verified_at = datetime.now()

            # Update merchant KYC status
            merchant.kyc_status = "verified"
            merchant.verification_level = "verified"
            merchant.kyc_completed_on = datetime.now()

            # IMPORTANT: Update the user status from 'pending' to 'approved'
            from ..services import accounts as _accounts

            _accounts.approve_after_checks(merchant)  # never lifts a restriction

            db.session.commit()

            # --- SEND EMAIL NOTIFICATION ---
            try:
                msg = Message(
                    subject="✅ KYC Verification Approved - Tabital Pay",
                    recipients=[merchant.business_email or merchant.email],
                    html=f"""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="UTF-8">
                        <meta name="viewport" content="width=device-width, initial-scale=1.0">
                        <title>KYC Approved - Tabital Pay</title>
                        <style>
                            body {{
                                font-family: Arial, sans-serif;
                                background-color: #f5f7fa;
                                margin: 0;
                                padding: 0;
                            }}
                            .container {{
                                max-width: 600px;
                                margin: 0 auto;
                                background: white;
                                border-radius: 16px;
                                overflow: hidden;
                                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                            }}
                            .header {{
                                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                padding: 30px;
                                text-align: center;
                            }}
                            .header h1 {{
                                color: white;
                                margin: 0;
                                font-size: 28px;
                            }}
                            .content {{
                                padding: 30px;
                            }}
                            .success-badge {{
                                background: #10b981;
                                color: white;
                                padding: 10px 20px;
                                border-radius: 30px;
                                display: inline-block;
                                font-weight: 600;
                                margin: 20px 0;
                            }}
                            .feature-list {{
                                background: #f0fdf4;
                                padding: 20px;
                                border-radius: 12px;
                                margin: 20px 0;
                                border-left: 4px solid #10b981;
                            }}
                            .feature-list li {{
                                margin: 10px 0;
                                color: #065f46;
                            }}
                            .button {{
                                display: inline-block;
                                padding: 12px 30px;
                                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                color: white;
                                text-decoration: none;
                                border-radius: 30px;
                                margin: 20px 0;
                            }}
                            .footer {{
                                padding: 20px;
                                text-align: center;
                                background: #f8f9fa;
                                color: #6c757d;
                                font-size: 12px;
                            }}
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <div class="header">
                                <h1>🎉 Tabital Pay</h1>
                            </div>
                            <div class="content">
                                <div style="text-align: center;">
                                    <div class="success-badge">✅ KYC VERIFIED</div>
                                </div>
                                <h2>Congratulations, {merchant.business_name or merchant.full_name or 'Merchant'}!</h2>
                                <p>We are pleased to inform you that your KYC verification has been <strong>approved</strong>.</p>
                                <p>Your account status has been updated to <strong>approved</strong>. You now have full access to all merchant features:</p>
                                
                                <div class="feature-list">
                                    <ul style="list-style: none; padding: 0;">
                                        <li>✅ Add and manage products</li>
                                        <li>✅ Receive payments from customers</li>
                                        <li>✅ Access all merchant features</li>
                                        <li>✅ Apply for merchant loans</li>
                                        <li>✅ View analytics and reports</li>
                                    </ul>
                                </div>
                                
                                <p>You can now log in to your merchant dashboard to start using all these features.</p>
                                
                                <div style="text-align: center;">
                                    <a href="https://tabitalpay.com/merchant/dashboard" class="button">Go to Dashboard</a>
                                </div>
                                
                                <p style="color: #6b7280; font-size: 14px; margin-top: 20px;">
                                    <strong>Verification Details:</strong><br>
                                    Verified by: {current_admin.full_name or 'Admin'}<br>
                                    Verification Date: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
                                </p>
                            </div>
                            <div class="footer">
                                <p>&copy; 2024 Tabital Pay. All rights reserved.</p>
                                <p>Secure payment platform for your business</p>
                            </div>
                        </div>
                    </body>
                    </html>
                    """,
                )
                mail.send(msg)
                print(
                    f"KYC approval email sent to {merchant.business_email or merchant.email}"
                )

            except Exception as e:
                print(f"Error sending KYC approval email: {e}")
                # Continue with the process even if email fails

            # Create in-app notification for the merchant
            try:
                notification_title = "KYB verification approved"
                notification_message = (
                    f"Your business {merchant.business_name or merchant.full_name} is verified and "
                    "your account is approved. You can now add products, sell with Tabital Pay "
                    "and receive settlements."
                )

                Notifications.create_notification(
                    user_id=merchant.id,
                    user_role="merchant",
                    title=notification_title,
                    message=notification_message,
                    type="kyc",
                    link="/merchant/dashboard",
                    action_text="Go to Dashboard",
                    extra_data={
                        "kyc_status": "verified",
                        "user_status": "approved",
                        "approved_by": current_admin.id,
                        "approved_by_name": current_admin.full_name or "Admin",
                        "approved_at": datetime.now().isoformat(),
                    },
                )

                # Also create notification for admin
                Notifications.create_notification(
                    user_id=current_admin.id,
                    user_role="admin",
                    title=f"Merchant KYC Approved",
                    message=f"KYC verification for {merchant.business_name or merchant.full_name} has been approved. User status updated to 'approved'.",
                    type="kyc",
                    link="/admin/kyc-verification",
                    action_text="View Details",
                    extra_data={
                        "merchant_id": merchant.id,
                        "merchant_name": merchant.business_name or merchant.full_name,
                        "user_status": "approved",
                        "action": "approved",
                    },
                )
            except Exception as e:
                print(f"Notification error (non-critical): {str(e)}")

            return {
                "message": "KYC verification approved successfully",
                "merchant_id": merchant.id,
                **merchant_fees.describe(merchant),  # fee tier (§6.1)
                "kyc_status": "verified",
                "user_status": merchant.status,
                "email_sent": True,
                "notification_sent": True,
            }, 200

        except Exception as e:
            db.session.rollback()
            print(f"Error approving KYC: {str(e)}")
            return {"error": f"Failed to approve KYC: {str(e)}"}, 500


class AdminRejectKYCResource(Resource):
    @auth_required
    def put(self, merchant_id):
        """Reject a merchant's KYC verification with reason"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        rejection_reason = data.get("rejection_reason", "")

        if not rejection_reason:
            return {"error": "Rejection reason is required"}, 400

        merchant = User.query.get(merchant_id)
        if not merchant or merchant.role != "merchant":
            return {"error": "Merchant not found"}, 404

        try:
            # Update all documents to rejected
            documents = Document.query.filter_by(user_id=merchant.id).all()
            for doc in documents:
                doc.status = "rejected"
                doc.rejection_reason = rejection_reason
                doc.verified_by = current_admin.id
                doc.verified_at = datetime.now()

            # Update merchant KYC status to rejected
            merchant.kyc_status = "rejected"
            # Keep user status as 'pending' (do not change to approved)

            db.session.commit()

            # --- SEND EMAIL NOTIFICATION ---
            try:
                msg = Message(
                    subject="❌ KYC Verification Rejected - Tabital Pay",
                    recipients=[merchant.business_email or merchant.email],
                    html=f"""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="UTF-8">
                        <meta name="viewport" content="width=device-width, initial-scale=1.0">
                        <title>KYC Rejected - Tabital Pay</title>
                        <style>
                            body {{
                                font-family: Arial, sans-serif;
                                background-color: #f5f7fa;
                                margin: 0;
                                padding: 0;
                            }}
                            .container {{
                                max-width: 600px;
                                margin: 0 auto;
                                background: white;
                                border-radius: 16px;
                                overflow: hidden;
                                box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                            }}
                            .header {{
                                background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
                                padding: 30px;
                                text-align: center;
                            }}
                            .header h1 {{
                                color: white;
                                margin: 0;
                                font-size: 28px;
                            }}
                            .content {{
                                padding: 30px;
                            }}
                            .rejection-badge {{
                                background: #ef4444;
                                color: white;
                                padding: 10px 20px;
                                border-radius: 30px;
                                display: inline-block;
                                font-weight: 600;
                                margin: 20px 0;
                            }}
                            .reason-box {{
                                background: #fef2f2;
                                padding: 20px;
                                border-radius: 12px;
                                margin: 20px 0;
                                border-left: 4px solid #ef4444;
                            }}
                            .button {{
                                display: inline-block;
                                padding: 12px 30px;
                                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                                color: white;
                                text-decoration: none;
                                border-radius: 30px;
                                margin: 20px 0;
                            }}
                            .footer {{
                                padding: 20px;
                                text-align: center;
                                background: #f8f9fa;
                                color: #6c757d;
                                font-size: 12px;
                            }}
                        </style>
                    </head>
                    <body>
                        <div class="container">
                            <div class="header">
                                <h1>❌ Tabital Pay</h1>
                            </div>
                            <div class="content">
                                <div style="text-align: center;">
                                    <div class="rejection-badge">❌ KYC REJECTED</div>
                                </div>
                                <h2>Dear {merchant.business_name or merchant.full_name or 'Merchant'},</h2>
                                <p>We regret to inform you that your KYC verification has been <strong>rejected</strong>.</p>
                                
                                <div class="reason-box">
                                    <h3 style="color: #991b1b; margin-top: 0;">Rejection Reason:</h3>
                                    <p style="color: #991b1b; font-size: 16px;">{rejection_reason}</p>
                                </div>
                                
                                <h3>What to do next:</h3>
                                <ul>
                                    <li>Review the rejection reason above</li>
                                    <li>Ensure all documents are clear and readable</li>
                                    <li>Check that documents are not expired</li>
                                    <li>Verify all information matches your business registration</li>
                                    <li>Make sure all required documents are uploaded</li>
                                </ul>
                                
                                <p>Please upload corrected documents for re-verification.</p>
                                
                                <div style="text-align: center;">
                                    <a href="https://tabitalpay.com/merchant/documents" class="button">Upload Documents Again</a>
                                </div>
                                
                                <p style="color: #6b7280; font-size: 14px; margin-top: 20px;">
                                    <strong>Review Details:</strong><br>
                                    Reviewed by: {current_admin.full_name or 'Admin'}<br>
                                    Review Date: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
                                </p>
                            </div>
                            <div class="footer">
                                <p>&copy; 2024 Tabital Pay. All rights reserved.</p>
                                <p>Secure payment platform for your business</p>
                            </div>
                        </div>
                    </body>
                    </html>
                    """,
                )
                mail.send(msg)
                print(
                    f"KYC rejection email sent to {merchant.business_email or merchant.email}"
                )

            except Exception as e:
                print(f"Error sending KYC rejection email: {e}")
                # Continue with the process even if email fails

            # Create notification for the merchant
            try:
                notification_title = "❌ KYC Verification Rejected"
                notification_message = f"""Your KYC verification has been rejected.

Rejection Reason: {rejection_reason}

Please review the following issues:
• Ensure all documents are clear and readable
• Check that documents are not expired
• Verify that all information matches your business registration
• Make sure all required documents are uploaded

Please upload corrected documents for re-verification."""

                Notifications.create_notification(
                    user_id=merchant.id,
                    user_role="merchant",
                    title=notification_title,
                    message=notification_message,
                    type="kyc",
                    link="/merchant/documents",
                    action_text="Upload Documents Again",
                    extra_data={
                        "kyc_status": "rejected",
                        "rejection_reason": rejection_reason,
                        "rejected_by": current_admin.id,
                        "rejected_by_name": current_admin.full_name or "Admin",
                        "rejected_at": datetime.now().isoformat(),
                    },
                )

                # Also create notification for admin
                Notifications.create_notification(
                    user_id=current_admin.id,
                    user_role="admin",
                    title=f"Merchant KYC Rejected",
                    message=f"KYC verification for {merchant.business_name or merchant.full_name} has been rejected.\nReason: {rejection_reason}",
                    type="kyc",
                    link="/admin/kyc-verification",
                    action_text="View Details",
                    extra_data={
                        "merchant_id": merchant.id,
                        "merchant_name": merchant.business_name or merchant.full_name,
                        "rejection_reason": rejection_reason,
                        "action": "rejected",
                    },
                )
            except Exception as e:
                print(f"Notification error (non-critical): {str(e)}")

            return {
                "message": "KYC verification rejected",
                "merchant_id": merchant.id,
                **merchant_fees.describe(merchant),  # fee tier (§6.1)
                "kyc_status": "rejected",
                "rejection_reason": rejection_reason,
                "email_sent": True,
                "notification_sent": True,
            }, 200

        except Exception as e:
            db.session.rollback()
            print(f"Error rejecting KYC: {str(e)}")
            return {"error": f"Failed to reject KYC: {str(e)}"}, 500


class AdminApproveDocumentResource(Resource):
    @auth_required
    def put(self, document_id):
        """Approve a single document"""
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

            # Check if all merchant documents are verified
            merchant_docs = Document.query.filter_by(user_id=document.user_id).all()

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
                    # Update user status to approved when all documents are verified
                    from ..services import accounts as _accounts

                    _accounts.approve_after_checks(
                        merchant
                    )  # never lifts a restriction

                    db.session.commit()

                    # Send notification for full verification
                    try:
                        Notifications.create_notification(
                            user_id=merchant.id,
                            user_role="merchant",
                            title="✅ KYC Verification Complete",
                            message=f"Congratulations! All your documents have been verified. Your account is now fully verified and active.",
                            type="kyc",
                            link="/merchant/dashboard",
                            action_text="Go to Dashboard",
                            extra_data={
                                "kyc_status": "verified",
                                "user_status": "approved",
                            },
                        )
                    except Exception as e:
                        print(f"Notification error: {str(e)}")

            db.session.commit()

            # Send notification for document approval
            try:
                Notifications.create_notification(
                    user_id=document.user_id,
                    user_role="merchant",
                    title=f"Document Approved: {document.document_name}",
                    message=f"Your {document.document_name} has been approved. {'Your account is now fully verified!' if all_verified else 'Please wait for other documents to be reviewed.'}",
                    type="kyc",
                    link="/merchant/documents",
                    action_text="View Documents",
                    extra_data={
                        "document_id": document.id,
                        "document_type": document.document_type,
                    },
                )
            except Exception as e:
                print(f"Notification error: {str(e)}")

            return {
                "message": "Document approved successfully",
                "document_id": document.document_id,
                "status": "verified",
                "all_verified": all_verified if "all_verified" in locals() else False,
            }, 200

        except Exception as e:
            db.session.rollback()
            print(f"Error approving document: {str(e)}")
            return {"error": f"Failed to approve document: {str(e)}"}, 500


class AdminRejectDocumentResource(Resource):
    @auth_required
    def put(self, document_id):
        """Reject a single document with reason"""
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

            # Update merchant KYC status to rejected
            merchant = User.query.get(document.user_id)
            if merchant:
                merchant.kyc_status = "rejected"
                # Keep user status as 'pending' (do not change to approved)

            db.session.commit()

            # Send notification for document rejection
            try:
                Notifications.create_notification(
                    user_id=document.user_id,
                    user_role="merchant",
                    title=f"Document Rejected: {document.document_name}",
                    message=f"""Your {document.document_name} has been rejected.

Reason: {rejection_reason}

Please upload a corrected version of this document for re-verification.""",
                    type="kyc",
                    link="/merchant/documents",
                    action_text="Upload Again",
                    extra_data={
                        "document_id": document.id,
                        "document_type": document.document_type,
                        "rejection_reason": rejection_reason,
                    },
                )
            except Exception as e:
                print(f"Notification error: {str(e)}")

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
