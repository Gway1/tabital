"""Legacy admin endpoints that changed money or status without the ledger are turned off (410)."""
from app.models.instalment import InstalmentPlan
from app.models.user import User

from tests.test_ledger import app_ctx, approved_plan  # noqa: F401  (fixture)


def test_retired_endpoints_refuse_and_change_nothing(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    merchant = User.query.filter_by(role="merchant").one()
    before_status, before_rate = plan.status, merchant.commission_rate

    calls = [
        client.put(
            f"/admin/instalments/{plan.id}/status",
            headers=admin_h,
            json={"status": "completed"},
        ),
        client.put(
            "/admin/transactions/1/status",
            headers=admin_h,
            json={"status": "completed"},
        ),
        client.put(
            "/admin/transactions/1/delivery",
            headers=admin_h,
            json={"delivery_status": "delivered"},
        ),
        client.post(
            "/admin/transactions/1/refund", headers=admin_h, json={"refund_amount": 100}
        ),
        client.put(
            f"/admin/merchants/{merchant.id}/reserve",
            headers=admin_h,
            json={"reserve_amount": 50},
        ),
        client.put(
            f"/admin/merchants/{merchant.id}/commission",
            headers=admin_h,
            json={"commission_rate": 1},
        ),
    ]
    for res in calls:
        assert res.status_code == 410, res.get_json()
        assert res.get_json()["error"]
    assert InstalmentPlan.query.get(plan.id).status == before_status
    assert User.query.get(merchant.id).commission_rate == before_rate
