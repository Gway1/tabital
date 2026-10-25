from ..extensions import db
import sqlalchemy as sa
from datetime import datetime


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="customer")
    status = db.Column(db.String(50), default="pending")
    swift_code = db.Column(db.String(50), unique=True)
    # Role-specific IDs
    customer_id = db.Column(db.String(50), unique=True, nullable=True, index=True)
    merchant_id = db.Column(db.String(50), unique=True, nullable=True, index=True)
    monthly_salary = db.Column(db.String(100))

    # Customer Fields
    full_name = db.Column(db.String(100))
    dob = db.Column(db.String(50))
    national_id = db.Column(db.String(100))
    city = db.Column(db.String(100))
    gps = db.Column(db.String(200))
    agree = db.Column(db.Boolean, default=False)
    designation = db.Column(db.String(200))
    company = db.Column(db.String(200))
    address = db.Column(db.String(200))
    income_range = db.Column(db.String(100))
    product_name = db.Column(db.String(200))
    total_price = db.Column(db.Float)
    payment_plan = db.Column(db.String(50))
    payment_frequency = db.Column(db.String(50))
    ref_name = db.Column(db.String(100))
    ref_phone = db.Column(
        db.String(100)
    )  # not unique: two customers may share a referee
    ref_relationship = db.Column(db.String(100))
    shop_url = db.Column(db.String(200))
    # Payment gateway (hosted checkout for the merchant's own website)
    gateway_enabled = db.Column(db.Boolean, default=True)
    webhook_url = db.Column(db.String(500))
    webhook_secret = db.Column(db.String(80))

    # Merchant Fields
    business_name = db.Column(db.String(100))
    owner_name = db.Column(db.String(100))
    product_type = db.Column(db.String(100))
    has_shop = db.Column(db.String(10))
    years_in_business = db.Column(db.String(50))
    offers_credit = db.Column(db.String(10))
    price_range = db.Column(db.String(50))
    business_type = db.Column(db.String(50))
    registration_number = db.Column(db.String(100))
    tax_id = db.Column(db.String(100))
    business_address = db.Column(db.String(200))
    business_phone = db.Column(db.String(20), unique=True)
    business_email = db.Column(db.String(100), unique=True)
    email = db.Column(db.String(100))
    website = db.Column(db.String(200))
    description = db.Column(db.Text)
    total_products = db.Column(db.Integer, default=0)
    total_sales = db.Column(db.Float, default=0)
    rating = db.Column(db.Float, default=0)
    verified = db.Column(db.Boolean, default=False)
    payment_method = db.Column(db.String(50))
    momo_name = db.Column(db.String(100))
    momo_number = db.Column(db.String(100), unique=True)
    bank_name = db.Column(db.String(100))
    account_name = db.Column(db.String(100))
    branch_name = db.Column(db.String(100))
    account_number = db.Column(db.String(100), unique=True)

    # Underwriting (Phase 3). national_id holds the Ghana Card number.
    monthly_salary = db.Column(db.Numeric(12, 2))
    employment_start_date = db.Column(db.Date)
    salary_paid_to_bank = db.Column(db.Boolean, default=False)
    salary_verified = db.Column(
        db.Boolean, default=False
    )  # set by admin against the salary certificate
    risk_tier = db.Column(db.String(10))  # latest decision: low / medium / high
    credit_limit = db.Column(db.Numeric(12, 2))  # latest decision
    credit_limit_override = db.Column(
        db.Numeric(12, 2)
    )  # admin override, with a reason on the assessment
    limit_updated_at = db.Column(db.DateTime)

    # Merchant settlement and payouts (Phase 5)
    settlement_period_days = db.Column(db.Integer, default=7)  # 3, 7 or 30 (§13.1 D4)
    payout_method = db.Column(db.String(20))  # mobile_money or bank
    payout_bank_code = db.Column(db.String(20))  # Paystack bank / MoMo provider code
    paystack_recipient_code = db.Column(
        db.String(64)
    )  # created from the current payout details
    payout_details_updated_at = db.Column(db.DateTime)
    payout_hold_until = db.Column(db.DateTime)  # payouts paused after a details change

    # Employment verification (Phase 6, §9B): needed for a first purchase and high-ticket orders
    employment_verified_at = db.Column(db.DateTime)
    employment_verified_by = db.Column(db.Integer)  # admin user id
    employment_verification_method = db.Column(
        db.String(30)
    )  # employer_call / employer_letter / payslip / ssnit
    employment_verification_note = db.Column(db.String(500))

    # KYC Fields
    kyc_status = db.Column(db.String(50), default="pending")
    verification_level = db.Column(db.String(50), default="standard")
    # Admins only: 'management' (approvals, rates, settings, reveals, exports) or 'operations'
    # (read-only apart from reminders and notes). See services/access.py
    admin_level = db.Column(db.String(20))
    # Merchants only: premium | standard | high_risk, set by management (services/merchant_fees.py).
    # Empty means standard.
    merchant_fee_tier = db.Column(db.String(20))
    aml_screening = db.Column(db.String(50), default="pending")
    kyc_completed_on = db.Column(db.DateTime)

    # Settlement Fields
    commission_rate = db.Column(db.Float, default=2.5)
    pending_payout = db.Column(db.Float, default=0)
    next_settlement = db.Column(db.String(50))

    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, onupdate=db.func.now())
    reset_otp = db.Column(db.String(64))  # keyed hash, never the code itself
    reset_otp_expiry = db.Column(db.DateTime)
    reset_otp_attempts = db.Column(db.Integer, default=0)
    reset_token = db.Column(db.String(100))
    reset_token_expiry = db.Column(db.DateTime)
    # Sessions started before this are signed out (services/auth_service.py: password_changed)
    password_changed_at = db.Column(db.DateTime)

    # Flask-Praetorian Methods
    @property
    def identity(self):
        return str(self.id)

    @property
    def rolenames(self):
        return [self.role] if self.role else ["customer"]

    @classmethod
    def lookup(cls, identity):
        return cls.query.filter(cls.phone == identity).first()

    @classmethod
    def identify(cls, id):
        return cls.query.get(id)

    def is_valid(self):
        """Checked by flask_praetorian when a token is issued or refreshed (not on every request, so a
        suspended user keeps access until their token expires, JWT_ACCESS_MINUTES). 'active' is the status admins set
        to reinstate an account; 'restricted' can still sign in to pay (not buy: customer_purchase.py).
        """
        return self.status in ("approved", "active", "restricted")

    @staticmethod
    def get_next_customer_id():
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(User.customer_id, 2).cast(sa.Integer))
            )
            .filter(User.customer_id.isnot(None))
            .scalar()
        )
        return (result + 1) if result else 1

    @staticmethod
    def get_next_merchant_id():
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(User.merchant_id, 2).cast(sa.Integer))
            )
            .filter(User.merchant_id.isnot(None))
            .scalar()
        )
        return (result + 1) if result else 1

    def generate_customer_id(self):
        return f"C{User.get_next_customer_id():03d}"

    def generate_merchant_id(self):
        return f"M{User.get_next_merchant_id():03d}"
