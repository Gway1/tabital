# resources/customer_profile.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from flask import request as flask_request
from ..models.user import User
from ..extensions import db
from datetime import datetime


def safe_str(v):
    return v if v is not None else ""


class CustomerGetProfileResource(Resource):
    @auth_required
    def get(self):
        """Get customer profile information"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        return {
            "id": current_customer.id,
            "customer_id": safe_str(current_customer.customer_id),
            "full_name": safe_str(current_customer.full_name),
            "email": safe_str(
                current_customer.business_email or current_customer.email
            ),
            "phone": safe_str(current_customer.phone),
            "business_name": safe_str(current_customer.business_name),
            "city": safe_str(current_customer.city),
            "address": safe_str(current_customer.address),
            "gps": safe_str(current_customer.gps),
            "status": safe_str(current_customer.status),
            "kyc_status": safe_str(current_customer.kyc_status),
            "income_range": safe_str(current_customer.income_range),
            "created_at": current_customer.created_at.isoformat()
            if current_customer.created_at
            else "",
            "total_spent": float(current_customer.total_sales or 0),
            "active_plans_count": 0,
        }


class CustomerUpdateProfileResource(Resource):
    @auth_required
    def put(self):
        """Update customer profile information"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        data = request.get_json() or {}

        # The verified name is the one on the Ghana Card (Phase 6): it can't be edited afterwards
        if (
            current_customer.kyc_status == "verified"
            and "full_name" in data
            and (data["full_name"] or "").strip()
            != (current_customer.full_name or "").strip()
        ):
            return {
                "error": "Your name is verified against your Ghana Card. Contact support to change it.",
                "field": "full_name",
            }, 409

        # income_range feeds underwriting, so it can only change through re-verification
        allowed_fields = [
            "full_name",
            "business_name",
            "email",
            "city",
            "address",
            "gps",
        ]

        for field in allowed_fields:
            if field in data:
                setattr(current_customer, field, data[field])

        db.session.commit()

        return {"message": "Profile updated successfully"}, 200


class CustomerUpdatePasswordResource(Resource):
    @auth_required
    def put(self):
        """Update customer password"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        from ..services.auth_service import change_password

        data = request.get_json() or {}
        return change_password(
            current_customer, data.get("current_password"), data.get("new_password")
        )


class CustomerGetKYCStatusResource(Resource):
    @auth_required
    def get(self):
        """Get customer KYC status"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        return {
            "status": safe_str(current_customer.kyc_status or "not_submitted"),
            "level": safe_str(current_customer.verification_level or "basic"),
            "submitted_at": current_customer.kyc_completed_on.isoformat()
            if current_customer.kyc_completed_on
            else None,
            "verified_at": current_customer.kyc_completed_on.isoformat()
            if current_customer.kyc_status == "verified"
            else None,
            "rejection_reason": None,
        }


# ============================================
# CUSTOMER DOCUMENT UPLOAD
# ============================================


class CustomerUploadDocumentsResource(Resource):
    @auth_required
    def post(self):
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # DEBUGGING
        print("Received files:", request.files)
        print("Form data:", request.form)

        # GET FILES
        front_image = request.files.get("front_image")
        back_image = request.files.get("back_image")
        salary_certificate = request.files.get("salary_certificate" "")
        bank_statement = request.files.get("bank_statement")

        # OPTIONAL
        notes = request.form.get("notes", "")

        # VALIDATION
        if not front_image:
            return {"error": "Front Ghana Card image is required"}, 400

        if not back_image:
            return {"error": "Back Ghana Card image is required"}, 400

        if not salary_certificate:
            return {"error": "Salary certificate is required"}, 400

        if not bank_statement:
            return {"error": "Bank statement is required"}, 400

        # UPDATE KYC STATUS
        current_customer.kyc_status = "pending"

        db.session.commit()

        return {
            "message": "Documents uploaded successfully",
            "status": "pending",
            "notes": notes,
        }, 200
