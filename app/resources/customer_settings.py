# resources/customer_settings.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.notification_settings import NotificationSetting
from ..extensions import db
from datetime import datetime


def safe_str(v):
    return v if v is not None else ""


class CustomerGetPreferencesResource(Resource):
    @auth_required
    def get(self):
        """Get customer preferences"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Return default preferences
        # You can store these in a separate table if needed
        return {
            "language": "en",
            "timezone": "Africa/Accra",
            "currency": "GHS",
            "date_format": "DD/MM/YYYY",
            "dashboard_layout": "default",
            "items_per_page": 20,
        }


class CustomerUpdatePreferencesResource(Resource):
    @auth_required
    def put(self):
        """Update customer preferences"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # In production, save to a preferences table
        # For now, just return success

        return {"message": "Preferences updated successfully"}, 200
