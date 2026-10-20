from flask_restful import Resource
from ..extensions import *


from flask_praetorian import auth_required, current_user


class ProtectedResource(Resource):
    @auth_required  # NOT @guard.auth_required
    def get(self):
        user = current_user()
        return {
            "message": "This is a protected endpoint",
            "user": {"id": user.id, "phone": user.phone, "role": user.role},
        }
