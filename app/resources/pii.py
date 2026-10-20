"""Reveal one masked personal value, with a reason, and see who revealed what."""
from flask import request
from flask_praetorian import auth_required, current_user
from flask_restful import Resource

from ..extensions import db
from ..models.pii_access import PiiAccess
from ..models.user import User
from ..services import pii


class AdminPiiRevealResource(Resource):
    @auth_required
    def post(self):
        admin = current_user()
        if admin.role != "admin":
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        field = data.get("field")
        reason = (data.get("reason") or "").strip()
        if field not in pii.REVEALABLE:
            return {"error": "That field can't be revealed"}, 400
        if len(reason) < 5:
            return {
                "error": "Say why you need it (at least 5 characters). Every reveal is logged."
            }, 400
        user = User.query.get(data.get("user_id"))
        if not user:
            return {"error": "User not found"}, 404
        from ..services.attempts import client_ip

        ip = (
            client_ip()
        )  # the proxy-added hop: a client can't forge the address in the audit log
        db.session.add(
            PiiAccess(
                admin_id=admin.id,
                user_id=user.id,
                field=field,
                reason=reason[:300],
                ip=ip,
            )
        )
        db.session.commit()
        return {
            "field": field,
            "label": pii.REVEALABLE[field],
            "value": getattr(user, field),
        }, 200


class AdminPiiAccessLogResource(Resource):
    @auth_required
    def get(self):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        q = PiiAccess.query
        if request.args.get("user_id", type=int):
            q = q.filter(PiiAccess.user_id == request.args.get("user_id", type=int))
        rows = q.order_by(PiiAccess.created_at.desc()).limit(200).all()
        return {"reveals": [r.to_dict() for r in rows]}, 200
