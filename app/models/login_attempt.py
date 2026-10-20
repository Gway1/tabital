"""Sign-in, reset-code and sign-up attempts: used to slow down guessing (services/attempts.py) and for audit."""
from datetime import datetime

from ..extensions import db


class LoginAttempt(db.Model):
    __tablename__ = "login_attempts"

    LOGIN, OTP, SIGNUP, RESET = (
        "login",
        "otp",
        "signup",
        "reset",
    )  # RESET: reset-code requests

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False, default=LOGIN, index=True)
    # Whose attempt: "user:<id>" when the account exists, else "id:<phone/email as typed>" (or "ip:<ip>")
    subject = db.Column(db.String(140), nullable=False, index=True)
    identifier = db.Column(
        db.String(120), nullable=False, index=True
    )  # what was typed (normalised)
    ip = db.Column(db.String(64), index=True)
    success = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True
    )
