from flask_restful import Resource
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..extensions import db


class AdminStatsResource(Resource):
    @auth_required
    def get(self):
        current_user_obj = current_user()

        if current_user_obj.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get statistics
        total_users = User.query.count()
        pending_users = User.query.filter_by(status="pending").count()
        approved_users = User.query.filter_by(status="approved").count()
        rejected_users = User.query.filter_by(status="rejected").count()

        # Get role counts
        admin_count = User.query.filter_by(role="admin").count()
        merchant_count = User.query.filter_by(role="merchant").count()
        customer_count = User.query.filter_by(role="customer").count()

        return {
            "total_users": total_users,
            "pending_users": pending_users,
            "approved_users": approved_users,
            "rejected_users": rejected_users,
            "by_role": {
                "admin": admin_count,
                "merchant": merchant_count,
                "customer": customer_count,
            },
        }
