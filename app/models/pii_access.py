"""Audit log of admins revealing masked personal data (services/pii.py)."""
from datetime import datetime

from ..extensions import db


class PiiAccess(db.Model):
    __tablename__ = "pii_access_log"

    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    field = db.Column(db.String(40), nullable=False)
    reason = db.Column(db.String(300), nullable=False)
    ip = db.Column(db.String(64))
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True
    )

    admin = db.relationship("User", foreign_keys=[admin_id])
    user = db.relationship("User", foreign_keys=[user_id])

    def to_dict(self):
        return {
            "id": self.id,
            "admin": (self.admin.full_name or self.admin.phone) if self.admin else None,
            "user_id": self.user_id,
            "user_name": (self.user.full_name or self.user.business_name)
            if self.user
            else None,
            "field": self.field,
            "reason": self.reason,
            "at": self.created_at.isoformat() if self.created_at else None,
        }
