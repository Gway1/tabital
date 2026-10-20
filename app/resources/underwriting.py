"""Underwriting endpoints: customer credit status and details, admin review and overrides."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models.risk_assessment import RiskAssessment
from ..models.user import User
from ..services import fraud, risk
from ..services.fraud import normalise_ghana_card


class FieldError(ValueError):
    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def normalize_underwriting_fields(data, allow_verification=False):
    """Validate and convert underwriting fields from a request. Only keys present are returned."""
    out = {}
    if "national_id" in data:
        raw = (data.get("national_id") or "").strip()
        value = normalise_ghana_card(raw) if raw else None
        if raw and not value:
            raise FieldError(
                "national_id", "Enter the Ghana Card number as GHA-XXXXXXXXX-X"
            )
        out["national_id"] = value
    if "monthly_salary" in data:
        raw = data.get("monthly_salary")
        if raw in (None, ""):
            out["monthly_salary"] = None
        else:
            try:
                salary = Decimal(str(raw)).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                raise FieldError("monthly_salary", "Monthly salary must be a number")
            if salary <= 0:
                raise FieldError(
                    "monthly_salary", "Monthly salary must be greater than zero"
                )
            out["monthly_salary"] = salary
    if "employment_start_date" in data:
        raw = data.get("employment_start_date")
        if raw in (None, ""):
            out["employment_start_date"] = None
        else:
            start = risk.parse_date(raw)
            if start is None:
                raise FieldError("employment_start_date", "Use the format YYYY-MM-DD")
            if start > datetime.utcnow().date():
                raise FieldError(
                    "employment_start_date",
                    "Employment start date cannot be in the future",
                )
            out["employment_start_date"] = start
    if "salary_paid_to_bank" in data:
        out["salary_paid_to_bank"] = bool(data.get("salary_paid_to_bank"))
    if "momo_number" in data:
        value = (data.get("momo_number") or "").strip().replace(" ", "")
        if value and (not value.isdigit() or len(value) != 10):
            raise FieldError(
                "momo_number", "Enter a 10-digit Mobile Money number, e.g. 0241234567"
            )
        out["momo_number"] = value or None
    if allow_verification and "salary_verified" in data:
        out["salary_verified"] = bool(data.get("salary_verified"))
    return out


def _details(user):
    return {
        "national_id": user.national_id,
        "date_of_birth": user.dob,
        "monthly_salary": float(user.monthly_salary)
        if user.monthly_salary is not None
        else None,
        "employment_start_date": user.employment_start_date.isoformat()
        if user.employment_start_date
        else None,
        "salary_paid_to_bank": bool(user.salary_paid_to_bank),
        "salary_verified": bool(user.salary_verified),
        "momo_number": user.momo_number,
        "company": user.company,
        "kyc_status": user.kyc_status,
        "verification_level": user.verification_level,
        # Employment verification (Phase 6, §9B)
        "employment_verified_at": user.employment_verified_at.isoformat()
        if user.employment_verified_at
        else None,
        "employment_verification_method": user.employment_verification_method,
        "employment_verification_note": user.employment_verification_note,
        "credit_limit_override": float(user.credit_limit_override)
        if user.credit_limit_override is not None
        else None,
    }


def _apply(user, fields):
    for key, value in fields.items():
        setattr(user, key, value)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        raise FieldError(
            "momo_number",
            "This Mobile Money number is already linked to another account",
        )


# ---------------------------------------------------------------- customer


class CustomerCreditResource(Resource):
    @auth_required
    def get(self):
        """The customer's current eligibility and spending limit (not stored)."""
        customer = current_user()
        if customer.role != "customer":
            return {"error": "Unauthorized"}, 403
        decision, _ = risk.decide(customer)
        return {**risk.decision_view(decision), "details": _details(customer)}, 200


class CustomerUnderwritingDetailsResource(Resource):
    @auth_required
    def put(self):
        """Customer submits or updates employment/income details.

        Changing salary or employment means the salary has to be verified again by Tabital.
        """
        customer = current_user()
        if customer.role != "customer":
            return {"error": "Unauthorized"}, 403
        try:
            fields = normalize_underwriting_fields(request.get_json() or {})
        except FieldError as e:
            return {"error": e.message, "field": e.field}, 400

        # The Ghana Card is what identity was verified against (Phase 6)
        if (
            customer.kyc_status == "verified"
            and "national_id" in fields
            and fields["national_id"] != normalise_ghana_card(customer.national_id)
        ):
            return {
                "error": "Your Ghana Card is already verified. Contact support to change it.",
                "field": "national_id",
            }, 409

        changes_income = any(
            k in fields and fields[k] != getattr(customer, k)
            for k in ("monthly_salary", "employment_start_date", "salary_paid_to_bank")
        )
        try:
            _apply(customer, fields)
        except FieldError as e:
            return {"error": e.message, "field": e.field}, 409
        if changes_income:
            customer.salary_verified = False
        if "national_id" in fields or "momo_number" in fields:
            fraud.check_duplicates(
                customer
            )  # same Ghana Card / MoMo on another account (§9D)
        db.session.commit()

        decision, _ = risk.decide(customer)
        return {
            "message": "Details saved. We'll verify your salary before updating your limit."
            if changes_income
            else "Details saved.",
            **risk.decision_view(decision),
            "details": _details(customer),
        }, 200


# ---------------------------------------------------------------- admin


def _admin_customer(customer_id):
    admin = current_user()
    if admin.role != "admin":
        return admin, None, ({"error": "Unauthorized"}, 403)
    customer = User.query.filter_by(id=customer_id, role="customer").first()
    if not customer:
        return admin, None, ({"error": "Customer not found"}, 404)
    return admin, customer, None


class AdminCustomerUnderwritingResource(Resource):
    @auth_required
    def get(self, customer_id):
        """Current decision, the facts behind it, and the decision history."""
        _, customer, error = _admin_customer(customer_id)
        if error:
            return error
        decision, facts = risk.decide(customer)
        history = (
            RiskAssessment.query.filter_by(user_id=customer.id)
            .order_by(RiskAssessment.created_at.desc(), RiskAssessment.id.desc())
            .limit(25)
            .all()
        )
        return {
            "current": risk.decision_view(decision),
            "facts": risk._facts_json(facts),
            "details": _details(customer),
            "history": [h.to_dict() for h in history],
        }, 200

    @auth_required
    def put(self, customer_id):
        """Admin records verified details (e.g. salary from the salary certificate) and re-assesses."""
        admin, customer, error = _admin_customer(customer_id)
        if error:
            return error
        data = request.get_json() or {}
        try:
            fields = normalize_underwriting_fields(data, allow_verification=True)
            _apply(customer, fields)
        except FieldError as e:
            return {"error": e.message, "field": e.field}, 400
        decision, row = risk.evaluate(
            customer,
            RiskAssessment.ADMIN_RERUN,
            created_by=admin,
            note=(data.get("note") or "Underwriting details updated by admin"),
        )
        db.session.commit()
        return {
            "current": risk.decision_view(decision),
            "assessment": row.to_dict(),
            "details": _details(customer),
        }, 200


class AdminRerunUnderwritingResource(Resource):
    @auth_required
    def post(self, customer_id):
        admin, customer, error = _admin_customer(customer_id)
        if error:
            return error
        decision, row = risk.evaluate(
            customer,
            RiskAssessment.ADMIN_RERUN,
            created_by=admin,
            note=(request.get_json(silent=True) or {}).get("note"),
        )
        db.session.commit()
        return {
            "current": risk.decision_view(decision),
            "assessment": row.to_dict(),
        }, 200


class AdminCreditLimitOverrideResource(Resource):
    @auth_required
    def put(self, customer_id):
        """Set or clear a manual credit limit. A reason is required and stored with the decision."""
        admin, customer, error = _admin_customer(customer_id)
        if error:
            return error
        data = request.get_json() or {}
        reason = (data.get("reason") or "").strip()
        if not reason:
            return {"error": "A reason is required for a credit limit override"}, 400

        raw = data.get("credit_limit")
        if raw in (None, ""):
            customer.credit_limit_override = None
        else:
            try:
                value = Decimal(str(raw)).quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                return {"error": "Credit limit must be a number"}, 400
            if value < 0:
                return {"error": "Credit limit can't be negative"}, 400
            customer.credit_limit_override = value

        decision, row = risk.evaluate(
            customer, RiskAssessment.ADMIN_OVERRIDE, created_by=admin, note=reason
        )
        db.session.commit()
        return {
            "message": "Credit limit override cleared"
            if customer.credit_limit_override is None
            else f"Credit limit set to GHS {customer.credit_limit_override:,.2f}",
            "customer_id": customer.id,
            "credit_limit": float(decision.credit_limit),
            "current": risk.decision_view(decision),
            "assessment": row.to_dict(),
        }, 200
