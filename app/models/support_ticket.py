# models/support_ticket.py
from ..extensions import db
from datetime import datetime
import sqlalchemy as sa


class SupportTicket(db.Model):
    __tablename__ = "support_tickets"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    subject = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    priority = db.Column(db.String(20), default="medium")
    status = db.Column(db.String(20), default="open")

    attachments = db.Column(db.Text)  # JSON array of file URLs

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    resolved_at = db.Column(db.DateTime)
    closed_at = db.Column(db.DateTime)

    # Relationships
    user = db.relationship("User", backref="support_tickets")
    messages = db.relationship(
        "TicketMessage", backref="ticket", cascade="all, delete-orphan"
    )

    def generate_ticket_id(self):
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(SupportTicket.ticket_id, 5).cast(sa.Integer))
            )
            .filter(SupportTicket.ticket_id.isnot(None))
            .scalar()
        )
        return f"TKT{(result + 1) if result else 1:04d}"


class TicketMessage(db.Model):
    __tablename__ = "ticket_messages"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("support_tickets.id"), nullable=False
    )
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    message = db.Column(db.Text, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    attachments = db.Column(db.Text)  # JSON array of file URLs

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    user = db.relationship("User", backref="ticket_messages")
