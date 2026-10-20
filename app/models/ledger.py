"""Append-only money ledger (CLAUDE.md §5.4, §12).

Every change to what a customer owes, or what Tabital owes a merchant, is one row here.
Rows are never updated or deleted; corrections are new rows. Amounts are integer pesewas
(1 GHS = 100 pesewas), so there's no float rounding.

Sign convention for the customer account: a positive amount increases what the customer owes
(a charge), a negative amount reduces it (a payment, waiver or refund).
"""
from datetime import datetime

from ..extensions import db


class LedgerEntry(db.Model):
    __tablename__ = "ledger_entries"

    # entry_type values
    PLAN_OPENED = "plan_opened"  # + total payable of the contract
    PAYMENT_RECEIVED = "payment_received"  # - money received from the customer
    LATE_FEE_CHARGED = "late_fee_charged"  # + late fee
    LATE_FEE_WAIVED = "late_fee_waived"  # - late fee reversed by an admin
    DEFERMENT_FEE = (
        "deferment_fee"  # + fee to push an instalment back (§4); paid at once
    )
    BALANCE_WRITTEN_OFF = "balance_written_off"  # - remaining balance cancelled (e.g. dispute won by customer)
    MERCHANT_FEE = "merchant_fee"  # merchant account: MDR kept by Tabital
    MERCHANT_PAYABLE = (
        "merchant_payable"  # merchant account: settlement owed to merchant
    )
    MERCHANT_SETTLED = (
        "merchant_settled"  # merchant account: - paid out to the merchant
    )
    MERCHANT_CLAWBACK = (
        "merchant_clawback"  # merchant account: - taken back (refund / dispute lost)
    )

    CUSTOMER = "customer"
    MERCHANT = "merchant"

    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(
        db.Integer, db.ForeignKey("instalment_plans.id"), nullable=False, index=True
    )
    payment_id = db.Column(
        db.Integer, db.ForeignKey("instalment_payments.id"), nullable=True, index=True
    )
    account = db.Column(db.String(20), nullable=False, default=CUSTOMER)
    entry_type = db.Column(db.String(40), nullable=False)
    amount_pesewas = db.Column(db.BigInteger, nullable=False)
    reference = db.Column(db.String(100))
    note = db.Column(db.String(255))
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    plan = db.relationship(
        "InstalmentPlan", backref=db.backref("ledger_entries", lazy="dynamic")
    )
