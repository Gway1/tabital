"""The underwriting rules on their own (no database)."""
from datetime import date
from decimal import Decimal

from app.services.risk import Facts, assess, rules

D = Decimal
TODAY = date(2026, 9, 26)


def good(**overrides):
    base = dict(
        today=TODAY,
        date_of_birth=date(1995, 5, 1),
        ghana_card_number="GHA-123456789-0",
        kyc_verified=True,
        monthly_salary=D("5000"),
        salary_verified=True,
        employment_start=date(2023, 1, 1),
        salary_paid_to_bank=True,
        momo_number="0241234567",
    )
    base.update(overrides)
    return Facts(**base)


def test_new_customer_is_medium_with_salary_limit():
    d = assess(good())
    assert d.eligible and d.tier == "medium"
    assert d.pay_in_4_dp_rate == D("0.40")
    assert d.credit_limit == D("5000.00")  # 1.0 x salary (§8.2)
    assert d.available_limit == D("5000.00")


def test_every_failed_rule_is_reported():
    d = assess(
        good(
            date_of_birth=date(2010, 1, 1),
            monthly_salary=D("1500"),
            employment_start=date(2026, 6, 1),
            salary_paid_to_bank=False,
            momo_number="",
            ghana_card_number=None,
            kyc_verified=False,
        )
    )
    assert not d.eligible and d.credit_limit == 0
    text = " | ".join(d.reasons)
    for expected in [
        "18",
        "Ghana Card",
        "KYC",
        "GHS 2,000",
        "6 months",
        "bank",
        "Mobile Money",
    ]:
        assert expected in text


def test_unverified_salary_blocks_credit():
    d = assess(good(salary_verified=False))
    assert not d.eligible and "not been verified" in " ".join(d.reasons)


def test_high_tier_gets_50_percent_down_and_half_salary_limit():
    # High tier via the employment rule, with the threshold raised for this test
    r = rules({"high_tier_if_employment_months_below": 12})
    d = assess(good(employment_start=date(2026, 1, 1)), r)  # 8 months
    assert d.eligible and d.tier == "high"
    assert d.pay_in_4_dp_rate == D("0.50")
    assert d.credit_limit == D("2500.00")  # 0.5 x salary


def test_clean_history_promotes_to_low_and_grows_limit():
    d = assess(good(completed_clean_plans=2, consecutive_clean_plans=2))
    assert d.tier == "low"
    assert d.limit_multiplier == D("1.7")  # 1.5 + 2 x 0.1
    assert d.credit_limit == D("8500.00")


def test_growth_is_capped():
    d = assess(good(completed_clean_plans=20, consecutive_clean_plans=20))
    assert d.limit_multiplier == D("2.0")  # 1.5 + cap 0.5


def test_outstanding_balance_reduces_available_limit():
    d = assess(good(outstanding_balance=D("3200")))
    assert d.credit_limit == D("5000.00") and d.available_limit == D("1800.00")


def test_overdue_payment_freezes_new_credit():
    d = assess(good(currently_overdue=True, max_days_past_due=5))
    assert d.eligible and d.tier == "high" and d.available_limit == 0


def test_seriously_overdue_or_defaulted_is_declined():
    assert not assess(good(max_days_past_due=31, currently_overdue=True)).eligible
    assert not assess(good(defaulted_plans=1)).eligible


def test_late_payment_history_demotes_to_high():
    d = assess(good(late_payments_total=2, completed_clean_plans=3))
    assert d.tier == "high"


def test_employment_threshold_is_6_months():
    """Founder decision 2026-09-26: 6 months (not 16). Under 6 is declined; 6+ is medium."""
    assert not assess(good(employment_start=date(2026, 4, 1))).eligible  # ~5 months
    assert assess(good(employment_start=date(2026, 3, 1))).tier == "medium"  # ~7 months
    assert (
        assess(good(employment_start=date(2025, 7, 1))).tier == "medium"
    )  # ~14 months


def test_each_late_payment_takes_half_a_salary_off_the_limit():
    """Founder decision 2026-09-26: -0.5 x salary per instalment paid late."""
    # 2 clean plans and 1 late payment: not low tier (needs zero late payments), so medium:
    # 1.0 + 0.2 growth - 0.5 = 0.7 x salary
    d = assess(
        good(completed_clean_plans=2, consecutive_clean_plans=0, late_payments_total=1)
    )
    assert d.tier == "medium"  # low tier needs no late payments at all
    assert d.limit_multiplier == D("0.7")  # medium 1.0 + 0.2 growth - 0.5
    assert d.credit_limit == D("3500.00")
    assert any("reduced by 0.5" in r for r in d.reasons)


def test_high_tier_keeps_a_minimum_limit_of_a_quarter_salary():
    """Founder option B: late payments can't push the limit below 0.25 x salary."""
    d = assess(good(late_payments_total=2))  # high tier 0.5 - 1.0 -> floored at 0.25
    assert d.eligible and d.tier == "high"
    assert d.limit_multiplier == D("0.25") and d.credit_limit == D("1250.00")
    assert d.available_limit == D("1250.00")
    assert d.pay_in_4_dp_rate == D("0.50")
    assert assess(good(late_payments_total=6)).credit_limit == D("1250.00")


def test_overdue_still_freezes_the_minimum_limit():
    d = assess(good(late_payments_total=2, currently_overdue=True, max_days_past_due=3))
    assert d.credit_limit == D("1250.00") and d.available_limit == D("0.00")


def test_extended_plan_eligibility_after_three_clean_plans():
    assert not assess(
        good(completed_clean_plans=2, consecutive_clean_plans=2)
    ).extended_plans_eligible
    assert assess(
        good(completed_clean_plans=3, consecutive_clean_plans=3)
    ).extended_plans_eligible


def test_rules_can_be_overridden():
    r = rules(
        {"min_monthly_salary": 3000, "tiers": {"medium": {"limit_multiplier": "0.8"}}}
    )
    assert not assess(good(monthly_salary=D("2500")), r).eligible
    assert assess(good(), r).credit_limit == D("4000.00")
