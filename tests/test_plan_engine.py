from datetime import datetime
from decimal import Decimal

import pytest

from app.services.plan_engine import (
    PlanError,
    add_months,
    build_plan,
    down_payment_rate_for,
    quote,
)

D = Decimal


def settings(overrides=None):
    values = {
        "down_payment_percentage": 40,
        "down_payment_percentage_short_plans": 50,
        "service_fee": 0,
        "merchant_fee_percentage": 10,
        "delivery_fee": 50,
    }
    values.update(overrides or {})
    return lambda key, default=None: values.get(key, default)


def test_reference_example_pay_in_4():
    """CLAUDE.md §5.3: GHS 4,000 smartphone, Pay in 4, 40% down."""
    plan = build_plan(D("4000"), 4, D("0.40"), start=datetime(2026, 1, 15))
    assert plan["down_payment"] == D("1600.00")
    assert plan["financed_balance"] == D("2400.00")
    assert plan["installments"] == [D("800.00")] * 3
    assert plan["total_payable"] == D("4000.00")
    assert plan["merchant_fee"] == D("400.00")
    assert plan["merchant_settlement"] == D("3600.00")
    assert [p["amount"] for p in plan["schedule"]] == [
        D("1600.00"),
        D("800.00"),
        D("800.00"),
        D("800.00"),
    ]


def test_delivery_fee_is_paid_with_down_payment_and_not_financed():
    """§13.1 D3."""
    plan = build_plan(D("4000"), 4, D("0.40"), delivery_fee=D("50"))
    assert plan["financed_balance"] == D("2400.00")
    assert plan["installments"] == [D("800.00")] * 3
    assert plan["due_now"] == D("1650.00")
    assert plan["schedule"][0]["delivery_fee"] == D("50.00")
    assert plan["total_payable"] == D("4050.00")
    # MDR is charged on P only (§5.2)
    assert plan["merchant_fee"] == D("400.00")


def test_uneven_balance_puts_remainder_on_last_installment():
    plan = build_plan(D("999.99"), 4, D("0.40"))
    assert plan["down_payment"] == D("400.00")
    assert plan["financed_balance"] == D("599.99")
    assert plan["installments"] == [D("200.00"), D("200.00"), D("199.99")]
    assert sum(p["amount"] for p in plan["schedule"]) == plan["total_payable"]


def test_pay_in_3_and_pay_in_2_use_50_percent_down():
    get = settings()
    p3 = quote("4000", 1, 3, get)
    assert p3["down_payment"] == D("2000.00")
    assert p3["installments"] == [D("1000.00"), D("1000.00")]
    p2 = quote("4000", 1, 2, get)
    assert p2["installments"] == [D("2000.00")]


def test_full_payment_is_all_due_now():
    plan = quote("4000", 1, 1, settings({"delivery_fee": 0}))
    assert plan["installments"] == []
    assert plan["due_now"] == D("4000.00")
    assert len(plan["schedule"]) == 1


def test_quantity_multiplies_price():
    plan = quote("2000", 2, 4, settings({"delivery_fee": 0}))
    assert plan["price"] == D("4000.00")
    assert plan["down_payment"] == D("1600.00")


def test_due_dates_same_calendar_day_clamped_to_month_end():
    assert add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert add_months(datetime(2026, 1, 15), 3) == datetime(2026, 4, 15)
    assert add_months(datetime(2026, 11, 30), 2) == datetime(2027, 1, 30)
    plan = build_plan(D("4000"), 4, D("0.40"), start=datetime(2026, 1, 31))
    assert [p["due_date"].date().isoformat() for p in plan["schedule"]] == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
        "2026-04-30",
    ]


def test_unsupported_plan_rejected():
    with pytest.raises(PlanError):
        down_payment_rate_for(6, settings())


def test_rates_are_read_from_settings():
    plan = quote(
        "4000",
        1,
        4,
        settings({"down_payment_percentage": 25, "merchant_fee_percentage": 8}),
    )
    assert plan["down_payment"] == D("1000.00")
    assert plan["merchant_fee"] == D("320.00")


def test_invalid_price_rejected():
    with pytest.raises(PlanError):
        build_plan(D("0"), 4, D("0.40"))
