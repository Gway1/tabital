"""Default system settings, inserted when missing (run.py and `flask seed-data`)."""


def init_system_settings():
    """Initialize default system settings if they don't exist"""
    from app.models.system_settings import SystemSetting
    from app.models.user import User

    default_settings = {
        "down_payment_percentage": {
            "value": 40,
            "type": "number",
            "description": "Percentage of product price paid upfront",
        },
        "merchant_fee_percentage": {
            "value": 10,
            "type": "number",
            "description": "Fee percentage deducted from merchant payout",
        },
        "late_fee_percentage": {
            "value": 10,
            "type": "number",
            "description": "Penalty percentage for overdue payments",
        },
        "service_fee": {
            "value": 0,
            "type": "number",
            "description": "Additional service fee charged to customer",
        },
        "min_installments": {
            "value": 2,
            "type": "number",
            "description": "Minimum number of installments allowed",
        },
        "max_installments": {
            "value": 24,
            "type": "number",
            "description": "Maximum number of installments allowed",
        },
        "default_installments": {
            "value": 4,
            "type": "number",
            "description": "Default number of installments",
        },
        "late_fee_grace_period_days": {
            "value": 0,
            "type": "number",
            "description": "No grace period: late fee applies the day after the due date (CLAUDE.md 13.1 D5)",
        },
        "late_fee_cap_percentage": {
            "value": 25,
            "type": "number",
            "description": "Total late fees on a plan can't exceed this % of the order's total payable",
        },
        # Unit economics (§7, §13 #14): margin model rates, all in %
        "gateway_fee_percentage": {
            "value": 2,
            "type": "number",
            "description": "Payment gateway cost as % of product price (included in the MDR, §6.1)",
        },
        "expected_credit_loss_percentage": {
            "value": 5,
            "type": "number",
            "description": "Expected default reserve as % of the financed balance (§7)",
        },
        "collections_cost_percentage": {
            "value": 3,
            "type": "number",
            "description": "Collections cost as % of the financed balance (§7)",
        },
        "fraud_loss_reserve_percentage": {
            "value": 0,
            "type": "number",
            "description": "Fraud loss reserve as % of the financed balance (§13 #14; not set yet)",
        },
        "cost_of_capital_annual_percentage": {
            "value": 0,
            "type": "number",
            "description": "Annual cost of the capital that funds the financed balance (§13 #14; not set yet)",
        },
        # Deferment (§4, §13 #8 interim rule; confirm with the founder)
        "deferment_enabled": {
            "value": True,
            "type": "boolean",
            "description": "Customers may pay a fee to push an instalment back",
        },
        "deferment_fee_percentage": {
            "value": 10,
            "type": "number",
            "description": "Deferment fee as a % of the deferred instalment (§4)",
        },
        "deferment_max_per_plan": {
            "value": 1,
            "type": "number",
            "description": "How many deferments a customer may use on one plan (§13 #8)",
        },
        "deferment_months": {
            "value": 1,
            "type": "number",
            "description": "How many months a deferment pushes the instalment (and the ones after it) back",
        },
        "down_payment_percentage_short_plans": {
            "value": 50,
            "type": "number",
            "description": "Down payment % for Pay in 2 and Pay in 3",
        },
        "delivery_fee": {
            "value": 50,
            "type": "number",
            "description": "Delivery fee, paid with the down payment and never financed",
        },
        # Phase 1 plans only, all 0% interest (CLAUDE.md §4). Extended 6- and 12-month plans
        # are for eligible customers and stay inactive until their rules are set (13.1 D7, D12).
        "installment_options": {
            "value": [
                {
                    "months": 1,
                    "label": "Full Payment",
                    "interest_rate": 0,
                    "is_active": True,
                },
                {
                    "months": 2,
                    "label": "Pay in 2",
                    "interest_rate": 0,
                    "is_active": True,
                },
                {
                    "months": 3,
                    "label": "Pay in 3",
                    "interest_rate": 0,
                    "is_active": True,
                },
                {
                    "months": 4,
                    "label": "Pay in 4",
                    "interest_rate": 0,
                    "is_active": True,
                },
                {
                    "months": 6,
                    "label": "6 Months",
                    "interest_rate": 10,
                    "is_active": False,
                },
                {
                    "months": 12,
                    "label": "12 Months",
                    "interest_rate": 0,
                    "is_active": False,
                },
            ],
            "type": "json",
            "description": "Available installment plan options",
        },
    }

    admin_user = User.query.filter_by(role="admin").first()
    admin_id = admin_user.id if admin_user else None

    for key, setting_data in default_settings.items():
        existing = SystemSetting.query.filter_by(setting_key=key).first()
        if not existing:
            SystemSetting.set_value(
                key=key,
                value=setting_data["value"]
                if setting_data["type"] != "json"
                else __import__("json").dumps(setting_data["value"]),
                value_type=setting_data["type"],
                description=setting_data["description"],
                updated_by=admin_id,
            )
            print(f"✅ Created setting: {key} = {setting_data['value']}")
        else:
            print(f"⚠️ Setting already exists: {key}")
