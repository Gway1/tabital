"""Admin screens get masked personal data by default; a reveal needs a reason and is logged."""
from app.models.pii_access import PiiAccess
from app.models.user import User
from app.services import pii

from tests.test_ledger import app_ctx, approved_plan, token  # noqa: F401  (fixture)

M = pii.MASK_CHAR


def test_mask_formats():
    assert pii.mask_value("national_id", "GHA-123456789-0") == f"GHA-{M * 5}6789-0"
    assert pii.mask_value("customer_phone", "0241234567") == f"024{M * 5}67"
    assert pii.mask_value("account_number", "1234567890") == f"{M * 6}7890"
    assert (
        pii.mask_value("IDNumber", "GHA-123456789-0") == f"GHA-{M * 5}6789-0"
    )  # Smile ID's own keys
    assert pii.mask_value("PhoneNumber2", "0241234567") == f"024{M * 5}67"
    assert (
        pii.mask_value("full_name", "Ama Mensah") == "Ama Mensah"
    )  # not an identifier
    nested = pii.mask_payload(
        {"orders": [{"customer_phone": "0241234567", "total": 4050}]}
    )
    assert (
        nested["orders"][0]["customer_phone"].endswith("67")
        and nested["orders"][0]["total"] == 4050
    )


def test_admin_responses_are_masked_but_merchant_and_customer_ones_are_not(
    app_ctx,
):  # noqa: F811
    client, admin_h, plan = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    orders = client.get("/admin/orders", headers=admin_h).get_json()["orders"]
    assert (
        M in orders[0]["customer_phone"]
        and orders[0]["customer_phone"] != customer.phone
    )
    uw = client.get(
        f"/admin/customers/{customer.id}/underwriting", headers=admin_h
    ).get_json()
    assert M in uw["details"]["national_id"] and M in uw["details"]["momo_number"]
    # The customer's own view and the merchant's view are not masked
    own = client.get(
        "/customer/credit", headers=token(client, customer.phone)
    ).get_json()
    assert M not in own["details"]["national_id"]
    merchant = User.query.filter_by(role="merchant").one()
    morders = client.get(
        "/merchant/orders", headers=token(client, merchant.phone)
    ).get_json()["orders"]
    assert morders[0]["customer_phone"] == customer.phone


def test_reveal_needs_a_reason_and_is_logged(app_ctx):  # noqa: F811
    client, admin_h, _ = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    url = "/admin/pii/reveal"
    assert (
        client.post(
            url, headers=admin_h, json={"user_id": customer.id, "field": "national_id"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            url,
            headers=admin_h,
            json={"user_id": customer.id, "field": "password", "reason": "curious"},
        ).status_code
        == 400
    )
    res = client.post(
        url,
        headers=admin_h,
        json={
            "user_id": customer.id,
            "field": "national_id",
            "reason": "Customer called about KYC, checking card",
        },
    )
    assert res.status_code == 200 and res.get_json()["value"] == customer.national_id
    log = PiiAccess.query.one()
    assert (
        log.field == "national_id"
        and log.user_id == customer.id
        and "KYC" in log.reason
    )
    assert (
        client.get("/admin/pii/access-log", headers=admin_h).get_json()["reveals"][0][
            "field"
        ]
        == "national_id"
    )
    assert (
        client.post(
            url,
            headers=token(client, customer.phone),
            json={"user_id": customer.id, "field": "phone", "reason": "trying"},
        ).status_code
        == 403
    )


def test_saving_a_masked_value_never_overwrites_real_data(app_ctx):  # noqa: F811
    client, admin_h, _ = approved_plan(app_ctx)
    customer = User.query.filter_by(role="customer").one()
    real = customer.national_id
    masked = pii.mask_value("national_id", real)
    client.put(
        f"/admin/customers/{customer.id}",
        headers=admin_h,
        json={"national_id": masked, "city": "Accra"},
    )
    fresh = User.query.get(customer.id)
    assert fresh.national_id == real and fresh.city == "Accra"
