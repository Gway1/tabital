"""Rules-based underwriting (CLAUDE.md §2, §8, §9, §13 #17, §13.1 D7/D9).

`assess()` is a pure function: facts about a customer in, a decision out. The same inputs and
rules always give the same result, and every decision is stored with its inputs and rules
version (see RiskAssessment). All thresholds live in RULES and can be overridden through the
`risk_rules` system setting without a code change.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

CENT = Decimal("0.01")

RULES = {
    "version": "2026-09-26.8",
    # Eligibility (hard declines)
    "min_age": 18,
    "min_monthly_salary": 2000,  # GHS, §2
    "min_employment_months": 6,  # website criteria, §13 #17
    "max_dpd_allowed": 30,  # anything later than this blocks new credit (§8.4)
    # Tiers (§8.2). New customers start at medium: no repayment history yet.
    "tiers": {
        "low": {"pay_in_4_dp_rate": "0.40", "limit_multiplier": "1.5"},
        "medium": {
            "pay_in_4_dp_rate": "0.40",
            "limit_multiplier": "1.0",
        },  # §13 #4 interim 40%
        "high": {"pay_in_4_dp_rate": "0.50", "limit_multiplier": "0.5"},
    },
    # Promotion to low: at least this many plans completed with no late payment
    "low_tier_min_clean_plans": 1,
    # Demotion to high (founder, 2026-09-26: under 6 months employed, or 2+ late payments).
    # Note: 6 months is also min_employment_months, so the employment trigger only matters
    # if the eligibility minimum is ever lowered.
    "high_tier_if_employment_months_below": 6,
    "high_tier_if_late_payments_at_least": 2,
    # Dynamic limit (D9, founder 2026-09-26): each plan completed with no late payment adds
    # 0.1 x salary (capped at +0.5); each instalment paid late takes 0.5 x salary off.
    # The multiplier never goes below min_multiplier, so eligible high-tier customers can
    # still buy on 50% down (founder option B, 2026-09-26). A payment currently overdue
    # still freezes new credit regardless.
    "growth_per_clean_plan": "0.1",
    "growth_cap": "0.5",
    "reduction_per_late_payment": "0.5",
    "min_multiplier": "0.25",
    # A late instalment paid within this many days of its due date, together with its late
    # fee, is "cured" (founder, 2026-09-26, all tiers): it never lowers the limit or tier.
    # The only consequence: that plan doesn't count toward any limit increase (growth,
    # promotion to low tier, or the extended-plan streak).
    "late_payment_cure_days": 7,
    # Late payments that weren't cured stop lowering the limit/tier this many months after
    # their due date (founder, 2026-09-26). They stay in the history, and their plans still
    # don't earn limit growth.
    "late_payment_expiry_months": 12,
    # Extended 6/12-month plans unlock after this many consecutive on-time purchases (D7).
    # They stay off until their terms are set (D12); this only reports eligibility.
    "extended_plan_min_consecutive_clean_plans": 3,
    # A paid deferment (§4) is never a late payment, so it never lowers the limit or tier.
    # Like a cured late payment, a plan with a deferment doesn't earn limit growth or
    # promotion unless this is switched on (default pending founder confirmation).
    "deferred_plans_count_as_clean": False,
}


def _deep_merge(base, overrides):
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def rules(overrides=None):
    """Default RULES with any overrides applied (nested keys can be overridden one at a time)."""
    merged = deepcopy(RULES)
    if overrides:
        _deep_merge(merged, overrides)
    return merged


@dataclass
class Facts:
    """Everything the decision uses. Built from the database by `facts_for()`."""

    today: date
    date_of_birth: Optional[date]
    ghana_card_number: Optional[str]
    kyc_verified: bool
    monthly_salary: Optional[Decimal]
    salary_verified: bool
    employment_start: Optional[date]
    salary_paid_to_bank: bool
    momo_number: Optional[str]
    outstanding_balance: Decimal = Decimal("0.00")
    max_days_past_due: int = 0  # worst currently unpaid instalment
    currently_overdue: bool = False
    late_payments_total: int = 0  # late instalments that count (not cured, not expired)
    late_payments_cured: int = (
        0  # late but paid within the cure window with the fee (no penalty)
    )
    late_payments_expired: int = (
        0  # not cured, but older than the expiry window (no penalty)
    )
    completed_clean_plans: int = 0  # completed with no late payment that counts
    consecutive_clean_plans: int = (
        0  # most recent completed plans in a row with no late payment
    )
    defaulted_plans: int = 0


@dataclass
class Decision:
    eligible: bool
    tier: Optional[str]
    pay_in_4_dp_rate: Optional[Decimal]
    limit_multiplier: Decimal
    credit_limit: Decimal
    available_limit: Decimal
    extended_plans_eligible: bool
    reasons: List[str] = field(default_factory=list)
    rules_version: str = RULES["version"]


def _months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def _age(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def assess(f: Facts, r=None) -> Decision:
    r = r or rules()
    reasons: List[str] = []

    # ---- eligibility: every failing rule is reported, not just the first ----
    if f.date_of_birth is None:
        reasons.append("Date of birth is missing")
    elif _age(f.date_of_birth, f.today) < r["min_age"]:
        reasons.append(f"Customer must be at least {r['min_age']} years old")
    if not (f.ghana_card_number or "").strip():
        reasons.append("Ghana Card number is missing")
    if not f.kyc_verified:
        reasons.append("KYC verification is not complete")
    if f.monthly_salary is None:
        reasons.append("Monthly salary is missing")
    elif f.monthly_salary < Decimal(str(r["min_monthly_salary"])):
        reasons.append(f"Monthly salary is below GHS {r['min_monthly_salary']:,}")
    if f.monthly_salary is not None and not f.salary_verified:
        reasons.append("Monthly salary has not been verified")
    employment_months = (
        _months_between(f.employment_start, f.today) if f.employment_start else None
    )
    if employment_months is None:
        reasons.append("Employment start date is missing")
    elif employment_months < r["min_employment_months"]:
        reasons.append(
            f"Employment must be at least {r['min_employment_months']} months"
        )
    if not f.salary_paid_to_bank:
        reasons.append("Salary must be paid into a bank account")
    if not (f.momo_number or "").strip():
        reasons.append("A Mobile Money number must be linked")
    if f.defaulted_plans > 0:
        reasons.append("Customer has a defaulted plan")
    if f.max_days_past_due > r["max_dpd_allowed"]:
        reasons.append(
            f"An instalment is more than {r['max_dpd_allowed']} days overdue"
        )

    if reasons:
        return Decision(
            False,
            None,
            None,
            Decimal("0"),
            Decimal("0.00"),
            Decimal("0.00"),
            False,
            reasons,
            r["version"],
        )

    # ---- tier ----
    if (
        employment_months < r["high_tier_if_employment_months_below"]
        or f.late_payments_total >= r["high_tier_if_late_payments_at_least"]
        or f.currently_overdue
    ):
        tier = "high"
        reasons.append(
            "High tier: short employment, late payment history or a payment currently overdue"
        )
    elif (
        f.completed_clean_plans >= r["low_tier_min_clean_plans"]
        and f.late_payments_total == 0
    ):
        # Low tier: plans paid on time and no late payments that count. Cured late payments
        # don't demote, but their plans aren't "clean", so they don't help anyone get promoted.
        tier = "low"
        reasons.append("Low tier: plans completed on time")
    else:
        tier = "medium"
        reasons.append("Medium tier: not enough repayment history yet")

    terms = r["tiers"][tier]
    multiplier = Decimal(str(terms["limit_multiplier"]))

    # ---- dynamic limit (D9): grow with clean history (never for high tier), shrink with late payments ----
    if tier != "high" and f.completed_clean_plans:
        growth = min(
            Decimal(str(r["growth_per_clean_plan"])) * f.completed_clean_plans,
            Decimal(str(r["growth_cap"])),
        )
        if growth:
            multiplier += growth
            reasons.append(
                f"Limit increased by {growth} x salary for {f.completed_clean_plans} plan(s) paid on time"
            )
    if f.late_payments_total:
        reduction = (
            Decimal(str(r["reduction_per_late_payment"])) * f.late_payments_total
        )
        floor = Decimal(str(r["min_multiplier"]))
        new_multiplier = max(multiplier - reduction, floor)
        if new_multiplier != multiplier:
            reasons.append(
                f"Limit reduced by {multiplier - new_multiplier} x salary for "
                f"{f.late_payments_total} late payment(s)"
            )
        multiplier = new_multiplier

    if f.late_payments_expired:
        reasons.append(
            f"{f.late_payments_expired} late payment(s) older than "
            f"{r.get('late_payment_expiry_months', 12)} months no longer reduce the limit"
        )
    if f.late_payments_cured:
        reasons.append(
            f"{f.late_payments_cured} late payment(s) cleared within the grace window: "
            "limit kept, but those plans don't count toward a limit increase"
        )

    credit_limit = (f.monthly_salary * multiplier).quantize(CENT, ROUND_HALF_UP)
    available = max(credit_limit - f.outstanding_balance, Decimal("0.00")).quantize(
        CENT
    )
    if f.currently_overdue:
        available = Decimal("0.00")
        reasons.append("No new credit while a payment is overdue")

    return Decision(
        eligible=True,
        tier=tier,
        pay_in_4_dp_rate=Decimal(str(terms["pay_in_4_dp_rate"])),
        limit_multiplier=multiplier,
        credit_limit=credit_limit,
        available_limit=available,
        extended_plans_eligible=(
            f.consecutive_clean_plans >= r["extended_plan_min_consecutive_clean_plans"]
            and not f.currently_overdue
        ),
        reasons=reasons,
        rules_version=r["version"],
    )


# ----------------------------------------------------------------------------
# Database side: gather facts, apply overrides, store the decision
# ----------------------------------------------------------------------------


def parse_date(value) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    from datetime import datetime

    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def current_rules():
    from ..models.system_settings import SystemSetting

    return rules(SystemSetting.get_value("risk_rules", None))


def facts_for(user, today=None) -> Facts:
    """Build Facts for a customer from the database (repayment history comes from their plans)."""
    from datetime import datetime
    from ..models.instalment import InstalmentPlan
    from ..models.instalment_payment import InstalmentPayment
    from . import ledger

    today = today or datetime.utcnow().date()
    r = current_rules()
    cure_days = int(r.get("late_payment_cure_days", 7))
    expiry_months = int(r.get("late_payment_expiry_months", 12))
    plans = InstalmentPlan.query.filter_by(customer_id=user.id).all()
    plan_ids = [p.id for p in plans]
    payments = (
        InstalmentPayment.query.filter(InstalmentPayment.plan_id.in_(plan_ids)).all()
        if plan_ids
        else []
    )

    # Late instalments that count against the customer, per plan. A late instalment is
    # cured (doesn't count) if paid within `cure_days` of its due date with its late fee.
    # An unpaid one only counts once it's past the cure window.
    late_by_plan = {}  # late (not cured) per plan: these plans are never "clean"
    cured_by_plan = {}
    cured = 0
    counting = 0  # late payments that currently lower the limit/tier
    expired = 0
    # A waived fee isn't a cleared late payment (D14): instalments with any waiver never count as cured
    from ..extensions import db
    from ..models.ledger import LedgerEntry

    pay_ids = [p.id for p in payments if p.id is not None]
    waived_ids = (
        {
            pid
            for (pid,) in db.session.query(LedgerEntry.payment_id)
            .filter(
                LedgerEntry.entry_type == LedgerEntry.LATE_FEE_WAIVED,
                LedgerEntry.payment_id.in_(pay_ids),
            )
            .distinct()
        }
        if pay_ids
        else set()
    )
    for p in payments:
        if p.late_fee_applied_date is None or not p.due_date:
            continue
        due = p.due_date.date()
        if p.status == "paid" and p.paid_date:
            # Cured: paid within the window, and a late fee was charged and paid (not waived)
            if (
                (p.paid_date.date() - due).days <= cure_days
                and p.late_fee_paid
                and p.late_fee
                and p.id not in waived_ids
            ):
                cured += 1
                cured_by_plan[p.plan_id] = cured_by_plan.get(p.plan_id, 0) + 1
                continue
        elif (today - due).days <= cure_days:
            continue  # still inside the cure window
        late_by_plan[p.plan_id] = late_by_plan.get(p.plan_id, 0) + 1
        if _months_between(due, today) >= expiry_months:
            expired += 1
        else:
            counting += 1

    # Unpaid instalments past their due date (awaiting-verification ones get the benefit of the
    # doubt, and plans paused by an open dispute don't count as overdue)
    paused_plan_ids = {p.id for p in plans if p.paused_at is not None}
    max_dpd = 0
    for p in payments:
        if p.plan_id in paused_plan_ids:
            continue
        if (
            p.status in ("pending", "overdue")
            and p.due_date
            and p.due_date.date() < today
        ):
            max_dpd = max(max_dpd, (today - p.due_date.date()).days)

    completed = sorted(
        [p for p in plans if p.status == "completed"],
        key=lambda p: p.completed_at or p.created_at,
        reverse=True,
    )
    # "Clean" = completed with no late payment at all. Plans with only cured late payments
    # aren't penalised, but they don't earn limit growth, low-tier promotion or streak credit.
    from ..models.deferment import Deferment

    deferred_plan_ids = (
        set()
        if r.get("deferred_plans_count_as_clean")
        else {
            d.plan_id
            for d in Deferment.query.filter(
                Deferment.plan_id.in_(plan_ids), Deferment.status == Deferment.APPLIED
            ).all()
        }
        if plan_ids
        else set()
    )

    def is_clean(plan):
        return (
            not late_by_plan.get(plan.id)
            and not cured_by_plan.get(plan.id)
            and plan.id not in deferred_plan_ids
        )

    clean = [p for p in completed if is_clean(p)]
    consecutive = 0
    for p in completed:
        if not is_clean(p):
            break
        consecutive += 1

    active = [p for p in plans if p.status == "active"]
    outstanding = (
        sum(
            (ledger.plan_balances(active)[p.id]["outstanding"] for p in active),
            Decimal("0.00"),
        )
        if active
        else Decimal("0.00")
    )

    return Facts(
        today=today,
        date_of_birth=parse_date(user.dob),
        ghana_card_number=user.national_id,
        kyc_verified=user.kyc_status == "verified",
        monthly_salary=Decimal(str(user.monthly_salary))
        if user.monthly_salary is not None
        else None,
        salary_verified=bool(user.salary_verified),
        employment_start=parse_date(user.employment_start_date),
        salary_paid_to_bank=bool(user.salary_paid_to_bank),
        momo_number=user.momo_number,
        outstanding_balance=outstanding,
        max_days_past_due=max_dpd,
        currently_overdue=max_dpd > 0,
        late_payments_total=counting,
        late_payments_cured=cured,
        late_payments_expired=expired,
        completed_clean_plans=len(clean),
        consecutive_clean_plans=consecutive,
        defaulted_plans=len([p for p in plans if p.status == "defaulted"]),
    )


def _facts_json(f: Facts):
    out = {}
    for key, value in f.__dict__.items():
        out[key] = (
            value.isoformat()
            if isinstance(value, date)
            else (str(value) if isinstance(value, Decimal) else value)
        )
    return out


def decide(user, today=None):
    """Assess a customer and apply any admin limit override, without storing anything.

    Used for display (limits, quotes). Returns (Decision, Facts).
    """
    facts = facts_for(user, today=today)
    decision = assess(facts, current_rules())
    if decision.eligible and user.credit_limit_override is not None:
        override = Decimal(str(user.credit_limit_override)).quantize(CENT)
        decision.credit_limit = override
        decision.available_limit = (
            Decimal("0.00")
            if facts.currently_overdue
            else max(override - facts.outstanding_balance, Decimal("0.00")).quantize(
                CENT
            )
        )
        decision.reasons.append(
            f"Credit limit set by admin override: GHS {override:,.2f}"
        )
    return decision, facts


def decision_view(decision: Decision):
    """JSON-safe summary of a decision for API responses."""
    return {
        "eligible": decision.eligible,
        "tier": decision.tier,
        "credit_limit": float(decision.credit_limit),
        "available_limit": float(decision.available_limit),
        "pay_in_4_down_payment_percent": float(decision.pay_in_4_dp_rate * 100)
        if decision.pay_in_4_dp_rate
        else None,
        "extended_plans_eligible": decision.extended_plans_eligible,
        "reasons": decision.reasons,
        "rules_version": decision.rules_version,
    }


def evaluate(user, source, created_by=None, note=None, today=None):
    """Assess a customer, store the decision, and update the user's tier and limit.

    Doesn't commit. Returns (Decision, RiskAssessment).
    """
    import json
    from datetime import datetime
    from ..extensions import db
    from ..models.risk_assessment import RiskAssessment

    decision, facts = decide(user, today=today)

    row = RiskAssessment(
        user_id=user.id,
        source=source,
        eligible=decision.eligible,
        tier=decision.tier,
        pay_in_4_dp_rate=decision.pay_in_4_dp_rate,
        limit_multiplier=decision.limit_multiplier,
        credit_limit=decision.credit_limit,
        outstanding=facts.outstanding_balance,
        available_limit=decision.available_limit,
        extended_plans_eligible=decision.extended_plans_eligible,
        reasons=json.dumps(decision.reasons),
        inputs=json.dumps(_facts_json(facts)),
        rules_version=decision.rules_version,
        note=(note or None) and note[:255],
        created_by=created_by.id if created_by is not None else None,
    )
    db.session.add(row)
    db.session.flush()

    user.risk_tier = decision.tier
    user.credit_limit = decision.credit_limit
    user.limit_updated_at = datetime.utcnow()
    return decision, row
