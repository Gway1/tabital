# resources/customer_support.py (Complete version)
from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from flask import request as flask_request
from ..models.user import User
from ..models.support_ticket import SupportTicket, TicketMessage
from ..extensions import db
from datetime import datetime
import json


def safe_str(v):
    return v if v is not None else ""


class CustomerCreateTicketResource(Resource):
    @auth_required
    def post(self):
        """Create a new support ticket"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        subject = data.get("subject")
        message = data.get("message")
        category = data.get("category")
        priority = data.get("priority", "medium")

        if not subject or not message or not category:
            return {"error": "Subject, message and category are required"}, 400

        ticket = SupportTicket(
            ticket_id=SupportTicket.generate_ticket_id(SupportTicket),
            user_id=current_customer.id,
            subject=subject,
            message=message,
            category=category,
            priority=priority,
            status="open",
        )

        db.session.add(ticket)
        db.session.commit()

        return {
            "message": "Support ticket created successfully",
            "ticket_id": ticket.ticket_id,
            "id": ticket.id,
        }, 201


class CustomerGetTicketsResource(Resource):
    @auth_required
    def get(self):
        """Get customer support tickets"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        status = request.args.get("status", "")
        limit = request.args.get("limit", 50, type=int)

        query = SupportTicket.query.filter_by(user_id=current_customer.id)

        if status:
            query = query.filter(SupportTicket.status == status)

        tickets = query.order_by(SupportTicket.created_at.desc()).limit(limit).all()

        return [
            {
                "id": t.id,
                "ticket_id": t.ticket_id,
                "subject": t.subject,
                "message": t.message[:200],
                "category": t.category,
                "priority": t.priority,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else "",
                "updated_at": t.updated_at.isoformat() if t.updated_at else "",
            }
            for t in tickets
        ]


class CustomerGetTicketDetailsResource(Resource):
    @auth_required
    def get(self, ticket_id):
        """Get specific ticket details"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        ticket = SupportTicket.query.filter_by(
            id=ticket_id, user_id=current_customer.id
        ).first()

        if not ticket:
            return {"error": "Ticket not found"}, 404

        messages = (
            TicketMessage.query.filter_by(ticket_id=ticket.id)
            .order_by(TicketMessage.created_at.asc())
            .all()
        )

        return {
            "id": ticket.id,
            "ticket_id": ticket.ticket_id,
            "subject": ticket.subject,
            "message": ticket.message,
            "category": ticket.category,
            "priority": ticket.priority,
            "status": ticket.status,
            "created_at": ticket.created_at.isoformat() if ticket.created_at else "",
            "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else "",
            "messages": [
                {
                    "id": m.id,
                    "message": m.message,
                    "is_admin": m.is_admin,
                    "admin_name": m.user.full_name if m.is_admin else None,
                    "created_at": m.created_at.isoformat() if m.created_at else "",
                }
                for m in messages
            ],
        }


class CustomerAddTicketMessageResource(Resource):
    @auth_required
    def post(self, ticket_id):
        """Add a message to a ticket"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        ticket = SupportTicket.query.filter_by(
            id=ticket_id, user_id=current_customer.id
        ).first()

        if not ticket:
            return {"error": "Ticket not found"}, 404

        if ticket.status in ["closed", "resolved"]:
            return {"error": "Cannot reply to closed or resolved ticket"}, 400

        data = request.get_json()
        message = data.get("message")

        if not message:
            return {"error": "Message is required"}, 400

        ticket_message = TicketMessage(
            ticket_id=ticket.id,
            user_id=current_customer.id,
            message=message,
            is_admin=False,
        )

        ticket.updated_at = datetime.now()

        db.session.add(ticket_message)
        db.session.commit()

        return {"message": "Reply added successfully"}, 200


class CustomerCloseTicketResource(Resource):
    @auth_required
    def put(self, ticket_id):
        """Close a support ticket"""
        current_customer = current_user()

        if current_customer.role != "customer":
            return {"error": "Unauthorized"}, 403

        ticket = SupportTicket.query.filter_by(
            id=ticket_id, user_id=current_customer.id
        ).first()

        if not ticket:
            return {"error": "Ticket not found"}, 404

        ticket.status = "closed"
        ticket.closed_at = datetime.now()
        db.session.commit()

        return {"message": "Ticket closed successfully"}, 200
