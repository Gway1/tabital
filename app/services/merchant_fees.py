"""Merchant fee tiers (CLAUDE.md §6.1; founder, 2026-09-27).

Management puts each merchant on a tier, optionally when approving them (KYB) or later with a
reason. The tier sets the merchant fee (MDR) on **new** orders; the fee is fixed on each contract when
the order is approved (ledger MERCHANT_FEE, §5.4), so changing a tier never changes an existing plan.

    premium    merchant_fee_premium_percentage    (default 8%)
    standard   merchant_fee_percentage            (default 10%, also for merchants with no tier set)
    high_risk  merchant_fee_high_risk_percentage  (default 12%)
"""
import json
from decimal import Decimal

from ..extensions import db

PREMIUM, STANDARD, HIGH_RISK = "premium", "standard", "high_risk"
TIERS = {
    PREMIUM: ("merchant_fee_premium_percentage", 8, "Premium / low-risk"),
    STANDARD: ("merchant_fee_percentage", 10, "Standard"),
    HIGH_RISK: ("merchant_fee_high_risk_percentage", 12, "High-risk category"),
}


class FeeTierError(ValueError):
    pass


def tier_of(merchant) -> str:
    tier = getattr(merchant, "merchant_fee_tier", None)
    return tier if tier in TIERS else STANDARD


def rate_percent(tier: str) -> Decimal:
    from ..models.system_settings import SystemSetting

    key, default, _ = TIERS[tier if tier in TIERS else STANDARD]
    return Decimal(str(SystemSetting.get_value(key, default)))


def rate_for(merchant) -> Decimal:
    """The merchant's current fee as a fraction (0.10), for new orders."""
    return rate_percent(tier_of(merchant)) / 100


def describe(merchant) -> dict:
    tier = tier_of(merchant)
    return {
        "fee_tier": tier,
        "fee_tier_label": TIERS[tier][2],
        "fee_percentage": float(rate_percent(tier)),
        "fee_tier_set": getattr(merchant, "merchant_fee_tier", None) in TIERS,
    }


def options() -> list:
    return [
        {"value": t, "label": label, "fee_percentage": float(rate_percent(t))}
        for t, (_k, _d, label) in TIERS.items()
    ]


def transaction_split(transaction, merchant=None):
    """(fee, payout) in GHS for a sale Transaction, for display. Approval stores the payout computed
    with the merchant's tier, so the fee is amount - payout; older rows without a payout use the
    merchant's current tier."""
    amount = Decimal(str(transaction.amount or 0))
    if transaction.payout_amount is not None:
        payout = Decimal(str(transaction.payout_amount))
    else:
        from ..models.user import User

        merchant = merchant or User.query.get(transaction.merchant_id)
        payout = (amount * (1 - rate_for(merchant))).quantize(Decimal("0.01"))
    return float(amount - payout), float(payout)


def transaction_fields(transaction, merchant=None) -> dict:
    """commission_rate (%), commission_amount and payout_amount of a sale, for API responses."""
    fee, payout = transaction_split(transaction, merchant)
    amount = float(transaction.amount or 0)
    return {
        "commission_rate": round(fee / amount * 100, 2) if amount else 0.0,
        "commission_amount": fee,
        "payout_amount": payout,
        # No payout stored on this (older) sale: worked out from the merchant's current tier
        "payout_estimated": transaction.payout_amount is None,
    }


def apply_at_approval(merchant, data, admin):
    """Optional tier chosen while approving a merchant: data['fee_tier'] (+ optional 'fee_tier_reason').
    No tier given leaves the merchant as they are (standard unless set before). Doesn't commit.
    """
    tier = (data or {}).get("fee_tier")
    if not tier:
        return False
    return set_tier(
        merchant,
        tier,
        admin,
        (data or {}).get("fee_tier_reason") or "Chosen when approving the merchant",
    )


def set_tier(merchant, tier, admin, reason):
    """Change a merchant's tier with an audit record (setting_changes). Doesn't commit."""
    from ..models.system_settings import SettingChange

    if tier not in TIERS:
        raise FeeTierError("fee_tier must be premium, standard or high_risk")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise FeeTierError(
            "Say why (at least 5 characters). Fee tier changes are logged."
        )
    old = getattr(merchant, "merchant_fee_tier", None)
    if old == tier:
        return False
    merchant.merchant_fee_tier = tier
    db.session.add(
        SettingChange(
            setting_key=f"merchant_fee_tier:{merchant.id}",
            old_value=json.dumps(old),
            new_value=json.dumps(tier),
            changed_by=admin.id,
            reason=reason[:500],
        )
    )
    return True
