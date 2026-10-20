"""Shared test data."""
from datetime import date, datetime
from decimal import Decimal


def eligible_customer(extra, phone):
    """Underwriting fields that pass every eligibility rule (fake data).

    Salary GHS 10,000 on medium tier gives a GHS 10,000 limit, enough for the test phones.
    Only applied to KYC-verified customers; explicit values in `extra` win.
    """
    if extra.get("kyc_status") != "verified":
        return extra
    defaults = {
        "dob": "1990-01-15",
        "national_id": f"GHA-{phone[-9:]}-1",
        "monthly_salary": Decimal("10000"),
        "salary_verified": True,
        "employment_start_date": date(2020, 1, 1),
        "salary_paid_to_bank": True,
        "momo_number": phone,
        "employment_verified_at": datetime(2026, 1, 5),  # §9B, Phase 6
    }
    return {**defaults, **extra}
