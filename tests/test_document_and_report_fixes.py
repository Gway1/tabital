"""Found by scripts/endpoint_report.py: merchant documents and the merchant customer report."""
from datetime import datetime

import pytest
from sqlalchemy import func

from app import create_app
from app.extensions import db, guard
from app.models.document import Document
from app.models.transaction import Transaction
from app.models.user import User


@pytest.fixture()
def seeded():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        result = app.test_cli_runner().invoke(
            args=["seed-data", "--customers", "6", "--merchants", "2", "--products", "3"])
        assert result.exit_code == 0, result.output
        yield app
        db.session.remove()
        db.drop_all()


def headers(user):
    return {"Authorization": f"Bearer {guard.encode_jwt_token(user)}"}


def test_admin_documents_for_a_merchant_use_the_users_id(seeded):
    admin = User.query.filter_by(role="admin", admin_level="management").first()
    merchant = User.query.filter_by(role="merchant").first()
    client = seeded.test_client()

    created = client.post(f"/admin/merchants/{merchant.id}/documents/upload", headers=headers(admin),
                          json={"document_type": "business_registration", "document_name": "Certificate"})
    assert created.status_code == 201, created.get_json()
    assert Document.query.get(created.get_json()["id"]).user_id == merchant.id

    listed = client.get(f"/admin/merchants/{merchant.id}/documents", headers=headers(admin))
    assert listed.status_code == 200
    names = [d["document_name"] for d in listed.get_json()]
    assert "Certificate" in names and len(names) == Document.query.filter_by(user_id=merchant.id).count()


def test_merchant_customer_report_returns_dates_as_text(seeded):
    merchant_id = (db.session.query(Transaction.merchant_id, func.count(Transaction.id))
                   .filter(Transaction.status == "completed").group_by(Transaction.merchant_id)
                   .order_by(func.count(Transaction.id).desc()).first())[0]
    merchant = User.query.get(merchant_id)

    res = seeded.test_client().get("/merchant/reports/customers", headers=headers(merchant))
    assert res.status_code == 200, res.get_json()
    top = res.get_json()["top_customers"]
    assert top
    datetime.fromisoformat(top[0]["last_transaction"])


def test_customer_product_detail_has_no_dropped_specifications_column(seeded):
    from app.models.product import Product
    customer = User.query.filter_by(role="customer", status="approved").first()
    product = Product.query.filter_by(status="active").first()

    res = seeded.test_client().get(f"/customer/products/{product.id}", headers=headers(customer))
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["name"] == product.name and body["price"] == product.price
    assert body["gallery_images"] == [] and "specifications" not in body


def _top_merchant():
    merchant_id = (db.session.query(Transaction.merchant_id, func.count(Transaction.id))
                   .filter(Transaction.status == "completed").group_by(Transaction.merchant_id)
                   .order_by(func.count(Transaction.id).desc()).first())[0]
    return User.query.get(merchant_id)


@pytest.mark.parametrize("bad", ["999999", "STL-M001-20261020-ab12", "2026-99", "abc-def"])
def test_settlement_detail_rejects_a_malformed_id_with_400(seeded, bad):
    res = seeded.test_client().get(f"/merchant/settlements/{bad}", headers=headers(_top_merchant()))
    assert res.status_code == 400
    assert "year-week" in res.get_json()["error"]


def test_settlement_detail_matches_what_the_list_counted(seeded):
    merchant = _top_merchant()
    client = seeded.test_client()
    weeks = client.get("/merchant/settlements", headers=headers(merchant)).get_json()["settlements"]
    assert weeks and all(w["period_id"] for w in weeks)

    for week in weeks:
        detail = client.get(f"/merchant/settlements/{week['period_id']}", headers=headers(merchant))
        assert detail.status_code == 200, detail.get_json()
        assert {t["id"] for t in detail.get_json()["transactions"]} == {t["id"] for t in week["transactions"]}


def test_a_sunday_sale_belongs_to_the_week_it_is_listed_under(seeded):
    merchant = _top_merchant()
    sale = Transaction.query.filter_by(merchant_id=merchant.id, status="completed").first()
    sale.completion_date = datetime(2026, 10, 18, 15, 30)         # a Sunday afternoon
    db.session.commit()
    client = seeded.test_client()

    listed = next(w for w in client.get("/merchant/settlements", headers=headers(merchant)).get_json()["settlements"]
                  if sale.id in {t["id"] for t in w["transactions"]})
    assert listed["period_id"] == "2026-41" and listed["end_date"] == "2026-10-18"
    detail = client.get(f"/merchant/settlements/{listed['period_id']}", headers=headers(merchant)).get_json()
    assert sale.id in {t["id"] for t in detail["transactions"]}


def test_document_verify_parses_the_expiry_date(seeded):
    admin = User.query.filter_by(role="admin", admin_level="management").first()
    document = Document.query.filter_by(status="uploaded").first()
    client = seeded.test_client()
    url = f"/admin/documents/{document.id}/verify"

    ok = client.put(url, headers=headers(admin), json={"status": "verified", "expiry_date": "2027-06-30"})
    assert ok.status_code == 200, ok.get_json()
    assert Document.query.get(document.id).expiry_date == datetime(2027, 6, 30)

    bad = client.put(url, headers=headers(admin), json={"status": "rejected", "expiry_date": "next year"})
    assert bad.status_code == 400
    kept = Document.query.get(document.id)
    assert kept.status == "verified" and kept.expiry_date == datetime(2027, 6, 30)   # nothing changed

    cleared = client.put(url, headers=headers(admin), json={"expiry_date": ""})
    assert cleared.status_code == 200 and Document.query.get(document.id).expiry_date is None


def _two_merchants():
    first, second = User.query.filter_by(role="merchant").order_by(User.id).limit(2).all()
    return first, second


@pytest.mark.parametrize(
    "url, field, label",
    [
        ("/merchant/payout-account", "momo_number", "Mobile Money number"),
        ("/merchant/payout-account", "account_number", "bank account number"),
        ("/merchant/bank-details", "momo_number", "Mobile Money number"),
        ("/merchant/bank-details", "account_number", "bank account number"),
    ],
)
def test_payout_details_already_used_by_another_account_give_409(seeded, url, field, label):
    mine, other = _two_merchants()
    client = seeded.test_client()

    res = client.put(url, headers=headers(mine), json={field: getattr(other, field)})
    assert res.status_code == 409, res.get_json()
    assert label in res.get_json()["error"]
    assert getattr(User.query.get(mine.id), field) != getattr(other, field)

    # Sending your own number again is not a conflict
    again = client.put(url, headers=headers(mine), json={field: getattr(mine, field)})
    assert again.status_code == 200, again.get_json()


def test_bank_details_swift_code_already_used_gives_409(seeded):
    mine, other = _two_merchants()
    other.swift_code = "SEEDGHAC"
    db.session.commit()
    res = seeded.test_client().put("/merchant/bank-details", headers=headers(mine), json={"swift_code": "SEEDGHAC"})
    assert res.status_code == 409 and "SWIFT" in res.get_json()["error"]


def test_merchant_profile_refuses_a_phone_or_email_that_belongs_to_someone_else(seeded):
    mine, other = _two_merchants()
    client = seeded.test_client()
    for field in ("phone", "business_email", "business_phone"):
        res = client.put("/merchant/settings/profile", headers=headers(mine), json={field: getattr(other, field)})
        assert res.status_code == 409, (field, res.get_json())
        assert res.get_json()["field"] == field

    ok = client.put("/merchant/settings/profile", headers=headers(mine),
                    json={"phone": mine.phone, "city": "Takoradi"})
    assert ok.status_code == 200 and User.query.get(mine.id).city == "Takoradi"


def test_admin_edits_refuse_a_phone_or_email_that_belongs_to_someone_else(seeded):
    admin = User.query.filter_by(role="admin", admin_level="management").first()
    mine, other = _two_merchants()
    customers = User.query.filter_by(role="customer").order_by(User.id).limit(2).all()
    client = seeded.test_client()

    taken = client.put(f"/admin/customers/{customers[0].id}", headers=headers(admin),
                       json={"phone": customers[1].phone, "city": "Elsewhere"})
    assert taken.status_code == 409 and taken.get_json()["field"] == "phone"
    assert User.query.get(customers[0].id).city != "Elsewhere"        # nothing was changed

    for field in ("phone", "business_email", "business_phone"):
        res = client.put(f"/admin/merchants/{mine.id}", headers=headers(admin), json={field: getattr(other, field)})
        assert res.status_code == 409, (field, res.get_json())
        assert res.get_json()["field"] == field

    ok = client.put(f"/admin/merchants/{mine.id}", headers=headers(admin),
                    json={"phone": mine.phone, "city": "Takoradi"})
    assert ok.status_code == 200 and User.query.get(mine.id).city == "Takoradi"
