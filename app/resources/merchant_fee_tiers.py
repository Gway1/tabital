"""Merchant fee tiers (§6.1): the options, and management changing a merchant's tier with a reason.
Changes are Management-only (enforced centrally, services/access.py) and logged in setting_changes."""
import json

from flask import request
from flask_praetorian import auth_required, current_user
from flask_restful import Resource

from ..extensions import db
from ..models.system_settings import SettingChange
from ..models.user import User
from ..services import merchant_fees


class AdminMerchantFeeTiersResource(Resource):
    @auth_required
    def get(self):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        return {
            "tiers": merchant_fees.options(),
            "default": merchant_fees.STANDARD,
        }, 200


class AdminMerchantFeeTierResource(Resource):
    @auth_required
    def get(self, merchant_id):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        merchant = User.query.filter_by(id=merchant_id, role="merchant").first()
        if not merchant:
            return {"error": "Merchant not found"}, 404
        history = (
            SettingChange.query.filter_by(
                setting_key=f"merchant_fee_tier:{merchant.id}"
            )
            .order_by(SettingChange.created_at.desc())
            .limit(50)
            .all()
        )
        return {
            **merchant_fees.describe(merchant),
            "tiers": merchant_fees.options(),
            "history": [
                {
                    "from": json.loads(h.old_value) if h.old_value else None,
                    "to": json.loads(h.new_value),
                    "by": (h.changer.full_name or h.changer.phone)
                    if h.changer
                    else None,
                    "reason": h.reason,
                    "at": h.created_at.isoformat() if h.created_at else None,
                }
                for h in history
            ],
        }, 200

    @auth_required
    def put(self, merchant_id):
        admin = current_user()
        if admin.role != "admin":
            return {"error": "Unauthorized"}, 403
        merchant = User.query.filter_by(id=merchant_id, role="merchant").first()
        if not merchant:
            return {"error": "Merchant not found"}, 404
        data = request.get_json() or {}
        try:
            merchant_fees.set_tier(
                merchant, data.get("fee_tier"), admin, data.get("reason")
            )
        except merchant_fees.FeeTierError as e:
            return {"error": str(e)}, 400
        db.session.commit()
        return {
            **merchant_fees.describe(merchant),
            "message": "Fee tier saved. It applies to orders approved from now on; existing plans keep their fee.",
        }, 200
