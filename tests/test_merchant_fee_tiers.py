"""Merchant fee tiers (§6.1): management picks the tier (optionally at approval); the fee is fixed on
each contract at approval, so a later tier change never alters an existing plan (§5.4)."""
from app.extensions import db
from app.models.ledger import LedgerEntry
from app.models.instalment import InstalmentPlan
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.system_settings import SettingChange
from app.models.user import User
from app.services import ledger, merchant_fees

from tests.test_ledger import (
    app_ctx,
    approved_plan,
    make_user,
    token,
)  # noqa: F401  (fixture)


def _fee(plan):
    e = LedgerEntry.query.filter_by(
        plan_id=plan.id, entry_type=LedgerEntry.MERCHANT_FEE
    ).one()
    return ledger.to_cedis(e.amount_pesewas), e.note


def test_default_is_standard_ten_percent(app_ctx):  # noqa: F811
    _, _, plan = approved_plan(app_ctx)
    fee, note = _fee(plan)
    assert float(fee) == 400.0 and "10.00%" in note and "Standard" in note


def test_tier_sets_the_fee_on_new_contracts_only(app_ctx):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    merchant = User.query.filter_by(role="merchant").one()

    # No reason: refused. With a reason: saved and logged
    url = f"/admin/merchants/{merchant.id}/fee-tier"
    assert (
        client.put(url, headers=admin_h, json={"fee_tier": "premium"}).status_code
        == 400
    )
    assert (
        client.put(
            url, headers=admin_h, json={"fee_tier": "gold", "reason": "No such tier"}
        ).status_code
        == 400
    )
    res = client.put(
        url,
        headers=admin_h,
        json={"fee_tier": "premium", "reason": "Low-risk chain store"},
    )
    assert res.status_code == 200 and res.get_json()["fee_percentage"] == 8.0
    change = SettingChange.query.filter_by(
        setting_key=f"merchant_fee_tier:{merchant.id}"
    ).one()
    assert change.reason == "Low-risk chain store"

    # The existing contract keeps 10%
    assert float(_fee(plan)[0]) == 400.0

    # A new order approved now is priced at 8%: 4,000 x 8% = 320, payout 3,680
    customer = User.query.filter_by(role="customer").one()
    product = Product.query.first()
    assert (
        client.post(
            "/customer/purchase",
            headers=token(client, customer.phone),
            json={
                "accept_terms": True,
                "product_id": product.id,
                "number_of_installments": 1,
                "delivery_address": "Osu, Accra",
            },
        ).status_code
        == 201
    )
    order = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first()
    assert (
        client.put(
            f"/admin/orders/{order.id}/approve",
            headers=admin_h,
            json={"down_payment_reference": "MM-TIER-1"},
        ).status_code
        == 200
    )
    new_plan = InstalmentPlan.query.order_by(InstalmentPlan.id.desc()).first()
    fee, note = _fee(new_plan)
    assert float(fee) == 320.0 and "8.00%" in note and "Premium" in note
    payable = LedgerEntry.query.filter_by(
        plan_id=new_plan.id, entry_type=LedgerEntry.MERCHANT_PAYABLE
    ).one()
    assert float(ledger.to_cedis(payable.amount_pesewas)) == 3680.0

    # Merchant detail shows the tier and its history
    detail = client.get(url, headers=admin_h).get_json()
    assert detail["fee_tier"] == "premium" and detail["history"][0]["to"] == "premium"


def test_tier_is_optional_at_approval_and_management_only(app_ctx):  # noqa: F811
    client = app_ctx.test_client()
    admin = make_user("admin", "0200000401")
    staff = make_user("admin", "0200000402", admin_level="operations")
    merchant = make_user("merchant", "0200000403")
    merchant.status = "pending"
    db.session.commit()
    admin_h, staff_h = token(client, admin.phone), token(client, staff.phone)

    assert (
        client.put(
            f"/admin/kyc/approve/{merchant.id}",
            headers=staff_h,
            json={"fee_tier": "premium"},
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"/admin/kyc/approve/{merchant.id}",
            headers=admin_h,
            json={"fee_tier": "nope"},
        ).status_code
        == 400
    )
    assert (
        User.query.get(merchant.id).status == "pending"
    )  # nothing saved on a bad tier
    assert (
        client.put(
            f"/admin/kyc/approve/{merchant.id}",
            headers=admin_h,
            json={"fee_tier": "high_risk"},
        ).status_code
        == 200
    )
    m = User.query.get(merchant.id)
    assert m.status == "approved" and m.merchant_fee_tier == "high_risk"
    assert float(merchant_fees.rate_for(m)) == 0.12

    # Approving without a tier leaves the standard tier
    other = make_user("merchant", "0200000404")
    other.status, other.kyc_status = "pending", "verified"  # KYB passed
    db.session.commit()
    assert (
        client.post(f"/admin/approve/{other.id}", headers=admin_h, json={}).status_code
        == 200
    )
    assert merchant_fees.tier_of(User.query.get(other.id)) == "standard"

    tiers = client.get("/admin/merchant-fee-tiers", headers=admin_h).get_json()["tiers"]
    assert [(t["value"], t["fee_percentage"]) for t in tiers] == [
        ("premium", 8.0),
        ("standard", 10.0),
        ("high_risk", 12.0),
    ]
