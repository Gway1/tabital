from ..extensions import db
from datetime import datetime


class InstalmentPayment(db.Model):
    __tablename__ = "instalment_payments"

    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(db.String(50), unique=True, nullable=False, index=True)
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=False
    )

    # Payment Details
    installment_number = db.Column(db.Integer, nullable=False)
    due_date = db.Column(db.DateTime, nullable=False)
    paid_date = db.Column(db.DateTime)
    amount = db.Column(db.Float, nullable=False)
    paid_amount = db.Column(db.Float, default=0)

    # Status
    status = db.Column(
        db.String(50), default="pending"
    )  # pending, paid, overdue, partial
    payment_method = db.Column(db.String(50))
    payment_reference = db.Column(db.String(100))

    # Late fee
    late_fee = db.Column(db.Float, default=0)
    late_fee_paid = db.Column(db.Boolean, default=False)
    late_fee_applied_date = db.Column(db.DateTime)  # Track when late fee was applied
    # 0 = no fee yet, 1 = first fee (day after due), 2 = second fee (31+ days past due, §6.2)
    late_fee_stage = db.Column(db.Integer, default=0)
    second_late_fee_applied_date = db.Column(db.DateTime)
    original_due_date = db.Column(
        db.DateTime
    )  # set when a deferment (§4) moved the due date

    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Relationship
    plan = db.relationship("InstalmentPlan", backref="payments")

    def generate_payment_id(self):
        """Generate a payment ID in format PAY001, PAY002, etc."""
        from sqlalchemy import func

        result = (
            db.session.query(
                func.max(func.substr(InstalmentPayment.payment_id, 4).cast(db.Integer))
            )
            .filter(InstalmentPayment.payment_id.isnot(None))
            .scalar()
        )
        return f"PAY{(result + 1) if result else 1:03d}"

    def _fee_amount(self, today, pct_key, default_pct):
        """A fee of pct of the overdue part of the instalment (LF = O x LR, §5.2), limited by what's
        left under the plan's cap. Part payments go to the instalment first, so they lower O.
        """
        from decimal import Decimal, ROUND_HALF_UP
        from .system_settings import SystemSetting
        from ..services import ledger

        pct = Decimal(str(SystemSetting.get_value(pct_key, default_pct)))
        overdue = max(
            Decimal("0"),
            Decimal(str(self.amount)).quantize(Decimal("0.01")) - self.part_paid(),
        )
        fee = (overdue * pct / 100).quantize(Decimal("0.01"), ROUND_HALF_UP)
        return min(fee, ledger.late_fee_cap_remaining(self.plan))

    def part_payments(self):
        from .part_payment import InstalmentPartPayment

        if self.id is None:
            return []
        return (
            InstalmentPartPayment.query.filter_by(payment_id=self.id)
            .order_by(InstalmentPartPayment.id)
            .all()
        )

    def part_paid(self):
        """Money already received towards this instalment by part payments (Decimal GHS)."""
        from decimal import Decimal
        from .part_payment import InstalmentPartPayment

        if self.id is None:
            return Decimal("0.00")
        pesewas = (
            db.session.query(
                db.func.coalesce(db.func.sum(InstalmentPartPayment.amount_pesewas), 0)
            )
            .filter(InstalmentPartPayment.payment_id == self.id)
            .scalar()
        )
        return (Decimal(int(pesewas)) / 100).quantize(Decimal("0.01"))

    def gross_due(self):
        """Instalment + unpaid late fees, before any part payments (Decimal GHS)."""
        from decimal import Decimal

        fee = (
            Decimal(str(self.late_fee or 0)) if not self.late_fee_paid else Decimal("0")
        )
        return (Decimal(str(self.amount)) + fee).quantize(Decimal("0.01"))

    def apply_late_fee(self, today=None, commit=True):
        """
        First late fee, the day after the due date (no grace period, CLAUDE.md §13.1 D5):
        late_fee_percentage (default 10%) of the instalment amount (§6.2), limited so a plan's
        total late fees never exceed late_fee_cap_percentage (default 25%) of the order's total
        payable (D14). Waived fees don't count toward the cap. Charged once per instalment.
        """
        from ..services import ledger

        if self.status in ("paid", "pending_verification") or not self.due_date:
            return False
        today = today or datetime.utcnow().date()
        if not today > self.due_date.date():
            return False
        # Only once per instalment (a waived fee must not be re-charged)
        if (
            self.late_fee
            or self.late_fee_paid
            or self.late_fee_applied_date is not None
        ):
            return False

        fee = self._fee_amount(today, "late_fee_percentage", 10)
        # Still overdue (and recorded as late) even when the cap leaves no fee to charge
        self.late_fee = float(fee)
        self.late_fee_applied_date = datetime.combine(today, datetime.min.time())
        self.late_fee_stage = 1
        self.status = "overdue"
        if fee > 0:
            ledger.late_fee_charged(self.plan, self, fee)
        if commit:
            db.session.commit()
        return True

    def apply_second_late_fee(self, today=None, commit=True):
        """
        Second late fee once the instalment is 31+ days past due (§6.2 'additional 10%'):
        second_late_fee_percentage (default 10%) of the still-overdue instalment amount
        (§13 #7), within the same plan cap. Charged once per instalment.
        """
        from ..models.system_settings import SystemSetting
        from ..services import ledger

        if self.status in ("paid", "pending_verification") or not self.due_date:
            return False
        if (self.late_fee_stage or 0) >= 2 or self.late_fee_applied_date is None:
            return False
        today = today or datetime.utcnow().date()
        start_day = int(SystemSetting.get_value("second_late_fee_after_days", 31))
        if (today - self.due_date.date()).days < start_day:
            return False

        fee = self._fee_amount(today, "second_late_fee_percentage", 10)
        self.late_fee_stage = 2
        self.second_late_fee_applied_date = datetime.combine(today, datetime.min.time())
        if fee > 0:
            self.late_fee = float((self.late_fee or 0) + float(fee))
            self.late_fee_paid = False
            ledger.late_fee_charged(self.plan, self, fee)
        if commit:
            db.session.commit()
        return True

    def get_total_due(self):
        """What's still owed on this instalment: amount + unpaid late fees - money received.
        For a paid instalment that's any late fee charged after the customer started paying
        (paid_amount holds everything received, part payments included)."""
        from decimal import Decimal

        if self.status == "paid":
            received = Decimal(str(self.paid_amount or 0))
            return float(max(Decimal("0.00"), self.gross_due() - received))
        return float(max(Decimal("0.00"), self.gross_due() - self.part_paid()))
