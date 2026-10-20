# resources/merchant_notifications.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.notification_settings import NotificationSetting
from ..models.purchase_order import PurchaseOrder
from ..models.transaction import Transaction
from ..models.instalment import InstalmentPlan
from ..models.instalment_payment import InstalmentPayment
from ..extensions import db
from datetime import datetime, timedelta


def safe_str(v):
    return v if v is not None else ""


def safe_bool(v):
    return v if v is not None else False


class MerchantGetNotificationsResource(Resource):
    @auth_required
    def get(self):
        """Get merchant notifications"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        notifications = []

        # Check for pending orders
        pending_orders = (
            PurchaseOrder.query.filter_by(
                merchant_id=current_merchant.id, status="pending"
            )
            .order_by(PurchaseOrder.created_at.desc())
            .limit(5)
            .all()
        )

        for order in pending_orders:
            days_old = (datetime.now() - order.created_at).days
            notifications.append(
                {
                    "id": f"pending_order_{order.id}",
                    "title": "New Order Pending Approval",
                    "message": f"Order #{order.order_id} for {order.product_name} (Qty: {order.quantity}) is pending admin approval.",
                    "read": False,
                    "type": "order",
                    "created_at": order.created_at.isoformat(),
                    "link": f"/merchant/orders/{order.id}",
                    "action_text": "View Order",
                }
            )

        # Check for recent payments received
        recent_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.merchant_id == current_merchant.id,
                InstalmentPayment.status == "paid",
                InstalmentPayment.paid_date >= datetime.now() - timedelta(days=7),
            )
            .order_by(InstalmentPayment.paid_date.desc())
            .limit(10)
            .all()
        )

        for payment in recent_payments:
            plan = InstalmentPlan.query.get(payment.plan_id)
            notifications.append(
                {
                    "id": f"payment_{payment.id}",
                    "title": "Payment Received",
                    "message": f"Payment of {payment.amount:.2f} GHS received for {plan.plan_name} from customer.",
                    "read": False,
                    "type": "payment",
                    "created_at": payment.paid_date.isoformat(),
                    "link": "/merchant/transactions",
                    "action_text": "View Transaction",
                }
            )

        # Check for completed instalment plans
        completed_plans = (
            InstalmentPlan.query.filter(
                InstalmentPlan.merchant_id == current_merchant.id,
                InstalmentPlan.status == "completed",
                InstalmentPlan.completed_at >= datetime.now() - timedelta(days=7),
            )
            .order_by(InstalmentPlan.completed_at.desc())
            .limit(5)
            .all()
        )

        for plan in completed_plans:
            notifications.append(
                {
                    "id": f"completed_plan_{plan.id}",
                    "title": "Instalment Plan Completed",
                    "message": f"Customer has completed their instalment plan for {plan.plan_name}. Total amount: {plan.total_amount:.2f} GHS",
                    "read": False,
                    "type": "system",
                    "created_at": plan.completed_at.isoformat(),
                    "link": f"/merchant/instalments/{plan.id}",
                    "action_text": "View Plan",
                }
            )

        # Check for overdue instalments
        overdue_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.merchant_id == current_merchant.id,
                InstalmentPayment.status == "pending",
                InstalmentPayment.due_date < datetime.now(),
            )
            .order_by(InstalmentPayment.due_date.asc())
            .limit(10)
            .all()
        )

        for payment in overdue_payments:
            plan = InstalmentPlan.query.get(payment.plan_id)
            days_overdue = (datetime.now() - payment.due_date).days
            notifications.append(
                {
                    "id": f"overdue_{payment.id}",
                    "title": "Overdue Payment Alert",
                    "message": f"Payment of {payment.amount:.2f} GHS for {plan.plan_name} is {days_overdue} days overdue.",
                    "read": False,
                    "type": "warning",
                    "created_at": payment.due_date.isoformat(),
                    "link": f"/merchant/instalments/{plan.id}",
                    "action_text": "View Details",
                }
            )

        # Check for KYC status
        if current_merchant.kyc_status == "pending":
            notifications.append(
                {
                    "id": "kyc_pending",
                    "title": "KYC Verification Pending",
                    "message": "Your KYC verification is pending. Please complete your profile to access all features.",
                    "read": False,
                    "type": "kyc",
                    "created_at": datetime.now().isoformat(),
                    "link": "/merchant/documents",
                    "action_text": "Complete KYC",
                }
            )
        elif current_merchant.kyc_status == "rejected":
            notifications.append(
                {
                    "id": "kyc_rejected",
                    "title": "KYC Verification Failed",
                    "message": "Your KYC verification was rejected. Please upload correct documents.",
                    "read": False,
                    "type": "kyc",
                    "created_at": datetime.now().isoformat(),
                    "link": "/merchant/documents",
                    "action_text": "Re-upload Documents",
                }
            )
        elif current_merchant.kyc_status == "verified":
            notifications.append(
                {
                    "id": "kyc_verified",
                    "title": "KYC Verified",
                    "message": "Your KYC verification has been approved! You now have full access.",
                    "read": False,
                    "type": "success",
                    "created_at": datetime.now().isoformat(),
                    "link": "/merchant/dashboard",
                    "action_text": "Go to Dashboard",
                }
            )

        # Check for pending settlements
        pending_settlements = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.payment_status == "processing",
        ).all()

        if pending_settlements:
            total_amount = sum(t.amount for t in pending_settlements)
            notifications.append(
                {
                    "id": "pending_settlement",
                    "title": "Pending Settlement",
                    "message": f"You have {len(pending_settlements)} pending transactions totaling {total_amount:.2f} GHS waiting for settlement.",
                    "read": False,
                    "type": "settlement",
                    "created_at": datetime.now().isoformat(),
                    "link": "/merchant/settlements",
                    "action_text": "View Settlements",
                }
            )

        # Check for low stock (if you have product stock tracking)
        # This would require a Product model with stock_quantity
        # low_stock_products = Product.query.filter(
        #     Product.merchant_id == current_merchant.id,
        #     Product.stock_quantity <= 5,
        #     Product.stock_quantity > 0
        # ).all()
        #
        # for product in low_stock_products:
        #     notifications.append({
        #         "id": f"low_stock_{product.id}",
        #         "title": "Low Stock Alert",
        #         "message": f"{product.name} has only {product.stock_quantity} units left in stock.",
        #         "read": False,
        #         "type": "warning",
        #         "created_at": datetime.now().isoformat(),
        #         "link": f"/merchant/products/{product.id}",
        #         "action_text": "View Product"
        #     })

        # Sort by date (newest first) and limit to 50
        notifications.sort(key=lambda x: x["created_at"], reverse=True)

        return {"notifications": notifications[:50], "total": len(notifications)}, 200


class MerchantMarkNotificationReadResource(Resource):
    @auth_required
    def put(self, notification_id):
        """Mark notification as read"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # In production, you would have a MerchantNotification table to track read status
        # For now, just return success
        return {"message": "Notification marked as read"}, 200


class MerchantMarkAllNotificationsReadResource(Resource):
    @auth_required
    def post(self):
        """Mark all notifications as read"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # In production, update all unread notifications for this merchant
        return {"message": "All notifications marked as read"}, 200


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
            settings = NotificationSetting(user_id=current_merchant.id)
            db.session.add(settings)
            db.session.commit()

        return {
            "email_notifications": safe_bool(settings.email_notifications),
            "sms_notifications": safe_bool(settings.sms_notifications),
            "push_notifications": safe_bool(settings.push_notifications),
            "order_alerts": safe_bool(settings.order_alerts)
            if hasattr(settings, "order_alerts")
            else True,
            "payment_alerts": safe_bool(settings.payment_alerts)
            if hasattr(settings, "payment_alerts")
            else True,
            "settlement_alerts": safe_bool(settings.settlement_alerts)
            if hasattr(settings, "settlement_alerts")
            else True,
            "dispute_alerts": safe_bool(settings.dispute_alerts)
            if hasattr(settings, "dispute_alerts")
            else True,
            "promotional_emails": safe_bool(settings.promotional_emails),
            "newsletter": safe_bool(settings.newsletter),
            "daily_summary": safe_bool(settings.daily_summary),
            "weekly_report": safe_bool(settings.weekly_report),
        }, 200


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

        # Update settings - all fields are boolean
        allowed_fields = [
            "email_notifications",
            "sms_notifications",
            "push_notifications",
            "order_alerts",
            "payment_alerts",
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


class MerchantUnreadNotificationCountResource(Resource):
    @auth_required
    def get(self):
        """Get count of unread notifications for merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        unread_count = 0

        # Check for pending orders
        pending_orders = PurchaseOrder.query.filter_by(
            merchant_id=current_merchant.id, status="pending"
        ).count()
        unread_count += pending_orders

        # Check for recent payments (last 7 days)
        recent_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.merchant_id == current_merchant.id,
                InstalmentPayment.status == "paid",
                InstalmentPayment.paid_date >= datetime.now() - timedelta(days=7),
            )
            .count()
        )
        unread_count += recent_payments

        # Check for completed plans (last 7 days)
        completed_plans = InstalmentPlan.query.filter(
            InstalmentPlan.merchant_id == current_merchant.id,
            InstalmentPlan.status == "completed",
            InstalmentPlan.completed_at >= datetime.now() - timedelta(days=7),
        ).count()
        unread_count += completed_plans

        # Check for overdue payments
        overdue_payments = (
            InstalmentPayment.query.join(InstalmentPlan)
            .filter(
                InstalmentPlan.merchant_id == current_merchant.id,
                InstalmentPayment.status == "pending",
                InstalmentPayment.due_date < datetime.now(),
            )
            .count()
        )
        unread_count += overdue_payments

        # Check for KYC status
        if current_merchant.kyc_status in ["pending", "rejected"]:
            unread_count += 1

        # Check for pending settlements
        pending_settlements = Transaction.query.filter_by(
            merchant_id=current_merchant.id, payment_status="processing"
        ).count()
        if pending_settlements > 0:
            unread_count += 1

        return {"unread_count": unread_count}, 200


class MerchantDeleteNotificationResource(Resource):
    @auth_required
    def delete(self, notification_id):
        """Delete a notification (in production, you would have a notifications table)"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # In production, delete from merchant_notifications table
        return {"message": "Notification deleted"}, 200


class MerchantClearAllNotificationsResource(Resource):
    @auth_required
    def delete(self):
        """Clear all notifications"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # In production, clear all merchant notifications
        return {"message": "All notifications cleared"}, 200
