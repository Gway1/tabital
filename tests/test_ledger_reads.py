"""Screens that show balances read them from the ledger, and none of them crash."""
import pytest

from app import create_app
from app.extensions import db
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.user import User

from tests.test_ledger import approved_plan, token


@pytest.fixture()
def world():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        client, admin_headers, plan = approved_plan(app)
        # Pay instalment 2 so there's a mix of paid and unpaid
        p2 = InstalmentPayment.query.filter_by(
            plan_id=plan.id, installment_number=2
        ).one()
        assert (
            client.put(
                f"/admin/instalments/payments/{p2.id}/mark-paid",
                headers=admin_headers,
                json={"payment_reference": "MOMO-2"},
            ).status_code
            == 200
        )
        customer = User.query.filter_by(role="customer").one()
        merchant = User.query.filter_by(role="merchant").one()
        yield {
            "client": client,
            "admin": admin_headers,
            "customer": token(client, customer.phone),
            "merchant": token(client, merchant.phone),
            "plan": plan,
            "customer_id": customer.id,
            "merchant_id": merchant.id,
        }
        db.session.remove()
        db.drop_all()


def get(w, who, url):
    res = w["client"].get(url, headers=w[who])
    assert (
        res.status_code < 500
    ), f"{url} -> {res.status_code}: {res.get_data(as_text=True)[:500]}"
    return res


def test_admin_screens_use_ledger(world):
    w = world
    plan = w["plan"]
    stats = get(w, "admin", "/admin/instalments/stats").get_json()
    assert stats["total_outstanding"] == 1600.0  # 4,050 - 1,650 - 800
    assert stats["total_paid"] == 2450.0

    listing = get(w, "admin", "/admin/instalments").get_json()
    row = listing["plans"][0]
    assert row["remaining_amount"] == 1600.0 and row["paid_amount"] == 2450.0

    detail = get(w, "admin", f"/admin/instalments/{plan.id}").get_json()
    assert detail["plan"]["remaining_amount"] == 1600.0
    assert detail["ledger"]["customer_balance"] == 1600.0

    merchant = get(w, "admin", f"/admin/merchants/{w['merchant_id']}").get_json()
    assert "error" not in merchant

    dash = get(w, "admin", "/admin/dashboard/stats").get_json()
    assert dash["stats"]["revenue_mtd"] == 400.0  # 10% MDR on GHS 4,000
    assert dash["portfolio"]["total_exposure"] == 1600.0
    assert dash["alerts"]["chargebacks"] == 0

    get(w, "admin", "/admin/instalments/export")
    get(w, "admin", "/admin/customers/stats")
    get(w, "admin", "/admin/customers")
    get(w, "admin", f"/admin/customers/{w['customer_id']}")
    get(w, "admin", "/admin/products")
    get(w, "admin", "/admin/transactions")
    tx_id = plan.transaction_id
    get(w, "admin", f"/admin/transactions/{tx_id}")
    p3 = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=3).one()
    get(w, "admin", f"/admin/collection/overdue/{p3.id}")


def test_customer_screens_use_ledger(world):
    w = world
    plan = w["plan"]
    dash = get(w, "customer", "/customer/dashboard/stats").get_json()
    assert dash["total_outstanding"] == 1600.0

    plans = get(w, "customer", "/customer/instalments").get_json()["plans"]
    assert plans[0]["amount_outstanding"] == 1600.0
    assert plans[0]["amount_paid"] == 2450.0

    detail = get(w, "customer", f"/customer/instalments/{plan.id}").get_json()
    assert detail["amount_outstanding"] == 1600.0

    get(w, "customer", "/customer/upcoming-payments")
    get(w, "customer", "/customer/transactions/stats")


def test_merchant_screens_do_not_crash(world):
    w = world
    get(w, "merchant", "/merchant/reports/instalments")
    get(w, "merchant", "/merchant/dashboard/stats")
