# models/system_settings.py
from ..extensions import db
from datetime import datetime
import json


class SystemSetting(db.Model):
    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), unique=True, nullable=False)
    setting_value = db.Column(db.Text, nullable=False)
    setting_type = db.Column(
        db.String(50), default="string"
    )  # string, number, json, boolean
    description = db.Column(db.String(500))
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

    # Relationship
    updater = db.relationship("User", foreign_keys=[updated_by])

    @classmethod
    def get_value(cls, key, default=None):
        setting = cls.query.filter_by(setting_key=key).first()
        if not setting:
            return default

        if setting.setting_type == "number":
            return float(setting.setting_value)
        elif setting.setting_type == "boolean":
            return setting.setting_value.lower() == "true"
        elif setting.setting_type == "json":
            return json.loads(setting.setting_value)
        else:
            return setting.setting_value

    @classmethod
    def set_value(
        cls, key, value, value_type="string", description=None, updated_by=None
    ):
        setting = cls.query.filter_by(setting_key=key).first()
        if setting:
            setting.setting_value = str(value)
            setting.setting_type = value_type
            if description:
                setting.description = description
            setting.updated_by = updated_by
            setting.updated_at = datetime.utcnow()
        else:
            setting = cls(
                setting_key=key,
                setting_value=str(value),
                setting_type=value_type,
                description=description,
                updated_by=updated_by,
            )
            db.session.add(setting)
        db.session.commit()
        return setting


class SettingChange(db.Model):
    """Audit log: who changed which business setting, from what, to what, and why."""

    __tablename__ = "setting_changes"

    id = db.Column(db.Integer, primary_key=True)
    setting_key = db.Column(db.String(100), nullable=False, index=True)
    old_value = db.Column(db.Text)  # JSON
    new_value = db.Column(db.Text, nullable=False)
    changed_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    changer = db.relationship("User", foreign_keys=[changed_by])


class TransactionCharge(db.Model):
    __tablename__ = "transaction_charges"

    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(
        db.Integer, db.ForeignKey("transactions.id"), nullable=False
    )

    # Charges applied to this transaction
    down_payment_percentage = db.Column(db.Float, nullable=False)
    down_payment_amount = db.Column(db.Float, nullable=False)
    merchant_fee_percentage = db.Column(db.Float, nullable=False)
    merchant_fee_amount = db.Column(db.Float, nullable=False)
    service_fee = db.Column(db.Float, default=0)
    late_fee_percentage = db.Column(db.Float, default=10)

    # Calculated amounts
    remaining_balance = db.Column(db.Float, nullable=False)
    installment_amount = db.Column(db.Float, nullable=False)
    total_payable = db.Column(db.Float, nullable=False)
    merchant_payout = db.Column(db.Float, nullable=False)

    # Installment details
    number_of_installments = db.Column(db.Integer, nullable=False)
    remaining_installments = db.Column(db.Integer, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship
    transaction = db.relationship("Transaction", backref="charges")
