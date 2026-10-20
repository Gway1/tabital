"""Collections can't quietly move a due date, and reminder channels are only the connected ones."""
from app.models.instalment_payment import InstalmentPayment

from tests.test_ledger import app_ctx, approved_plan  # noqa: F401  (fixture)


def test_due_date_change_from_collections_is_turned_off(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    payment = InstalmentPayment.query.filter_by(
        plan_id=plan.id, status="pending"
    ).first()
    before = payment.due_date
    res = client.post(
        f"/admin/collection/{payment.id}/payment-plan",
        headers=admin_h,
        json={"plan_type": "extension", "new_due_date": "2030-01-01"},
    )
    assert res.status_code == 410
    assert InstalmentPayment.query.get(payment.id).due_date == before


def test_manual_reminder_channels(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    payment = InstalmentPayment.query.filter_by(
        plan_id=plan.id, status="pending"
    ).first()
    url = f"/admin/collection/{payment.id}/reminder"
    assert (
        client.post(
            url, headers=admin_h, json={"reminder_type": "whatsapp"}
        ).status_code
        == 400
    )
    assert (
        client.post(url, headers=admin_h, json={"reminder_type": "email"}).status_code
        == 400
    )
    assert (
        client.post(url, headers=admin_h, json={"reminder_type": "in_app"}).status_code
        == 200
    )
