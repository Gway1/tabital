"""Plain-text email through Flask-Mail, with the same result shape as the SMS providers."""
from flask import current_app

from ..extensions import mail
from .sms import SendResult


def send(to, subject, body):
    cfg = current_app.config
    if not cfg.get("MAIL_USERNAME") or not cfg.get("MAIL_PASSWORD"):
        # Same default as SMS: with no mail account set up, write it to the log
        current_app.logger.info("Email (log only) to %s: %s | %s", to, subject, body)
        return SendResult(ok=True, provider="log")
    from flask_mail import Message

    try:
        mail.send(Message(subject=subject, recipients=[to], body=body))
    except Exception as e:  # a mail failure is recorded on the outbox row and retried
        return SendResult(ok=False, provider="smtp", error=str(e)[:255])
    return SendResult(ok=True, provider="smtp")
