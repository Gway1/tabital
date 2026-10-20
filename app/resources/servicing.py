"""Phase 4 endpoints: saved cards and autopay, disputes (buyer protection), servicing ops."""
from datetime import datetime, timedelta

from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request

from ..docs import BOOL, INT, MESSAGE, STR, api_doc, obj
from ..extensions import db
from ..models.dispute import Dispute
from ..models.instalment import InstalmentPlan
from ..models.instalment_payment import InstalmentPayment
from ..models.message_outbox import MessageOutbox
from ..models.payment_method import PaymentMethod
from ..services import ledger, paystack

_PAYMENT_METHOD = obj(
    {
        "id": INT,
        "channel": STR,
        "card_type": STR,
        "bank": STR,
        "last4": STR,
        "expiry": {**STR, "nullable": True, "description": "month/year"},
        "reusable": BOOL,
        "autopay_enabled": BOOL,
        "is_default": BOOL,
        "created_at": STR,
    }
)

DISPUTE_REASONS = (
    "product_not_received",
    "defective",
    "not_as_described",
    "unauthorized",
    "other",
)


def _customer():
    user = current_user()
    return user if user.role == "customer" else None


def _admin():
    user = current_user()
    return user if user.role == "admin" else None


# ---------------------------------------------------------------- saved cards / autopay


class CustomerPaymentMethodsResource(Resource):
    @api_doc(
        "The customer's saved cards and payment methods",
        responses={
            200: (
                "Methods, newest first",
                obj({"payment_methods": {"type": "array", "items": _PAYMENT_METHOD}}),
            ),
            403: "Not a customer",
        },
    )
    @auth_required
    def get(self):
        customer = _customer()
        if not customer:
            return {"error": "Unauthorized"}, 403
        methods = (
            PaymentMethod.query.filter_by(customer_id=customer.id, revoked_at=None)
            .order_by(PaymentMethod.created_at.desc())
            .all()
        )
        return {"payment_methods": [m.to_dict() for m in methods]}, 200


class CustomerPaymentMethodResource(Resource):
    def _method(self, method_id):
        customer = _customer()
        if not customer:
            return None, ({"error": "Unauthorized"}, 403)
        method = PaymentMethod.query.filter_by(
            id=method_id, customer_id=customer.id, revoked_at=None
        ).first()
        if not method:
            return None, ({"error": "Payment method not found"}, 404)
        return method, None

    @api_doc(
        "Turn autopay on or off for a card, or make it the default",
        body=obj({"autopay_enabled": BOOL, "is_default": BOOL}),
        responses={
            200: ("Saved", obj({"payment_method": _PAYMENT_METHOD, "message": STR})),
            400: "This method can't be charged automatically",
            403: "Not a customer",
            404: "Payment method not found",
        },
    )
    @auth_required
    def put(self, method_id):
        """Turn autopay on/off, or make this the default card."""
        method, error = self._method(method_id)
        if error:
            return error
        data = request.get_json() or {}
        if data.get("is_default"):
            for other in PaymentMethod.query.filter_by(
                customer_id=method.customer_id, revoked_at=None
            ).all():
                other.is_default = other.id == method.id
        if "autopay_enabled" in data:
            if data["autopay_enabled"] and not method.reusable:
                return {
                    "error": "This payment method can't be charged automatically"
                }, 400
            method.autopay_enabled = bool(data["autopay_enabled"])
            if method.autopay_enabled:
                for other in PaymentMethod.query.filter_by(
                    customer_id=method.customer_id, revoked_at=None
                ).all():
                    other.is_default = other.id == method.id
        db.session.commit()
        return {
            "payment_method": method.to_dict(),
            "message": "Autopay is on: we'll charge this card on each due date."
            if method.autopay_enabled
            else "Saved",
        }, 200

    @api_doc(
        "Remove a saved card (also turns off its autopay)",
        responses={
            200: ("Removed", MESSAGE),
            403: "Not a customer",
            404: "Payment method not found",
        },
    )
    @auth_required
    def delete(self, method_id):
        method, error = self._method(method_id)
        if error:
            return error
        method.revoked_at = datetime.utcnow()
        method.autopay_enabled = False
        method.is_default = False
        if paystack.is_configured():
            paystack.deactivate_authorization(method.authorization_code)
        db.session.commit()
        return {"message": "Card removed"}, 200


# ---------------------------------------------------------------- disputes (buyer protection)


def _dispute_view(d, plan=None):
    return {
        "id": d.id,
        "dispute_id": d.dispute_id,
        "reason": d.reason,
        "description": d.description,
        "amount": d.amount,
        "status": d.status,
        "resolution": d.resolution,
        "resolution_notes": d.resolution_notes,
        "refund_amount": d.refund_amount,
        "plan_id": plan.id if plan else None,
        "product_name": plan.plan_name if plan else None,
        "paused": bool(plan and plan.paused_at),
        "respond_by": _respond_by(d),
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
    }


def _respond_by(d):
    from ..models.system_settings import SystemSetting

    days = int(SystemSetting.get_value("dispute_resolution_days", 21))
    return (
        (d.created_at + timedelta(days=days)).date().isoformat()
        if d.created_at
        else None
    )


def _plan_for_dispute(d):
    return InstalmentPlan.query.filter_by(transaction_id=d.transaction_id).first()


class CustomerDisputesResource(Resource):
    @auth_required
    def get(self):
        customer = _customer()
        if not customer:
            return {"error": "Unauthorized"}, 403
        rows = (
            Dispute.query.filter_by(customer_id=customer.id)
            .order_by(Dispute.created_at.desc())
            .all()
        )
        return {"disputes": [_dispute_view(d, _plan_for_dispute(d)) for d in rows]}, 200

    @auth_required
    def post(self):
        """Report a problem with a purchase. Remaining payments are paused while it's reviewed."""
        customer = _customer()
        if not customer:
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        plan = InstalmentPlan.query.filter_by(
            id=data.get("plan_id"), customer_id=customer.id
        ).first()
        if not plan:
            return {"error": "Plan not found"}, 404
        if plan.status != "active":
            return {"error": "Only active plans can be disputed"}, 400
        if not plan.transaction_id:
            return {"error": "This plan has no purchase record to dispute"}, 400
        reason = (data.get("reason") or "").strip()
        description = (data.get("description") or "").strip()
        if reason not in DISPUTE_REASONS:
            return {
                "error": f"Reason must be one of: {', '.join(DISPUTE_REASONS)}"
            }, 400
        if len(description) < 10:
            return {
                "error": "Please describe the problem (at least 10 characters)"
            }, 400
        open_dispute = Dispute.query.filter(
            Dispute.transaction_id == plan.transaction_id,
            Dispute.status.in_(("open", "under_review", "escalated")),
        ).first()
        if open_dispute:
            return {"error": "There's already an open dispute for this purchase"}, 409

        d = Dispute(
            dispute_id=Dispute.generate_dispute_id(Dispute),
            transaction_id=plan.transaction_id,
            merchant_id=plan.merchant_id,
            customer_id=customer.id,
            reason=reason,
            description=description[:5000],
            amount=float(ledger.plan_balance(plan)["outstanding"]),
            evidence_notes=(data.get("evidence_notes") or "")[:5000] or None,
            status="open",
        )
        db.session.add(d)
        plan.paused_at = datetime.utcnow()
        plan.paused_reason = f"Dispute {d.dispute_id}: {reason}"
        from ..services import fraud

        fraud.check_quick_dispute(
            plan, d
        )  # a dispute right after delivery may be a fake sale (§9E)
        db.session.commit()
        return {
            "message": "We've paused your remaining payments while we look into this.",
            "dispute": _dispute_view(d, plan),
        }, 201


class AdminDisputesResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        status = request.args.get("status")
        q = Dispute.query
        if status:
            q = q.filter(Dispute.status == status)
        rows = q.order_by(Dispute.created_at.desc()).limit(200).all()
        return {
            "disputes": [
                {
                    **_dispute_view(d, _plan_for_dispute(d)),
                    "customer_id": d.customer_id,
                    "merchant_id": d.merchant_id,
                    "merchant_notes": d.merchant_notes,
                }
                for d in rows
            ]
        }, 200


class AdminResolveDisputeResource(Resource):
    @auth_required
    def put(self, dispute_id):
        """Resolve a dispute.

        merchant_won: payments resume; unpaid due dates move forward by the days paused, so
                      the customer isn't charged late fees for the pause.
        customer_won: the rest of the balance is written off in the ledger and the plan is
                      cancelled; what the customer already paid is flagged for refund.
        """
        admin = _admin()
        if not admin:
            return {"error": "Unauthorized"}, 403
        d = Dispute.query.get(dispute_id)
        if not d:
            return {"error": "Dispute not found"}, 404
        if d.status in ("resolved", "closed"):
            return {"error": f"Dispute already {d.status}"}, 400
        data = request.get_json() or {}
        outcome = data.get("outcome")
        notes = (data.get("notes") or "").strip()
        if outcome not in ("merchant_won", "customer_won"):
            return {"error": "Outcome must be merchant_won or customer_won"}, 400
        if not notes:
            return {"error": "Resolution notes are required"}, 400

        plan = _plan_for_dispute(d)
        now = datetime.utcnow()
        if plan and outcome == "merchant_won" and plan.paused_at:
            paused_days = max((now.date() - plan.paused_at.date()).days, 0)
            for p in InstalmentPayment.query.filter(
                InstalmentPayment.plan_id == plan.id,
                InstalmentPayment.status.in_(("pending", "overdue")),
            ).all():
                if paused_days:
                    p.due_date = p.due_date + timedelta(days=paused_days)
            if plan.end_date and paused_days:
                plan.end_date = plan.end_date + timedelta(days=paused_days)
            plan.paused_at = None
            plan.paused_reason = None
        elif plan and outcome == "customer_won":
            outstanding = ledger.plan_balance(plan)["outstanding"]
            if outstanding > 0:
                ledger.record(
                    plan,
                    "balance_written_off",
                    -outstanding,
                    note=f"Dispute {d.dispute_id} resolved for customer",
                    user=admin,
                )
            for p in InstalmentPayment.query.filter(
                InstalmentPayment.plan_id == plan.id,
                InstalmentPayment.status.in_(
                    ("pending", "overdue", "pending_verification")
                ),
            ).all():
                p.status = "cancelled"
            plan.status = "cancelled"
            plan.cancelled_at = now
            plan.paused_at = None
            d.refund_amount = float(ledger.plan_balance(plan)["paid"])
            # The merchant doesn't keep the sale: not paid out, or recovered from the next settlement
            from ..services import settlements

            settlements.clawback_plan(
                plan, f"Dispute {d.dispute_id} won by customer", user=admin
            )

        d.status = "resolved"
        d.resolution = outcome
        d.resolution_notes = notes[:5000]
        d.resolved_by = admin.id
        d.resolved_at = now
        db.session.commit()
        message = (
            "Dispute resolved in the merchant's favour; payments resume with due dates moved forward."
            if outcome == "merchant_won"
            else "Dispute resolved in the customer's favour; the remaining balance is written off. "
            "Refund what the customer already paid (see refund_amount)."
        )
        return {"message": message, "dispute": _dispute_view(d, plan)}, 200


# ---------------------------------------------------------------- servicing ops


class AdminRunServicingResource(Resource):
    @auth_required
    def post(self):
        """Run today's servicing now (normally the daily cron job does this)."""
        if not _admin():
            return {"error": "Unauthorized"}, 403
        from ..services import servicing

        summary = servicing.run_daily()
        return {"summary": summary.to_dict()}, 200


class AdminMessagesResource(Resource):
    @auth_required
    def get(self):
        """Recent outgoing messages (reminders), for checking what customers were sent."""
        if not _admin():
            return {"error": "Unauthorized"}, 403
        q = MessageOutbox.query
        if request.args.get("plan_id"):
            q = q.filter(MessageOutbox.plan_id == request.args.get("plan_id", type=int))
        if request.args.get("status"):
            q = q.filter(MessageOutbox.status == request.args["status"])
        rows = q.order_by(MessageOutbox.created_at.desc()).limit(200).all()
        return {"messages": [m.to_dict() for m in rows]}, 200
