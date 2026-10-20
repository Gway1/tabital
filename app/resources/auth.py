from flask import request
from flask_restful import Resource

from app.docs import BOOL, ERROR_REF, MESSAGE, STR, api_doc, obj
from app.models.user import User
from ..services.auth_service import (
    AuthError,
    RegistrationError,
    login_user,
    register_user,
)


_SIGNUP_BODY = obj(
    {
        "role": {"type": "string", "enum": ["customer", "merchant"]},
        "phone": STR,
        "password": {"type": "string", "minLength": 6},
        "full_name": {**STR, "description": "customer"},
        "business_email": STR,
        "dob": STR,
        "city": STR,
        "address": STR,
        "gps": STR,
        "designation": STR,
        "company": STR,
        "income_range": STR,
        "ref_name": STR,
        "ref_phone": STR,
        "ref_relationship": STR,
        "national_id": {**STR, "description": "customer, Ghana Card"},
        "monthly_salary": {"type": "number", "description": "customer"},
        "employment_start_date": {**STR, "description": "customer, YYYY-MM-DD"},
        "salary_paid_to_bank": BOOL,
        "momo_number": STR,
        "business_name": {**STR, "description": "merchant"},
        "owner_name": {**STR, "description": "merchant"},
        "product_type": STR,
        "has_shop": BOOL,
        "shop_url": STR,
        "years_in_business": STR,
        "business_type": STR,
        "business_address": STR,
        "business_phone": STR,
        "description": STR,
        "agree": BOOL,
    },
    required=["role", "phone", "password"],
    description="Which other fields apply depends on role (customer or merchant). Unknown fields are ignored.",
)


class RegisterResource(Resource):
    @api_doc(
        "Sign up as a customer or merchant",
        description="The account starts as `pending` until an admin approves it. Failed sign-ups are "
        "limited per IP address.",
        body=_SIGNUP_BODY,
        responses={
            201: ("Registered", MESSAGE),
            400: (
                "A field is missing or invalid. `field` names it.",
                obj(
                    {"error": STR, "field": STR},
                    description="Also repeats the message under its field name",
                ),
            ),
            409: ("Phone, email or MoMo number is already registered", ERROR_REF),
            429: "Too many failed sign-ups from this address",
        },
    )
    def post(self):
        """Sign-up. It has to say when a phone/email is already registered, so failed sign-ups are
        limited per IP (services/attempts.py) to stop it being used to look up who has an account.
        """
        from datetime import datetime, timedelta
        from flask import current_app
        from ..models.login_attempt import LoginAttempt
        from ..services import attempts

        data = request.get_json() or {}
        ip = attempts.client_ip()
        now = datetime.utcnow()
        window = now - timedelta(
            minutes=current_app.config.get("LOGIN_WINDOW_MINUTES", 15)
        )
        subject = f"ip:{ip}"
        if ip and attempts.failures(
            LoginAttempt.SIGNUP, subject, window
        ) >= current_app.config.get("SIGNUP_MAX_FAILURES_PER_IP", 10):
            return {
                "error": "Too many sign-up attempts. Please wait a few minutes and try again."
            }, 429
        try:
            register_user(data)
        except RegistrationError as e:
            if (
                e.status == 409
            ):  # "already registered" answers are what an attacker wants
                attempts.begin(
                    LoginAttempt.SIGNUP,
                    subject,
                    (data.get("phone") or "")[:120],
                    ip,
                    now,
                )
            return {"error": e.message, "field": e.field, e.field: e.message}, e.status
        return {"message": "User registered successfully"}, 201


class LoginResource(Resource):
    @api_doc(
        "Sign in with phone (or business email) and password",
        description="Send the returned token as `Authorization: Bearer <access_token>`. Restricted "
        "accounts can sign in to pay what they owe.",
        body=obj(
            {
                "phone": {**STR, "description": "Phone number or business email"},
                "password": STR,
            },
            required=["phone", "password"],
        ),
        responses={
            200: ("Signed in", obj({"access_token": STR})),
            400: "Phone or password missing",
            401: "Invalid phone or password",
            403: "Account not approved yet",
            429: "Too many failed attempts. Wait for the window to pass or reset the password.",
        },
    )
    def post(self):
        data = request.get_json() or {}
        try:
            token = login_user(data.get("phone"), data.get("password"))
        except AuthError as e:
            return {"error": e.message, "message": e.message}, e.status
        _record_login_device(data.get("phone"))
        return {"access_token": token}, 200


def _record_login_device(phone):
    """Device checks (§9D). Never stops a login: a failure here is only logged."""
    from flask import current_app
    from ..extensions import db
    from ..services import fraud

    try:
        user = User.query.filter_by(phone=(phone or "").strip()).first()
        if user:
            fraud.record_device(user)
            db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("Device check failed at login")


class CheckUserExistsResource(Resource):
    @api_doc("Turned off: always answers 410", responses={410: "Not available"})
    def post(self):
        """Turned off: a public "is this phone/email registered?" lookup let anyone collect the phone
        numbers of Tabital customers. Sign-up reports a duplicate when the form is submitted.
        """
        return {
            "error": "Not available. Sign-up tells you if the phone or email is already registered."
        }, 410
