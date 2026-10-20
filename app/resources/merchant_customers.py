from flask_restful import Resource, request
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, or_


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class MerchantGetCustomersResource(Resource):
    @auth_required
    def get(self):
        """Get all customers who have transacted with this merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get unique customers from transactions
        customer_ids = (
            db.session.query(Transaction.customer_id)
            .filter(Transaction.merchant_id == current_merchant.id)
            .distinct()
            .all()
        )

        customer_ids = [c[0] for c in customer_ids if c[0]]

        if not customer_ids:
            return {"message": "No customers found"}, 200

        # Get query parameters
        search = request.args.get("search", "").strip()
        status = request.args.get("status", "").strip()
        sort_by = request.args.get("sort_by", "created_at")
        sort_order = request.args.get("sort_order", "desc")
        limit = request.args.get("limit", 50, type=int)
        page = request.args.get("page", 1, type=int)

        # Build query
        query = User.query.filter(User.id.in_(customer_ids), User.role == "customer")

        if search:
            query = query.filter(
                or_(
                    User.full_name.ilike(f"%{search}%"),
                    User.business_name.ilike(f"%{search}%"),
                    User.phone.ilike(f"%{search}%"),
                    User.email.ilike(f"%{search}%"),
                )
            )

        if status:
            query = query.filter_by(status=status)

        # Sorting
        if sort_by == "name":
            order_col = User.full_name
        elif sort_by == "created_at":
            order_col = User.created_at
        elif sort_by == "total_spent":
            order_col = User.total_spent
        else:
            order_col = User.created_at

        if sort_order == "asc":
            query = query.order_by(order_col.asc())
        else:
            query = query.order_by(order_col.desc())

        # Pagination
        total = query.count()
        customers = query.offset((page - 1) * limit).limit(limit).all()

        # Get additional stats for each customer
        result = []
        for customer in customers:
            # Get customer transaction stats with this merchant
            transactions = Transaction.query.filter_by(
                merchant_id=current_merchant.id, customer_id=customer.id
            ).all()

            total_spent = sum(t.amount for t in transactions if t.status == "completed")
            total_transactions = len(transactions)
            completed_transactions = len(
                [t for t in transactions if t.status == "completed"]
            )
            last_transaction = (
                max([t.transaction_date for t in transactions if t.transaction_date])
                if transactions
                else None
            )

            # Get instalment plans for this customer
            instalment_plans = [
                t for t in transactions if t.payment_plan and t.payment_plan != ""
            ]
            active_instalments = len(
                [i for i in instalment_plans if i.status == "pending"]
            )

            result.append(
                {
                    "id": customer.id,
                    "customer_id": safe_str(customer.customer_id),
                    "full_name": safe_str(customer.full_name),
                    "business_name": safe_str(customer.business_name),
                    "phone": safe_str(customer.phone),
                    "email": safe_str(customer.business_email or customer.email),
                    "city": safe_str(customer.city),
                    "address": safe_str(customer.address),
                    "status": safe_str(customer.status),
                    "kyc_status": safe_str(customer.kyc_status),
                    "total_spent": safe_float(total_spent),
                    "total_transactions": safe_int(total_transactions),
                    "completed_transactions": safe_int(completed_transactions),
                    "active_instalments": safe_int(active_instalments),
                    "last_transaction": last_transaction.isoformat()
                    if last_transaction
                    else "",
                    "created_at": customer.created_at.isoformat()
                    if customer.created_at
                    else "",
                    "is_active": customer.status == "active",
                }
            )

        return {
            "customers": result,
            "total": total,
            "page": page,
            "limit": limit,
            "total_pages": (total + limit - 1) // limit,
        }


class MerchantGetCustomerDetailsResource(Resource):
    @auth_required
    def get(self, customer_id):
        """Get detailed customer information"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        customer = User.query.get(customer_id)

        if not customer or customer.role != "customer":
            return {"error": "Customer not found"}, 404

        # Check if customer has transacted with this merchant
        transactions = Transaction.query.filter_by(
            merchant_id=current_merchant.id, customer_id=customer.id
        ).all()

        if not transactions:
            return {"error": "Customer not found"}, 404

        # Get customer stats
        total_spent = sum(t.amount for t in transactions if t.status == "completed")
        total_transactions = len(transactions)
        completed_transactions = len(
            [t for t in transactions if t.status == "completed"]
        )
        last_transaction = (
            max([t.transaction_date for t in transactions if t.transaction_date])
            if transactions
            else None
        )

        # Get recent transactions
        recent_transactions = sorted(
            transactions, key=lambda x: x.created_at, reverse=True
        )[:10]

        # Get instalment plans
        instalment_plans = [
            t for t in transactions if t.payment_plan and t.payment_plan != ""
        ]

        return {
            "id": customer.id,
            "customer_id": safe_str(customer.customer_id),
            "full_name": safe_str(customer.full_name),
            "business_name": safe_str(customer.business_name),
            "phone": safe_str(customer.phone),
            "email": safe_str(customer.business_email or customer.email),
            "city": safe_str(customer.city),
            "address": safe_str(customer.address),
            "gps": safe_str(customer.gps),
            "status": safe_str(customer.status),
            "kyc_status": safe_str(customer.kyc_status),
            "designation": safe_str(customer.designation),
            "company": safe_str(customer.company),
            "income_range": safe_str(customer.income_range),
            "total_spent": safe_float(total_spent),
            "total_transactions": safe_int(total_transactions),
            "completed_transactions": safe_int(completed_transactions),
            "active_instalments": len(
                [i for i in instalment_plans if i.status == "pending"]
            ),
            "total_instalments": len(instalment_plans),
            "last_transaction": last_transaction.isoformat()
            if last_transaction
            else "",
            "created_at": customer.created_at.isoformat()
            if customer.created_at
            else "",
            "is_active": customer.status == "active",
            "recent_transactions": [
                {
                    "id": t.id,
                    "transaction_id": safe_str(t.transaction_id),
                    "amount": safe_float(t.amount),
                    "product_name": safe_str(t.product_name),
                    "status": safe_str(t.status),
                    "transaction_date": t.transaction_date.isoformat()
                    if t.transaction_date
                    else "",
                    "is_instalment": t.payment_plan is not None
                    and t.payment_plan != "",
                }
                for t in recent_transactions
            ],
        }


class MerchantUpdateCustomerResource(Resource):
    @auth_required
    def put(self, customer_id):
        """Update customer information"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        customer = User.query.get(customer_id)

        if not customer or customer.role != "customer":
            return {"error": "Customer not found"}, 404

        # Check if customer has transacted with this merchant
        transaction_exists = Transaction.query.filter_by(
            merchant_id=current_merchant.id, customer_id=customer.id
        ).first()

        if not transaction_exists:
            return {"error": "Customer not found"}, 404

        # A customer's identity, contact, income and account status belong to the customer
        # and Tabital. Merchants can view them but not change them.
        return {
            "error": "Customer details can only be changed by the customer or Tabital support"
        }, 403


class MerchantGetCustomerStatsResource(Resource):
    @auth_required
    def get(self):
        """Get customer statistics for the merchant"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get unique customers
        customer_ids = (
            db.session.query(Transaction.customer_id)
            .filter(Transaction.merchant_id == current_merchant.id)
            .distinct()
            .all()
        )

        customer_ids = [c[0] for c in customer_ids if c[0]]

        if not customer_ids:
            return {
                "total_customers": 0,
                "active_customers": 0,
                "new_customers": 0,
                "returning_customers": 0,
                "total_spent": 0,
                "average_spent": 0,
                "customers_with_instalments": 0,
            }

        customers = User.query.filter(User.id.in_(customer_ids)).all()

        total_customers = len(customers)
        active_customers = len([c for c in customers if c.status == "active"])

        # New customers (last 30 days)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        new_customers = len([c for c in customers if c.created_at >= thirty_days_ago])

        # Returning vs one-time
        returning_customers = 0
        one_time_customers = 0

        for customer in customers:
            transaction_count = Transaction.query.filter_by(
                merchant_id=current_merchant.id, customer_id=customer.id
            ).count()
            if transaction_count > 1:
                returning_customers += 1
            else:
                one_time_customers += 1

        # Total spent across all customers
        total_spent = 0
        customers_with_instalments = 0

        for customer in customers:
            transactions = Transaction.query.filter_by(
                merchant_id=current_merchant.id,
                customer_id=customer.id,
                status="completed",
            ).all()

            customer_total = sum(t.amount for t in transactions)
            total_spent += customer_total

            # Check for instalments
            has_instalment = (
                Transaction.query.filter_by(
                    merchant_id=current_merchant.id, customer_id=customer.id
                )
                .filter(Transaction.payment_plan.isnot(None))
                .first()
            )

            if has_instalment:
                customers_with_instalments += 1

        average_spent = total_spent / total_customers if total_customers > 0 else 0

        # Top customers by spending
        top_customers = []
        for customer in customers:
            transactions = Transaction.query.filter_by(
                merchant_id=current_merchant.id,
                customer_id=customer.id,
                status="completed",
            ).all()
            customer_total = sum(t.amount for t in transactions)

            top_customers.append(
                {
                    "id": customer.id,
                    "name": customer.full_name
                    or customer.business_name
                    or customer.phone,
                    "total_spent": safe_float(customer_total),
                    "transaction_count": len(transactions),
                }
            )

        top_customers = sorted(
            top_customers, key=lambda x: x["total_spent"], reverse=True
        )[:5]

        return {
            "total_customers": total_customers,
            "active_customers": active_customers,
            "new_customers": new_customers,
            "returning_customers": returning_customers,
            "one_time_customers": one_time_customers,
            "total_spent": safe_float(total_spent),
            "average_spent": safe_float(average_spent),
            "customers_with_instalments": customers_with_instalments,
            "top_customers": top_customers,
        }


class MerchantExportCustomersResource(Resource):
    @auth_required
    def get(self):
        """Export customers to CSV"""
        from flask import Response
        import csv
        from io import StringIO

        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get unique customers
        customer_ids = (
            db.session.query(Transaction.customer_id)
            .filter(Transaction.merchant_id == current_merchant.id)
            .distinct()
            .all()
        )

        customer_ids = [c[0] for c in customer_ids if c[0]]

        if not customer_ids:
            return {"error": "No customers found"}, 404

        customers = User.query.filter(User.id.in_(customer_ids)).all()

        output = StringIO()
        writer = csv.writer(output)

        # Write headers
        writer.writerow(
            [
                "Customer ID",
                "Name",
                "Business Name",
                "Phone",
                "Email",
                "City",
                "Address",
                "Status",
                "KYC Status",
                "Total Spent",
                "Total Transactions",
                "Last Transaction Date",
                "Member Since",
            ]
        )

        # Write data
        for customer in customers:
            transactions = Transaction.query.filter_by(
                merchant_id=current_merchant.id, customer_id=customer.id
            ).all()

            total_spent = sum(t.amount for t in transactions if t.status == "completed")
            total_transactions = len(transactions)
            last_transaction = (
                max([t.transaction_date for t in transactions if t.transaction_date])
                if transactions
                else None
            )

            writer.writerow(
                [
                    customer.customer_id or "",
                    customer.full_name or "",
                    customer.business_name or "",
                    customer.phone,
                    customer.business_email or customer.email or "",
                    customer.city or "",
                    customer.address or "",
                    customer.status,
                    customer.kyc_status or "",
                    total_spent,
                    total_transactions,
                    last_transaction.strftime("%Y-%m-%d") if last_transaction else "",
                    customer.created_at.strftime("%Y-%m-%d")
                    if customer.created_at
                    else "",
                ]
            )

        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename=customers_{datetime.now().strftime("%Y%m%d")}.csv'
            },
        )
