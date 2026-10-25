# resources/gateway.py - merchant payment gateway (hosted checkout)
from datetime import datetime

from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request

from ..docs import BOOL, INT, MESSAGE, NUM, STR, api_doc, obj
from ..extensions import db
from ..models.gateway import CheckoutSession, MerchantApiKey, WebhookDelivery
from ..models.user import User
from ..services import gateway, webhooks
from .customer_purchase import place_order

SESSION_SCHEMA = obj(
    {
        "session_id": INT,
        "reference": STR,
        "amount": NUM,
        "currency": STR,
        "status": STR,
        "checkout_url": STR,
        "expires_at": STR,
        "order_id": STR,
    }
)


def _merchant():
    user = current_user()
    if user.role != "merchant":
        return None
    return user


def _gateway_off(merchant):
    """403 body when an admin has switched the gateway off for this merchant, else None."""
    if merchant.gateway_enabled is False:
        return {
            "error": "The payment gateway isn't enabled for your account. Contact Tabital support.",
            "code": "gateway_disabled",
        }, 403
    return None


def _key_view(key):
    return {
        "id": key.id,
        "label": key.label,
        "key": f"{key.key_prefix}…{key.last4}",
        "status": key.status,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "last_used_at": key.last_used_at.isoformat() if key.last_used_at else None,
    }


# ---------------------------------------------------------------- merchant dashboard (JWT)


class MerchantGatewayKeysResource(Resource):
    @api_doc(
        "List the merchant's gateway API keys",
        responses={200: ("Keys (the secret is never shown again)", obj({"keys": {"type": "array", "items": obj({"id": INT, "label": STR, "key": STR, "status": STR})}}))},
    )
    @auth_required
    def get(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        keys = (
            MerchantApiKey.query.filter_by(merchant_id=merchant.id)
            .order_by(MerchantApiKey.id.desc())
            .all()
        )
        return {"keys": [_key_view(k) for k in keys]}, 200

    @api_doc(
        "Create a gateway API key",
        description="The secret is returned once. Keep it on your server; never put it in browser code.",
        body=obj({"label": STR}),
        responses={201: ("Key created", obj({"id": INT, "api_key": STR})), 409: "Conflict"},
    )
    @auth_required
    def post(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        if merchant.status not in ("approved", "active"):
            return {"error": "Your merchant account must be approved first"}, 403
        if _gateway_off(merchant):
            return _gateway_off(merchant)
        data = request.get_json(silent=True) or {}
        try:
            key, secret = gateway.create_api_key(merchant, data.get("label"))
        except gateway.GatewayError as e:
            return {"error": e.message}, e.status
        db.session.commit()
        return {
            **_key_view(key),
            "api_key": secret,
            "message": "Copy this key now: it won't be shown again",
        }, 201


class MerchantGatewayKeyResource(Resource):
    @api_doc("Revoke a gateway API key", responses={200: ("Revoked", MESSAGE), 404: "Not found"})
    @auth_required
    def delete(self, key_id):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        key = MerchantApiKey.query.filter_by(id=key_id, merchant_id=merchant.id).first()
        if not key:
            return {"error": "Key not found"}, 404
        key.status = "revoked"
        key.revoked_at = datetime.utcnow()
        db.session.commit()
        return {"message": "API key revoked"}, 200


class MerchantGatewaySettingsResource(Resource):
    @api_doc(
        "Gateway settings: storefront URL and webhook",
        responses={
            200: (
                "Settings",
                obj({"enabled": BOOL, "storefront_url": STR, "webhook_url": STR, "webhook_secret_last4": STR}),
            )
        },
    )
    @auth_required
    def get(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        return self._view(merchant), 200

    @staticmethod
    def _view(merchant):
        return {
            "enabled": merchant.gateway_enabled is not False,
            "account_approved": merchant.status in ("approved", "active"),
            "storefront_url": merchant.shop_url,
            "webhook_url": merchant.webhook_url,
            "webhook_secret_last4": (merchant.webhook_secret or "")[-4:] or None,
        }

    @api_doc(
        "Update the storefront URL and webhook URL",
        description="`rotate_webhook_secret` (or setting a webhook URL for the first time) returns a new "
        "signing secret, shown once. Webhooks are signed `X-Tabital-Signature: t=<unix>,v1=<HMAC-SHA256(secret, t.body)>`.",
        body=obj({"storefront_url": STR, "webhook_url": STR, "rotate_webhook_secret": BOOL}),
        responses={200: ("Updated", obj({"webhook_secret": STR})), 400: "Invalid request"},
    )
    @auth_required
    def put(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        if _gateway_off(merchant):
            return _gateway_off(merchant)
        data = request.get_json(silent=True) or {}
        new_secret = None
        try:
            if "storefront_url" in data:
                merchant.shop_url = (
                    gateway.validate_storefront_url(data["storefront_url"])
                    if data["storefront_url"]
                    else None
                )
            if "webhook_url" in data:
                if data["webhook_url"]:
                    merchant.webhook_url = gateway.check_webhook_url(data["webhook_url"])
                    if not merchant.webhook_secret:
                        new_secret = webhooks.new_secret()
                else:
                    merchant.webhook_url = None
        except gateway.GatewayError as e:
            db.session.rollback()
            return {"error": e.message}, e.status
        if data.get("rotate_webhook_secret") and merchant.webhook_url:
            new_secret = webhooks.new_secret()
        if new_secret:
            merchant.webhook_secret = new_secret
        db.session.commit()
        body = self._view(merchant)
        if new_secret:
            body["webhook_secret"] = new_secret
            body["message"] = "Copy the webhook secret now: it won't be shown again"
        return body, 200


class MerchantGatewaySessionsResource(Resource):
    @api_doc("Recent checkout sessions", query={"limit": (INT, "Default 50, max 200")},
             responses={200: ("Sessions", obj({"sessions": {"type": "array", "items": SESSION_SCHEMA}}))})
    @auth_required
    def get(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
        except ValueError:
            limit = 50
        rows = (
            CheckoutSession.query.filter_by(merchant_id=merchant.id)
            .order_by(CheckoutSession.id.desc())
            .limit(limit)
            .all()
        )
        return {"sessions": [gateway.session_view(s, include_url=False) for s in rows]}, 200


class MerchantGatewayWebhooksResource(Resource):
    @api_doc("Recent webhook deliveries", responses={200: ("Deliveries", obj({"deliveries": {"type": "array", "items": obj({"id": INT, "event": STR, "status": STR, "attempts": INT})}}))})
    @auth_required
    def get(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        rows = (
            WebhookDelivery.query.filter_by(merchant_id=merchant.id)
            .order_by(WebhookDelivery.id.desc())
            .limit(100)
            .all()
        )
        return {
            "deliveries": [
                {
                    "id": r.id,
                    "event": r.event,
                    "status": r.status,
                    "attempts": r.attempts,
                    "last_status_code": r.last_status_code,
                    "last_error": r.last_error,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        }, 200


# ---------------------------------------------------------------- server-to-server (API key)


class GatewaySessionsResource(Resource):
    @api_doc(
        "Create a hosted checkout session (server-to-server)",
        description="Authenticate with `Authorization: Bearer tb_sk_...`. Send the customer to `checkout_url`. "
        "`success_url` and `cancel_url` must be on your registered storefront. Repeating a `reference` "
        "returns the same open session.",
        body=obj(
            {
                "reference": STR,
                "amount": NUM,
                "currency": STR,
                "description": STR,
                "items": {"type": "array", "items": obj({"name": STR, "quantity": INT, "unit_price": NUM})},
                "customer_hint": STR,
                "medium": {**STR, "description": '"ecommerce" (default) or "pos"'},
                "customer_phone": {**STR, "description": "POS only: the customer's Tabital phone number"},
                "channels": {"type": "array", "items": STR, "description": 'POS only: any of "sms", "email", "in_app"'},
                "terminal_id": STR,
                "success_url": STR,
                "cancel_url": STR,
            },
            required=["reference", "amount"],
        ),
        responses={
            201: ("Session created", SESSION_SCHEMA),
            200: ("Existing open session for this reference", SESSION_SCHEMA),
            400: "Invalid request",
            401: "Invalid API key",
            409: "Conflict",
        },
    )
    @gateway.gateway_auth
    def post(self, merchant):
        try:
            session, created = gateway.create_session(merchant, request.get_json(silent=True))
        except gateway.GatewayError as e:
            db.session.rollback()
            body = {"error": e.message}
            if e.code:
                body["code"] = e.code
            return body, e.status
        db.session.commit()
        view = gateway.session_view(session)
        if created and session.medium == gateway.POS:
            data = request.get_json(silent=True) or {}
            channels = data.get("channels")
            try:
                gateway.notify_customer(
                    session, merchant, channels if isinstance(channels, list) else None
                )
            except gateway.GatewayError as e:
                session.status = CheckoutSession.CANCELLED  # nothing was sent, so nobody can pay it
                db.session.commit()
                return {"error": e.message}, e.status
            view = gateway.session_view(session)
        return view, 201 if created else 200


class GatewaySessionResource(Resource):
    @api_doc("Look up a checkout session", responses={200: ("Session", SESSION_SCHEMA), 404: "Not found"})
    @gateway.gateway_auth
    def get(self, merchant, session_id):
        session = CheckoutSession.query.filter_by(id=session_id, merchant_id=merchant.id).first()
        if not session:
            return {"error": "Session not found"}, 404
        return gateway.session_view(session), 200


class GatewaySessionCancelResource(Resource):
    @api_doc(
        "Cancel an unpaid checkout session (e.g. the cashier changed the sale)",
        responses={200: ("Session", SESSION_SCHEMA), 404: "Not found", 409: "Already used"},
    )
    @gateway.gateway_auth
    def post(self, merchant, session_id):
        session = CheckoutSession.query.filter_by(id=session_id, merchant_id=merchant.id).first()
        if not session:
            return {"error": "Session not found"}, 404
        if session.status == CheckoutSession.USED:
            return {"error": "This checkout already has an order"}, 409
        session.status = CheckoutSession.CANCELLED
        db.session.commit()
        return gateway.session_view(session), 200


class GatewaySessionResendResource(Resource):
    @api_doc(
        "Send a POS customer the checkout link again",
        body=obj({"channels": {"type": "array", "items": STR}}),
        responses={200: ("Per-channel result", SESSION_SCHEMA), 404: "Not found", 409: "Not a POS session or closed"},
    )
    @gateway.gateway_auth
    def post(self, merchant, session_id):
        session = CheckoutSession.query.filter_by(id=session_id, merchant_id=merchant.id).first()
        if not session:
            return {"error": "Session not found"}, 404
        if session.medium != gateway.POS or not session.is_open():
            return {"error": "Only an open POS checkout can be resent"}, 409
        channels = (request.get_json(silent=True) or {}).get("channels")
        try:
            gateway.notify_customer(
                session, merchant, channels if isinstance(channels, list) else None, resend=True
            )
        except gateway.GatewayError as e:
            return {"error": e.message}, e.status
        return gateway.session_view(session), 200


class GatewayOrderResource(Resource):
    @api_doc(
        "Look up an order by your reference or Tabital order id",
        responses={200: ("Order", obj({"order_id": STR, "reference": STR, "status": STR})), 404: "Not found"},
    )
    @gateway.gateway_auth
    def get(self, merchant, ref):
        from ..models.purchase_order import PurchaseOrder

        order = (
            PurchaseOrder.query.filter(
                PurchaseOrder.merchant_id == merchant.id,
                PurchaseOrder.checkout_session_id.isnot(None),
                (PurchaseOrder.order_id == ref) | (PurchaseOrder.merchant_reference == ref),
            )
            .order_by(PurchaseOrder.id.desc())
            .first()
        )
        if not order:
            return {"error": "Order not found"}, 404
        return webhooks.order_payload(order, "order.status")["data"], 200


# ---------------------------------------------------------------- customer checkout (JWT)


def _load_session(token, lock=False):
    query = CheckoutSession.query.filter_by(token=token)
    return (query.with_for_update() if lock else query).first()


class CustomerCheckoutSessionResource(Resource):
    @api_doc(
        "Show the checkout a merchant's website sent the customer to",
        responses={200: ("Merchant, amount and items", SESSION_SCHEMA), 404: "Not found"},
    )
    @auth_required
    def get(self, token):
        customer = current_user()
        if customer.role != "customer":
            return {"error": "Only Tabital customers can use this checkout. Sign up as a customer first."}, 403
        session = _load_session(token)
        if not session:
            return {"error": "Checkout not found"}, 404
        merchant = User.query.get(session.merchant_id)
        view = gateway.session_view(session, include_url=False)
        view.update(
            merchant_name=merchant.business_name or merchant.full_name,
            cancel_url=session.cancel_url,
            success_url=session.success_url,
            customer_hint=session.customer_hint,
        )
        if session.medium == "pos" and session.customer_id != customer.id:
            return {"error": "This checkout was sent to a different customer"}, 403
        if session.customer_id and session.customer_id != customer.id:
            return {"error": "This checkout was already used"}, 410
        view["terminal_id"] = session.terminal_id
        return view, 200


class CustomerCheckoutPurchaseResource(Resource):
    @api_doc(
        "Pay for a gateway checkout with a Tabital plan",
        body=obj(
            {"number_of_installments": INT, "delivery_address": STR, "accept_terms": BOOL},
            required=["accept_terms"],
        ),
        responses={
            201: ("Order created; `authorization_url` is the down payment page", obj({"order_id": STR, "authorization_url": STR})),
            403: "Not allowed",
            404: "Not found",
            409: "Conflict",
            410: "Checkout expired or used",
        },
    )
    @auth_required
    def post(self, token):
        customer = current_user()
        if customer.role != "customer":
            return {"error": "Only Tabital customers can use this checkout. Sign up as a customer first."}, 403
        if customer.status not in ("approved", "active"):
            return {"error": "Your account isn't approved for new purchases yet"}, 403
        if customer.kyc_status != "verified":
            return {"error": "Complete KYC verification before making a purchase"}, 403

        data = request.get_json(silent=True) or {}
        if data.get("accept_terms") is not True:
            return {
                "error": "Please read and accept the Terms and Conditions to continue",
                "code": "terms_not_accepted",
            }, 400
        try:
            installments = int(data.get("number_of_installments", 4))
        except (TypeError, ValueError):
            return {"error": "number_of_installments must be a number"}, 400
        delivery_address = (data.get("delivery_address") or "").strip()
        if not delivery_address:
            return {"error": "Delivery address is required"}, 400

        session = _load_session(token, lock=True)
        if not session:
            return {"error": "Checkout not found"}, 404
        if not session.is_open():
            return {"error": "This checkout has expired or was already used", "code": "checkout_closed"}, 410

        if session.medium == "pos" and session.customer_id != customer.id:
            return {"error": "This checkout was sent to a different customer"}, 403

        merchant = User.query.get(session.merchant_id)
        if (
            not merchant
            or merchant.role != "merchant"
            or merchant.status not in ("approved", "active")
            or merchant.gateway_enabled is False
        ):
            return {"error": "Merchant not available"}, 404

        import json as _json

        items = _json.loads(session.items) if session.items else []
        name = (
            session.description
            or (items[0]["name"] if len(items) == 1 else None)
            or (f"{len(items)} items" if items else None)
            or f"Order {session.merchant_reference}"
        )[:150]
        return place_order(
            customer,
            merchant=merchant,
            price=session.amount,
            quantity=1,
            installments=installments,
            delivery_address=delivery_address,
            session=session,
            item_name=f"{name} ({merchant.business_name or merchant.full_name})"[:200],
            item_description=session.description or "",
        )
