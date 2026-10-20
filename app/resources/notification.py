# resources/notification.py
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.notifications import Notifications
from ..extensions import db
from datetime import datetime
import json


def safe_str(v):
    return v if v is not None else ""


class GetNotificationsResource(Resource):
    @auth_required
    def get(self):
        """Get user's notifications"""
        current_user_obj = current_user()

        page = request.args.get("page", 1, type=int)
        limit = request.args.get("limit", 50, type=int)
        type_filter = request.args.get("type", "")
        read_filter = request.args.get("read", "")

        query = Notifications.query.filter_by(user_id=current_user_obj.id)

        if type_filter:
            query = query.filter(Notifications.type == type_filter)

        if read_filter == "true":
            query = query.filter(Notifications.read == True)
        elif read_filter == "false":
            query = query.filter(Notifications.read == False)

        total = query.count()
        notifications = (
            query.order_by(Notifications.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        return {
            "notifications": [
                {
                    "id": n.id,
                    "notification_id": n.notification_id,
                    "title": n.title,
                    "message": n.message,
                    "type": n.type,
                    "read": n.read,
                    "link": n.link,
                    "action_text": n.action_text,
                    "created_at": n.created_at.isoformat() if n.created_at else "",
                    "read_at": n.read_at.isoformat() if n.read_at else "",
                    "extra_data": json.loads(n.extra_data) if n.extra_data else {},
                }
                for n in notifications
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }, 200


class MarkNotificationReadResource(Resource):
    @auth_required
    def put(self, notification_id):
        """Mark a single notification as read"""
        current_user_obj = current_user()

        notification = Notifications.query.filter_by(
            id=notification_id, user_id=current_user_obj.id
        ).first()

        if not notification:
            return {"error": "Notification not found"}, 404

        notification.read = True
        notification.read_at = datetime.now()
        db.session.commit()

        return {"message": "Notification marked as read"}, 200


class MarkAllNotificationsReadResource(Resource):
    @auth_required
    def post(self):
        """Mark all user's notifications as read"""
        current_user_obj = current_user()

        Notifications.query.filter_by(user_id=current_user_obj.id, read=False).update(
            {Notifications.read: True, Notifications.read_at: datetime.now()}
        )

        db.session.commit()

        return {"message": "All notifications marked as read"}, 200


class DeleteNotificationResource(Resource):
    @auth_required
    def delete(self, notification_id):
        """Delete a notification"""
        current_user_obj = current_user()

        notification = Notifications.query.filter_by(
            id=notification_id, user_id=current_user_obj.id
        ).first()

        if not notification:
            return {"error": "Notification not found"}, 404

        db.session.delete(notification)
        db.session.commit()

        return {"message": "Notification deleted"}, 200


class ClearAllNotificationsResource(Resource):
    @auth_required
    def delete(self):
        """Clear all user's notifications"""
        current_user_obj = current_user()

        Notifications.query.filter_by(user_id=current_user_obj.id).delete()
        db.session.commit()

        return {"message": "All notifications cleared"}, 200


class GetUnreadCountResource(Resource):
    @auth_required
    def get(self):
        """Get unread notification count"""
        current_user_obj = current_user()

        count = Notifications.query.filter_by(
            user_id=current_user_obj.id, read=False
        ).count()

        return {"unread_count": count}, 200
