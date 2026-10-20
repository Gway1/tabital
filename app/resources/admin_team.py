"""Admin team and Management Access (services/access.py). Management only (enforced centrally)."""
import json

from flask import request
from flask_praetorian import auth_required, current_user
from flask_restful import Resource

from ..extensions import db
from ..models.system_settings import SettingChange
from ..models.user import User
from ..services import access


def _admin(u):
    return {
        "id": u.id,
        "name": u.full_name or u.phone,
        "phone": u.phone,
        "status": u.status,
        "admin_level": u.admin_level or access.OPERATIONS,
    }


class AdminTeamResource(Resource):
    @auth_required
    def get(self):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        admins = User.query.filter_by(role="admin").order_by(User.id).all()
        changes = (
            SettingChange.query.filter(SettingChange.setting_key.like("admin_access:%"))
            .order_by(SettingChange.created_at.desc())
            .limit(50)
            .all()
        )
        return {
            "admins": [_admin(u) for u in admins],
            "history": [
                {
                    "admin_id": int(c.setting_key.split(":", 1)[1]),
                    "from": json.loads(c.old_value) if c.old_value else None,
                    "to": json.loads(c.new_value),
                    "by": (c.changer.full_name or c.changer.phone)
                    if c.changer
                    else None,
                    "reason": c.reason,
                    "at": c.created_at.isoformat() if c.created_at else None,
                }
                for c in changes
            ],
        }, 200


class AdminTeamMemberResource(Resource):
    @auth_required
    def put(self, admin_id):
        me = current_user()
        if me.role != "admin":
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        level = data.get("admin_level")
        reason = (data.get("reason") or "").strip()
        if level not in access.LEVELS:
            return {"error": "admin_level must be management or operations"}, 400
        if len(reason) < 5:
            return {
                "error": "Say why (at least 5 characters). Access changes are logged."
            }, 400
        admin = User.query.filter_by(id=admin_id, role="admin").first()
        if not admin:
            return {"error": "Admin not found"}, 404
        old = admin.admin_level or access.OPERATIONS
        if old == level:
            return {"admin": _admin(admin)}, 200
        if old == access.MANAGEMENT:
            others = User.query.filter(
                User.role == "admin",
                User.admin_level == access.MANAGEMENT,
                User.id != admin.id,
            ).count()
            if others == 0:
                return {
                    "error": "There must always be at least one admin with Management Access."
                }, 400
        admin.admin_level = level
        db.session.add(
            SettingChange(
                setting_key=f"admin_access:{admin.id}",
                old_value=json.dumps(old),
                new_value=json.dumps(level),
                changed_by=me.id,
                reason=reason[:500],
            )
        )
        db.session.commit()
        return {"admin": _admin(admin)}, 200
