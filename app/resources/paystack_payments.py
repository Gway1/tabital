"""Paystack checkout for instalments (CLAUDE.md §13.1 D11).

Flow: customer asks to pay -> server fixes the amount and starts a Paystack transaction ->
customer pays on Paystack's page -> Paystack redirects back and also sends a signed webhook ->
the server verifies the transaction with Paystack's API and only then marks the instalment paid.
"""
import json
import uuid

from ..docs import BOOL, INT, NUM, STR, api_doc, obj
from flask import current_app, request
from flask_praetorian import auth_required, current_user
from flask_restful import Resource

from ..extensions import db
from ..models.instalment import InstalmentPlan
from ..models.payment_intent import PaymentIntent
from ..services import paystack
from ..services.ledger import to_pesewas
from ..services.payments import apply_verified_transaction, next_payable_instalment


_INTENT_VIEW = obj(
    {
        "reference": STR,
        "purpose": STR,
        "status": STR,
        "outcome": {**STR, "nullable": True},
        "amount": NUM,
        "currency": STR,
        "channel": STR,
        "plan_id": INT,
        "order_id": {**STR, "nullable": True},
        "order_status": {**STR, "nullable": True},
        "installment_number": INT,
    }
)


def _customer_email(user):
    # Paystack requires an email; customers without one get an internal placeholder
    return user.business_email or user.email or f"{user.phone}@customers.tabitalpay.com"


def _new_reference(plan, payment):
    return f"TBP-{plan.id}-{payment.installment_number}-{uuid.uuid4().hex[:12]}"


def _merchant_return(order):
    """Merchant name and success URL when the order came from a gateway checkout."""
    if not order or not order.checkout_session_id:
        return None
    from ..models.gateway import CheckoutSession

    session = CheckoutSession.query.get(order.checkout_session_id)
    if not session:
        return None
    return {
        "merchant_name": order.merchant.business_name or order.merchant.full_name,
        "url": session.success_url,  # None for a POS checkout: there is no website to return to
        "reference": session.merchant_reference,
        "medium": session.medium or "ecommerce",
    }


def _intent_view(intent, outcome=None):
    return {
        "reference": intent.reference,
        "purpose": intent.purpose,
        "status": intent.status,
        "outcome": outcome,
        "amount": intent.amount_pesewas / 100,
        "currency": intent.currency,
        "channel": intent.channel,
        "plan_id": intent.plan_id,
        "order_id": intent.order.order_id if intent.order else None,
        "order_status": intent.order.status if intent.order else None,
        # Gateway orders: where to send the customer back to the merchant's website
        "merchant_return": _merchant_return(intent.order),
        "installment_number": intent.payment.installment_number
        if intent.payment
        else 1,
    }


class CustomerPaymentConfigResource(Resource):
    @api_doc(
        "Whether online (Paystack) payment is available",
        responses={200: ("Flag", obj({"paystack_enabled": BOOL}))},
    )
    @auth_required
    def get(self):
        return {"paystack_enabled": paystack.is_configured()}, 200


class CustomerPaystackInitializeResource(Resource):
    @api_doc(
        "Start a Paystack payment for the next instalment",
        description="The server sets the amount: the instalment plus any unpaid late fee. Send the customer "
        "to `authorization_url`; Paystack then redirects back and sends a webhook.",
        body=obj({"plan_id": INT}, required=["plan_id"]),
        responses={
            201: (
                "Payment started",
                obj(
                    {
                        "reference": STR,
                        "authorization_url": STR,
                        "access_code": STR,
                        "amount": NUM,
                        "currency": STR,
                        "installment_number": INT,
                    }
                ),
            ),
            400: "No payments due on this plan",
            403: "Not a customer",
            404: "Instalment plan not found",
            502: "Paystack couldn't start the payment",
            503: "Online payment isn't available",
        },
    )
    @auth_required
    def post(self):
        customer = current_user()
        if customer.role != "customer":
            return {"error": "Unauthorized"}, 403
        if not paystack.is_configured():
            return {
                "error": "Online payment isn't available right now. Please try again later."
            }, 503

        data = request.get_json() or {}
        plan = InstalmentPlan.query.filter_by(
            id=data.get("plan_id"), customer_id=customer.id
        ).first()
        if not plan:
            return {"error": "Instalment plan not found"}, 404

        payment = next_payable_instalment(plan)
        if not payment:
            return {"error": "No payments due on this plan"}, 400

        # The server decides the amount: the instalment plus any unpaid late fee
        amount_pesewas = to_pesewas(payment.get_total_due())
        intent = PaymentIntent(
            reference=_new_reference(plan, payment),
            purpose=PaymentIntent.INSTALMENT,
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
                },
            )
        except paystack.PaystackError as e:
            db.session.rollback()
            current_app.logger.warning("Paystack initialize failed: %s", e)
            return {"error": "Could not start the payment. Please try again."}, 502

        db.session.commit()
        return {
            "reference": intent.reference,
            "authorization_url": result.get("authorization_url"),
            "access_code": result.get("access_code"),
            "amount": amount_pesewas / 100,
            "currency": "GHS",
            "installment_number": payment.installment_number,
        }, 201


class CustomerPaystackVerifyResource(Resource):
    @api_doc(
        "Confirm a Paystack payment after the customer returns from Paystack",
        description="Asks Paystack, then applies the payment if it succeeded. `outcome` says what happened "
        "(`already_applied` if the webhook got there first).",
        responses={
            200: ("Payment state", _INTENT_VIEW),
            404: "Payment not found",
            502: "Paystack can't confirm yet; it updates automatically",
        },
    )
    @auth_required
    def get(self, reference):
        """Called by the payment-callback page after Paystack redirects the customer back."""
        customer = current_user()
        intent = (
            PaymentIntent.query.filter_by(reference=reference).with_for_update().first()
        )
        if not intent or (
            customer.role != "admin" and intent.customer_id != customer.id
        ):
            return {"error": "Payment not found"}, 404

        if intent.status == PaymentIntent.SUCCESS:
            return _intent_view(intent, "already_applied"), 200

        try:
            data = paystack.verify_transaction(reference)
        except paystack.PaystackError as e:
            db.session.rollback()
            current_app.logger.warning(
                "Paystack verify failed for %s: %s", reference, e
            )
            return {
                "error": "Could not confirm the payment yet. We'll update it automatically."
            }, 502

        outcome = apply_verified_transaction(intent, data)
        db.session.commit()
        return _intent_view(intent, outcome), 200


class PaystackWebhookResource(Resource):
    @api_doc(
        "Paystack server-to-server notifications (charges and merchant transfers)",
        description="Public but signed: the `x-paystack-signature` header is checked. Charges are re-verified "
        "with Paystack before anything is applied.",
        body=obj(
            {
                "event": {
                    "type": "string",
                    "description": "charge.success, transfer.success, transfer.failed, transfer.reversed",
                },
                "data": obj({"reference": STR}),
            }
        ),
        responses={
            200: ("Handled or ignored", obj({"status": STR})),
            400: "Invalid body",
            401: "Invalid signature",
            502: "Verification failed; Paystack will retry",
        },
    )
    def post(self):
        """Paystack server-to-server notifications. Public, but every call must be signed."""
        raw = request.get_data()
        if not paystack.valid_webhook_signature(
            raw, request.headers.get("x-paystack-signature", "")
        ):
            return {"error": "Invalid signature"}, 401

        try:
            event = json.loads(raw or b"{}")
        except ValueError:
            return {"error": "Invalid body"}, 400

        # Merchant payouts (Phase 5)
        if event.get("event") in (
            "transfer.success",
            "transfer.failed",
            "transfer.reversed",
        ):
            from ..services import settlements

            reference = (event.get("data") or {}).get("reference")
            if not reference:
                return {"status": "ignored"}, 200
            try:
                outcome = settlements.handle_transfer_event(event["event"], reference)
            except paystack.PaystackError as e:
                db.session.rollback()
                current_app.logger.warning(
                    "Paystack transfer verify failed for %s: %s", reference, e
                )
                return {"error": "verify failed"}, 502
            return {"status": outcome}, 200

        if event.get("event") != "charge.success":
            return {"status": "ignored"}, 200

        reference = (event.get("data") or {}).get("reference")
        intent = (
            PaymentIntent.query.filter_by(reference=reference).with_for_update().first()
            if reference
            else None
        )
        if not intent:
            # Not one of ours (or not yet committed); acknowledge so Paystack stops retrying
            current_app.logger.warning(
                "Paystack webhook for unknown reference %s", reference
            )
            return {"status": "unknown_reference"}, 200

        # Don't trust the webhook body alone: confirm with Paystack's API
        try:
            data = paystack.verify_transaction(reference)
        except paystack.PaystackError as e:
            db.session.rollback()
            current_app.logger.warning(
                "Paystack verify (webhook) failed for %s: %s", reference, e
            )
            return {"error": "verify failed"}, 502  # non-2xx makes Paystack retry later

        outcome = apply_verified_transaction(intent, data)
        db.session.commit()
        return {"status": outcome}, 200
