"""Ledger: every money event is recorded, and balances come from the entries."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db, guard
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.ledger import LedgerEntry
from app.models.product import Product
from app.models.user import User
from tests.helpers import eligible_customer
from app.services import ledger, servicing

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


def make_user(role, phone, **extra):
    if role == "admin":
        extra.setdefault("admin_level", "management")  # services/access.py
    user = User(
        phone=phone,
        role=role,
        status="approved",
        password=guard.hash_password("Secret123!"),
        **eligible_customer(extra, phone),
    )
    db.session.add(user)
    db.session.commit()
    return user


def token(client, phone):
    res = client.post("/login", json={"phone": phone, "password": "Secret123!"})
    return {"Authorization": f"Bearer {res.get_json()['access_token']}"}


def approved_plan(app, down_payment_reference="MOMO-DP-1"):
    """Customer buys a GHS 4,000 phone on Pay in 4; admin approves."""
    client = app.test_client()
    admin = make_user("admin", "0200000101")
    merchant = make_user("merchant", "0200000102")
    customer = make_user("customer", "0200000103", kyc_status="verified")
    product = Product(
        product_id="PRD0101",
        merchant_id=merchant.id,
        name="Phone",
        price=4000,
        stock_quantity=3,
        status="active",
    )
    db.session.add(product)
    db.session.commit()
    assert (
        client.post(
            "/customer/purchase",
            headers=token(client, customer.phone),
            json={
                "accept_terms": True,
                "product_id": product.id,
                "number_of_installments": 4,
                "delivery_address": "Osu, Accra",
            },
        ).status_code
        == 201
    )
    order_id = db.session.execute(db.text("select id from purchase_orders")).scalar()
    admin_headers = token(client, admin.phone)
    res = client.put(
        f"/admin/orders/{order_id}/approve",
        headers=admin_headers,
        json={"down_payment_reference": down_payment_reference},
    )
    assert res.status_code == 200, res.get_json()
    return client, admin_headers, InstalmentPlan.query.one()


def test_approval_writes_contract_merchant_side_and_down_payment(app_ctx):
    _, _, plan = approved_plan(app_ctx)
    types = [
        (e.account, e.entry_type, e.amount_pesewas)
        for e in plan.ledger_entries.order_by(LedgerEntry.id)
    ]
    assert types == [
        ("customer", "plan_opened", 405000),  # GHS 4,000 + GHS 50 delivery
        ("merchant", "merchant_fee", 40000),  # 10% MDR on P
        ("merchant", "merchant_payable", 360000),  # §5.3 settlement
        ("customer", "payment_received", -165000),  # down payment + delivery
    ]
    assert ledger.customer_balance(plan) == D("2400.00")


def test_payments_and_late_fees_move_the_balance(app_ctx):
    client, admin_headers, plan = approved_plan(app_ctx)
    p2, p3 = (
        InstalmentPayment.query.filter_by(plan_id=plan.id)
        .order_by(InstalmentPayment.installment_number)
        .all()[1:3]
    )

    assert (
        client.put(
            f"/admin/instalments/payments/{p2.id}/mark-paid",
            headers=admin_headers,
            json={"payment_reference": "MOMO-2"},
        ).status_code
        == 200
    )
    assert ledger.customer_balance(plan) == D("1600.00")

    # Instalment 3 is overdue: the late fee is charged the day after its due date (D5)
    p3.due_date = datetime.utcnow() - timedelta(days=2)
    db.session.commit()
    assert servicing.run_daily(send_reminders=False, run_autopay=False).first_fees == 1
    assert ledger.customer_balance(plan) == D("1680.00")  # + 10% of 800

    res = client.post(
        f"/admin/instalments/payments/{p3.id}/waive-late-fee",
        headers=admin_headers,
        json={"reason": "First missed payment, customer called in"},
    )
    assert res.status_code == 200, res.get_json()
    assert ledger.customer_balance(plan) == D("1600.00")

    # A waived fee is never charged again
    assert servicing.run_daily(send_reminders=False, run_autopay=False).first_fees == 0
    assert ledger.customer_balance(plan) == D("1600.00")


def test_late_fee_not_charged_on_due_date(app_ctx):
    _, _, plan = approved_plan(app_ctx)
    p2 = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    p2.due_date = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    db.session.commit()
    assert servicing.run_daily(send_reminders=False, run_autopay=False).first_fees == 0


def test_waive_requires_reason(app_ctx):
    client, admin_headers, plan = approved_plan(app_ctx)
    p2 = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    p2.due_date = datetime.utcnow() - timedelta(days=3)
    db.session.commit()
    servicing.run_daily(send_reminders=False, run_autopay=False).first_fees
    res = client.post(
        f"/admin/instalments/payments/{p2.id}/waive-late-fee",
        headers=admin_headers,
        json={},
    )
    assert res.status_code == 400


def test_admin_plan_detail_shows_ledger(app_ctx):
    client, admin_headers, plan = approved_plan(app_ctx)
    res = client.get(f"/admin/instalments/{plan.id}", headers=admin_headers)
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["ledger"]["customer_balance"] == 2400.0
    assert len(body["ledger"]["entries"]) == 4


def test_backfill_creates_entries_for_old_plans(app_ctx):
    customer = make_user("customer", "0200000201")
    merchant = make_user("merchant", "0200000202")
    plan = InstalmentPlan(
        plan_id="IP900",
        merchant_id=merchant.id,
        customer_id=customer.id,
        plan_name="Old plan",
        total_amount=4000,
        down_payment=1600,
        remaining_amount=1600,
        number_of_installments=4,
        installment_amount=800,
        start_date=datetime.utcnow(),
        paid_installments=2,
    )
    db.session.add(plan)
    db.session.flush()
    for n, amount, status, fee in [
        (1, 1600, "paid", 0),
        (2, 800, "paid", 0),
        (3, 800, "overdue", 80),
        (4, 800, "pending", 0),
    ]:
        db.session.add(
            InstalmentPayment(
                payment_id=f"PAY9{n}",
                plan_id=plan.id,
                installment_number=n,
                due_date=datetime.utcnow(),
                amount=amount,
                status=status,
                paid_amount=amount if status == "paid" else 0,
                late_fee=fee,
            )
        )
    db.session.commit()

    runner = app_ctx.test_cli_runner()
    result = runner.invoke(args=["ledger-backfill"])
    assert "Backfilled 1 plan" in result.output, result.output
    assert ledger.customer_balance(plan) == D("1680.00")  # 4000 - 1600 - 800 + 80

    # Running again doesn't duplicate anything
    runner.invoke(args=["ledger-backfill"])
    assert ledger.customer_balance(plan) == D("1680.00")


def test_purchase_needs_terms_and_records_the_version(app_ctx):
    """Consent (§10): no order without accepting the Terms; the order records which version."""
    client, _, _ = approved_plan(app_ctx)
    from app.models.purchase_order import PurchaseOrder

    order = PurchaseOrder.query.one()
    assert (
        order.terms_version == app_ctx.config["TERMS_VERSION"]
        and order.terms_accepted_at is not None
    )
    customer = User.query.filter_by(role="customer").one()
    product = Product.query.first()
    res = client.post(
        "/customer/purchase",
        headers=token(client, customer.phone),
        json={
            "product_id": product.id,
            "number_of_installments": 1,
            "delivery_address": "Osu, Accra",
        },
    )
    assert res.status_code == 400 and res.get_json()["code"] == "terms_not_accepted"


def test_quote_includes_key_facts_from_settings(app_ctx):
    client, admin_h, _ = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    q = client.post(
        "/installment/calculate",
        headers=token(client, customer.phone),
        json={"product_price": 4000, "number_of_installments": 4},
    ).get_json()
    kf = q["key_facts"]
    assert kf["late_fee_percentage"] == 10 and kf["late_fee_cap_percentage"] == 25
    assert kf["deferment_fee_percentage"] == 10 and kf["deferment_max_per_plan"] == 1
    assert kf["terms_url"].startswith("https://") and kf["terms_version"]


def test_customer_sees_schedule_and_late_fee(app_ctx):
    """The instalments screen gets each payment, and an unpaid late fee with the total now due."""
    client, _, plan = approved_plan(app_ctx)
    p2 = InstalmentPayment.query.filter_by(plan_id=plan.id, installment_number=2).one()
    p2.due_date = datetime.utcnow() - timedelta(days=2)
    db.session.commit()
    p2.apply_late_fee()
    customer = User.query.filter_by(role="customer").one()
    h = token(client, customer.phone)
    listed = client.get("/customer/instalments", headers=h).get_json()["plans"][0][
        "payment_schedule"
    ]
    assert [r["installment_number"] for r in listed] == [1, 2, 3, 4]
    row = listed[1]
    assert row["late_fee"] == 80 and row["amount_due"] == 880  # §5.3 late fee example
    assert listed[0]["status"] == "paid" and listed[0]["amount_due"] == 0
    detail = client.get(f"/customer/instalments/{plan.id}", headers=h)
    if detail.status_code == 200:
        assert detail.get_json()["payment_schedule"][1]["amount_due"] == 880


def test_shop_preview_and_plan_options(app_ctx):
    """Vault shop: product cards and checkout get server-priced plans (§5.3 example)."""
    client, _, _ = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    h = token(client, customer.phone)
    product = Product.query.first()
    listed = client.get("/customer/products", headers=h).get_json()["products"]
    preview = next(p for p in listed if p["id"] == product.id)["split_preview"]
    assert (
        preview["down_payment"] == 1600
        and preview["installments"] == 3
        and preview["installment_amount"] == 800
    )
    res = client.post(
        "/customer/plan-options",
        headers=h,
        json={"product_id": product.id, "quantity": 1},
    )
    body = res.get_json()
    assert res.status_code == 200 and [o["n_payments"] for o in body["options"]] == [
        1,
        2,
        3,
        4,
    ]
    p4 = body["options"][3]
    assert (
        p4["due_now"] == 1650
        and p4["installment_amount"] == 800
        and p4["total_payable"] == 4050
    )
    assert (
        len(p4["payment_schedule"]) == 4
        and body["key_facts"]["late_fee_cap_percentage"] == 25
    )
    assert body["options"][0]["available"] is True  # paying in full needs no credit
    assert (
        client.post(
            "/customer/plan-options",
            headers=h,
            json={"product_id": product.id, "quantity": 99},
        ).status_code
        == 400
    )
