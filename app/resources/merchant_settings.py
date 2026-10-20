from flask_restful import Resource, request
from ..services import merchant_fees
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..extensions import db
from datetime import datetime, timedelta
import re

from ..models.notification_settings import NotificationSetting


def safe_str(v):
    return v if v is not None else ""


def safe_bool(v):
    return v if v is not None else False


def safe_float(v):
    return v if v is not None else 0.0


class MerchantGetProfileResource(Resource):
    @auth_required
    def get(self):
        """Get merchant profile information"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        return {
            "id": current_merchant.id,
            "merchant_id": safe_str(current_merchant.merchant_id),
            "business_name": safe_str(current_merchant.business_name),
            "owner_name": safe_str(current_merchant.owner_name),
            "full_name": safe_str(current_merchant.full_name),
            "phone": safe_str(current_merchant.phone),
            "email": safe_str(
                current_merchant.business_email or current_merchant.email
            ),
            "business_email": safe_str(current_merchant.business_email),
            "business_phone": safe_str(current_merchant.business_phone),
            "website": safe_str(current_merchant.website),
            "description": safe_str(current_merchant.description),
            "business_type": safe_str(current_merchant.business_type),
            "registration_number": safe_str(current_merchant.registration_number),
            "tax_id": safe_str(current_merchant.tax_id),
            "city": safe_str(current_merchant.city),
            "address": safe_str(current_merchant.address),
            "business_address": safe_str(current_merchant.business_address),
            "gps": safe_str(current_merchant.gps),
            "status": safe_str(current_merchant.status),
            "verified": safe_bool(current_merchant.verified),
            "kyc_status": safe_str(current_merchant.kyc_status),
            "verification_level": safe_str(current_merchant.verification_level),
            "commission_rate": merchant_fees.describe(current_merchant)[
                "fee_percentage"
            ],  # current tier (§6.1)
            "payment_method": safe_str(current_merchant.payment_method),
            "bank_name": safe_str(current_merchant.bank_name),
            "account_name": safe_str(current_merchant.account_name),
            "account_number": safe_str(current_merchant.account_number),
            "momo_name": safe_str(current_merchant.momo_name),
            "momo_number": safe_str(current_merchant.momo_number),
            "created_at": current_merchant.created_at.isoformat()
            if current_merchant.created_at
            else "",
            "total_products": safe_float(current_merchant.total_products),
            "total_sales": safe_float(current_merchant.total_sales),
            "rating": safe_float(current_merchant.rating),
        }


class MerchantUpdateProfileResource(Resource):
    @auth_required
    def put(self):
        """Update merchant profile information"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # Business Information
        allowed_fields = [
            "business_name",
            "owner_name",
            "full_name",
            "business_email",
            "business_phone",
            "website",
            "description",
            "business_type",
            "registration_number",
            "tax_id",
            "city",
            "address",
            "business_address",
            "gps",
        ]

        # These three are unique: refuse a clash with a clear message instead of a database error
        for field, label in (
            ("phone", "phone number"),
            ("business_email", "email address"),
            ("business_phone", "business phone number"),
        ):
            value = data.get(field)
            if (
                value
                and getattr(current_merchant, field) != value
                and User.query.filter(getattr(User, field) == value, User.id != current_merchant.id).first()
            ):
                return {"error": f"This {label} is already registered to another account.", "field": field}, 409

        for field in allowed_fields:
            if field in data:
                setattr(current_merchant, field, data[field])

        # Update phone if provided
        if "phone" in data:
            current_merchant.phone = data["phone"]

        db.session.commit()

        return {"message": "Profile updated successfully"}, 200


class MerchantUpdatePasswordResource(Resource):
    @auth_required
    def put(self):
        """Update merchant password"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        from ..services.auth_service import change_password

        data = request.get_json() or {}
        return change_password(
            current_merchant, data.get("current_password"), data.get("new_password")
        )


class MerchantUpdatePaymentSettingsResource(Resource):
    @auth_required
    def put(self):
        """Update payment settings"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        from .merchant_payouts import PayoutError, apply_payout_details

        data = request.get_json() or {}

        # Payout details go through one validated path: a change pauses payouts for 48 hours
        try:
            apply_payout_details(current_merchant, data)
        except PayoutError as e:
            return {"error": str(e)}, e.status

        # Default Payment Method
        if "payment_method" in data:
            current_merchant.payment_method = data["payment_method"]

        db.session.commit()

        return {"message": "Payment settings updated successfully"}, 200


class MerchantGetNotificationSettingsResource(Resource):
    @auth_required
    def get(self):
        """Get notification settings"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # You can store these in a separate settings table
        # For now, return default settings
        return {
            "email_notifications": True,
            "sms_notifications": True,
            "push_notifications": True,
            "transaction_alerts": True,
            "settlement_alerts": True,
            "dispute_alerts": True,
            "promotional_emails": False,
            "newsletter": False,
            "daily_summary": True,
            "weekly_report": True,
        }


class MerchantUpdateNotificationSettingsResource(Resource):
    @auth_required
    def put(self):
        """Update notification settings"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # Here you would save to a settings table
        # For now, just return success

        return {"message": "Notification settings updated successfully"}, 200


class MerchantGetPreferencesResource(Resource):
    @auth_required
    def get(self):
        """Get merchant preferences"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Return default preferences
        return {
            "language": "en",
            "timezone": "Africa/Accra",
            "currency": "GHS",
            "date_format": "DD/MM/YYYY",
            "dashboard_layout": "default",
            "items_per_page": 20,
            "default_report_type": "monthly",
        }


class MerchantUpdatePreferencesResource(Resource):
    @auth_required
    def put(self):
        """Update merchant preferences"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # Here you would save to a preferences table
        # For now, just return success

        return {"message": "Preferences updated successfully"}, 200


class MerchantUpdateKYCResource(Resource):
    @auth_required
    def put(self):
        """Update KYC information"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # KYC status and verification level are set by Tabital after review, never by the merchant
        return {"error": "KYC status is set by Tabital after document review"}, 403


class MerchantUploadDocumentResource(Resource):
    @auth_required
    def post(self):
        """Upload merchant document"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # Here you would handle file upload
        # For now, just return success

        return {"message": "Document uploaded successfully"}, 201


class MerchantGetActivityLogResource(Resource):
    @auth_required
    def get(self):
        """Get merchant activity log"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Return sample activity log
        # In production, you would fetch from an activity log table
        return {
            "activities": [
                {
                    "id": 1,
                    "action": "Login",
                    "details": "Successful login",
                    "ip_address": "192.168.1.1",
                    "created_at": datetime.now().isoformat(),
                },
                {
                    "id": 2,
                    "action": "Transaction",
                    "details": "Processed transaction INV-00012345",
                    "ip_address": "192.168.1.1",
                    "created_at": (datetime.now() - timedelta(hours=1)).isoformat(),
                },
            ]
        }


class MerchantGetNotificationSettingsResource(Resource):
    @auth_required
    def get(self):
        """Get notification settings for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get or create notification settings
        settings = NotificationSetting.query.filter_by(
            user_id=current_merchant.id
        ).first()

        if not settings:
            # Create default settings
            settings = NotificationSetting(user_id=current_merchant.id)
            db.session.add(settings)
            db.session.commit()

        return {
            "email_notifications": settings.email_notifications,
            "sms_notifications": settings.sms_notifications,
            "push_notifications": settings.push_notifications,
            "transaction_alerts": settings.transaction_alerts,
            "settlement_alerts": settings.settlement_alerts,
            "dispute_alerts": settings.dispute_alerts,
            "promotional_emails": settings.promotional_emails,
            "newsletter": settings.newsletter,
            "daily_summary": settings.daily_summary,
            "weekly_report": settings.weekly_report,
        }


class MerchantUpdateNotificationSettingsResource(Resource):
    @auth_required
    def put(self):
        """Update notification settings for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # Get or create notification settings
        settings = NotificationSetting.query.filter_by(
            user_id=current_merchant.id
        ).first()

        if not settings:
            settings = NotificationSetting(user_id=current_merchant.id)
            db.session.add(settings)

        # Update settings
        allowed_fields = [
            "email_notifications",
            "sms_notifications",
            "push_notifications",
            "transaction_alerts",
            "settlement_alerts",
            "dispute_alerts",
            "promotional_emails",
            "newsletter",
            "daily_summary",
            "weekly_report",
        ]

        for field in allowed_fields:
            if field in data:
                setattr(settings, field, data[field])

        db.session.commit()

        return {"message": "Notification settings updated successfully"}, 200
