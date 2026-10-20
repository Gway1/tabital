"""Deferment endpoints (CLAUDE.md §4): quote, pay the fee through Paystack, admin list."""
import uuid

from flask import current_app
from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request

from ..extensions import db
from ..models.deferment import Deferment
from ..models.instalment import InstalmentPlan
from ..models.instalment_payment import InstalmentPayment
from ..models.payment_intent import PaymentIntent
from ..services import deferment, paystack
from ..services.ledger import to_pesewas
from ..services.payments import next_payable_instalment
from .paystack_payments import _customer_email


def _plan_and_payment(customer, plan_id, payment_id=None):
    plan = InstalmentPlan.query.filter_by(id=plan_id, customer_id=customer.id).first()
    if not plan:
        return None, None
    if payment_id:
        payment = InstalmentPayment.query.filter_by(
            id=payment_id, plan_id=plan.id
        ).first()
    else:
        payment = next_payable_instalment(plan)
    return plan, payment


class CustomerDefermentResource(Resource):
    @auth_required
    def get(self, plan_id):
        """Fee, new dates and new total for deferring an instalment (shown before the customer agrees)."""
        customer = current_user()
        if customer.role != "customer":
            return {"error": "Unauthorized"}, 403
        plan, payment = _plan_and_payment(
            customer, plan_id, request.args.get("payment_id", type=int)
        )
        if not plan:
            return {"error": "Instalment plan not found"}, 404
        body = deferment.quote(plan, payment)
        body["history"] = [
            d.to_dict()
            for d in Deferment.query.filter_by(plan_id=plan.id)
            .order_by(Deferment.created_at.desc())
            .all()
        ]
        body["online_payment"] = paystack.is_configured()
        return body, 200

    @auth_required
    def post(self, plan_id):
        """Start paying the deferment fee. The dates move once Paystack confirms the payment."""
        customer = current_user()
        if customer.role != "customer":
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        plan, payment = _plan_and_payment(customer, plan_id, data.get("payment_id"))
        if not plan:
            return {"error": "Instalment plan not found"}, 404
        if not data.get("agree"):
            return {
                "error": "Please agree to the deferment fee and the new payment dates"
            }, 400
        reasons = deferment.reasons_not_allowed(plan, payment)
        if reasons:
            return {"error": reasons[0], "reasons": reasons}, 400
        if not paystack.is_configured():
            return {
                "error": "Online payment isn't available right now. Please try again later."
            }, 503

        amount_pesewas = to_pesewas(deferment.fee_for(payment))
        intent = PaymentIntent(
            reference=f"TBD-{plan.id}-{payment.installment_number}-{uuid.uuid4().hex[:12]}",
            purpose=PaymentIntent.DEFERMENT_FEE,
            plan_id=plan.id,
            payment_id=payment.id,
            customer_id=customer.id,
            amount_pesewas=amount_pesewas,
            currency="GHS",
        )
        db.session.add(intent)
        db.session.flush()
        try:
            result = paystack.initialize_transaction(
                email=_customer_email(customer),
                amount_pesewas=amount_pesewas,
                reference=intent.reference,
                callback_url=current_app.config.get("PAYSTACK_CALLBACK_URL"),
                metadata={
                    "plan_id": plan.id,
                    "installment_number": payment.installment_number,
                    "customer_id": customer.id,
                    "purpose": "deferment_fee",
                },
            )
        except paystack.PaystackError as e:
            db.session.rollback()
            current_app.logger.warning("Paystack initialize (deferment) failed: %s", e)
            return {"error": "Could not start the payment. Please try again."}, 502
        db.session.commit()
        return {
            "reference": intent.reference,
            "authorization_url": result.get("authorization_url"),
            "amount": amount_pesewas / 100,
            "currency": "GHS",
            "installment_number": payment.installment_number,
        }, 201


class AdminDefermentsResource(Resource):
    @auth_required
    def get(self):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        q = Deferment.query
        if request.args.get("status"):
            q = q.filter(Deferment.status == request.args["status"])
        rows = q.order_by(Deferment.created_at.desc()).limit(200).all()
        plans = (
            {
                p.id: p
                for p in InstalmentPlan.query.filter(
                    InstalmentPlan.id.in_([d.plan_id for d in rows])
                ).all()
            }
            if rows
            else {}
        )
        return {
            "deferments": [
                {
                    **d.to_dict(),
                    "plan_ref": plans[d.plan_id].plan_id
                    if d.plan_id in plans
                    else None,
                    "customer_name": plans[d.plan_id].customer_name
                    if d.plan_id in plans
                    else None,
                }
                for d in rows
            ]
        }, 200
