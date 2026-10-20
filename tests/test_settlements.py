"""Phase 5: settlements, clawbacks, payout-account security, statements, payment links."""
import hashlib
import hmac
import json
from datetime import date, datetime, timedelta

import pytest

from app import create_app
from app.extensions import db
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.settlement import PaymentLink, Settlement, SettlementLine
from app.models.user import User
from app.services import paystack, settlements

from tests.test_ledger import approved_plan, token

FAKE_SECRET = "sk_test_fake_key_for_unit_tests"


class FakeTransfers:
    def __init__(self):
        self.recipients = []
        self.transfers = []
        self.transfer_status = "success"
        self.verify_status = "success"

    def create_recipient(self, **kw):
        self.recipients.append(kw)
        return f"RCP_{len(self.recipients)}"

    def initiate(self, **kw):
        self.transfers.append(kw)
        return {
            "status": self.transfer_status,
            "transfer_code": "TRF_1",
            "reference": kw["reference"],
        }

    def verify(self, reference):
        return {"status": self.verify_status, "reference": reference}


@pytest.fixture()
def env(monkeypatch):
    app = create_app()
    app.config["TESTING"] = True
    app.config[
        "PAYSTACK_SECRET_KEY"
    ] = None  # manual down-payment flow for approved_plan
    fake = FakeTransfers()
    monkeypatch.setattr(paystack, "create_transfer_recipient", fake.create_recipient)
    monkeypatch.setattr(paystack, "initiate_transfer", fake.initiate)
    monkeypatch.setattr(paystack, "verify_transfer", fake.verify)
    with app.app_context():
        db.drop_all()
        db.create_all()
        client, admin_headers, plan = approved_plan(app)
        merchant = User.query.filter_by(role="merchant").one()
        customer = User.query.filter_by(role="customer").one()
        # A verified payout account set by an admin (no 48-hour hold)
        merchant.payout_method, merchant.payout_bank_code = "mobile_money", "MTN"
        merchant.momo_number, merchant.momo_name = "0240000102", "Phone Shop"
        db.session.commit()
        env = {
            "app": app,
            "client": client,
            "admin": admin_headers,
            "plan": plan,
            "fake": fake,
            "merchant": merchant,
            "merchant_headers": token(client, merchant.phone),
            "customer": customer,
            "customer_headers": token(client, customer.phone),
        }
        yield env
        db.session.remove()
        db.drop_all()


def deliver(env, order=None):
    order = order or PurchaseOrder.query.first()
    res = env["client"].put(
        f"/merchant/orders/{order.id}/delivery",
        headers=env["merchant_headers"],
        json={"delivery_status": "delivered"},
    )
    assert res.status_code == 200, res.get_json()


def pay_config(env):
    env["app"].config["PAYSTACK_SECRET_KEY"] = FAKE_SECRET


# ---------------------------------------------------------------- lines and batches


def test_delivery_makes_the_sale_payable_once(env):
    deliver(env)
    settlements.record_delivery(env["plan"], None)  # a repeat call adds nothing
    db.session.commit()
    line = SettlementLine.query.one()
    assert (
        line.line_type == "sale" and line.net_pesewas == 360000
    )  # 4,000 - 10% MDR (§5.3)
    assert line.fee_pesewas == 40000 and line.gross_pesewas == 400000


def test_no_batch_before_the_cycle_ends(env):
    deliver(env)
    assert (
        settlements.generate_batches(datetime.utcnow().date() + timedelta(days=6)) == []
    )
    batches = settlements.generate_batches(datetime.utcnow().date() + timedelta(days=7))
    assert len(batches) == 1
    b = batches[0]
    assert b.status == Settlement.PENDING_APPROVAL and b.net_pesewas == 360000


def test_merchant_chooses_3_7_or_30_day_cycle(env):
    h = env["merchant_headers"]
    assert (
        env["client"]
        .put("/merchant/payout-account", headers=h, json={"settlement_period_days": 14})
        .status_code
        == 400
    )
    assert (
        env["client"]
        .put("/merchant/payout-account", headers=h, json={"settlement_period_days": 3})
        .status_code
        == 200
    )
    deliver(env)
    assert (
        len(settlements.generate_batches(datetime.utcnow().date() + timedelta(days=3)))
        == 1
    )


def test_approval_pays_through_paystack_and_settles_the_ledger(env):
    pay_config(env)
    deliver(env)
    batch = settlements.generate_batches(datetime.utcnow().date() + timedelta(days=7))[
        0
    ]
    res = env["client"].post(
        f"/admin/settlement-batches/{batch.id}/approve", headers=env["admin"]
    )
    assert res.status_code == 200 and res.get_json()["outcome"] == "paid"
    fake = env["fake"]
    assert (
        fake.recipients[0]["payout_method"] == "mobile_money"
        and fake.recipients[0]["bank_code"] == "MTN"
    )
    assert fake.transfers[0]["amount_pesewas"] == 360000
    db.session.refresh(batch)
    assert batch.status == Settlement.PAID and batch.paid_at
    assert settlements.merchant_owed_pesewas(env["plan"]) == 0


def test_pending_transfer_is_finished_by_signed_webhook(env):
    pay_config(env)
    env["fake"].transfer_status = "pending"
    deliver(env)
    batch = settlements.generate_batches(datetime.utcnow().date() + timedelta(days=7))[
        0
    ]
    env["client"].post(
        f"/admin/settlement-batches/{batch.id}/approve", headers=env["admin"]
    )
    db.session.refresh(batch)
    assert batch.status == Settlement.PROCESSING
    raw = json.dumps(
        {"event": "transfer.success", "data": {"reference": batch.transfer_reference}}
    ).encode()
    sig = hmac.new(FAKE_SECRET.encode(), raw, hashlib.sha512).hexdigest()
    res = env["client"].post(
        "/webhooks/paystack",
        data=raw,
        headers={"x-paystack-signature": sig, "Content-Type": "application/json"},
    )
    assert res.get_json()["status"] == "paid"
    db.session.refresh(batch)
    assert batch.status == Settlement.PAID


def test_failed_transfer_can_be_retried(env):
    pay_config(env)

    def boom(**kw):
        raise paystack.PaystackError("Insufficient balance")

    env_initiate = paystack.initiate_transfer
    paystack.initiate_transfer = boom
    try:
        deliver(env)
        batch = settlements.generate_batches(
            datetime.utcnow().date() + timedelta(days=7)
        )[0]
        res = env["client"].post(
            f"/admin/settlement-batches/{batch.id}/approve", headers=env["admin"]
        )
        assert res.status_code == 400 and "Insufficient" in res.get_json()["message"]
    finally:
        paystack.initiate_transfer = env_initiate
    res = env["client"].post(
        f"/admin/settlement-batches/{batch.id}/approve", headers=env["admin"]
    )
    assert res.get_json()["outcome"] == "paid"


# ---------------------------------------------------------------- clawbacks


def test_clawback_before_payout_removes_the_sale(env):
    deliver(env)
    settlements.clawback_plan(env["plan"], "Dispute won by customer")
    db.session.commit()
    assert SettlementLine.query.count() == 0
    assert settlements.merchant_owed_pesewas(env["plan"]) == 0
    assert (
        settlements.generate_batches(datetime.utcnow().date() + timedelta(days=30))
        == []
    )


def test_clawback_before_delivery_means_nothing_is_ever_paid(env):
    settlements.clawback_plan(env["plan"], "Cancelled")
    db.session.commit()
    deliver(env)
    assert SettlementLine.query.count() == 0


def test_clawback_after_payout_comes_off_the_next_settlement(env):
    pay_config(env)
    deliver(env)
    first = settlements.generate_batches(datetime.utcnow().date() + timedelta(days=7))[
        0
    ]
    settlements.approve_and_pay(first, User.query.filter_by(role="admin").one())

    settlements.clawback_plan(env["plan"], "Defective, refunded")
    db.session.commit()
    claw = SettlementLine.query.filter_by(line_type="clawback").one()
    assert claw.net_pesewas == -360000 and claw.settlement_id is None
    assert (
        settlements.merchant_owed_pesewas(env["plan"]) == -360000
    )  # merchant owes it back

    # A new GHS 4,000 sale nets against it: 3,600 - 3,600 = 0, so it carries forward
    new_sale = SettlementLine(
        merchant_id=env["merchant"].id,
        plan_id=env["plan"].id,
        line_type="sale",
        gross_pesewas=400000,
        fee_pesewas=40000,
        net_pesewas=360000,
        description="test",
    )
    db.session.add(new_sale)
    db.session.commit()
    assert (
        settlements.generate_batches(datetime.utcnow().date() + timedelta(days=15))
        == []
    )


def test_customer_won_dispute_claws_back_automatically(env):
    deliver(env)
    env["client"].post(
        "/customer/disputes",
        headers=env["customer_headers"],
        json={
            "plan_id": env["plan"].id,
            "reason": "defective",
            "description": "Screen cracked on arrival",
        },
    )
    from app.models.dispute import Dispute

    d = Dispute.query.one()
    env["client"].put(
        f"/admin/disputes/{d.id}/resolve",
        headers=env["admin"],
        json={"outcome": "customer_won", "notes": "Photos confirm damage"},
    )
    assert SettlementLine.query.filter_by(line_type="sale").count() == 0
    assert settlements.merchant_owed_pesewas(env["plan"]) == 0


# ---------------------------------------------------------------- payout account security


def test_merchant_changing_payout_details_holds_payouts(env):
    pay_config(env)
    env["merchant"].paystack_recipient_code = "RCP_old"
    db.session.commit()
    res = env["client"].put(
        "/merchant/payout-account",
        headers=env["merchant_headers"],
        json={"momo_number": "0559999999"},
    )
    assert res.status_code == 200 and "48 hours" in res.get_json()["message"]
    m = User.query.get(env["merchant"].id)
    assert m.paystack_recipient_code is None and m.payout_hold_until > datetime.utcnow()

    deliver(env)
    batch = settlements.generate_batches(datetime.utcnow().date() + timedelta(days=7))[
        0
    ]
    assert batch.status == Settlement.ON_HOLD
    res = env["client"].post(
        f"/admin/settlement-batches/{batch.id}/approve", headers=env["admin"]
    )
    assert res.status_code == 400 and res.get_json()["outcome"] == "on_hold"
    assert env["fake"].transfers == []

    m.payout_hold_until = datetime.utcnow() - timedelta(minutes=1)  # hold has passed
    db.session.commit()
    res = env["client"].post(
        f"/admin/settlement-batches/{batch.id}/approve", headers=env["admin"]
    )
    assert res.get_json()["outcome"] == "paid"
    assert (
        env["fake"].recipients[-1]["account_number"] == "0559999999"
    )  # new recipient created


def test_all_payout_edit_endpoints_are_guarded(env):
    h = env["merchant_headers"]
    for url, method, body in [
        ("/merchant/settings/payment", "put", {"account_number": "1234567890"}),
        ("/merchant/settlements/settings", "put", {"account_number": "1234567891"}),
        ("/merchant/bank-details", "put", {"account_number": "1234567892"}),
    ]:
        m = User.query.get(env["merchant"].id)
        m.payout_hold_until = None
        db.session.commit()
        res = getattr(env["client"], method)(url, headers=h, json=body)
        assert res.status_code == 200, (url, res.get_json())
        db.session.expire_all()
        assert User.query.get(env["merchant"].id).payout_hold_until is not None, url


def test_invalid_payout_details_rejected(env):
    h = env["merchant_headers"]
    assert (
        env["client"]
        .put("/merchant/payout-account", headers=h, json={"momo_number": "123"})
        .status_code
        == 400
    )
    assert (
        env["client"]
        .put("/merchant/payout-account", headers=h, json={"payout_method": "cash"})
        .status_code
        == 400
    )


# ---------------------------------------------------------------- statements


def test_statement_and_csv(env):
    pay_config(env)
    deliver(env)
    batch = settlements.generate_batches(datetime.utcnow().date() + timedelta(days=7))[
        0
    ]
    settlements.approve_and_pay(batch, User.query.filter_by(role="admin").one())
    body = (
        env["client"]
        .get("/merchant/statement", headers=env["merchant_headers"])
        .get_json()
    )
    assert body["totals"] == {
        "sales": 4000.0,
        "fees": 400.0,
        "clawbacks": 0.0,
        "paid_out": 3600.0,
    }
    assert [r["type"] for r in body["rows"]] == ["sale", "payout"]
    csv_res = env["client"].get(
        "/merchant/statement?format=csv", headers=env["merchant_headers"]
    )
    assert csv_res.status_code == 200 and csv_res.mimetype == "text/csv"
    assert "3600.00" in csv_res.get_data(as_text=True)
    listing = (
        env["client"]
        .get("/merchant/settlement-batches", headers=env["merchant_headers"])
        .get_json()
    )
    assert (
        listing["settlements"][0]["status"] == "paid"
        and listing["settlements"][0]["net"] == 3600.0
    )


def test_old_manual_payout_request_is_retired(env):
    res = env["client"].post(
        "/merchant/settlements/request-payout",
        headers=env["merchant_headers"],
        json={"amount": 100},
    )
    assert res.status_code == 410
    # Nor can an admin tick transactions off as "settled" without a transfer
    tx_id = PurchaseOrder.query.first().transaction_id
    assert (
        env["client"]
        .post(
            "/admin/settlements/process-bulk",
            headers=env["admin"],
            json={"settlement_ids": [tx_id]},
        )
        .status_code
        == 410
    )
    assert (
        env["client"]
        .put(
            f"/admin/settlements/{tx_id}/process",
            headers=env["admin"],
            json={"payment_reference": "X"},
        )
        .status_code
        == 410
    )


def test_in_store_quote_has_no_delivery_fee(env):
    h = env["customer_headers"]
    home = (
        env["client"]
        .post(
            "/installment/calculate",
            headers=h,
            json={"product_price": 4000, "number_of_installments": 4},
        )
        .get_json()
    )
    shop = (
        env["client"]
        .post(
            "/installment/calculate",
            headers=h,
            json={"product_price": 4000, "number_of_installments": 4, "in_store": True},
        )
        .get_json()
    )
    assert home["fees"]["delivery_fee"] > 0 and shop["fees"]["delivery_fee"] == 0
    assert shop["totals"]["total_payable"] == 4000


def test_delivery_updates_the_orders_own_transaction(env):
    """Two orders for the same product used to be matched by product name."""
    first_order = PurchaseOrder.query.one()
    assert first_order.transaction_id is not None
    deliver(env, first_order)
    line = SettlementLine.query.one()
    assert line.transaction_id == first_order.transaction_id


# ---------------------------------------------------------------- payment links


def test_payment_link_flow(env):
    product = Product.query.first()
    res = env["client"].post(
        "/merchant/payment-links",
        headers=env["merchant_headers"],
        json={"product_id": product.id, "quantity": 1, "note": "Counter 2"},
    )
    assert res.status_code == 201, res.get_json()
    link = res.get_json()
    assert link["url"].endswith(f"/customer/pay-link/{link['token']}")
    assert link["qr_svg"].lstrip().startswith("<?xml") or "<svg" in link["qr_svg"]

    view = env["client"].get(
        f"/customer/payment-links/{link['token']}", headers=env["customer_headers"]
    )
    assert view.status_code == 200 and view.get_json()["product"]["id"] == product.id

    buy = env["client"].post(
        "/customer/purchase",
        headers=env["customer_headers"],
        json={
            "accept_terms": True,
            "payment_link": link["token"],
            "product_id": 999,
            "quantity": 5,
            "number_of_installments": 4,
        },
    )
    assert buy.status_code == 201, buy.get_json()
    order = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first()
    assert order.product_id == product.id and order.quantity == 1  # from the link
    assert (
        order.delivery_address == "Collected in store"
        and order.payment_link_id == link["id"]
    )
    assert (
        buy.get_json()["payment_schedule"][0]["delivery_fee"] == 0
    )  # collected in store

    again = env["client"].post(
        "/customer/purchase",
        headers=env["customer_headers"],
        json={
            "accept_terms": True,
            "payment_link": link["token"],
            "number_of_installments": 4,
        },
    )
    assert again.status_code == 410  # one use


def test_expired_link_and_other_merchants_products(env):
    product = Product.query.first()
    link = PaymentLink(
        token="expiredtoken123",
        merchant_id=env["merchant"].id,
        product_id=product.id,
        quantity=1,
        expires_at=datetime.utcnow() - timedelta(minutes=1),
    )
    db.session.add(link)
    other = User(
        phone="0209990001",
        role="merchant",
        status="approved",
        password=env["merchant"].password,
    )
    db.session.add(other)
    db.session.commit()
    assert (
        env["client"]
        .get("/customer/payment-links/expiredtoken123", headers=env["customer_headers"])
        .status_code
        == 410
    )
    res = env["client"].post(
        "/merchant/payment-links",
        headers=token(env["client"], other.phone),
        json={"product_id": product.id},
    )
    assert res.status_code == 400


def test_merchant_orders_show_payout_and_search(env):
    """Vault orders: payout from the ledger (P x (1 - MDR)), settlement status, working search."""
    body = (
        env["client"]
        .get("/merchant/orders", headers=env["merchant_headers"])
        .get_json()
    )
    o = body["orders"][0]
    assert (
        o["product_total"] == 4000
        and o["merchant_fee"] == 400
        and o["merchant_payout"] == 3600
    )
    assert o["payout_estimated"] is False and o["payout_status"] == "after_delivery"
    deliver(env)
    o = (
        env["client"]
        .get("/merchant/orders", headers=env["merchant_headers"])
        .get_json()["orders"][0]
    )
    assert o["payout_status"] == "next_settlement"
    none = (
        env["client"]
        .get(
            "/merchant/orders?search=nothing-like-this", headers=env["merchant_headers"]
        )
        .get_json()
    )
    assert none["orders"] == []
    hit = (
        env["client"]
        .get("/merchant/orders?search=Phone", headers=env["merchant_headers"])
        .get_json()
    )
    assert len(hit["orders"]) == 1
