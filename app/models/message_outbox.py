"""Outgoing customer messages (SMS, WhatsApp, in-app). One row per message; never sent twice."""
from datetime import datetime

from ..extensions import db


class MessageOutbox(db.Model):
    __tablename__ = "message_outbox"

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    channel = db.Column(db.String(20), nullable=False)  # sms, whatsapp, in_app
    to_address = db.Column(db.String(50))  # phone in +233 format for sms/whatsapp
    template = db.Column(
        db.String(40), nullable=False
    )  # due_in_3, due_today, late_fee_charged, ...
    title = db.Column(db.String(200))
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default=PENDING, index=True)
    attempts = db.Column(db.Integer, default=0)
    last_error = db.Column(db.String(255))
    provider = db.Column(db.String(30))
    provider_message_id = db.Column(db.String(100))
    # Prevents the same reminder going out twice (e.g. the daily job ran twice)
    dedupe_key = db.Column(db.String(120), unique=True, nullable=False)
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=True, index=True
    )
    payment_id = db.Column(
        db.Integer, db.ForeignKey("instalment_payments.id"), nullable=True
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    sent_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "id": self.id,
            "channel": self.channel,
            "template": self.template,
            "title": self.title,
            "body": self.body,
            "status": self.status,
            "provider": self.provider,
            "last_error": self.last_error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }
