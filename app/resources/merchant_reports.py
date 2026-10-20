from flask_restful import Resource, request
from ..services import merchant_fees
from flask_praetorian import auth_required, current_user
from ..models.user import User
from ..models.transaction import Transaction
from ..models.instalment import InstalmentPlan
from ..models.dispute import Dispute
from ..extensions import db
from datetime import datetime, timedelta
from sqlalchemy import func, and_, or_
import json


def safe_str(v):
    return v if v is not None else ""


def safe_float(v):
    return v if v is not None else 0.0


def safe_int(v):
    return v if v is not None else 0


class MerchantSalesReportResource(Resource):
    @auth_required
    def get(self):
        """Generate sales report"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        report_type = request.args.get(
            "type", "daily"
        )  # daily, weekly, monthly, yearly
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        # Set date range
        if start_date:
            start = datetime.fromisoformat(start_date)
        else:
            start = datetime.now() - timedelta(days=30)

        if end_date:
            end = datetime.fromisoformat(end_date)
        else:
            end = datetime.now()

        # Query transactions
        query = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
        )

        transactions = query.all()

        # Generate report based on type
        if report_type == "daily":
            report_data = self.generate_daily_report(transactions, start, end)
        elif report_type == "weekly":
            report_data = self.generate_weekly_report(transactions, start, end)
        elif report_type == "monthly":
            report_data = self.generate_monthly_report(transactions, start, end)
        elif report_type == "yearly":
            report_data = self.generate_yearly_report(transactions, start, end)
        else:
            report_data = self.generate_daily_report(transactions, start, end)

        # Calculate summary
        total_sales = sum(t.amount for t in transactions)
        total_transactions = len(transactions)
        average_order_value = (
            total_sales / total_transactions if total_transactions > 0 else 0
        )

        # Calculate growth
        previous_start = start - timedelta(days=(end - start).days)
        previous_end = start
        previous_transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
            Transaction.transaction_date >= previous_start,
            Transaction.transaction_date <= previous_end,
        ).all()

        previous_total = sum(t.amount for t in previous_transactions)
        sales_growth = (
            ((total_sales - previous_total) / previous_total * 100)
            if previous_total > 0
            else 0
        )

        # Top products
        product_sales = {}
        for t in transactions:
            product_sales[t.product_name] = (
                product_sales.get(t.product_name, 0) + t.amount
            )

        top_products = sorted(
            [{"name": k, "amount": v} for k, v in product_sales.items()],
            key=lambda x: x["amount"],
            reverse=True,
        )[:10]

        # Payment method breakdown
        payment_methods = {}
        for t in transactions:
            method = t.payment_method or "other"
            payment_methods[method] = payment_methods.get(method, 0) + t.amount

        return {
            "report_type": report_type,
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "summary": {
                "total_sales": safe_float(total_sales),
                "total_transactions": safe_int(total_transactions),
                "average_order_value": safe_float(average_order_value),
                "sales_growth": safe_float(sales_growth),
            },
            "report_data": report_data,
            "top_products": top_products,
            "payment_methods": payment_methods,
        }

    def generate_daily_report(self, transactions, start, end):
        data = []
        current = start
        delta = timedelta(days=1)

        while current <= end:
            day_transactions = [
                t for t in transactions if t.transaction_date.date() == current.date()
            ]
            data.append(
                {
                    "period": current.strftime("%Y-%m-%d"),
                    "day": current.strftime("%A"),
                    "sales": safe_float(sum(t.amount for t in day_transactions)),
                    "transactions": len(day_transactions),
                    "average": safe_float(
                        sum(t.amount for t in day_transactions) / len(day_transactions)
                    )
                    if day_transactions
                    else 0,
                }
            )
            current += delta

        return data

    def generate_weekly_report(self, transactions, start, end):
        data = []
        current = start
        delta = timedelta(days=7)

        while current <= end:
            week_end = min(current + delta, end)
            week_transactions = [
                t for t in transactions if current <= t.transaction_date <= week_end
            ]
            data.append(
                {
                    "period": f"Week of {current.strftime('%b %d')}",
                    "week_number": current.isocalendar()[1],
                    "sales": safe_float(sum(t.amount for t in week_transactions)),
                    "transactions": len(week_transactions),
                    "average": safe_float(
                        sum(t.amount for t in week_transactions)
                        / len(week_transactions)
                    )
                    if week_transactions
                    else 0,
                }
            )
            current = week_end + timedelta(days=1)

        return data

    def generate_monthly_report(self, transactions, start, end):
        data = []
        current = start.replace(day=1)

        while current <= end:
            next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
            month_end = min(next_month - timedelta(days=1), end)
            month_transactions = [
                t for t in transactions if current <= t.transaction_date <= month_end
            ]
            data.append(
                {
                    "period": current.strftime("%B %Y"),
                    "month": current.month,
                    "year": current.year,
                    "sales": safe_float(sum(t.amount for t in month_transactions)),
                    "transactions": len(month_transactions),
                    "average": safe_float(
                        sum(t.amount for t in month_transactions)
                        / len(month_transactions)
                    )
                    if month_transactions
                    else 0,
                }
            )
            current = next_month

        return data

    def generate_yearly_report(self, transactions, start, end):
        data = []
        current_year = start.year

        while current_year <= end.year:
            year_start = datetime(current_year, 1, 1)
            year_end = datetime(current_year, 12, 31)
            year_transactions = [
                t for t in transactions if year_start <= t.transaction_date <= year_end
            ]
            data.append(
                {
                    "period": str(current_year),
                    "year": current_year,
                    "sales": safe_float(sum(t.amount for t in year_transactions)),
                    "transactions": len(year_transactions),
                    "average": safe_float(
                        sum(t.amount for t in year_transactions)
                        / len(year_transactions)
                    )
                    if year_transactions
                    else 0,
                }
            )
            current_year += 1

        return data


class MerchantTransactionReportResource(Resource):
    @auth_required
    def get(self):
        """Generate detailed transaction report"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()
        status = request.args.get("status", "").strip()
        payment_status = request.args.get("payment_status", "").strip()

        # Set date range
        if start_date:
            start = datetime.fromisoformat(start_date)
        else:
            start = datetime.now() - timedelta(days=30)

        if end_date:
            end = datetime.fromisoformat(end_date)
        else:
            end = datetime.now()

        # Query transactions
        query = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.transaction_date >= start,
            Transaction.transaction_date <= end,
        )

        if status:
            query = query.filter_by(status=status)
        if payment_status:
            query = query.filter_by(payment_status=payment_status)

        transactions = query.order_by(Transaction.transaction_date.desc()).all()

        # Status breakdown
        status_breakdown = {}
        for t in transactions:
            status_breakdown[t.status] = status_breakdown.get(t.status, 0) + 1

        # Payment method breakdown
        payment_breakdown = {}
        for t in transactions:
            method = t.payment_method or "other"
            payment_breakdown[method] = payment_breakdown.get(method, 0) + 1

        # Hourly distribution
        hourly_distribution = {}
        for t in transactions:
            hour = t.transaction_date.hour
            hourly_distribution[hour] = hourly_distribution.get(hour, 0) + 1

        return {
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "summary": {
                "total_transactions": len(transactions),
                "total_amount": safe_float(sum(t.amount for t in transactions)),
                "completed": len([t for t in transactions if t.status == "completed"]),
                "pending": len([t for t in transactions if t.status == "pending"]),
                "refunded": len([t for t in transactions if t.status == "refunded"]),
            },
            "status_breakdown": status_breakdown,
            "payment_breakdown": payment_breakdown,
            "hourly_distribution": hourly_distribution,
            "transactions": [
                {
                    "id": t.id,
                    "transaction_id": t.transaction_id,
                    "customer_name": safe_str(
                        t.customer.full_name or t.customer.business_name
                    ),
                    "amount": t.amount,
                    "product_name": t.product_name,
                    "status": t.status,
                    "payment_status": t.payment_status,
                    "payment_method": t.payment_method,
                    "date": t.transaction_date.isoformat(),
                }
                for t in transactions[:100]
            ],  # Limit to 100 for performance
        }


class MerchantCustomerReportResource(Resource):
    @auth_required
    def get(self):
        """Generate customer analytics report"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        if start_date:
            start = datetime.fromisoformat(start_date)
        else:
            start = datetime.now() - timedelta(days=90)

        if end_date:
            end = datetime.fromisoformat(end_date)
        else:
            end = datetime.now()

        # Get unique customers
        customer_ids = (
            db.session.query(Transaction.customer_id)
            .filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.transaction_date >= start,
                Transaction.transaction_date <= end,
            )
            .distinct()
            .all()
        )

        customer_ids = [c[0] for c in customer_ids if c[0]]

        if not customer_ids:
            return {
                "total_customers": 0,
                "new_customers": 0,
                "returning_customers": 0,
                "average_spent": 0,
                "customer_segments": [],
                "top_customers": [],
            }

        customers = User.query.filter(User.id.in_(customer_ids)).all()

        # Customer segments based on spending
        segments = {
            "high_value": {"min": 5000, "customers": []},
            "medium_value": {"min": 1000, "max": 5000, "customers": []},
            "low_value": {"max": 1000, "customers": []},
        }

        customer_data = []
        for customer in customers:
            # Get customer transactions
            customer_transactions = Transaction.query.filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.customer_id == customer.id,
                Transaction.status == "completed",
                Transaction.transaction_date >= start,
                Transaction.transaction_date <= end,
            ).all()

            total_spent = sum(t.amount for t in customer_transactions)
            transaction_count = len(customer_transactions)

            customer_info = {
                "id": customer.id,
                "customer_id": customer.customer_id,
                "name": customer.full_name or customer.business_name or customer.phone,
                "phone": customer.phone,
                "email": customer.business_email or customer.email,
                "total_spent": total_spent,
                "transaction_count": transaction_count,
                "average_order": total_spent / transaction_count
                if transaction_count > 0
                else 0,
                "last_transaction": max(
                    t.transaction_date for t in customer_transactions
                ).isoformat()
                if customer_transactions
                else None,
            }
            customer_data.append(customer_info)

            # Segment customers
            if total_spent >= 5000:
                segments["high_value"]["customers"].append(customer_info)
            elif total_spent >= 1000:
                segments["medium_value"]["customers"].append(customer_info)
            else:
                segments["low_value"]["customers"].append(customer_info)

        # Sort customers by total spent
        customer_data.sort(key=lambda x: x["total_spent"], reverse=True)

        # Calculate customer lifetime value
        clv = (
            sum(c["total_spent"] for c in customer_data) / len(customer_data)
            if customer_data
            else 0
        )

        # New vs returning customers
        new_customers = 0
        returning_customers = 0

        for customer in customers:
            transaction_count = Transaction.query.filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.customer_id == customer.id,
            ).count()

            if transaction_count > 1:
                returning_customers += 1
            else:
                new_customers += 1

        return {
            "date_range": {"start": start.isoformat(), "end": end.isoformat()},
            "summary": {
                "total_customers": len(customer_data),
                "new_customers": new_customers,
                "returning_customers": returning_customers,
                "customer_lifetime_value": safe_float(clv),
                "average_spent": safe_float(
                    sum(c["total_spent"] for c in customer_data) / len(customer_data)
                )
                if customer_data
                else 0,
            },
            "customer_segments": {
                "high_value": {
                    "count": len(segments["high_value"]["customers"]),
                    "customers": segments["high_value"]["customers"][:10],
                },
                "medium_value": {
                    "count": len(segments["medium_value"]["customers"]),
                    "customers": segments["medium_value"]["customers"][:10],
                },
                "low_value": {
                    "count": len(segments["low_value"]["customers"]),
                    "customers": segments["low_value"]["customers"][:10],
                },
            },
            "top_customers": customer_data[:10],
        }


class MerchantFinancialReportResource(Resource):
    @auth_required
    def get(self):
        """Generate financial report"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get query parameters
        year = request.args.get("year", type=int)
        if not year:
            year = datetime.now().year

        # Get monthly breakdown
        monthly_data = []
        for month in range(1, 13):
            month_start = datetime(year, month, 1)
            if month == 12:
                month_end = datetime(year + 1, 1, 1) - timedelta(days=1)
            else:
                month_end = datetime(year, month + 1, 1) - timedelta(days=1)

            # Sales
            sales_transactions = Transaction.query.filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.status == "completed",
                Transaction.transaction_date >= month_start,
                Transaction.transaction_date <= month_end,
            ).all()

            total_sales = sum(t.amount for t in sales_transactions)
            total_transactions = len(sales_transactions)
            commission = sum(
                merchant_fees.transaction_split(t, current_merchant)[0]
                for t in sales_transactions
            )
            net_income = total_sales - commission

            # Refunds
            refunds = Transaction.query.filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.status == "refunded",
                Transaction.transaction_date >= month_start,
                Transaction.transaction_date <= month_end,
            ).all()
            total_refunds = sum(t.amount for t in refunds)

            monthly_data.append(
                {
                    "month": month_start.strftime("%B"),
                    "total_sales": safe_float(total_sales),
                    "transactions": total_transactions,
                    "commission": safe_float(commission),
                    "net_income": safe_float(net_income),
                    "refunds": safe_float(total_refunds),
                }
            )

        # Year to date totals
        ytd_start = datetime(year, 1, 1)
        ytd_end = datetime.now()

        ytd_transactions = Transaction.query.filter(
            Transaction.merchant_id == current_merchant.id,
            Transaction.status == "completed",
            Transaction.transaction_date >= ytd_start,
            Transaction.transaction_date <= ytd_end,
        ).all()

        ytd_total = sum(t.amount for t in ytd_transactions)
        ytd_commission = sum(
            merchant_fees.transaction_split(t, current_merchant)[0]
            for t in ytd_transactions
        )
        ytd_net = ytd_total - ytd_commission

        # Projected annual
        if datetime.now().month < 12:
            avg_monthly = ytd_total / datetime.now().month
            projected_annual = avg_monthly * 12
        else:
            projected_annual = ytd_total

        return {
            "year": year,
            "summary": {
                "ytd_sales": safe_float(ytd_total),
                "ytd_commission": safe_float(ytd_commission),
                "ytd_net_income": safe_float(ytd_net),
                "projected_annual": safe_float(projected_annual),
            },
            "monthly_breakdown": monthly_data,
            "commission_rate": merchant_fees.describe(current_merchant)[
                "fee_percentage"
            ],  # current tier (§6.1)
        }


class MerchantInstalmentReportResource(Resource):
    @auth_required
    def get(self):
        """Generate instalment plans report"""
        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        # Get all instalment plans
        plans = InstalmentPlan.query.filter_by(merchant_id=current_merchant.id).all()

        total_plans = len(plans)
        active_plans = len([p for p in plans if p.status == "active"])
        completed_plans = len([p for p in plans if p.status == "completed"])
        overdue_plans = len([p for p in plans if p.status == "overdue"])

        total_value = sum(p.total_amount for p in plans)
        total_received = sum(p.paid_to_date for p in plans)
        total_remaining = sum(p.outstanding_balance for p in plans)

        # Average plan size
        avg_plan_size = total_value / total_plans if total_plans > 0 else 0

        # Popular instalment counts
        instalment_counts = {}
        for plan in plans:
            count = str(plan.number_of_installments)
            instalment_counts[count] = instalment_counts.get(count, 0) + 1

        # Monthly breakdown of instalment payments
        monthly_breakdown = {}
        for plan in plans:
            for payment in plan.payments:
                if payment.status == "paid" and payment.paid_date:
                    month_key = payment.paid_date.strftime("%Y-%m")
                    monthly_breakdown[month_key] = (
                        monthly_breakdown.get(month_key, 0) + payment.amount
                    )

        monthly_data = [
            {"month": k, "amount": v} for k, v in sorted(monthly_breakdown.items())
        ]

        return {
            "summary": {
                "total_plans": total_plans,
                "active_plans": active_plans,
                "completed_plans": completed_plans,
                "overdue_plans": overdue_plans,
                "total_value": safe_float(total_value),
                "total_received": safe_float(total_received),
                "total_remaining": safe_float(total_remaining),
                "average_plan_size": safe_float(avg_plan_size),
            },
            "instalment_counts": instalment_counts,
            "monthly_payments": monthly_data[-12:],  # Last 12 months
        }


class MerchantExportReportResource(Resource):
    @auth_required
    def get(self):
        """Export report as CSV"""
        from flask import Response
        import csv
        from io import StringIO

        current_merchant = current_user()

        if current_merchant.role != "merchant":
            return {"error": "Unauthorized"}, 403

        report_type = request.args.get("type", "sales")
        start_date = request.args.get("start_date", "").strip()
        end_date = request.args.get("end_date", "").strip()

        if start_date:
            start = datetime.fromisoformat(start_date)
        else:
            start = datetime.now() - timedelta(days=30)

        if end_date:
            end = datetime.fromisoformat(end_date)
        else:
            end = datetime.now()

        # Query data based on report type
        if report_type == "transactions":
            transactions = (
                Transaction.query.filter(
                    Transaction.merchant_id == current_merchant.id,
                    Transaction.transaction_date >= start,
                    Transaction.transaction_date <= end,
                )
                .order_by(Transaction.transaction_date.desc())
                .all()
            )

            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "Transaction ID",
                    "Date",
                    "Customer",
                    "Product",
                    "Amount",
                    "Status",
                    "Payment Status",
                    "Payment Method",
                ]
            )

            for t in transactions:
                writer.writerow(
                    [
                        t.transaction_id,
                        t.transaction_date.strftime("%Y-%m-%d %H:%M"),
                        t.customer.full_name
                        or t.customer.business_name
                        or t.customer.phone,
                        t.product_name,
                        t.amount,
                        t.status,
                        t.payment_status,
                        t.payment_method or "N/A",
                    ]
                )

        elif report_type == "customers":
            customer_ids = (
                db.session.query(Transaction.customer_id)
                .filter(Transaction.merchant_id == current_merchant.id)
                .distinct()
                .all()
            )
            customer_ids = [c[0] for c in customer_ids if c[0]]

            customers = User.query.filter(User.id.in_(customer_ids)).all()

            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(
                [
                    "Customer ID",
                    "Name",
                    "Phone",
                    "Email",
                    "Total Spent",
                    "Total Transactions",
                    "Last Transaction",
                ]
            )

            for customer in customers:
                transactions = Transaction.query.filter_by(
                    merchant_id=current_merchant.id,
                    customer_id=customer.id,
                    status="completed",
                ).all()

                total_spent = sum(t.amount for t in transactions)
                last_transaction = (
                    max([t.transaction_date for t in transactions])
                    if transactions
                    else None
                )

                writer.writerow(
                    [
                        customer.customer_id or "",
                        customer.full_name or customer.business_name or customer.phone,
                        customer.phone,
                        customer.business_email or customer.email or "",
                        total_spent,
                        len(transactions),
                        last_transaction.strftime("%Y-%m-%d")
                        if last_transaction
                        else "",
                    ]
                )

        else:  # sales report
            transactions = Transaction.query.filter(
                Transaction.merchant_id == current_merchant.id,
                Transaction.status == "completed",
                Transaction.transaction_date >= start,
                Transaction.transaction_date <= end,
            ).all()

            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(
                ["Date", "Total Sales", "Transaction Count", "Average Order Value"]
            )

            # Group by day
            daily_data = {}
            for t in transactions:
                date_key = t.transaction_date.strftime("%Y-%m-%d")
                if date_key not in daily_data:
                    daily_data[date_key] = {"sales": 0, "count": 0}
                daily_data[date_key]["sales"] += t.amount
                daily_data[date_key]["count"] += 1

            for date, data in sorted(daily_data.items()):
                writer.writerow(
                    [
                        date,
                        data["sales"],
                        data["count"],
                        data["sales"] / data["count"] if data["count"] > 0 else 0,
                    ]
                )

        output.seek(0)

        return Response(
            output.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename={report_type}_report_{datetime.now().strftime("%Y%m%d")}.csv'
            },
        )
