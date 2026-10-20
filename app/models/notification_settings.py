from ..extensions import db
from datetime import datetime


class NotificationSetting(db.Model):
    __tablename__ = "notification_settings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, unique=True
    )

    # Notification Channels
    email_notifications = db.Column(db.Boolean, default=True)
    sms_notifications = db.Column(db.Boolean, default=True)
    push_notifications = db.Column(db.Boolean, default=True)

    # Alert Types
    transaction_alerts = db.Column(db.Boolean, default=True)
    settlement_alerts = db.Column(db.Boolean, default=True)
    dispute_alerts = db.Column(db.Boolean, default=True)

    # Marketing
    promotional_emails = db.Column(db.Boolean, default=False)
    newsletter = db.Column(db.Boolean, default=False)

    # Reports
    daily_summary = db.Column(db.Boolean, default=True)
    weekly_report = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Relationship
    user = db.relationship("User", backref="notification_settings")
