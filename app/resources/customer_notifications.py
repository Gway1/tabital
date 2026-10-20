# resources/customer_notifications.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.notification_settings import NotificationSetting
from ..extensions import db
from datetime import datetime


def safe_str(v):
    return v if v is not None else ""


def safe_bool(v):
    return v if v is not None else False


class CustomerGetNotificationsResource(Resource):
    @auth_required
    def get(self):
        """Get customer notifications"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # In production, create a Notification model to store individual notifications
        # For now, generate dynamic notifications based on customer data
        from ..models.instalment import InstalmentPlan
        from ..models.instalment_payment import InstalmentPayment

        notifications = []

        # Check for upcoming payments
        upcoming_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.customer_id == current_customer.id,
                InstalmentPayment.status == "pending",
                InstalmentPayment.due_date >= datetime.now(),
            )
            .order_by(InstalmentPayment.due_date.asc())
            .limit(3)
            .all()
        )

        for payment in upcoming_payments:
            days_until_due = (payment.due_date - datetime.now()).days
            if days_until_due <= 3:
                notifications.append(
                    {
                        "id": f"payment_{payment.id}",
                        "message": f"Payment of {payment.amount} GHS for {payment.plan.plan_name} is due in {days_until_due} days",
                        "read": False,
                        "type": "payment",
                        "created_at": payment.due_date.isoformat(),
                        "link": f"/customer/payments/{payment.plan_id}",
                    }
                )

        # Check for KYC status
        if current_customer.kyc_status == "pending":
            notifications.append(
                {
                    "id": "kyc_pending",
                    "message": "Your KYC verification is pending. Please complete your profile.",
                    "read": False,
                    "type": "warning",
                    "created_at": datetime.now().isoformat(),
                    "link": "/customer/profile",
                }
            )
        elif current_customer.kyc_status == "rejected":
            notifications.append(
                {
                    "id": "kyc_rejected",
                    "message": "Your KYC verification was rejected. Please upload correct documents.",
                    "read": False,
                    "type": "danger",
                    "created_at": datetime.now().isoformat(),
                    "link": "/customer/profile",
                }
            )

        # Check for completed plans
        completed_plans = InstalmentPlan.query.filter(
            InstalmentPlan.customer_id == current_customer.id,
            InstalmentPlan.status == "completed",
            InstalmentPlan.completed_at
            >= datetime.now().replace(hour=0, minute=0, second=0),
        ).all()

        for plan in completed_plans:
            notifications.append(
                {
                    "id": f"completed_{plan.id}",
                    "message": f"Congratulations! You've completed your payment plan for {plan.plan_name}",
                    "read": False,
                    "type": "success",
                    "created_at": plan.completed_at.isoformat(),
                    "link": f"/customer/instalments/{plan.id}",
                }
            )

        # Sort by date (newest first) and limit to 20
        notifications.sort(key=lambda x: x["created_at"], reverse=True)

        return notifications[:20]


class CustomerMarkNotificationReadResource(Resource):
    @auth_required
    def put(self, notification_id):
        """Mark notification as read - In production, store read status in a separate table"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # For now, just return success
        # In production, you would have a CustomerNotification table to track read status
        return {"message": "Notification marked as read"}, 200


class CustomerMarkAllNotificationsReadResource(Resource):
    @auth_required
    def post(self):
        """Mark all notifications as read"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # In production, update all unread notifications for this user
        return {"message": "All notifications marked as read"}, 200


class CustomerGetNotificationSettingsResource(Resource):
    @auth_required
    def get(self):
        """Get notification settings for the customer"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Get or create notification settings
        settings = NotificationSetting.query.filter_by(
            user_id=current_customer.id
        ).first()

        if not settings:
            settings = NotificationSetting(user_id=current_customer.id)
            db.session.add(settings)
            db.session.commit()

        return {
            "email_notifications": safe_bool(settings.email_notifications),
            "sms_notifications": safe_bool(settings.sms_notifications),
            "push_notifications": safe_bool(settings.push_notifications),
            "transaction_alerts": safe_bool(settings.transaction_alerts),
            "settlement_alerts": safe_bool(settings.settlement_alerts),
            "dispute_alerts": safe_bool(settings.dispute_alerts),
            "promotional_emails": safe_bool(settings.promotional_emails),
            "newsletter": safe_bool(settings.newsletter),
            "daily_summary": safe_bool(settings.daily_summary),
            "weekly_report": safe_bool(settings.weekly_report),
        }


class CustomerUpdateNotificationSettingsResource(Resource):
    @auth_required
    def put(self):
        """Update notification settings for the customer"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()

        # Get or create notification settings
        settings = NotificationSetting.query.filter_by(
            user_id=current_customer.id
        ).first()

        if not settings:
            settings = NotificationSetting(user_id=current_customer.id)
            db.session.add(settings)

        # Update settings - all fields are boolean
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
                setattr(settings, field, bool(data[field]))

        db.session.commit()

        return {"message": "Notification settings updated successfully"}, 200


class CustomerUnreadNotificationCountResource(Resource):
    @auth_required
    def get(self):
        """Get count of unread notifications"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        # Calculate unread count dynamically
        from ..models.instalment import InstalmentPlan
        from ..models.instalment_payment import InstalmentPayment

        unread_count = 0

        # Check for upcoming payments (within 3 days)
        upcoming_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.customer_id == current_customer.id,
                InstalmentPayment.status == "pending",
                InstalmentPayment.due_date >= datetime.now(),
            )
            .all()
        )

        for payment in upcoming_payments:
            days_until_due = (payment.due_date - datetime.now()).days
            if days_until_due <= 3:
                unread_count += 1

        # Check for pending KYC
        if current_customer.kyc_status in ["pending", "rejected"]:
            unread_count += 1

        # Check for recently completed plans (today)
        completed_plans = InstalmentPlan.query.filter(
            InstalmentPlan.customer_id == current_customer.id,
            InstalmentPlan.status == "completed",
            InstalmentPlan.completed_at
            >= datetime.now().replace(hour=0, minute=0, second=0),
        ).count()
        unread_count += completed_plans

        return {"unread_count": unread_count}


# Optional: Create a separate table for tracking notification read status
# models/customer_notification.py
"""
class CustomerNotification(db.Model):
    __tablename__ = 'customer_notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notification_type = db.Column(db.String(50))  # payment, kyc, plan_completed, etc.
    reference_id = db.Column(db.Integer)  # ID of related record (payment_id, plan_id, etc.)
    title = db.Column(db.String(200))
    message = db.Column(db.Text)
    link = db.Column(db.String(200))
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref='notifications')
"""
