import calendar
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from flask import current_app
from flask_mail import Message
from flask_restful import Resource, request
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from ..docs import MESSAGE, STR, api_doc, obj
from ..extensions import db, guard, mail
from ..models.user import User


# Only these fields can be set at sign-up. Status, KYC, IDs, commission and payout fields
# are set by Tabital, never by the person registering. Admins are created with a CLI command.
CUSTOMER_SIGNUP_FIELDS = {
    "full_name",
    "business_email",
    "phone",
    "dob",
    "city",
    "gps",
    "address",
    "designation",
    "company",
    "income_range",
    "ref_name",
    "ref_phone",
    "ref_relationship",
    "agree",
}
MERCHANT_SIGNUP_FIELDS = {
    "business_name",
    "owner_name",
    "phone",
    "city",
    "address",
    "product_type",
    "has_shop",
    "shop_url",
    "years_in_business",
    "business_type",
    "business_address",
    "business_phone",
    "business_email",
    "description",
    "agree",
}
SIGNUP_FIELDS = {"customer": CUSTOMER_SIGNUP_FIELDS, "merchant": MERCHANT_SIGNUP_FIELDS}
# Validated and converted separately (see resources/underwriting.py)
CUSTOMER_UNDERWRITING_FIELDS = (
    "national_id",
    "monthly_salary",
    "employment_start_date",
    "salary_paid_to_bank",
    "momo_number",
)

MIN_PASSWORD_LENGTH = 6
OTP_TTL = timedelta(minutes=10)
OTP_RESEND_AFTER = timedelta(minutes=1)
OTP_MAX_ATTEMPTS = 5
RESET_TOKEN_TTL = timedelta(minutes=30)


def _digest(value):
    """Keyed hash of a reset code or token. Only this is stored, so a copy of the database (or a
    backup) can't be used to take over an account that has a reset in progress. Keyed because a
    6-digit code has too few values for a plain hash to hide."""
    return hmac.new(
        current_app.config["SECRET_KEY"].encode(),
        str(value).encode(),
        hashlib.sha256,
    ).hexdigest()


def _users_for_email(email):
    """The one account an email belongs to, or None (no match, or more than one: `email` isn't unique)."""
    users = User.query.filter(
        or_(User.business_email == email, db.func.lower(User.email) == email)
    ).all()
    exact = [u for u in users if u.business_email == email]
    if len(exact) == 1:
        return exact[0]
    if len(users) == 1:
        return users[0]
    if len(users) > 1:
        current_app.logger.warning("Password reset: several accounts share one email")
    return None


def _user_for_phone(phone):
    """The one account a phone number belongs to, whichever way it was written (024…, +23324…, 23324…)."""
    from . import sms

    variants = {phone}
    local, e164 = sms.to_local(phone), sms.to_e164(phone)
    if local:
        variants.add(local)
    if e164:
        variants.update({e164, e164[1:]})
    users = User.query.filter(User.phone.in_(variants)).all()
    return users[0] if len(users) == 1 else None


def _reset_target(data):
    """(channel, identifier, user) for a reset request that names an email or a phone number.
    user is None when nothing, or more than one account, matches. channel is None if neither was given."""
    email = (data.get("email") or "").strip().lower()
    if email:
        return "email", email, _users_for_email(email)
    phone = (data.get("phone") or "").strip()
    if phone:
        from . import sms

        return "sms", sms.to_local(phone) or phone, _user_for_phone(phone)
    return None, "", None


def password_changed(user):
    """Signs out every session started before now (checked in app.register_access_control)."""
    user.password_changed_at = datetime.utcnow()


def session_predates_password_change(user, issued_at):
    """True if a token issued at `issued_at` (epoch seconds, the JWT iat) is older than the last password change."""
    changed = getattr(user, "password_changed_at", None)
    if not changed or issued_at is None:
        return False
    return int(issued_at) < calendar.timegm(changed.utctimetuple())


class RegistrationError(ValueError):
    def __init__(self, field, message, status=400):
        super().__init__(message)
        self.field = field
        self.message = message
        self.status = status


def register_user(data):
    role = (data.get("role") or "").strip().lower()
    if role not in SIGNUP_FIELDS:
        raise RegistrationError("role", "Role must be customer or merchant")

    password = data.get("password") or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise RegistrationError(
            "password", f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    if not (data.get("phone") or "").strip():
        raise RegistrationError("phone", "Phone number is required")

    fields = {k: v for k, v in data.items() if k in SIGNUP_FIELDS[role]}
    if fields.get("business_email"):
        fields["business_email"] = fields["business_email"].strip().lower()

    if role == "merchant" and (fields.get("shop_url") or "").strip():
        # The storefront URL is the gateway's allowed redirect domain, so it must be a real address
        from . import gateway

        url = fields["shop_url"].strip()
        if "://" not in url:
            url = "https://" + url
        try:
            fields["shop_url"] = gateway.validate_storefront_url(url)
        except gateway.GatewayError as e:
            raise RegistrationError("shop_url", e.message)
    elif role == "merchant":
        fields.pop("shop_url", None)

    if role == "customer":
        # Underwriting details (Phase 3); the salary is verified later by Tabital, never at sign-up
        from ..resources.underwriting import FieldError, normalize_underwriting_fields

        try:
            fields.update(
                normalize_underwriting_fields(
                    {k: data[k] for k in CUSTOMER_UNDERWRITING_FIELDS if k in data}
                )
            )
        except FieldError as e:
            raise RegistrationError(e.field, e.message)

    user = User(**fields)
    user.role = role
    user.status = "pending"
    user.password = guard.hash_password(password)

    try:
        db.session.add(user)
        db.session.commit()
        _signup_fraud_checks(user)
        return user
    except IntegrityError as e:
        db.session.rollback()
        error_message = str(e.orig).lower() if e.orig else str(e).lower()
        if "business_email" in error_message:
            raise RegistrationError(
                "business_email",
                "This email address is already registered. Please use a different email or login.",
                409,
            )
        if "momo_number" in error_message:
            raise RegistrationError(
                "momo_number",
                "This Mobile Money number is already linked to another account.",
                409,
            )
        if "phone" in error_message:
            raise RegistrationError(
                "phone",
                "This phone number is already registered. Please use a different number or login.",
                409,
            )
        raise RegistrationError(
            "general",
            "Registration failed. The information you provided may already be registered.",
            409,
        )


class AuthError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.message = message
        self.status = status


def _signup_fraud_checks(user):
    """Duplicate Ghana Card / MoMo and device checks (§9D). They flag for review, never block sign-up."""
    from . import fraud

    try:
        fraud.record_device(user)
        fraud.check_duplicates(user)
        db.session.commit()
    except Exception:  # noqa: BLE001
        db.session.rollback()
        current_app.logger.exception("Fraud checks failed at sign-up")


def login_user(identifier, password):
    """Login with phone OR business_email. Returns a JWT or raises AuthError.

    Guessing is limited (services/attempts.py): LOGIN_MAX_FAILURES failures per account in
    LOGIN_WINDOW_MINUTES, and password spraying from one address across many accounts. The attempt
    is recorded before the password is checked, and the answer is the same whether or not the
    account exists (a dummy hash check keeps the timing the same too).
    """
    from . import attempts
    from ..models.login_attempt import LoginAttempt

    identifier = (identifier or "").strip()
    if not identifier or not password:
        raise AuthError("Phone and password are required", 400)

    key = identifier.lower()
    cfg = current_app.config
    now = datetime.utcnow()
    window = now - timedelta(minutes=cfg.get("LOGIN_WINDOW_MINUTES", 15))
    ip = attempts.client_ip()

    user = User.query.filter(
        or_(User.phone == identifier, User.business_email == key)
    ).first()
    subject = attempts.subject_for(user, key)

    attempt = attempts.begin(LoginAttempt.LOGIN, subject, key, ip, now)
    if attempts.failures(LoginAttempt.LOGIN, subject, window) > cfg.get(
        "LOGIN_MAX_FAILURES", 5
    ) or attempts.ip_spraying(
        LoginAttempt.LOGIN,
        ip,
        subject,
        window,
        cfg.get("LOGIN_MAX_ACCOUNTS_PER_IP", 20),
    ):
        # A refused try isn't counted (it was only written so parallel requests see each other),
        # so waiting out the window always works
        attempts.discard(attempt)
        minutes = cfg.get("LOGIN_WINDOW_MINUTES", 15)
        raise AuthError(
            f"Too many failed attempts. Please wait {minutes} minutes and try again, "
            "or reset your password.",
            429,
        )

    if user:
        ok = guard.pwd_ctx.verify(password, user.password)
    else:
        guard.pwd_ctx.dummy_verify()  # same time taken, so unknown accounts can't be told apart
        ok = False
    if not ok:
        raise AuthError("Invalid phone or password", 401)
    attempts.succeed(attempt)
    # Restricted accounts can still sign in to pay what they owe (they can't buy: customer_purchase.py)
    if user.status not in ("approved", "active", "restricted"):
        raise AuthError(
            "Account not approved yet. Please wait for admin approval.", 403
        )

    return guard.encode_jwt_token(user)


def _generic_reset_response():
    # Same response whether or not the account exists, so the endpoint can't be used to find accounts
    return {
        "message": "If your account exists, you will receive a password reset code"
    }, 200


class ForgotPasswordResource(Resource):
    @api_doc(
        "Email or text a password-reset code",
        description="Send the account's email or phone number. Answers the same whether or not the account exists, so it can't be used to find accounts.",
        body=obj({"email": STR, "phone": STR}),
        responses={
            200: ("Sent, if the account exists", MESSAGE),
            400: "Email and phone both missing",
        },
    )
    def post(self):
        """Request password reset - sends OTP to the user's email or phone"""
        data = request.get_json() or {}
        channel, identifier, user = _reset_target(data)

        if not channel:
            return {"error": "Email address or phone number is required"}, 400
        email = identifier if channel == "email" else ""

        # At most RESET_REQUESTS_PER_IP_PER_HOUR from one address (same answer either way), so this
        # can't be used to flood someone's inbox
        from . import attempts
        from ..models.login_attempt import LoginAttempt

        ip = attempts.client_ip()
        hour_ago = datetime.utcnow() - timedelta(hours=1)
        attempts.begin(LoginAttempt.RESET, f"ip:{ip}", identifier, ip)
        if ip and attempts.failures(
            LoginAttempt.RESET, f"ip:{ip}", hour_ago
        ) > current_app.config.get("RESET_REQUESTS_PER_IP_PER_HOUR", 10):
            return _generic_reset_response()

        if not user:
            return _generic_reset_response()

        now = datetime.utcnow()
        # Throttle: one code per minute
        if (
            user.reset_otp_expiry
            and user.reset_otp_expiry - OTP_TTL + OTP_RESEND_AFTER > now
        ):
            return _generic_reset_response()
        # At most RESET_CODES_PER_ACCOUNT_PER_DAY codes a day for one account: with 5 guesses per code
        # that bounds guessing from many addresses (the owner is emailed every code, so it's visible)
        issued_today = LoginAttempt.query.filter(
            LoginAttempt.kind == LoginAttempt.RESET,
            LoginAttempt.subject == attempts.subject_for(user, identifier),
            LoginAttempt.created_at >= now - timedelta(days=1),
        ).count()
        # The owner asking from an address they've signed in from before still gets a code, so someone
        # using up the allowance can't lock them out
        known_address = (
            bool(ip)
            and LoginAttempt.query.filter(
                LoginAttempt.kind == LoginAttempt.LOGIN,
                LoginAttempt.subject == attempts.subject_for(user, identifier),
                LoginAttempt.ip == ip,
                LoginAttempt.success.is_(True),
                LoginAttempt.created_at >= now - timedelta(days=30),
            ).first()
            is not None
        )
        if (
            issued_today
            >= current_app.config.get("RESET_CODES_PER_ACCOUNT_PER_DAY", 10)
            and not known_address
        ):
            return _generic_reset_response()
        attempts.begin(
            LoginAttempt.RESET, attempts.subject_for(user, identifier), identifier, ip, now
        )

        otp = f"{secrets.randbelow(1_000_000):06d}"
        user.reset_otp = _digest(otp)
        user.reset_otp_expiry = now + OTP_TTL
        user.reset_otp_attempts = 0
        db.session.commit()

        if channel == "sms":
            _send_reset_sms(user, otp)
            return _generic_reset_response()

        try:
            msg = Message(
                subject="Tabital Pay - Password Reset Code",
                recipients=[email],
                html=f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Password Reset - Tabital Pay</title>
                    <style>
                        body {{
                            font-family: Arial, sans-serif;
                            background-color: #f5f7fa;
                            margin: 0;
                            padding: 0;
                        }}
                        .container {{
                            max-width: 600px;
                            margin: 0 auto;
                            background: white;
                            border-radius: 16px;
                            overflow: hidden;
                            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
                        }}
                        .header {{
                            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                            padding: 30px;
                            text-align: center;
                        }}
                        .header h1 {{
                            color: white;
                            margin: 0;
                            font-size: 28px;
                        }}
                        .content {{
                            padding: 30px;
                        }}
                        .otp-code {{
                            background: #f0f2f5;
                            padding: 20px;
                            text-align: center;
                            font-size: 32px;
                            font-weight: 700;
                            letter-spacing: 8px;
                            color: #667eea;
                            border-radius: 12px;
                            margin: 20px 0;
                        }}
                        .footer {{
                            padding: 20px;
                            text-align: center;
                            background: #f8f9fa;
                            color: #6c757d;
                            font-size: 12px;
                        }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>🔐 Tabital Pay</h1>
                        </div>
                        <div class="content">
                            <h2>Password Reset Request</h2>
                            <p>Hello {user.full_name or user.business_name or 'User'},</p>
                            <p>We received a request to reset your password. Use the OTP code below to proceed:</p>
                            <div class="otp-code">
                                {otp}
                            </div>
                            <p>This OTP is valid for <strong>10 minutes</strong>. If you didn't request this, please ignore this email.</p>
                            <p>For security reasons, never share this OTP with anyone.</p>
                        </div>
                        <div class="footer">
                            <p>&copy; Tabital Pay. All rights reserved.</p>
                        </div>
                    </div>
                </body>
                </html>
                """,
            )
            _send_in_background(msg)
        except Exception:
            # Logged, but the answer stays the same: an error only for real accounts would tell
            # anyone which emails are registered
            current_app.logger.exception("Failed to send password reset email")

        return _generic_reset_response()


def _send_reset_sms(user, otp):
    """Text the code, off the request, so a real account answers as fast as an unknown one."""
    import threading

    from . import sms

    app = current_app._get_current_object()
    to = user.phone
    body = (
        f"Your Tabital Pay reset code is {otp}. It expires in "
        f"{int(OTP_TTL.total_seconds() // 60)} minutes. Never share it."
    )

    def run():
        with app.app_context():
            try:
                result = sms.provider().send(to, body)
                if not result.ok:
                    app.logger.error("Reset SMS not sent: %s", result.error)
            except Exception:  # noqa: BLE001
                app.logger.exception("Failed to send password reset SMS")

    if app.config.get("TESTING"):
        run()
        return
    threading.Thread(target=run, daemon=True).start()


def _send_in_background(msg):
    """Send without making the request wait, so a real account answers as fast as an unknown one."""
    import threading

    app = current_app._get_current_object()
    if app.config.get("TESTING"):
        mail.send(msg)
        return

    def run():
        with app.app_context():
            try:
                mail.send(msg)
            except Exception:  # noqa: BLE001
                app.logger.exception("Failed to send password reset email")

    threading.Thread(target=run, daemon=True).start()


class VerifyResetOTPResource(Resource):
    @api_doc(
        "Check the emailed or texted code and get a reset token",
        description="Send the same email or phone the code was requested for. One answer (401) for every failure, so it can't be used to find accounts.",
        body=obj({"email": STR, "phone": STR, "otp": STR}, required=["otp"]),
        responses={
            200: ("Code accepted", obj({"message": STR, "reset_token": STR})),
            400: "Email or phone, or the code, missing",
            401: "Code is wrong or has expired",
            429: "Too many attempts",
        },
    )
    def post(self):
        """Verify OTP and return reset token"""
        data = request.get_json() or {}
        channel, identifier, user = _reset_target(data)
        otp = str(data.get("otp") or "").strip()

        if not channel or not otp:
            return {"error": "Email or phone, and OTP, are required"}, 400

        # One answer for every failure (unknown email, no code, wrong or expired code), so this can't
        # be used to find accounts. Attempts are recorded first, then counted: at most
        # OTP_MAX_ATTEMPTS per code, OTP_MAX_FAILURES_PER_DAY per account, and spraying from one
        # address across accounts is refused (services/attempts.py).
        from . import attempts
        from ..models.login_attempt import LoginAttempt

        bad = (
            {
                "error": "That code is wrong or has expired. Request a new code and try again."
            },
            401,
        )
        cfg = current_app.config
        now = datetime.utcnow()
        ip = attempts.client_ip()
        active = bool(
            user
            and user.reset_otp
            and user.reset_otp_expiry
            and user.reset_otp_expiry >= now
        )
        # Guesses against a real, issued code count on the account. Anything else (unknown email, no
        # code) counts on the caller's address, so a stranger can't use up the owner's daily quota.
        subject = attempts.subject_for(user, identifier) if active else f"ip:{ip}"
        attempt = attempts.begin(LoginAttempt.OTP, subject, identifier, ip, now)

        day = now - timedelta(days=1)
        # Daily cap per (account, address): a stranger using up their own allowance doesn't lock the
        # owner out. Guessing one code from many addresses is stopped by the per-code limit below.
        if attempts.failures(LoginAttempt.OTP, subject, day, ip=ip) > cfg.get(
            "OTP_MAX_FAILURES_PER_DAY", 10
        ) or attempts.ip_spraying(
            LoginAttempt.OTP, ip, subject, day, cfg.get("OTP_MAX_ACCOUNTS_PER_IP", 10)
        ):
            attempts.discard(attempt)
            return {
                "error": "Too many attempts. Please try again tomorrow or contact support."
            }, 429

        if not active:
            hmac.compare_digest("000000", otp)  # same work either way
            return bad
        issued = user.reset_otp_expiry - OTP_TTL
        if attempts.failures(LoginAttempt.OTP, subject, issued) > OTP_MAX_ATTEMPTS:
            user.reset_otp = None  # this code is used up
            db.session.commit()
            return bad
        if not hmac.compare_digest(user.reset_otp, _digest(otp)):
            return bad

        attempts.succeed(attempt)
        reset_token = secrets.token_urlsafe(32)
        user.reset_otp = None
        user.reset_otp_attempts = 0
        user.reset_token = _digest(reset_token)
        user.reset_token_expiry = datetime.utcnow() + RESET_TOKEN_TTL
        db.session.commit()

        return {
            "message": "OTP verified successfully",
            "reset_token": reset_token,
        }, 200


class ResetPasswordResource(Resource):
    @api_doc(
        "Set a new password with the reset token",
        body=obj(
            {"reset_token": STR, "new_password": {"type": "string", "minLength": 6}},
            required=["reset_token", "new_password"],
        ),
        responses={
            200: "Password changed",
            400: "Token or password missing, or password too short",
            401: "Reset token has expired",
            404: "Invalid reset token",
        },
    )
    def post(self):
        """Reset password using token"""
        data = request.get_json() or {}
        reset_token = data.get("reset_token")
        new_password = data.get("new_password") or ""

        if not reset_token or not new_password:
            return {"error": "Reset token and new password are required"}, 400

        if len(new_password) < MIN_PASSWORD_LENGTH:
            return {
                "error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            }, 400

        user = User.query.filter_by(reset_token=_digest(reset_token)).first()
        if not user:
            return {"error": "Invalid or expired reset token"}, 404

        if user.reset_token_expiry and user.reset_token_expiry < datetime.utcnow():
            return {"error": "Reset token has expired. Please request a new one."}, 401

        user.password = guard.hash_password(new_password)
        password_changed(user)
        user.reset_otp = None
        user.reset_otp_expiry = None
        user.reset_otp_attempts = 0
        user.reset_token = None
        user.reset_token_expiry = None
        db.session.commit()
        # A lock from failed sign-ins can't keep the owner out after they've proved it's them
        from . import attempts

        attempts.clear(user)

        recipient = user.business_email or user.email
        if recipient:
            try:
                msg = Message(
                    subject="Tabital Pay - Password Changed Successfully",
                    recipients=[recipient],
                    html=f"""
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="UTF-8">
                        <title>Password Changed - Tabital Pay</title>
                    </head>
                    <body style="font-family: Arial, sans-serif;">
                        <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                            <h2 style="color: #28a745;">✅ Password Changed Successfully</h2>
                            <p>Hello {user.full_name or user.business_name or 'User'},</p>
                            <p>Your password has been successfully changed.</p>
                            <p>If you did not make this change, please contact our support team immediately.</p>
                            <hr>
                            <p style="color: #6c757d; font-size: 12px;">Tabital Pay - Secure Payment Platform</p>
                        </div>
                    </body>
                    </html>
                    """,
                )
                mail.send(msg)
            except Exception:
                current_app.logger.exception(
                    "Failed to send password change confirmation"
                )

        return {"message": "Password reset successfully. You can now login."}, 200


def change_password(user, current_password, new_password):
    """Shared by the customer and merchant password endpoints. Returns (body, status)."""
    if not current_password or not new_password:
        return {"error": "Current password and new password are required"}, 400
    if not guard.pwd_ctx.verify(current_password, user.password):
        return {"error": "Current password is incorrect"}, 401
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return {
            "error": f"New password must be at least {MIN_PASSWORD_LENGTH} characters"
        }, 400
    user.password = guard.hash_password(new_password)
    password_changed(user)
    db.session.commit()
    return {"message": "Password updated successfully"}, 200
