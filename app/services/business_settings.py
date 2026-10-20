"""Business settings the founder/admin can change (CLAUDE.md §5.4: every rate is configurable).

This registry is the single description of each editable setting: label, type, range, default
and what a change affects. Defaults here must equal the defaults the code passes to
SystemSetting.get_value (a test checks this). Settings that aren't listed (reminder templates,
risk rules, legacy instalment options) can't be edited through this screen.

Every change is validated, needs a reason, and is written to the setting_changes audit log.
"""
import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from ..extensions import db
from ..models.system_settings import SettingChange, SystemSetting

NEW_ORDERS = "New orders only. Plans already approved keep the schedule they were approved with (§5.4)."
FROM_NOW = "From now on, including on existing plans."
MARGIN = "Reporting only (Unit Economics). Doesn't change what anyone is charged."

GROUPS = [
    ("pricing", "Plans and pricing"),
    ("late_fees", "Late fees"),
    ("deferment", "Deferment"),
    ("servicing", "Collections and servicing"),
    ("payouts", "Merchant payouts"),
    ("identity", "Identity and fraud checks"),
    ("margin", "Margin model (reporting)"),
]


def _s(
    key,
    group,
    label,
    kind,
    default,
    help,
    applies,
    minimum=None,
    maximum=None,
    ref=None,
):
    return {
        "key": key,
        "group": group,
        "label": label,
        "kind": kind,
        "default": default,
        "help": help,
        "applies": applies,
        "min": minimum,
        "max": maximum,
        "ref": ref,
    }


# kind: percent | money | integer | days | hours | boolean | text | day_list
SETTINGS = [
    # Plans and pricing
    _s(
        "down_payment_percentage",
        "pricing",
        "Pay in 4 down payment",
        "percent",
        40,
        "Payment 1 as a % of the price. A customer's risk tier can set its own rate.",
        NEW_ORDERS,
        25,
        100,
        "§4, §13 #3",
    ),
    _s(
        "down_payment_percentage_short_plans",
        "pricing",
        "Pay in 2 and Pay in 3 down payment",
        "percent",
        50,
        "Payment 1 as a % of the price on the shorter plans.",
        NEW_ORDERS,
        25,
        100,
        "§13 #5",
    ),
    _s(
        "delivery_fee",
        "pricing",
        "Delivery fee",
        "money",
        50,
        "Added to Payment 1 only; never financed. In-store pickups pay no delivery fee.",
        NEW_ORDERS,
        0,
        1000,
        "D3",
    ),
    _s(
        "service_fee",
        "pricing",
        "Service fee",
        "money",
        0,
        "Extra fee added to the financed balance. Standard plans are 0% interest, so keep this at 0.",
        NEW_ORDERS,
        0,
        1000,
        "§5.2, §13 #11",
    ),
    _s(
        "merchant_fee_percentage",
        "pricing",
        "Merchant fee (MDR): standard tier",
        "percent",
        10,
        "Kept from the merchant's payout. Includes the 2% gateway fee. Merchants are on this tier unless "
        "management picks another.",
        NEW_ORDERS,
        0,
        30,
        "§6.1",
    ),
    _s(
        "merchant_fee_premium_percentage",
        "pricing",
        "Merchant fee (MDR): premium / low-risk tier",
        "percent",
        8,
        "For merchants management puts on the premium tier. Includes the 2% gateway fee.",
        NEW_ORDERS,
        0,
        30,
        "§6.1",
    ),
    _s(
        "merchant_fee_high_risk_percentage",
        "pricing",
        "Merchant fee (MDR): high-risk tier",
        "percent",
        12,
        "For merchants management puts on the high-risk tier. Includes the 2% gateway fee.",
        NEW_ORDERS,
        0,
        30,
        "§6.1, §13 #2",
    ),
    # Late fees
    _s(
        "late_fee_percentage",
        "late_fees",
        "First late fee",
        "percent",
        10,
        "% of the overdue instalment, charged the day after the due date (no grace period).",
        FROM_NOW,
        0,
        50,
        "§6.2, D5",
    ),
    _s(
        "second_late_fee_percentage",
        "late_fees",
        "Second late fee",
        "percent",
        10,
        "% of the still-overdue instalment, charged once it reaches the day below.",
        FROM_NOW,
        0,
        50,
        "§13 #7",
    ),
    _s(
        "second_late_fee_after_days",
        "late_fees",
        "Second late fee from",
        "days",
        31,
        "Days past due when the second late fee is charged.",
        FROM_NOW,
        2,
        90,
        "§6.2",
    ),
    _s(
        "late_fee_cap_percentage",
        "late_fees",
        "Late fee cap",
        "percent",
        25,
        "Total late fees on a plan can't exceed this % of the order's total payable.",
        FROM_NOW,
        0,
        100,
        "D14",
    ),
    # Deferment
    _s(
        "deferment_enabled",
        "deferment",
        "Deferment available",
        "boolean",
        True,
        "Customers may pay a fee to push an upcoming instalment back.",
        FROM_NOW,
        ref="§4",
    ),
    _s(
        "deferment_fee_percentage",
        "deferment",
        "Deferment fee",
        "percent",
        10,
        "% of the deferred instalment, paid up front.",
        FROM_NOW,
        0,
        50,
        "§4",
    ),
    _s(
        "deferment_max_per_plan",
        "deferment",
        "Deferments per plan",
        "integer",
        1,
        "How many times a customer may defer on one plan.",
        FROM_NOW,
        0,
        3,
        "§13 #8",
    ),
    _s(
        "deferment_months",
        "deferment",
        "Months per deferment",
        "integer",
        1,
        "How far the instalment (and the ones after it) move.",
        FROM_NOW,
        1,
        3,
        "§13 #8",
    ),
    # Collections and servicing
    _s(
        "charge_off_after_days",
        "servicing",
        "Charge-off after",
        "days",
        90,
        "Days past due when a plan is charged off (moves to defaulted). Collections continue.",
        FROM_NOW,
        60,
        365,
        "§8.4",
    ),
    _s(
        "high_ticket_threshold",
        "servicing",
        "High-ticket order",
        "money",
        10000,
        "Orders at or above this need employment verification and may go to legal recovery.",
        FROM_NOW,
        1000,
        1000000,
        "D6",
    ),
    _s(
        "dispute_resolution_days",
        "servicing",
        "Dispute target",
        "days",
        21,
        "Target for resolving a dispute; the plan is paused meanwhile.",
        FROM_NOW,
        1,
        90,
    ),
    _s(
        "autopay_retry_days",
        "servicing",
        "Autopay attempts",
        "day_list",
        [0, 1, 3],
        "Days after the due date to try the customer's saved card (0 = the due date).",
        FROM_NOW,
        0,
        30,
    ),
    # Merchant payouts
    _s(
        "payout_hold_hours",
        "payouts",
        "Payout hold after account change",
        "hours",
        48,
        "Payouts pause for this long after a merchant changes their payout account.",
        FROM_NOW,
        0,
        336,
    ),
    # Identity and fraud
    _s(
        "kyc_require_biometric",
        "identity",
        "Require the Smile ID selfie check",
        "boolean",
        True,
        "When Smile ID is set up, documents alone can't verify a customer.",
        FROM_NOW,
        ref="§9A",
    ),
    _s(
        "identity_max_attempts",
        "identity",
        "Identity check attempts",
        "integer",
        3,
        "Selfie checks a customer may try before they must contact support.",
        FROM_NOW,
        1,
        10,
    ),
    _s(
        "smileid_ghana_id_type",
        "identity",
        "Smile ID code for the Ghana Card",
        "text",
        "GHANA_CARD",
        "Confirm with Smile ID once Ghana is enabled on the account.",
        FROM_NOW,
    ),
    _s(
        "fraud_max_customer_accounts_per_device",
        "identity",
        "Customer accounts per device",
        "integer",
        2,
        "More customer accounts than this on one device raises a review flag.",
        FROM_NOW,
        1,
        20,
        "§9D",
    ),
    _s(
        "fraud_repeat_orders_review",
        "identity",
        "Repeat orders to flag",
        "integer",
        3,
        "Orders from one merchant to one customer within the window below that raise a review flag.",
        FROM_NOW,
        2,
        50,
        "§9E",
    ),
    _s(
        "fraud_repeat_order_window_days",
        "identity",
        "Repeat order window",
        "days",
        30,
        "Window for counting repeat orders.",
        FROM_NOW,
        1,
        365,
    ),
    _s(
        "fraud_quick_dispute_days",
        "identity",
        "Quick dispute window",
        "days",
        3,
        "A dispute within this many days of delivery raises a review flag on the merchant.",
        FROM_NOW,
        1,
        60,
    ),
    # Margin model
    _s(
        "gateway_fee_percentage",
        "margin",
        "Gateway cost",
        "percent",
        2,
        "% of product price.",
        MARGIN,
        0,
        10,
        "§7",
    ),
    _s(
        "expected_credit_loss_percentage",
        "margin",
        "Expected credit loss",
        "percent",
        5,
        "% of the financed balance.",
        MARGIN,
        0,
        50,
        "§7",
    ),
    _s(
        "collections_cost_percentage",
        "margin",
        "Collections cost",
        "percent",
        3,
        "% of the financed balance.",
        MARGIN,
        0,
        50,
        "§7",
    ),
    _s(
        "fraud_loss_reserve_percentage",
        "margin",
        "Fraud loss reserve",
        "percent",
        0,
        "% of the financed balance. Not set yet.",
        MARGIN,
        0,
        50,
        "§13 #14",
    ),
    _s(
        "cost_of_capital_annual_percentage",
        "margin",
        "Cost of capital (annual)",
        "percent",
        0,
        "Annual rate on the capital that funds the financed balance. Not set yet.",
        MARGIN,
        0,
        100,
        "§13 #14",
    ),
]
BY_KEY = {s["key"]: s for s in SETTINGS}


class SettingsError(ValueError):
    def __init__(self, errors):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _storage_type(kind):
    return {"boolean": "boolean", "text": "string", "day_list": "json"}.get(
        kind, "number"
    )


def current(key):
    spec = BY_KEY[key]
    value = SystemSetting.get_value(key, spec["default"])
    if spec["kind"] in ("integer", "days", "hours"):
        return int(value)
    if spec["kind"] in ("percent", "money"):
        return float(value)
    return value


def parse(spec, raw):
    """Validate one value for its setting. Returns the clean value or raises ValueError."""
    kind = spec["kind"]
    if kind == "boolean":
        if isinstance(raw, bool):
            return raw
        raise ValueError("must be on or off")
    if kind == "text":
        value = str(raw or "").strip()
        if (
            not value
            or len(value) > 60
            or not all(c.isalnum() or c in "_-" for c in value)
        ):
            raise ValueError("use 1-60 letters, digits, - or _")
        return value
    if kind == "day_list":
        if not isinstance(raw, list) or not raw or len(raw) > 6:
            raise ValueError("give 1 to 6 days")
        try:
            days = sorted({int(d) for d in raw})
        except (TypeError, ValueError):
            raise ValueError("days must be whole numbers")
        if days[0] < spec["min"] or days[-1] > spec["max"]:
            raise ValueError(f"days must be between {spec['min']} and {spec['max']}")
        return days
    if isinstance(raw, bool):
        raise ValueError("must be a number")
    try:
        number = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise ValueError("must be a number")
    if not number.is_finite():
        raise ValueError("must be a number")
    if kind in ("integer", "days", "hours"):
        if number != number.to_integral_value():
            raise ValueError("must be a whole number")
        number = int(number)
    else:
        if number.as_tuple().exponent < -2:
            raise ValueError("use at most 2 decimal places")
        number = float(number)
    if spec["min"] is not None and number < spec["min"]:
        raise ValueError(f"must be at least {spec['min']}")
    if spec["max"] is not None and number > spec["max"]:
        raise ValueError(f"must be at most {spec['max']}")
    return number


def _stored(spec, value):
    if spec["kind"] == "day_list":
        return json.dumps(value)
    if spec["kind"] == "boolean":
        return "true" if value else "false"
    return str(value)


def view():
    rows = {
        s.setting_key: s
        for s in SystemSetting.query.filter(
            SystemSetting.setting_key.in_(list(BY_KEY))
        ).all()
    }
    groups = []
    for gkey, glabel in GROUPS:
        items = []
        for spec in (s for s in SETTINGS if s["group"] == gkey):
            row = rows.get(spec["key"])
            value = current(spec["key"])
            items.append(
                {
                    **spec,
                    "value": value,
                    "is_default": value == spec["default"],
                    "updated_at": row.updated_at.isoformat()
                    if row and row.updated_at
                    else None,
                    "updated_by": (row.updater.full_name or row.updater.phone)
                    if row and row.updater
                    else None,
                }
            )
        groups.append({"key": gkey, "label": glabel, "settings": items})
    return {"groups": groups}


def apply_changes(changes, reason, admin):
    """Validate everything first, then save all changes in one commit with an audit row each."""
    if not isinstance(changes, dict) or not changes:
        raise SettingsError({"changes": "nothing to save"})
    reason = (reason or "").strip()
    errors, clean = {}, {}
    if len(reason) < 5:
        errors["reason"] = "add a reason (at least 5 characters)"
    for key, raw in changes.items():
        spec = BY_KEY.get(key)
        if not spec:
            errors[key] = "this setting can't be changed here"
            continue
        try:
            clean[key] = parse(spec, raw)
        except ValueError as e:
            errors[key] = str(e)
    # Cross-field rules
    lf = clean.get("late_fee_percentage", current("late_fee_percentage"))
    cap = clean.get("late_fee_cap_percentage", current("late_fee_cap_percentage"))
    if not errors and lf > cap:
        errors[
            "late_fee_cap_percentage"
        ] = "the cap can't be lower than the first late fee"
    # Merchant fee tiers must stay in order: premium <= standard <= high-risk (§6.1)
    premium = clean.get(
        "merchant_fee_premium_percentage", current("merchant_fee_premium_percentage")
    )
    standard = clean.get("merchant_fee_percentage", current("merchant_fee_percentage"))
    high = clean.get(
        "merchant_fee_high_risk_percentage",
        current("merchant_fee_high_risk_percentage"),
    )
    if not errors and not (premium <= standard <= high):
        errors["merchant_fee_percentage"] = (
            "merchant fee tiers must be in order: premium ≤ standard ≤ high-risk "
            f"(now {premium}%, {standard}%, {high}%)"
        )
    if errors:
        raise SettingsError(errors)

    saved = []
    now = datetime.utcnow()
    for key, value in clean.items():
        spec = BY_KEY[key]
        old = current(key)
        if old == value:
            continue
        row = SystemSetting.query.filter_by(setting_key=key).first()
        if not row:
            row = SystemSetting(setting_key=key, description=spec["help"][:500])
            db.session.add(row)
        row.setting_value = _stored(spec, value)
        row.setting_type = _storage_type(spec["kind"])
        row.updated_by = admin.id
        row.updated_at = now
        db.session.add(
            SettingChange(
                setting_key=key,
                old_value=json.dumps(old),
                new_value=json.dumps(value),
                changed_by=admin.id,
                reason=reason[:500],
                created_at=now,
            )
        )
        saved.append({"key": key, "label": spec["label"], "old": old, "new": value})
    db.session.commit()
    return saved


def history(limit=100):
    rows = (
        SettingChange.query.order_by(
            SettingChange.created_at.desc(), SettingChange.id.desc()
        )
        .limit(limit)
        .all()
    )
    return [
        {
            "key": r.setting_key,
            "label": BY_KEY.get(r.setting_key, {}).get("label", r.setting_key),
            "old": json.loads(r.old_value) if r.old_value else None,
            "new": json.loads(r.new_value),
            "reason": r.reason,
            "by": (r.changer.full_name or r.changer.phone) if r.changer else None,
            "at": r.created_at.isoformat(),
        }
        for r in rows
    ]
