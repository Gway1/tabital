"""Phase 7: unit economics (§7), portfolio health (§8.4), cohorts and liquidity (§12)."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db
from app.models.instalment_payment import InstalmentPayment
from app.models.user import User
from app.services import economics

from tests.test_ledger import approved_plan, token

D = Decimal


@pytest.fixture()
def app_ctx():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def defaults():
    return {k: D(str(v[0])) for k, v in economics.RATES.items()}


# ---------------------------------------------------------------- the margin model (§7)


def test_worked_example_gives_128(app_ctx):
    m = economics.unit_margin(D("4000"), D("2400"), D("0.10"), 3, defaults())
    lines = {l["key"]: l["amount"] for l in m["lines"]}
    assert m["revenue"] == D("400.00")
    assert lines["gateway_fee_percentage"] == D("80.00")  # 2% x price
    assert lines["expected_credit_loss_percentage"] == D("120.00")  # 5% x financed
    assert lines["collections_cost_percentage"] == D("72.00")  # 3% x financed
    assert lines["fraud_loss_reserve_percentage"] == D("0.00")
    assert lines["cost_of_capital_annual_percentage"] == D("0.00")
    assert m["net"] == D("128.00") and m["margin_pct_of_price"] == D("3.20")
    assert len(economics.rate_warnings(defaults())) == 2  # fraud + capital not set


def test_fraud_reserve_and_cost_of_capital_reduce_margin(app_ctx):
    r = {
        **defaults(),
        "fraud_loss_reserve_percentage": D("1"),
        "cost_of_capital_annual_percentage": D("24"),
    }
    m = economics.unit_margin(D("4000"), D("2400"), D("0.10"), 3, r)
    lines = {l["key"]: l["amount"] for l in m["lines"]}
    assert lines["fraud_loss_reserve_percentage"] == D("24.00")
    assert lines["cost_of_capital_annual_percentage"] == D(
        "72.00"
    )  # 2400 x 24% x 3/12 / 2
    assert m["net"] == D("32.00")
    assert economics.rate_warnings(r) == []


def test_scenario_endpoint(app_ctx):
    client, admin_h, _ = approved_plan(app_ctx)
    res = client.get(
        "/admin/economics/scenario?price=4000&n=4", headers=admin_h
    ).get_json()
    assert res["down_payment"] == 1600 and res["financed_balance"] == 2400
    assert res["installments"] == [800, 800, 800] and res["net"] == 128
    res = client.get(
        "/admin/economics/scenario?price=4000&n=4&mdr_percentage=8", headers=admin_h
    ).get_json()
    assert res["revenue"] == 320 and res["net"] == 48  # premium merchant 8% MDR (§6.1)
    assert (
        client.get("/admin/economics/scenario?price=abc", headers=admin_h).status_code
        == 400
    )


# ---------------------------------------------------------------- actual portfolio


def test_summary_from_the_ledger(app_ctx):
    client, admin_h, plan = approved_plan(app_ctx)
    today = datetime.utcnow().date()
    res = client.get(
        f"/admin/economics/summary?from={today.replace(day=1)}&to={today}",
        headers=admin_h,
    ).get_json()
    t = res["totals"]
    assert t["plans"] == 1 and t["gmv"] == 4000 and t["financed"] == 2400
    assert t["merchant_fee_revenue"] == 400 and t["modelled_net"] == 128
    assert t["modelled_margin_pct_of_gmv"] == 3.2
    assert res["by_month"][0]["month"] == today.strftime("%Y-%m")

    # A late fee is actual revenue; a charge-off is an actual loss
    p2 = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    p2.due_date = datetime.utcnow() - timedelta(days=3)
    db.session.commit()
    p2.apply_late_fee()
    plan.status, plan.charged_off_at = "defaulted", datetime.utcnow()
    db.session.commit()
    t = client.get(
        f"/admin/economics/summary?from={today.replace(day=1)}&to={today}",
        headers=admin_h,
    ).get_json()["totals"]
    assert t["late_fee_revenue"] == 80
    assert (
        t["actual_credit_losses"] == 2480 and t["plans_charged_off"] == 1
    )  # 2,400 + 80 late fee
    assert t["net_with_actuals"] == 128 + 120 - 2480 + 80


def test_portfolio_buckets_par_and_liquidity(app_ctx):
    client, admin_h, plan = approved_plan(app_ctx)
    p2 = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    p2.due_date = datetime.utcnow() + timedelta(days=5)
    db.session.commit()
    res = client.get("/admin/economics/portfolio", headers=admin_h).get_json()
    assert (
        res["outstanding"] == 2400 and res["buckets"]["current"]["outstanding"] == 2400
    )
    assert res["par_30"]["pct"] == 0
    liq = res["liquidity"]
    assert liq["owed_to_merchants"] == 3600  # §5.3 settlement not yet paid out
    assert liq["customer_payments_due_next_30_days"] == 800
    assert liq["funding_gap_next_30_days"] == 2800
    plan.dpd_bucket = "dpd_31_60"
    db.session.commit()
    res = client.get("/admin/economics/portfolio", headers=admin_h).get_json()
    assert res["par_30"]["pct"] == 100 and res["par_60"]["pct"] == 0


def test_cohorts_measure_repayment_quality(app_ctx):
    client, admin_h, plan = approved_plan(app_ctx)
    p2, p3 = (
        InstalmentPayment.query.filter_by(plan_id=plan.id)
        .filter(InstalmentPayment.installment_number.in_([2, 3]))
        .order_by(InstalmentPayment.installment_number)
        .all()
    )
    p2.due_date = datetime.utcnow() - timedelta(
        days=40
    )  # paid on time (before it was due)
    p2.status, p2.paid_date = "paid", datetime.utcnow() - timedelta(days=41)
    p3.due_date = datetime.utcnow() - timedelta(days=35)  # unpaid, 35 days late
    db.session.commit()
    c = client.get("/admin/economics/cohorts", headers=admin_h).get_json()["cohorts"][0]
    assert c["plans"] == 1 and c["instalments_due"] == 2
    assert c["on_time_rate"] == 50 and c["ever_30_plus_rate"] == 100
    assert c["loss_rate"] == 0 and c["beats_assumption"] is True
    plan.status = "defaulted"
    db.session.commit()
    c = client.get("/admin/economics/cohorts", headers=admin_h).get_json()["cohorts"][0]
    assert c["loss_rate"] > 5 and c["beats_assumption"] is False


def test_csv_and_admin_only(app_ctx):
    client, admin_h, _ = approved_plan(app_ctx)
    res = client.get("/admin/economics/export", headers=admin_h)
    assert res.status_code == 200 and res.mimetype == "text/csv"
    body = res.get_data(as_text=True)
    assert "Modelled net" in body and "128.00" in body and "hasn't been set" in body
    customer = User.query.filter_by(role="customer").one()
    h = token(client, customer.phone)
    for url in (
        "/admin/economics/summary",
        "/admin/economics/portfolio",
        "/admin/economics/cohorts",
        "/admin/economics/scenario",
        "/admin/economics/export",
    ):
        assert client.get(url, headers=h).status_code == 403
