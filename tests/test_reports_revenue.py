"""Admin revenue reports come from the ledger (§5.3 example: GHS 4,000 phone, 10% MDR = GHS 400)."""
from datetime import datetime

from tests.test_ledger import app_ctx, approved_plan  # noqa: F401  (fixture)


def test_revenue_report_uses_the_ledger(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    now = datetime.utcnow()
    res = client.get(
        f"/admin/reports/revenue?period=monthly&year={now.year}", headers=admin_h
    ).get_json()
    month = res["revenue_data"][now.month - 1]
    assert (
        month["merchant_fees"] == 400.0
        and month["revenue"] == 400.0
        and month["transactions"] == 1
    )
    assert res["total_revenue"] == 400.0

    daily = client.get(
        "/admin/reports/revenue?period=daily", headers=admin_h
    ).get_json()
    assert (
        daily["revenue_data"][-1]["revenue"] == 400.0
        and len(daily["revenue_data"]) == 30
    )

    # A late fee adds to revenue; waiving it takes it off again
    from app.models.instalment_payment import InstalmentPayment
    from app.services import ledger
    from app.extensions import db

    p = InstalmentPayment.query.filter_by(plan_id=plan.id, status="pending").first()
    ledger.late_fee_charged(plan, p, 80)
    db.session.commit()
    month = client.get("/admin/reports/revenue", headers=admin_h).get_json()[
        "revenue_data"
    ][now.month - 1]
    assert month["late_fees"] == 80.0 and month["revenue"] == 480.0
    ledger.late_fee_waived(plan, p, 80)
    db.session.commit()
    assert (
        client.get("/admin/reports/revenue", headers=admin_h).get_json()[
            "revenue_data"
        ][now.month - 1]["revenue"]
        == 400.0
    )


def test_kpis_use_the_ledger(app_ctx):  # noqa: F811
    client, admin_h, _ = approved_plan(app_ctx)
    body = client.get("/admin/reports/kpis", headers=admin_h).get_json()

    def find(obj, key):
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for v in obj.values():
                found = find(v, key)
                if found is not None:
                    return found
        return None

    assert find(body, "ytd_revenue") == 400.0 and find(body, "monthly_revenue") == 400.0
