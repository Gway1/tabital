# models/notification.py
from ..extensions import db
from datetime import datetime
import json
import uuid


class Notifications(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    notification_id = db.Column(
        db.String(50),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: f"NOT{str(uuid.uuid4())[:8].upper()}",
    )

    # Recipient
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    user_role = db.Column(db.String(50), nullable=False)  # customer, merchant, admin

    # Notifications Details
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    type = db.Column(
        db.String(50), nullable=False
    )  # payment, transaction, kyc, promotion, system, order, reminder

    # Status
    read = db.Column(db.Boolean, default=False)
    read_at = db.Column(db.DateTime)

    # Action
    link = db.Column(db.String(500))
    action_text = db.Column(db.String(100))

    # extra_data (JSON for additional data)
    extra_data = db.Column(db.Text)  # JSON string

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship("User", backref="notifications", foreign_keys=[user_id])

    @classmethod
    def generate_notification_id(cls):
        """Generate a unique notification ID"""
        return f"NOT{str(uuid.uuid4())[:8].upper()}"

    @classmethod
    def create_notification(
        cls,
        user_id,
        user_role,
        title,
        message,
        type,
        link=None,
        action_text=None,
        extra_data=None,
    ):
        """Create a new notification"""
        notification = cls(
            notification_id=cls.generate_notification_id(),
            user_id=user_id,
            user_role=user_role,
            title=title,
            message=message,
            type=type,
            link=link,
            action_text=action_text,
            extra_data=json.dumps(extra_data) if extra_data else None,
        )
        db.session.add(notification)
        db.session.commit()
        return notification

    def mark_as_read(self):
        """Mark notification as read"""
        self.read = True
        self.read_at = datetime.utcnow()
        db.session.commit()

    def to_dict(self):
        """Convert notification to dictionary"""
        return {
            "id": self.id,
            "notification_id": self.notification_id,
            "title": self.title,
            "message": self.message,
            "type": self.type,
            "read": self.read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "link": self.link,
            "action_text": self.action_text,
            "extra_data": json.loads(self.extra_data) if self.extra_data else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
