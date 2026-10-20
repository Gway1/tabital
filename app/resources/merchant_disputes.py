from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..models.dispute import Dispute
from ..extensions import db
from datetime import datetime
from sqlalchemy import or_


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class MerchantGetDisputesResource(Resource):
    @auth_required
    def get(self):
        """Get all disputes for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        status = request.args.get("status", "").strip()
        reason = request.args.get("reason", "").strip()
        search = request.args.get("search", "").strip()
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        limit = request.args.get("limit", 20, type=int)
        page = request.args.get("page", 1, type=int)

        # Base query
        query = Dispute.query.filter_by(merchant_id=current_merchant.id)

        # Apply filters
        if status:
            query = query.filter_by(status=status)
        if reason:
            query = query.filter_by(reason=reason)
        if search:
            query = query.filter(
                or_(
                    Dispute.dispute_id.ilike(f"%{search}%"),
                    Dispute.description.ilike(f"%{search}%"),
                )
            )
        if start_date:
            query = query.filter(Dispute.created_at >= start_date)
        if end_date:
            query = query.filter(Dispute.created_at <= end_date)

        # Pagination
        total = query.count()
        disputes = (
            query.order_by(Dispute.created_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )

        return {
            "disputes": [
                {
                    "id": d.id,
                    "dispute_id": safe_str(d.dispute_id),
                    "transaction_id": d.transaction_id,
                    "transaction_ref": safe_str(d.transaction.transaction_id)
                    if d.transaction
                    else "",
                    "customer_name": safe_str(
                        d.customer.full_name
                        or d.customer.business_name
                        or d.customer.phone
                    ),
                    "customer_phone": safe_str(d.customer.phone),
                    "reason": safe_str(d.reason),
                    "description": safe_str(d.description),
                    "amount": safe_float(d.amount),
                    "status": safe_str(d.status),
                    "resolution": safe_str(d.resolution) if d.resolution else "",
                    "created_at": d.created_at.isoformat() if d.created_at else "",
                    "updated_at": d.updated_at.isoformat() if d.updated_at else "",
                }
                for d in disputes
            ],
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }


class MerchantGetDisputeStatsResource(Resource):
    @auth_required
    def get(self):
        """Get dispute statistics for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        disputes = Dispute.query.filter_by(merchant_id=current_merchant.id).all()

        total_disputes = len(disputes)
        open_disputes = len([d for d in disputes if d.status == "open"])
        under_review = len([d for d in disputes if d.status == "under_review"])
        resolved = len([d for d in disputes if d.status == "resolved"])
        closed = len([d for d in disputes if d.status == "closed"])

        # Disputes by reason
        reason_breakdown = {}
        reasons = [
            "product_not_received",
            "defective",
            "not_as_described",
            "unauthorized",
            "other",
        ]
        for reason in reasons:
            count = len([d for d in disputes if d.reason == reason])
            if count > 0:
                reason_breakdown[reason] = count

        # Total amount in dispute
        total_amount = sum(d.amount for d in disputes)

        # Resolved amounts
        refunded_amount = sum(
            d.refund_amount for d in disputes if d.resolution == "refunded"
        )

        # Win rate (merchant won vs customer won)
        merchant_won = len([d for d in disputes if d.resolution == "merchant_won"])
        customer_won = len([d for d in disputes if d.resolution == "customer_won"])

        return {
            "total_disputes": total_disputes,
            "open_disputes": open_disputes,
            "under_review": under_review,
            "resolved": resolved,
            "closed": closed,
            "reason_breakdown": reason_breakdown,
            "total_amount": safe_float(total_amount),
            "refunded_amount": safe_float(refunded_amount),
            "merchant_won": merchant_won,
            "customer_won": customer_won,
            "win_rate": (merchant_won / (merchant_won + customer_won) * 100)
            if (merchant_won + customer_won) > 0
            else 0,
        }


class MerchantGetDisputeDetailsResource(Resource):
    @auth_required
    def get(self, dispute_id):
        """Get detailed dispute information"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        dispute = Dispute.query.get(dispute_id)

        if not dispute or dispute.merchant_id != current_merchant.id:
            return {"error": "Dispute not found"}, 404

        return {
            "id": dispute.id,
            "dispute_id": safe_str(dispute.dispute_id),
            "transaction_id": dispute.transaction_id,
            "transaction_ref": safe_str(dispute.transaction.transaction_id)
            if dispute.transaction
            else "",
            "transaction_amount": safe_float(dispute.transaction.amount)
            if dispute.transaction
            else 0,
            "transaction_date": dispute.transaction.transaction_date.isoformat()
            if dispute.transaction and dispute.transaction.transaction_date
            else "",
            "customer_id": dispute.customer_id,
            "customer_name": safe_str(
                dispute.customer.full_name
                or dispute.customer.business_name
                or dispute.customer.phone
            ),
            "customer_phone": safe_str(dispute.customer.phone),
            "customer_email": safe_str(
                dispute.customer.business_email or dispute.customer.email
            ),
            "reason": safe_str(dispute.reason),
            "description": safe_str(dispute.description),
            "amount": safe_float(dispute.amount),
            "status": safe_str(dispute.status),
            "resolution": safe_str(dispute.resolution) if dispute.resolution else "",
            "resolution_notes": safe_str(dispute.resolution_notes),
            "refund_amount": safe_float(dispute.refund_amount),
            "merchant_notes": safe_str(dispute.merchant_notes),
            "customer_notes": safe_str(dispute.customer_notes),
            "admin_notes": safe_str(dispute.admin_notes),
            "evidence_notes": safe_str(dispute.evidence_notes),
            "created_at": dispute.created_at.isoformat() if dispute.created_at else "",
            "resolved_at": dispute.resolved_at.isoformat()
            if dispute.resolved_at
            else "",
        }


class MerchantUpdateDisputeResource(Resource):
    @auth_required
    def put(self, dispute_id):
        """Update dispute with merchant response"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        dispute = Dispute.query.get(dispute_id)

        if not dispute or dispute.merchant_id != current_merchant.id:
            return {"error": "Dispute not found"}, 404

        data = request.get_json() or {}

        # Merchants add their side of the story; only Tabital resolves a dispute
        # (PUT /admin/disputes/<id>/resolve), which unpauses or cancels the plan through the ledger.
        if "status" in data:
            return {
                "error": "Only Tabital can resolve a dispute. Add your response, or escalate it."
            }, 400
        if "merchant_notes" in data:
            dispute.merchant_notes = (data["merchant_notes"] or "")[:2000]

        db.session.commit()

        return {"message": "Your response has been added"}, 200


def _merchant_response(dispute, text):
    """Record a merchant's response for the admin who resolves the dispute. Never resolves it."""
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    note = f"[{stamp}] {text}"
    dispute.merchant_notes = (
        f"{dispute.merchant_notes}\n{note}" if dispute.merchant_notes else note
    )
    if dispute.status not in ("resolved", "closed", "escalated"):
        dispute.status = "under_review"


class MerchantAcceptDisputeResource(Resource):
    @auth_required
    def post(self, dispute_id):
        """Accept dispute and process refund"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        dispute = Dispute.query.get(dispute_id)

        if not dispute or dispute.merchant_id != current_merchant.id:
            return {"error": "Dispute not found"}, 404

        if dispute.status in ("resolved", "closed"):
            return {"error": "This dispute is already closed"}, 400
        data = request.get_json() or {}
        notes = (data.get("notes") or "").strip()[:1000]

        # The merchant agrees with the customer. Tabital resolves it for the customer
        # (plan cancelled, balance written off, sale clawed back from settlements).
        _merchant_response(
            dispute, f"Merchant accepts the customer's claim. {notes}".strip()
        )
        db.session.commit()

        return {
            "message": "Thanks. Tabital will close the dispute in the customer's favour; the sale is then "
            "taken off your settlements."
        }, 200


class MerchantRejectDisputeResource(Resource):
    @auth_required
    def post(self, dispute_id):
        """Reject dispute"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        dispute = Dispute.query.get(dispute_id)

        if not dispute or dispute.merchant_id != current_merchant.id:
            return {"error": "Dispute not found"}, 404

        if dispute.status in ("resolved", "closed"):
            return {"error": "This dispute is already closed"}, 400
        data = request.get_json() or {}
        reason = (data.get("reason") or "").strip()[:1000]
        if len(reason) < 5:
            return {"error": "Say why you disagree (at least 5 characters)"}, 400

        # The merchant contests it; Tabital reviews both sides and resolves it
        _merchant_response(dispute, f"Merchant disputes the claim: {reason}")
        db.session.commit()

        return {
            "message": "Your response has been sent to Tabital, who will review it and decide."
        }, 200


class MerchantEscalateDisputeResource(Resource):
    @auth_required
    def post(self, dispute_id):
        """Escalate dispute to admin"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        dispute = Dispute.query.get(dispute_id)

        if not dispute or dispute.merchant_id != current_merchant.id:
            return {"error": "Dispute not found"}, 404

        if dispute.status in ("resolved", "closed"):
            return {"error": "This dispute is already closed"}, 400
        data = request.get_json() or {}
        notes = (data.get("notes") or "").strip()[:1000]

        _merchant_response(dispute, f"Escalated by merchant. {notes}".strip())
        dispute.status = "escalated"
        dispute.admin_notes = (
            f"{dispute.admin_notes}\nEscalated by merchant: {notes}"
            if dispute.admin_notes
            else f"Escalated by merchant: {notes}"
        )

        db.session.commit()

        return {"message": "Dispute escalated to admin"}, 200
