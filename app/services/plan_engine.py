"""Server-side instalment plan calculation (CLAUDE.md §5).

This is the only place plan amounts are computed. Clients never supply amounts;
they ask for a quote and the server stores what this module returns.
"""
import calendar
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")

# Plans available in Phase 1. N = total number of payments, including the down payment.
SUPPORTED_PLANS = (1, 2, 3, 4)


class PlanError(ValueError):
    pass


def money(value) -> Decimal:
    """Convert any numeric input to a 2-dp Decimal without going through float maths."""
    return Decimal(str(value)).quantize(CENT, ROUND_HALF_UP)


def rate(value) -> Decimal:
    return Decimal(str(value))


def add_months(start: datetime, months: int) -> datetime:
    """Same calendar day each month (§13 #9), clamped to the month's last day."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)


def build_plan(
    price,
    n_payments,
    dp_rate,
    service_fee="0",
    mdr="0.10",
    delivery_fee="0",
    start=None,
):
    """Build a plan per §5.2 / §5.5.

    price        P, product price x quantity
    n_payments   N, total payments including the down payment (Payment 1)
    dp_rate      DPR, e.g. "0.40"
    delivery_fee charged with Payment 1 only, never financed (§13.1 D3)
    """
    price = money(price)
    service_fee = money(service_fee)
    delivery_fee = money(delivery_fee)
    dp_rate = rate(dp_rate)
    mdr = rate(mdr)

    if price <= 0:
        raise PlanError("Product price must be greater than zero")
    if n_payments < 1:
        raise PlanError("A plan needs at least one payment")
    if not (Decimal("0") < dp_rate <= Decimal("1")):
        raise PlanError("Down payment rate must be between 0 and 100%")

    start = start or datetime.utcnow()
    deferred = n_payments - 1

    down_payment = (price * dp_rate).quantize(CENT, ROUND_HALF_UP)
    financed = price - down_payment + service_fee

    if deferred == 0:
        # Full payment: everything is due at checkout
        down_payment += financed
        financed = Decimal("0.00")
        installments = []
    else:
        inst = (financed / deferred).quantize(CENT, ROUND_HALF_UP)
        # Remainder goes on the final instalment so the schedule sums exactly to FB (§5.4)
        installments = [inst] * (deferred - 1) + [financed - inst * (deferred - 1)]

    merchant_fee = (price * mdr).quantize(CENT, ROUND_HALF_UP)
    total_payable = price + service_fee + delivery_fee

    schedule = [
        {
            "installment_number": 1,
            "type": "down_payment",
            "amount": down_payment + delivery_fee,
            "principal": down_payment,
            "delivery_fee": delivery_fee,
            "due_date": start,
            "status": "due_now",
            "description": f"Down payment ({(dp_rate * 100).normalize()}%)"
            + (" + delivery fee" if delivery_fee else ""),
        }
    ]
    for i, amount in enumerate(installments, start=1):
        schedule.append(
            {
                "installment_number": i + 1,
                "type": "installment",
                "amount": amount,
                "principal": amount,
                "delivery_fee": Decimal("0.00"),
                "due_date": add_months(start, i),
                "status": "pending",
                "description": f"Instalment {i} of {deferred}",
            }
        )

    assert (
        sum(p["amount"] for p in schedule) == total_payable
    ), "schedule must sum to TotalPayable"

    return {
        "price": price,
        "n_payments": n_payments,
        "deferred_payments": deferred,
        "down_payment_rate": dp_rate,
        "down_payment": down_payment,
        "delivery_fee": delivery_fee,
        "due_now": down_payment + delivery_fee,
        "service_fee": service_fee,
        "financed_balance": financed,
        "installment_amount": installments[0] if installments else Decimal("0.00"),
        "installments": installments,
        "total_payable": total_payable,
        "merchant_fee_rate": mdr,
        "merchant_fee": merchant_fee,
        "merchant_settlement": price - merchant_fee,
        "schedule": schedule,
    }


def down_payment_rate_for(n_payments, settings_get, pay_in_4_dp_rate=None):
    """DPR per plan (§4, §13 #5). Rates are read from system settings, in percent.

    pay_in_4_dp_rate is the customer's risk-tier rate for Pay in 4 (§8.2, set in the risk
    rules). When given it applies; otherwise the configured default does.
    """
    if n_payments not in SUPPORTED_PLANS:
        raise PlanError(
            f"Only {', '.join(map(str, SUPPORTED_PLANS))}-payment plans are available"
        )
    if n_payments == 1:
        return Decimal("1")
    if n_payments == 4:
        if pay_in_4_dp_rate is not None:
            return rate(pay_in_4_dp_rate)
        return rate(settings_get("down_payment_percentage", 40)) / 100
    return rate(settings_get("down_payment_percentage_short_plans", 50)) / 100


def quote(
    price,
    quantity,
    n_payments,
    settings_get,
    start=None,
    pay_in_4_dp_rate=None,
    in_store=False,
    mdr=None,
):
    """Build a plan using the current system settings (and the customer's tier, if given).

    settings_get(key, default) is normally SystemSetting.get_value. in_store: collected at
    the merchant (payment link / QR sale), so there's no delivery fee. mdr: the merchant's fee as a
    fraction (services/merchant_fees.rate_for); the standard tier when not given. The MDR only
    affects the merchant side, never what the customer pays.
    """
    quantity = int(quantity)
    if quantity < 1:
        raise PlanError("Quantity must be at least 1")
    return build_plan(
        price=money(price) * quantity,
        n_payments=n_payments,
        dp_rate=down_payment_rate_for(n_payments, settings_get, pay_in_4_dp_rate),
        service_fee=settings_get("service_fee", 0),
        mdr=rate(mdr)
        if mdr is not None
        else rate(settings_get("merchant_fee_percentage", 10)) / 100,
        delivery_fee=0 if in_store else settings_get("delivery_fee", 50),
        start=start,
    )


def schedule_to_json(schedule):
    """JSON-safe schedule (amounts as floats for the current API contract)."""
    return [
        {
            **item,
            "amount": float(item["amount"]),
            "principal": float(item["principal"]),
            "delivery_fee": float(item["delivery_fee"]),
            "due_date": item["due_date"].strftime("%Y-%m-%d"),
        }
        for item in schedule
    ]
