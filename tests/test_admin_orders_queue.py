"""Admin order queue: counts cover the whole queue, search works, the customer id comes back for reveals."""
from app.models.purchase_order import PurchaseOrder

from tests.test_ledger import app_ctx, approved_plan  # noqa: F401  (fixture)


def test_counts_search_and_customer_id(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    order = PurchaseOrder.query.first()

    res = client.get("/admin/orders?limit=1", headers=admin_h).get_json()
    assert (
        res["counts"].get(order.status)
        == PurchaseOrder.query.filter_by(status=order.status).count()
    )
    assert res["orders"][0]["customer_user_id"] == order.customer_id

    hit = client.get(
        f"/admin/orders?search={order.order_id}", headers=admin_h
    ).get_json()
    assert [o["id"] for o in hit["orders"]] == [order.id]
    miss = client.get(
        "/admin/orders?search=no-such-order-xyz", headers=admin_h
    ).get_json()
    assert miss["orders"] == [] and miss["counts"] == {}
    # Counts ignore the status tab, so every tab shows real numbers
    tab = client.get("/admin/orders?status=rejected", headers=admin_h).get_json()
    assert tab["counts"] == res["counts"]
