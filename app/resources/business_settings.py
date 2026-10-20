"""Admin business settings: list, change (validated, with a reason) and history."""
from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request

from ..services import business_settings as bs


class AdminBusinessSettingsResource(Resource):
    @auth_required
    def get(self):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        return bs.view(), 200

    @auth_required
    def put(self):
        admin = current_user()
        if admin.role != "admin":
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        try:
            saved = bs.apply_changes(data.get("changes"), data.get("reason"), admin)
        except bs.SettingsError as e:
            return {"error": "Some values need fixing", "errors": e.errors}, 400
        return {"saved": saved, **bs.view()}, 200


class AdminBusinessSettingsHistoryResource(Resource):
    @auth_required
    def get(self):
        if current_user().role != "admin":
            return {"error": "Unauthorized"}, 403
        return {"changes": bs.history()}, 200
