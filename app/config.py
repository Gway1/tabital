import os

from dotenv import load_dotenv

load_dotenv()


def _database_url():
    url = os.getenv("DATABASE_URL", "sqlite:///app.db")
    # Render/Heroku style URLs use the legacy "postgres://" scheme, which SQLAlchemy rejects
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _csv(name, default=""):
    return [v.strip() for v in os.getenv(name, default).split(",") if v.strip()]


class Config:
    # All secrets come from the environment. Never commit real values; see .env.example
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_DATABASE_URI = _database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    # Swagger UI at /swagger-ui. It lists the admin endpoints, so off in production unless set
    ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", os.getenv("FLASK_DEBUG", "0")) == "1"

    # Public URL of the web app (used in payment links / QR codes)
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:4200")

    # Origins allowed to call the API from a browser
    CORS_ORIGINS = _csv(
        "CORS_ORIGINS", "http://localhost:4200,https://app.tabitalpay.com"
    )

    # Token lifetimes for flask_praetorian
    JWT_ACCESS_LIFESPAN = {"minutes": int(os.getenv("JWT_ACCESS_MINUTES", "60"))}
    # Guessing limits (services/attempts.py)
    LOGIN_MAX_FAILURES = int(
        os.getenv("LOGIN_MAX_FAILURES", "5")
    )  # per account per window
    LOGIN_MAX_ACCOUNTS_PER_IP = int(
        os.getenv("LOGIN_MAX_ACCOUNTS_PER_IP", "20")
    )  # spraying from one address
    LOGIN_WINDOW_MINUTES = int(os.getenv("LOGIN_WINDOW_MINUTES", "15"))
    OTP_MAX_FAILURES_PER_DAY = int(
        os.getenv("OTP_MAX_FAILURES_PER_DAY", "10")
    )  # reset codes, per account
    OTP_MAX_ACCOUNTS_PER_IP = int(os.getenv("OTP_MAX_ACCOUNTS_PER_IP", "10"))
    SIGNUP_MAX_FAILURES_PER_IP = int(
        os.getenv("SIGNUP_MAX_FAILURES_PER_IP", "10")
    )  # per window
    RESET_REQUESTS_PER_IP_PER_HOUR = int(
        os.getenv("RESET_REQUESTS_PER_IP_PER_HOUR", "10")
    )
    RESET_CODES_PER_ACCOUNT_PER_DAY = int(
        os.getenv("RESET_CODES_PER_ACCOUNT_PER_DAY", "10")
    )
    # X-Forwarded-For hops added by our own proxies. Render: 1. Set 0 if nothing sits in front of the
    # app, or clients could pick their own IP address.
    TRUSTED_PROXY_COUNT = int(os.getenv("TRUSTED_PROXY_COUNT", "1"))
    JWT_REFRESH_LIFESPAN = {"days": int(os.getenv("JWT_REFRESH_DAYS", "7"))}

    # Paystack (CLAUDE.md §13.1 D11). Use sk_test_/pk_test_ keys until go-live.
    PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY")
    PAYSTACK_PUBLIC_KEY = os.getenv("PAYSTACK_PUBLIC_KEY")
    PAYSTACK_BASE_URL = os.getenv("PAYSTACK_BASE_URL", "https://api.paystack.co")
    # Where Paystack sends the customer after paying (a frontend page)
    PAYSTACK_CALLBACK_URL = os.getenv(
        "PAYSTACK_CALLBACK_URL", "http://localhost:4200/customer/payment-callback"
    )

    # SMS/WhatsApp provider for reminders: 'log' (default, writes to the log only) until a
    # Ghana provider is chosen (§13.1 D11)
    SMS_PROVIDER = os.getenv("SMS_PROVIDER", "log")
    # mNotify (founder choice 2026-09-26). The sender ID must be registered and approved in mNotify.
    MNOTIFY_API_KEY = os.getenv("MNOTIFY_API_KEY")
    MNOTIFY_SENDER_ID = os.getenv("MNOTIFY_SENDER_ID", "TabitalPay")
    MNOTIFY_BASE_URL = os.getenv("MNOTIFY_BASE_URL", "https://api.mnotify.com/api")

    # Smile ID identity checks (founder choice 2026-09-26). Sandbox until go-live.
    SMILEID_PARTNER_ID = os.getenv("SMILEID_PARTNER_ID")
    SMILEID_API_KEY = os.getenv("SMILEID_API_KEY")
    SMILEID_BASE_URL = os.getenv(
        "SMILEID_BASE_URL", "https://testapi.smileidentity.com"
    )
    # Public URL Smile ID posts results to (must be https in production)
    SMILEID_CALLBACK_URL = os.getenv("SMILEID_CALLBACK_URL")
    # Legal documents (§10). Bump TERMS_VERSION whenever the Terms change; each order records it.
    TERMS_URL = os.getenv("TERMS_URL", "https://tabitalpay.com/elementor-page-3332/")
    TERMS_VERSION = os.getenv("TERMS_VERSION", "2026-09-26")
    PRIVACY_POLICY_URL = os.getenv(
        "PRIVACY_POLICY_URL", "https://tabitalpay.com/privacy-policy/"
    )

    # Mail settings for password reset
    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "True") == "True"
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "False") == "True"
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", os.getenv("MAIL_USERNAME"))
