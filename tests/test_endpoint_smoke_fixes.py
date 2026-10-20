"""Regression tests for three endpoint bugs found in a smoke test of the dev server:
1. admin KYC lists crashed (merchant_fees was never imported in admin_kyc.py)
2. transaction / payout stats crashed (they queried a Transaction.commission_amount column that
   doesn't exist; the fee is worked out by merchant_fees.transaction_split)
3. a missing or bad token answered 500 instead of 401 (flask_restful swallowed the auth error)
"""
import pytest

from app.extensions import db
from app.models.transaction import Transaction

from tests.test_ledger import app_ctx, make_user, token  # noqa: F401  (fixture)


def _sale(customer, merchant, ref, amount, payout=None, status="completed"):
    t = Transaction(
        transaction_id=ref,
        customer_id=customer.id,
        merchant_id=merchant.id,
        amount=amount,
        payout_amount=payout,
        product_name="Phone",
        status=status,
    )
    db.session.add(t)
    db.session.commit()
    return t


# 1. Admin KYC lists --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,key,kyc_status",
    [
        ("/admin/kyc/verified", "verified_merchants", "verified"),
        ("/admin/kyc/rejected", "rejected_merchants", "rejected"),
    ],
)
def test_admin_kyc_lists_include_fee_tier(app_ctx, path, key, kyc_status):  # noqa: F811
    make_user("admin", "0200000001")
    make_user(
        "merchant", "0200000002", kyc_status=kyc_status, business_name="Kofi Shop"
    )
    client = app_ctx.test_client()
    res = client.get(path, headers=token(client, "0200000001"))
    assert res.status_code == 200, res.get_json()
    rows = res.get_json()[key]
    assert (
        len(rows) == 1
        and rows[0]["fee_tier"] == "standard"
        and rows[0]["fee_percentage"] == 10.0
    )


def test_admin_kyc_pending_list_works(app_ctx):  # noqa: F811
    make_user("admin", "0200000001")
    client = app_ctx.test_client()
    assert (
        client.get(
            "/admin/kyc/customers/pending", headers=token(client, "0200000001")
        ).status_code
        == 200
    )


# 2. Transaction and payout stats ---------------------------------------------------------------


def test_transaction_stats_commission_for_each_role(app_ctx):  # noqa: F811
    admin = make_user("admin", "0200000001")
    merchant = make_user("merchant", "0200000002", kyc_status="verified")
    customer = make_user("customer", "0200000003", kyc_status="verified")
    _sale(customer, merchant, "T1", 1000.0, payout=900.0)  # fee 100 stored at approval
    _sale(customer, merchant, "T2", 500.0)  # no payout stored: current tier, 10%
    client = app_ctx.test_client()

    for phone in (admin.phone, merchant.phone, customer.phone):
        res = client.get("/transactions/stats", headers=token(client, phone))
        assert res.status_code == 200, (phone, res.get_json())
        body = res.get_json()
        assert body["total_transactions"] == 2
        assert body["total_amount"] == 1500.0
        assert body["total_commission"] == pytest.approx(150.0)


def test_merchant_payout_stats_commission(app_ctx):  # noqa: F811
    merchant = make_user("merchant", "0200000002", kyc_status="verified")
    customer = make_user("customer", "0200000003", kyc_status="verified")
    _sale(customer, merchant, "T1", 1000.0, payout=920.0)  # premium-style 8% fee
    _sale(customer, merchant, "T2", 2000.0, payout=1800.0)
    _sale(
        customer, merchant, "T3", 700.0, payout=630.0, status="pending"
    )  # not counted
    client = app_ctx.test_client()
    res = client.get("/merchant/payouts/stats", headers=token(client, merchant.phone))
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["total_payout"] == 2720.0
    assert body["total_commission"] == pytest.approx(280.0)


def test_stats_with_no_sales(app_ctx):  # noqa: F811
    merchant = make_user("merchant", "0200000002", kyc_status="verified")
    client = app_ctx.test_client()
    h = token(client, merchant.phone)
    assert (
        client.get("/transactions/stats", headers=h).get_json()["total_commission"] == 0
    )
    assert (
        client.get("/merchant/payouts/stats", headers=h).get_json()["total_commission"]
        == 0
    )


# 3. Auth failures answer 401, not 500 ------------------------------------------------------------
# TESTING=True makes flask_restful re-raise errors, which hides the bug, so these run as production does.


@pytest.fixture()
def prod_app(app_ctx):  # noqa: F811
    app_ctx.config["TESTING"] = False
    app_ctx.config["PROPAGATE_EXCEPTIONS"] = False
    return app_ctx


@pytest.mark.parametrize(
    "path", ["/admin/business-settings", "/merchant/orders", "/transactions/stats"]
)
def test_missing_token_is_401(prod_app, path):
    res = prod_app.test_client().get(path)
    assert res.status_code == 401


def test_bad_token_is_401(prod_app):
    res = prod_app.test_client().get(
        "/admin/business-settings", headers={"Authorization": "Bearer x"}
    )
    assert res.status_code == 401


def test_expired_token_is_401(prod_app):
    from app.extensions import guard

    user = make_user("admin", "0200000001")
    expired = guard.encode_jwt_token(user, override_access_lifespan={"seconds": -10})
    res = prod_app.test_client().get(
        "/admin/business-settings", headers={"Authorization": f"Bearer {expired}"}
    )
    assert res.status_code == 401


def test_valid_token_still_works_and_wrong_role_is_403(prod_app):
    make_user("admin", "0200000001")
    make_user("customer", "0200000003", kyc_status="verified")
    client = prod_app.test_client()
    assert (
        client.get(
            "/admin/business-settings", headers=token(client, "0200000001")
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/admin/business-settings", headers=token(client, "0200000003")
        ).status_code
        == 403
    )
