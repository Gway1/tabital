"""Management Access: operations admins read (masked) and remind; everything else needs management."""
from app.extensions import db
from app.models.instalment_payment import InstalmentPayment
from app.models.purchase_order import PurchaseOrder
from app.models.user import User
from app.services import access

from tests.test_ledger import (
    app_ctx,
    approved_plan,
    make_user,
    token,
)  # noqa: F401  (fixture)


def _ops(client):
    staff = make_user("admin", "0200000199", admin_level="operations")
    return staff, token(client, staff.phone)


def test_rules():
    assert access.needs_management("PUT", "/admin/orders/1/approve")
    assert access.needs_management("POST", "/admin/identity-checks/3/decide")
    assert access.needs_management("POST", "/admin/some-new-endpoint")  # safe default
    assert access.needs_management("GET", "/admin/pii/access-log")
    assert access.needs_management("GET", "/admin/orders/export")
    assert access.needs_management("GET", "/admin/business-settings")
    assert not access.needs_management("GET", "/admin/orders")
    assert not access.needs_management("POST", "/admin/collection/7/reminder")
    assert not access.needs_management("POST", "/admin/customers/7/note")


def test_operations_admin_can_read_and_remind_but_not_approve_or_reveal(
    app_ctx,
):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    _, ops_h = _ops(client)
    customer = User.query.filter_by(role="customer").one()

    assert client.get("/admin/orders", headers=ops_h).status_code == 200
    payment = InstalmentPayment.query.filter_by(
        plan_id=plan.id, status="pending"
    ).first()
    assert (
        client.post(
            f"/admin/collection/{payment.id}/reminder",
            headers=ops_h,
            json={"reminder_type": "in_app"},
        ).status_code
        == 200
    )

    blocked = [
        client.post(
            "/admin/pii/reveal",
            headers=ops_h,
            json={
                "user_id": customer.id,
                "field": "phone",
                "reason": "calling about arrears",
            },
        ),
        client.put(
            f"/admin/collection/{payment.id}/mark-received",
            headers=ops_h,
            json={"amount_received": 800, "payment_reference": "MM123"},
        ),
        client.get("/admin/business-settings", headers=ops_h),
        client.get("/admin/economics/summary", headers=ops_h),
        client.put(
            f"/admin/customers/{customer.id}", headers=ops_h, json={"city": "Kumasi"}
        ),
    ]
    for res in blocked:
        assert (
            res.status_code == 403 and res.get_json()["code"] == "management_required"
        )
    assert InstalmentPayment.query.get(payment.id).status == "pending"
    assert User.query.get(customer.id).city != "Kumasi"

    # The same actions work for management
    assert (
        client.post(
            "/admin/pii/reveal",
            headers=admin_h,
            json={
                "user_id": customer.id,
                "field": "phone",
                "reason": "calling about arrears",
            },
        ).status_code
        == 200
    )


def test_order_approval_needs_management(app_ctx):  # noqa: F811
    client, _, _ = approved_plan(app_ctx)
    _, ops_h = _ops(client)
    order = PurchaseOrder.query.first()
    assert (
        client.put(
            f"/admin/orders/{order.id}/reject",
            headers=ops_h,
            json={"reason": "Testing the access rule"},
        ).status_code
        == 403
    )


def test_team_access_changes_are_logged_and_keep_one_manager(app_ctx):  # noqa: F811
    client, admin_h, _ = approved_plan(app_ctx)
    staff, ops_h = _ops(client)
    me = client.get("/admin/get_current_user", headers=ops_h).get_json()
    assert me["admin_level"] == "operations"
    assert client.get("/admin/team", headers=ops_h).status_code == 403

    url = f"/admin/team/{staff.id}"
    assert (
        client.put(url, headers=admin_h, json={"admin_level": "management"}).status_code
        == 400
    )  # no reason
    res = client.put(
        url,
        headers=admin_h,
        json={"admin_level": "management", "reason": "Head of operations"},
    )
    assert (
        res.status_code == 200 and User.query.get(staff.id).admin_level == "management"
    )
    team = client.get("/admin/team", headers=admin_h).get_json()
    assert (
        team["history"][0]["to"] == "management"
        and team["history"][0]["reason"] == "Head of operations"
    )

    # Demote everyone else, then the last manager can't be demoted
    first = User.query.filter(User.role == "admin", User.id != staff.id).one()
    assert (
        client.put(
            f"/admin/team/{first.id}",
            headers=admin_h,
            json={"admin_level": "operations", "reason": "Moving to operations"},
        ).status_code
        == 200
    )
    staff_h = token(client, staff.phone)
    assert (
        client.put(
            url,
            headers=staff_h,
            json={"admin_level": "operations", "reason": "Trying to leave none"},
        ).status_code
        == 400
    )
    db.session.expire_all()
    assert User.query.get(staff.id).admin_level == "management"
