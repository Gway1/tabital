"""Unit economics and portfolio reporting (Phase 7, CLAUDE.md §7, §8.4, §11, §12).

Gross margin per plan (§7 full model):
    merchant fee revenue (MDR x P)
  - gateway cost          (gateway % x P; the MDR includes it, §6.1)
  - expected credit loss  (ECL % x financed balance)
  - fraud loss reserve    (fraud % x financed balance)
  - collections cost      (collections % x financed balance)
  - cost of capital       (annual % x average financed balance x term)
All rates are settings (§13 #14). Fraud reserve and cost of capital default to 0 until the
founder sets them, which keeps the §7 worked example at GHS 128 (3.2%); the report says so.

Money is Decimal throughout; figures come from the ledger (MDR, late fees, deferment fees,
balances) and the instalment schedule (financed balance), never from UI numbers.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func

from ..extensions import db
from ..models.instalment import InstalmentPlan
from ..models.instalment_payment import InstalmentPayment
from ..models.ledger import LedgerEntry
from ..models.system_settings import SystemSetting
from . import ledger

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

# key: (default %, basis, label)
RATES = {
    "gateway_fee_percentage": (2, "price", "Gateway cost"),
    "expected_credit_loss_percentage": (5, "financed", "Expected credit loss"),
    "fraud_loss_reserve_percentage": (0, "financed", "Fraud loss reserve"),
    "collections_cost_percentage": (3, "financed", "Collections cost"),
    "cost_of_capital_annual_percentage": (0, "capital", "Cost of capital"),
}
NOT_SET_BY_DEFAULT = (
    "fraud_loss_reserve_percentage",
    "cost_of_capital_annual_percentage",
)


def q(x) -> Decimal:
    return Decimal(str(x)).quantize(CENT, ROUND_HALF_UP)


def rates(overrides=None):
    out = {
        k: Decimal(str(SystemSetting.get_value(k, default)))
        for k, (default, _, _) in RATES.items()
    }
    for k, v in (overrides or {}).items():
        if k in out and v is not None:
            out[k] = Decimal(str(v))
    return out


def rate_warnings(r):
    return [
        f"{RATES[k][2]} is 0% because it hasn't been set yet (§13 #14). Real margin will be lower."
        for k in NOT_SET_BY_DEFAULT
        if r[k] == 0
    ]


def unit_margin(price, financed, mdr, term_months, r):
    """Margin lines for one plan. price = P, financed = FB, mdr as a fraction (0.10)."""
    price, financed, mdr = (
        Decimal(str(price)),
        Decimal(str(financed)),
        Decimal(str(mdr)),
    )
    revenue = q(price * mdr)
    lines = []
    for key, (_, basis, label) in RATES.items():
        pct = r[key] / 100
        if basis == "price":
            amount = price * pct
            explain = f"{r[key].normalize()}% of product price"
        elif basis == "financed":
            amount = financed * pct
            explain = f"{r[key].normalize()}% of financed balance"
        else:
            # Balance repaid in equal monthly instalments: the average balance is about half
            amount = financed * pct * Decimal(term_months) / 12 / 2
            explain = f"{r[key].normalize()}% a year on the average balance over {term_months} month(s)"
        lines.append(
            {"key": key, "label": label, "basis": explain, "amount": q(amount)}
        )
    costs = sum((l["amount"] for l in lines), ZERO)
    net = revenue - costs
    return {
        "revenue": revenue,
        "lines": lines,
        "costs": costs,
        "net": net,
        "margin_pct_of_price": (net / price * 100).quantize(CENT, ROUND_HALF_UP)
        if price
        else ZERO,
    }


def scenario(price, n_payments, dp_rate, mdr, r):
    """The founder's what-if calculator: one product on one plan."""
    from .plan_engine import build_plan

    plan = build_plan(price, n_payments, dp_rate, mdr=mdr)
    m = unit_margin(
        plan["price"],
        plan["financed_balance"],
        plan["merchant_fee_rate"],
        plan["deferred_payments"],
        r,
    )
    return {
        "price": plan["price"],
        "n_payments": n_payments,
        "down_payment": plan["down_payment"],
        "financed_balance": plan["financed_balance"],
        "installments": plan["installments"],
        "merchant_fee": plan["merchant_fee"],
        "merchant_settlement": plan["merchant_settlement"],
        **m,
    }


# ------------------------------------------------------------------ per-plan facts


def _plan_facts(plans):
    """{plan_id: {price, merchant_fee, financed, term}} from the ledger and schedule."""
    if not plans:
        return {}
    ids = [p.id for p in plans]
    merchant = defaultdict(lambda: {"fee": 0, "payable": 0})
    for pid, etype, amount in (
        db.session.query(
            LedgerEntry.plan_id,
            LedgerEntry.entry_type,
            func.sum(LedgerEntry.amount_pesewas),
        )
        .filter(
            LedgerEntry.plan_id.in_(ids),
            LedgerEntry.entry_type.in_(
                [LedgerEntry.MERCHANT_FEE, LedgerEntry.MERCHANT_PAYABLE]
            ),
        )
        .group_by(LedgerEntry.plan_id, LedgerEntry.entry_type)
        .all()
    ):
        merchant[pid]["fee" if etype == LedgerEntry.MERCHANT_FEE else "payable"] = (
            amount or 0
        )
    financed = dict(
        db.session.query(InstalmentPayment.plan_id, func.sum(InstalmentPayment.amount))
        .filter(
            InstalmentPayment.plan_id.in_(ids), InstalmentPayment.installment_number > 1
        )
        .group_by(InstalmentPayment.plan_id)
        .all()
    )
    out = {}
    for p in plans:
        m = merchant.get(p.id)
        price = (
            ledger.to_cedis(m["fee"] + m["payable"]) if m else q(p.total_amount or 0)
        )
        fee = ledger.to_cedis(m["fee"]) if m else ZERO
        out[p.id] = {
            "price": price,
            "merchant_fee": fee,
            "financed": q(financed.get(p.id) or 0),
            "term": max(0, (p.number_of_installments or 1) - 1),
        }
    return out


def _sum_entries(entry_types, start, end, plan_ids=None):
    qy = db.session.query(
        func.coalesce(func.sum(LedgerEntry.amount_pesewas), 0)
    ).filter(
        LedgerEntry.entry_type.in_(entry_types),
        LedgerEntry.created_at >= start,
        LedgerEntry.created_at < end,
    )
    if plan_ids is not None:
        qy = qy.filter(LedgerEntry.plan_id.in_(plan_ids or [-1]))
    return ledger.to_cedis(qy.scalar())


def _month_key(dt):
    return dt.strftime("%Y-%m")


def _range(start: date, end: date):
    return datetime.combine(start, datetime.min.time()), datetime.combine(
        end + timedelta(days=1), datetime.min.time()
    )


# ------------------------------------------------------------------ revenue for reports


def revenue_between(start: datetime, end: datetime = None):
    """Revenue recognised in the ledger in [start, end): merchant fees (MDR) on plans opened, late fees
    net of waivers, and deferment fees. The one definition of revenue for the admin reports.

    Same basis as summary(): cancelled plans (a sale reversed, e.g. a dispute won by the customer and
    clawed back from the merchant) earn no merchant fee."""
    end = end or datetime.utcnow() + timedelta(days=1)
    merchant_fees = ledger.to_cedis(
        db.session.query(ledger._merchant_fee_expr())
        .join(InstalmentPlan, InstalmentPlan.id == LedgerEntry.plan_id)
        .filter(
            LedgerEntry.created_at >= start,
            LedgerEntry.created_at < end,
            InstalmentPlan.status != "cancelled",
        )
        .scalar()
    )
    late_fees = _sum_entries(
        [LedgerEntry.LATE_FEE_CHARGED, LedgerEntry.LATE_FEE_WAIVED], start, end
    )
    deferment_fees = _sum_entries([LedgerEntry.DEFERMENT_FEE], start, end)
    plans = InstalmentPlan.query.filter(
        InstalmentPlan.created_at >= start,
        InstalmentPlan.created_at < end,
        InstalmentPlan.status != "cancelled",
    ).count()
    return {
        "merchant_fees": merchant_fees,
        "late_fees": late_fees,
        "deferment_fees": deferment_fees,
        "total": merchant_fees + late_fees + deferment_fees,
        "plans": plans,
    }


# ------------------------------------------------------------------ period summary


def summary(start: date, end: date, r=None):
    """Unit economics for plans opened in [start, end], by month, with actual fees and losses."""
    r = r or rates()
    s, e = _range(start, end)
    plans = InstalmentPlan.query.filter(
        InstalmentPlan.created_at >= s,
        InstalmentPlan.created_at < e,
        InstalmentPlan.status != "cancelled",
    ).all()
    facts = _plan_facts(plans)
    months = defaultdict(
        lambda: {
            "plans": 0,
            "gmv": ZERO,
            "financed": ZERO,
            "revenue": ZERO,
            "costs": defaultdict(lambda: ZERO),
        }
    )
    for p in plans:
        f = facts[p.id]
        mdr = (f["merchant_fee"] / f["price"]) if f["price"] else ZERO
        m = unit_margin(f["price"], f["financed"], mdr, f["term"], r)
        row = months[_month_key(p.created_at)]
        row["plans"] += 1
        row["gmv"] += f["price"]
        row["financed"] += f["financed"]
        row["revenue"] += f["merchant_fee"]
        for line in m["lines"]:
            row["costs"][line["key"]] += line["amount"]

    def finish(row):
        costs = {k: row["costs"][k] for k in RATES}
        modelled_net = row["revenue"] - sum(costs.values(), ZERO)
        return {
            "plans": row["plans"],
            "gmv": row["gmv"],
            "financed": row["financed"],
            "average_ticket": q(row["gmv"] / row["plans"]) if row["plans"] else ZERO,
            "merchant_fee_revenue": row["revenue"],
            "costs": costs,
            "modelled_net": modelled_net,
            "modelled_margin_pct_of_gmv": q(modelled_net / row["gmv"] * 100)
            if row["gmv"]
            else ZERO,
        }

    by_month = [{"month": k, **finish(v)} for k, v in sorted(months.items())]
    total = {
        "plans": 0,
        "gmv": ZERO,
        "financed": ZERO,
        "revenue": ZERO,
        "costs": defaultdict(lambda: ZERO),
    }
    for v in months.values():
        total["plans"] += v["plans"]
        total["gmv"] += v["gmv"]
        total["financed"] += v["financed"]
        total["revenue"] += v["revenue"]
        for k in RATES:
            total["costs"][k] += v["costs"][k]
    totals = finish(total)

    # Actual customer fees recognised in the period (any plan)
    late_fees = _sum_entries(
        [LedgerEntry.LATE_FEE_CHARGED, LedgerEntry.LATE_FEE_WAIVED], s, e
    )
    deferment_fees = _sum_entries([LedgerEntry.DEFERMENT_FEE], s, e)
    # Actual credit losses: what was still owed on plans charged off in the period (§8.4 90+)
    charged_off = InstalmentPlan.query.filter(
        InstalmentPlan.charged_off_at >= s, InstalmentPlan.charged_off_at < e
    ).all()
    losses = (
        sum(
            (
                ledger.plan_balances(charged_off)[p.id]["outstanding"]
                for p in charged_off
            ),
            ZERO,
        )
        if charged_off
        else ZERO
    )
    totals.update(
        {
            "late_fee_revenue": late_fees,
            "deferment_fee_revenue": deferment_fees,
            "actual_credit_losses": losses,
            "plans_charged_off": len(charged_off),
            # Modelled margin with the ECL line swapped for losses actually booked, plus customer fees
            "net_with_actuals": totals["modelled_net"]
            + totals["costs"]["expected_credit_loss_percentage"]
            - losses
            + late_fees
            + deferment_fees,
        }
    )
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "rates": r,
        "warnings": rate_warnings(r),
        "totals": totals,
        "by_month": by_month,
    }


# ------------------------------------------------------------------ portfolio health now


def portfolio(today=None):
    """Outstanding book by §8.4 bucket, portfolio-at-risk, and the liquidity gap (§12)."""
    today = today or datetime.utcnow().date()
    book = InstalmentPlan.query.filter(
        InstalmentPlan.status.in_(["active", "defaulted"])
    ).all()
    balances = ledger.plan_balances(book)
    buckets = {
        b: {"plans": 0, "outstanding": ZERO}
        for b in ("current", "dpd_1_30", "dpd_31_60", "dpd_61_90", "dpd_90_plus")
    }
    for p in book:
        b = "dpd_90_plus" if p.status == "defaulted" else (p.dpd_bucket or "current")
        buckets.setdefault(b, {"plans": 0, "outstanding": ZERO})
        buckets[b]["plans"] += 1
        buckets[b]["outstanding"] += balances[p.id]["outstanding"]
    total = sum((v["outstanding"] for v in buckets.values()), ZERO)

    def par(*names):
        amount = sum((buckets[n]["outstanding"] for n in names), ZERO)
        return {
            "outstanding": amount,
            "pct": q(amount / total * 100) if total else ZERO,
        }

    # Liquidity: what we owe merchants vs what customers are due to pay us soon
    owed_to_merchants = ledger.to_cedis(
        db.session.query(func.coalesce(func.sum(LedgerEntry.amount_pesewas), 0))
        .filter(
            LedgerEntry.account == LedgerEntry.MERCHANT,
            LedgerEntry.entry_type.in_(
                [
                    LedgerEntry.MERCHANT_PAYABLE,
                    LedgerEntry.MERCHANT_SETTLED,
                    LedgerEntry.MERCHANT_CLAWBACK,
                ]
            ),
        )
        .scalar()
    )
    horizon = datetime.combine(today + timedelta(days=30), datetime.min.time())
    now = datetime.combine(today, datetime.min.time())
    upcoming = (
        InstalmentPayment.query.join(
            InstalmentPlan, InstalmentPlan.id == InstalmentPayment.plan_id
        )
        .filter(
            InstalmentPlan.status == "active",
            InstalmentPayment.status.in_(("pending", "overdue")),
            InstalmentPayment.due_date >= now,
            InstalmentPayment.due_date < horizon,
        )
        .all()
    )
    overdue = (
        InstalmentPayment.query.join(
            InstalmentPlan, InstalmentPlan.id == InstalmentPayment.plan_id
        )
        .filter(
            InstalmentPlan.status == "active",
            InstalmentPayment.status.in_(("pending", "overdue")),
            InstalmentPayment.due_date < now,
        )
        .all()
    )
    due_30 = sum((q(p.get_total_due()) for p in upcoming), ZERO)
    overdue_amount = sum((q(p.get_total_due()) for p in overdue), ZERO)
    return {
        "as_of": today.isoformat(),
        "plans": len(book),
        "outstanding": total,
        "buckets": buckets,
        "par_30": par("dpd_31_60", "dpd_61_90", "dpd_90_plus"),
        "par_60": par("dpd_61_90", "dpd_90_plus"),
        "par_90": par("dpd_90_plus"),
        "liquidity": {
            "owed_to_merchants": owed_to_merchants,
            "customer_payments_due_next_30_days": due_30,
            "customer_payments_overdue": overdue_amount,
            # Positive = cash Tabital must fund from its own reserve in the next 30 days (§11.3)
            "funding_gap_next_30_days": max(owed_to_merchants - due_30, ZERO),
        },
    }


# ------------------------------------------------------------------ cohorts (repayment quality)


def cohorts(today=None, r=None):
    """Repayment quality by the month plans were opened: the Year-1 mission (§12)."""
    today = today or datetime.utcnow().date()
    r = r or rates()
    plans = InstalmentPlan.query.filter(InstalmentPlan.status != "cancelled").all()
    facts = _plan_facts(plans)
    balances = ledger.plan_balances(plans)
    payments = defaultdict(list)
    if plans:
        for p in InstalmentPayment.query.filter(
            InstalmentPayment.plan_id.in_([x.id for x in plans]),
            InstalmentPayment.installment_number > 1,
        ).all():
            payments[p.plan_id].append(p)
    rows = defaultdict(
        lambda: {
            "plans": 0,
            "financed": ZERO,
            "due": 0,
            "on_time": 0,
            "late": 0,
            "ever_30": 0,
            "defaulted": 0,
            "defaulted_outstanding": ZERO,
            "completed": 0,
        }
    )
    for plan in plans:
        row = rows[_month_key(plan.created_at)]
        row["plans"] += 1
        row["financed"] += facts[plan.id]["financed"]
        worst = 0
        for p in payments[plan.id]:
            if not p.due_date or p.due_date.date() > today:
                continue
            row["due"] += 1
            if p.status == "paid" and p.paid_date:
                days_late = (p.paid_date.date() - p.due_date.date()).days
                row["on_time" if days_late <= 0 else "late"] += 1
            else:
                days_late = (today - p.due_date.date()).days
                row["late"] += 1 if days_late > 0 else 0
            worst = max(worst, days_late)
        row["ever_30"] += 1 if worst > 30 else 0
        if plan.status == "defaulted":
            row["defaulted"] += 1
            row["defaulted_outstanding"] += balances[plan.id]["outstanding"]
        if plan.status == "completed":
            row["completed"] += 1
    ecl = r["expected_credit_loss_percentage"]
    out = []
    for month, v in sorted(rows.items()):
        loss_rate = (
            q(v["defaulted_outstanding"] / v["financed"] * 100)
            if v["financed"]
            else ZERO
        )
        out.append(
            {
                "month": month,
                "plans": v["plans"],
                "financed": v["financed"],
                "completed": v["completed"],
                "instalments_due": v["due"],
                "on_time_rate": q(Decimal(v["on_time"]) / v["due"] * 100)
                if v["due"]
                else None,
                "ever_30_plus_rate": q(Decimal(v["ever_30"]) / v["plans"] * 100)
                if v["plans"]
                else ZERO,
                "defaulted": v["defaulted"],
                "loss_rate": loss_rate,
                "assumed_loss_rate": ecl,
                "beats_assumption": loss_rate <= ecl,
            }
        )
    return {"as_of": today.isoformat(), "assumed_loss_rate": ecl, "cohorts": out}
