from flask_restful import Resource, request
from ..services import merchant_fees
from flask_praetorian import auth_required, current_user
from app.models.user import User
from app.models.instalment import InstalmentPlan
from app.extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_


class AdminUserStatsResource(Resource):
    @auth_required
    def get(self):
        """Get user statistics"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Total Users
        total_users = User.query.count()

        # Total Customers
        total_customers = User.query.filter_by(role="customer").count()

        # Total Merchants
        total_merchants = User.query.filter_by(role="merchant").count()

        # Active Users
        active_users = User.query.filter(
            User.status.in_(["approved", "active"])
        ).count()

        # Pending Users
        pending_users = User.query.filter_by(status="pending").count()

        # Suspended Users
        suspended_users = User.query.filter_by(status="suspended").count()

        # Restricted Users
        restricted_users = User.query.filter_by(status="restricted").count()

        # New Users (last 30 days)
        last_30_days = datetime.now() - timedelta(days=30)
        new_users = User.query.filter(User.created_at >= last_30_days).count()

        # KYC Verified Users
        kyc_verified = User.query.filter_by(kyc_status="verified").count()

        # KYC Pending Users
        kyc_pending = User.query.filter_by(kyc_status="pending").count()

        return {
            "total_users": total_users,
            "total_customers": total_customers,
            "total_merchants": total_merchants,
            "active_users": active_users,
            "pending_users": pending_users,
            "suspended_users": suspended_users,
            "restricted_users": restricted_users,
            "new_users": new_users,
            "kyc_verified": kyc_verified,
            "kyc_pending": kyc_pending,
        }, 200


class AdminGetAllUsersResource(Resource):
    @auth_required
    def get(self):
        """Get all users with filters and pagination"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        search = request.args.get("search", "", type=str)
        role = request.args.get("role", "", type=str)
        status = request.args.get("status", "", type=str)
        kyc_status = request.args.get("kyc_status", "", type=str)
        sort_by = request.args.get("sort_by", "created_at", type=str)
        if sort_by not in (
            "created_at",
            "full_name",
            "business_name",
            "customer_id",
            "merchant_id",
            "status",
            "kyc_status",
            "city",
        ):  # never sort by an arbitrary column
            sort_by = "created_at"
        sort_order = request.args.get("sort_order", "desc", type=str)

        # Build query
        query = User.query.filter(User.role != "admin")  # admins: Team and access

        # Apply search filter
        if search:
            query = query.filter(
                or_(
                    User.full_name.ilike(f"%{search}%"),
                    User.business_name.ilike(f"%{search}%"),
                    User.phone.ilike(f"%{search}%"),
                    User.business_email.ilike(f"%{search}%"),
                    User.email.ilike(f"%{search}%"),
                    User.customer_id.ilike(f"%{search}%"),
                    User.merchant_id.ilike(f"%{search}%"),
                )
            )

        # Apply role filter
        if role:
            query = query.filter(User.role == role)

        # Apply status filter
        if status:
            query = query.filter(User.status == status)

        # Apply KYC status filter
        if kyc_status:
            query = query.filter(User.kyc_status == kyc_status)

        # Apply sorting
        if sort_order == "desc":
            query = query.order_by(getattr(User, sort_by).desc())
        else:
            query = query.order_by(getattr(User, sort_by).asc())

        # Pagination
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        users = []
        for user in paginated.items:
            # Calculate user metrics
            total_financed = (
                db.session.query(func.sum(InstalmentPlan.total_amount))
                .filter(InstalmentPlan.customer_id == user.id)
                .scalar()
                or 0
            )

            active_plans = (
                InstalmentPlan.query.filter_by(
                    customer_id=user.id, status="active"
                ).count()
                if user.role == "customer"
                else 0
            )

            users.append(
                {
                    "id": user.id,
                    "user_id": user.customer_id
                    or user.merchant_id
                    or f"U{user.id:04d}",
                    "full_name": user.full_name or user.business_name or "N/A",
                    "phone": user.phone,
                    "email": user.business_email or user.email or "N/A",
                    "role": user.role,
                    "status": user.status,
                    "kyc_status": user.kyc_status or "pending",
                    "verification_level": user.verification_level or "basic",
                    "city": user.city or "N/A",
                    "created_at": user.created_at.isoformat()
                    if user.created_at
                    else None,
                    "total_financed": float(total_financed),
                    "active_plans": active_plans,
                }
            )

        return {
            "users": users,
            "total": paginated.total,
            "page": page,
            "per_page": per_page,
            "total_pages": paginated.pages if paginated.pages > 0 else 1,
        }, 200


class AdminGetUserDetailResource(Resource):
    @auth_required
    def get(self, user_id):
        """Get detailed user information"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        user = User.query.get(user_id)
        if not user:
            return {"error": "User not found"}, 404

        if user.role == "admin":
            return {"error": "Admins are managed on Settings > Team and access."}, 403

        # Get user metrics based on role
        user_data = {
            "id": user.id,
            "user_id": user.customer_id or user.merchant_id or f"U{user.id:04d}",
            "role": user.role,
            "status": user.status,
            "kyc_status": user.kyc_status,
            "verification_level": user.verification_level,
            "phone": user.phone,
            "email": user.business_email or user.email,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "updated_at": user.updated_at.isoformat() if user.updated_at else None,
        }

        if user.role == "customer":
            user_data.update(
                {
                    "full_name": user.full_name,
                    "dob": user.dob,
                    "city": user.city,
                    "address": user.address,
                    "gps": user.gps,
                    "income_range": user.income_range,
                    "designation": user.designation,
                    "company": user.company,
                    "ref_name": user.ref_name,
                    "ref_phone": user.ref_phone,
                    "ref_relationship": user.ref_relationship,
                }
            )
        else:  # merchant
            user_data.update(
                {
                    "business_name": user.business_name,
                    "owner_name": user.owner_name,
                    "business_type": user.business_type,
                    "business_address": user.business_address,
                    "business_phone": user.business_phone,
                    "business_email": user.business_email,
                    "website": user.website,
                    "description": user.description,
                    "total_products": user.total_products,
                    "total_sales": user.total_sales,
                    "rating": user.rating,
                    "commission_rate": merchant_fees.describe(user)["fee_percentage"]
                    if user.role == "merchant"
                    else None,
                    "pending_payout": user.pending_payout,
                }
            )

        return user_data, 200


class AdminUpdateUserStatusResource(Resource):
    @auth_required
    def put(self, user_id):
        """Update user status"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        data = request.get_json()
        new_status = data.get("status")
        reason = data.get("reason", "")

        if new_status not in [
            "approved",
            "active",
            "pending",
            "suspended",
            "restricted",
        ]:
            return {"error": "Invalid status"}, 400

        user = User.query.get(user_id)
        if not user:
            return {"error": "User not found"}, 404

        if user.role == "admin":
            return {"error": "Admins are managed on Settings > Team and access."}, 403
        if len((reason or "").strip()) < 5:
            return {
                "error": "Give a reason (at least 5 characters). It's kept with the change."
            }, 400
        from ..services import accounts

        blocked = accounts.status_change_error(user, new_status)
        if blocked:
            return {"error": blocked}, 409

        from .admin_customers import _log_status_change

        _log_status_change(user, new_status, reason, current_admin)
        old_status = user.status
        user.status = new_status

        db.session.commit()

        return {
            "message": f"User status updated from {old_status} to {new_status}",
            "user_id": user.id,
            "status": new_status,
        }, 200


class AdminDeleteUserResource(Resource):
    @auth_required
    def delete(self, user_id):
        """Soft delete user (deactivate account)"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        user = User.query.get(user_id)
        if not user:
            return {"error": "User not found"}, 404

        if user.role == "admin":
            return {"error": "Admins are managed on Settings > Team and access."}, 403

        # Suspend, never delete; refused while money is still owed (services/accounts.py)
        from ..services import accounts

        try:
            accounts.deactivate(user)
        except ValueError as e:
            return {"error": str(e)}, 409

        return {
            "message": f"User {user_id} has been deactivated",
            "user_id": user.id,
            "status": "suspended",
        }, 200


class AdminExportUsersResource(Resource):
    @auth_required
    def get(self):
        """Export users to CSV"""
        current_admin = current_user()

        if current_admin.role != "admin":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        role = request.args.get("role", "", type=str)
        status = request.args.get("status", "", type=str)

        # Build query
        query = User.query.filter(User.role != "admin")  # admins: Team and access

        if role:
            query = query.filter(User.role == role)

        if status:
            query = query.filter(User.status == status)

        users = query.all()

        # Create CSV content
        import csv
        from io import StringIO
        from flask import make_response

        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(
            [
                "User ID",
                "Name",
                "Phone",
                "Email",
                "Role",
                "Status",
                "KYC Status",
                "City",
                "Created At",
            ]
        )

        # Write data
        for user in users:
            writer.writerow(
                [
                    user.customer_id or user.merchant_id or f"U{user.id:04d}",
                    user.full_name or user.business_name or "N/A",
                    user.phone,
                    user.business_email or user.email or "N/A",
                    user.role,
                    user.status,
                    user.kyc_status or "pending",
                    user.city or "N/A",
                    user.created_at.strftime("%Y-%m-%d %H:%M:%S")
                    if user.created_at
                    else "",
                ]
            )

        output.seek(0)
        response = make_response(output.getvalue())
        response.headers["Content-Type"] = "text/csv"
        response.headers[
            "Content-Disposition"
        ] = f'attachment; filename=users_{datetime.now().strftime("%Y%m%d")}.csv'
        return response
