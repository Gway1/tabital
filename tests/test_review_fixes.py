"""Go-live review fixes: nothing changes money, disputes or accounts outside the proper services."""
from datetime import datetime

from app.extensions import db
from app.models.instalment import InstalmentPlan
from app.models.transaction import Transaction
from app.models.user import User

from tests.test_ledger import (
    app_ctx,
    approved_plan,
    make_user,
    token,
)  # noqa: F401  (fixture)


def _open_dispute(client, plan):
    customer = User.query.filter_by(role="customer").one()
    res = client.post(
        "/customer/disputes",
        headers=token(client, customer.phone),
        json={
            "plan_id": plan.id,
            "reason": "defective",
            "description": "Screen cracked on arrival",
        },
    )
    assert res.status_code in (200, 201), res.get_json()
    from app.models.dispute import Dispute

    return Dispute.query.order_by(Dispute.id.desc()).first()


def test_merchant_can_respond_but_only_tabital_resolves(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    dispute = _open_dispute(client, plan)
    assert InstalmentPlan.query.get(plan.id).paused_at is not None
    merchant = User.query.filter_by(role="merchant").one()
    mh = token(client, merchant.phone)

    assert (
        client.put(
            f"/merchant/disputes/{dispute.id}", headers=mh, json={"status": "resolved"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/merchant/disputes/{dispute.id}/reject",
            headers=mh,
            json={"reason": "Customer dropped it; photos attached"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/merchant/disputes/{dispute.id}/accept", headers=mh, json={}
        ).status_code
        == 200
    )
    from app.models.dispute import Dispute

    d = Dispute.query.get(dispute.id)
    assert d.status == "under_review" and "photos attached" in d.merchant_notes
    assert (
        InstalmentPlan.query.get(plan.id).paused_at is not None
    )  # still paused: not resolved
    assert Transaction.query.get(plan.transaction_id).status != "refunded"

    # The admin resolution still works and unpauses the plan
    res = client.put(
        f"/admin/disputes/{dispute.id}/resolve",
        headers=admin_h,
        json={"outcome": "merchant_won", "notes": "Damage after delivery"},
    )
    assert res.status_code == 200, res.get_json()
    assert InstalmentPlan.query.get(plan.id).paused_at is None


def test_transactions_cant_change_money_state_or_be_deleted(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    t = Transaction.query.get(plan.transaction_id)
    merchant = User.query.filter_by(role="merchant").one()
    mh = token(client, merchant.phone)
    before = (t.status, t.payment_status, t.delivery_status)

    assert (
        client.put(
            f"/transactions/{t.id}/status",
            headers=admin_h,
            json={"status": "completed", "payment_status": "paid"},
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/merchant/transactions/{t.id}/status",
            headers=mh,
            json={"delivery_status": "delivered"},
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/merchant/transactions/{t.id}",
            headers=mh,
            json={"payment_reference": "X"},
        ).status_code
        == 400
    )
    assert (
        client.post(
            f"/merchant/transactions/{t.id}/refund", headers=mh, json={}
        ).status_code
        == 410
    )
    assert client.delete(f"/transactions/{t.id}", headers=admin_h).status_code == 410
    t = Transaction.query.get(t.id)
    assert (t.status, t.payment_status, t.delivery_status) == before
    # Fulfilment details still work
    assert (
        client.put(
            f"/merchant/transactions/{t.id}/status",
            headers=mh,
            json={"tracking_number": "TRK-1"},
        ).status_code
        == 200
    )


def test_accounts_are_suspended_not_deleted_and_edits_cant_skip_approval(
    app_ctx,
):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    merchant = User.query.filter_by(role="merchant").one()

    assert (
        client.put(
            f"/admin/customers/{customer.id}",
            headers=admin_h,
            json={"status": "approved"},
        ).status_code
        == 400
    )
    assert (
        client.put(
            f"/admin/merchants/{merchant.id}",
            headers=admin_h,
            json={"verified": True, "total_sales": 999999},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            "/admin/customers/bulk-update",
            headers=admin_h,
            json={"ids": [customer.id], "data": {"status": "approved"}},
        ).status_code
        == 400
    )

    # Still owes money: can't be deactivated (they must be able to sign in and pay)
    res = client.delete(f"/admin/customers/{customer.id}", headers=admin_h)
    assert res.status_code == 409 and "still owes" in res.get_json()["error"]
    assert User.query.get(customer.id).status == "approved"
    assert (
        client.put(
            f"/admin/customers/{customer.id}/status",
            headers=admin_h,
            json={"status": "suspended", "reason": "Testing the guard"},
        ).status_code
        == 409
    )
    # Restricted: can still sign in (to pay) but not buy
    assert (
        client.put(
            f"/admin/customers/{customer.id}/status",
            headers=admin_h,
            json={"status": "restricted", "reason": "Missed payments"},
        ).status_code
        == 200
    )
    ch = token(client, customer.phone)
    from app.models.product import Product

    res = client.post(
        "/customer/purchase",
        headers=ch,
        json={
            "accept_terms": True,
            "product_id": Product.query.first().id,
            "number_of_installments": 1,
            "delivery_address": "Osu",
        },
    )
    assert res.status_code == 403 and "restricted" in res.get_json()["error"]
    # Someone who owes nothing can be deactivated; records are kept
    other = make_user("customer", "0200000503")
    res = client.delete(f"/admin/customers/{other.id}", headers=admin_h)
    assert res.status_code == 200 and User.query.get(other.id).status == "suspended"
    assert InstalmentPlan.query.get(plan.id) is not None
    res = client.delete(f"/admin/merchants/{merchant.id}", headers=admin_h)
    assert res.status_code == 200 and User.query.get(merchant.id).status == "suspended"


def test_changing_a_ghana_card_reruns_duplicate_checks(app_ctx):  # noqa: F811
    client, admin_h, _ = approved_plan(app_ctx)
    first = User.query.filter_by(role="customer").one()
    other = make_user("customer", "0200000501", kyc_status="verified")
    assert (
        client.put(
            f"/admin/customers/{other.id}",
            headers=admin_h,
            json={"national_id": first.national_id},
        ).status_code
        == 200
    )
    from app.models.identity import FraudSignal

    assert FraudSignal.query.filter_by(code="duplicate_ghana_card").count() >= 1


def test_reports_are_management_only_and_skip_cancelled_sales(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    staff = make_user("admin", "0200000502", admin_level="operations")
    assert (
        client.get(
            "/admin/reports/revenue", headers=token(client, staff.phone)
        ).status_code
        == 403
    )

    month = datetime.utcnow().month - 1
    assert (
        client.get("/admin/reports/revenue", headers=admin_h).get_json()[
            "revenue_data"
        ][month]["merchant_fees"]
        == 400.0
    )
    InstalmentPlan.query.get(
        plan.id
    ).status = "cancelled"  # e.g. dispute won by the customer
    db.session.commit()
    assert (
        client.get("/admin/reports/revenue", headers=admin_h).get_json()[
            "revenue_data"
        ][month]["merchant_fees"]
        == 0.0
    )
