"""Phase 6 endpoints: Smile ID identity checks, the Smile ID webhook, employment verification,
and the admin identity / fraud review queues (CLAUDE.md §9)."""
import json
from datetime import datetime

from flask import current_app
from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request

from ..extensions import db
from ..models.identity import FraudSignal, IdentityCheck
from ..models.user import User
from ..services import fraud, identity, smileid

EMPLOYMENT_METHODS = ("employer_call", "employer_letter", "payslip", "ssnit")


def _admin():
    user = current_user()
    return user if user.role == "admin" else None


# ---------------------------------------------------------------- customer


class CustomerIdentityResource(Resource):
    @auth_required
    def get(self):
        user = current_user()
        if user.role != "customer":
            return {"error": "Unauthorized"}, 403
        identity.expire_stale(user)
        check = identity.latest(user)
        if check and check.status == IdentityCheck.SUBMITTED:
            try:
                identity.refresh(check)  # in case the webhook hasn't arrived
            except smileid.SmileIDError as e:
                current_app.logger.warning("Smile ID status check failed: %s", e)
        return {
            **identity.status_view(user),
            "employment_verified": bool(user.employment_verified_at),
        }, 200


class CustomerIdentityStartResource(Resource):
    @auth_required
    def post(self):
        user = current_user()
        data = request.get_json() or {}
        try:
            return identity.start(user, bool(data.get("consent"))), 200
        except identity.IdentityError as e:
            return {"error": str(e)}, 400
        except smileid.SmileIDError as e:
            current_app.logger.error("Smile ID token failed: %s", e)
            return {
                "error": "The identity service isn't available right now. Please try again shortly."
            }, 502


class CustomerIdentitySubmittedResource(Resource):
    @auth_required
    def post(self, check_id):
        user = current_user()
        data = request.get_json() or {}
        try:
            check = identity.mark_submitted(user, check_id, data.get("job_id"))
        except identity.IdentityError as e:
            return {"error": str(e)}, 400
        return {
            "message": "Thanks. We're checking your identity; this usually takes a few minutes.",
            "check": check.to_dict(),
        }, 200


# ---------------------------------------------------------------- Smile ID webhook


class SmileIDWebhookResource(Resource):
    def post(self):
        body = request.get_json(silent=True) or {}
        status, message = identity.handle_webhook(body, request.headers)
        return {"status": message}, status


# ---------------------------------------------------------------- admin: identity checks


class AdminIdentityChecksResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        identity.expire_stale()
        q = IdentityCheck.query
        status = request.args.get("status", IdentityCheck.REVIEW)
        if status:
            q = q.filter(IdentityCheck.status == status)
        rows = q.order_by(IdentityCheck.created_at.desc()).limit(200).all()
        return {
            "checks": [c.to_dict(admin=True) for c in rows],
            "smileid_configured": smileid.is_configured(),
            "smileid_sandbox": smileid.is_sandbox(),
            "biometric_required": identity.biometric_required(),
        }, 200


class AdminIdentityDecideResource(Resource):
    @auth_required
    def post(self, check_id):
        admin = _admin()
        if not admin:
            return {"error": "Unauthorized"}, 403
        check = IdentityCheck.query.get(check_id)
        if not check:
            return {"error": "Check not found"}, 404
        data = request.get_json() or {}
        try:
            identity.admin_decide(
                check, admin, bool(data.get("approve")), data.get("note")
            )
        except identity.IdentityError as e:
            return {"error": str(e)}, 400
        return {"check": check.to_dict(admin=True)}, 200


# ---------------------------------------------------------------- admin: employment verification (§9B)


class AdminEmploymentVerificationResource(Resource):
    @auth_required
    def put(self, customer_id):
        admin = _admin()
        if not admin:
            return {"error": "Unauthorized"}, 403
        customer = User.query.filter_by(id=customer_id, role="customer").first()
        if not customer:
            return {"error": "Customer not found"}, 404
        data = request.get_json() or {}
        note = (data.get("note") or "").strip()
        if data.get("verified"):
            method = data.get("method")
            if method not in EMPLOYMENT_METHODS:
                return {
                    "error": f"method must be one of {', '.join(EMPLOYMENT_METHODS)}"
                }, 400
            if len(note) < 5:
                return {
                    "error": "Add a note: who confirmed the employment and when"
                }, 400
            customer.employment_verified_at = datetime.utcnow()
            customer.employment_verified_by = admin.id
            customer.employment_verification_method = method
            customer.employment_verification_note = note[:500]
        else:
            customer.employment_verified_at = None
            customer.employment_verified_by = admin.id
            customer.employment_verification_method = None
            customer.employment_verification_note = note[:500] or "Verification removed"
        db.session.commit()
        return {
            "employment_verified": bool(customer.employment_verified_at),
            "employment_verified_at": customer.employment_verified_at.isoformat()
            if customer.employment_verified_at
            else None,
            "method": customer.employment_verification_method,
            "note": customer.employment_verification_note,
        }, 200


# ---------------------------------------------------------------- admin: fraud signals


class AdminFraudSignalsResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        q = FraudSignal.query
        status = request.args.get("status", FraudSignal.OPEN)
        if status:
            q = q.filter(FraudSignal.status == status)
        if request.args.get("severity"):
            q = q.filter(FraudSignal.severity == request.args["severity"])
        if request.args.get("user_id", type=int):
            uid = request.args.get("user_id", type=int)
            q = q.filter(
                db.or_(FraudSignal.user_id == uid, FraudSignal.related_user_id == uid)
            )
        rows = q.order_by(FraudSignal.created_at.desc()).limit(300).all()
        return {
            "signals": [s.to_dict() for s in rows],
            "open_counts": fraud.summary(),
        }, 200


class AdminFraudSignalResource(Resource):
    @auth_required
    def put(self, signal_id):
        admin = _admin()
        if not admin:
            return {"error": "Unauthorized"}, 403
        signal = FraudSignal.query.get(signal_id)
        if not signal:
            return {"error": "Signal not found"}, 404
        data = request.get_json() or {}
        status = data.get("status")
        note = (data.get("note") or "").strip()
        if status not in (FraudSignal.CLEARED, FraudSignal.CONFIRMED):
            return {"error": "status must be cleared or confirmed"}, 400
        if len(note) < 5:
            return {"error": "Add a note explaining the decision"}, 400
        signal.status = status
        signal.reviewed_by, signal.reviewed_at, signal.review_note = (
            admin.id,
            datetime.utcnow(),
            note[:500],
        )
        details = json.loads(signal.details_json) if signal.details_json else {}
        details.setdefault("history", []).append(
            {
                "status": status,
                "by": admin.id,
                "at": signal.reviewed_at.isoformat(),
                "note": note[:500],
            }
        )
        signal.details_json = json.dumps(details)
        db.session.commit()
        return {"signal": signal.to_dict()}, 200
