# models/activity_log.py
from ..extensions import db
from datetime import datetime


class ActivityLog(db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    action = db.Column(
        db.String(100), nullable=False
    )  # Login, Profile Update, Password Change, etc.
    description = db.Column(db.Text, nullable=False)
    ip_address = db.Column(db.String(50))
    user_agent = db.Column(db.String(500))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship
    user = db.relationship("User", backref="activity_logs")

    @staticmethod
    def log_activity(user_id, action, description, ip_address=None, user_agent=None):
        """Helper method to log user activity"""
        log = ActivityLog(
            user_id=user_id,
            action=action,
            description=description,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.session.add(log)
        db.session.commit()
        return log
