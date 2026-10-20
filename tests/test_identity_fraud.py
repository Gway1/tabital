"""Phase 6: Smile ID identity checks, employment verification, fraud signals (CLAUDE.md §9)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app import create_app
from app.extensions import db
from app.models.identity import DeviceSeen, FraudSignal, IdentityCheck
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.risk_assessment import RiskAssessment
from app.models.settlement import Settlement, SettlementLine
from app.models.user import User
from app.services import fraud, settlements, smileid

from tests.test_ledger import make_user, token

JOB = "job_01m326p53pe1ztjdxh2d9jn9ck"


class FakeSmile:
    def __init__(self):
        self.tokens = []
        self.status = "clear"

    def mint(self, **kw):
        self.tokens.append(kw)
        return "tok_test"

    def job_status(self, job_id):
        return {"status": self.status, "job_id": job_id}


@pytest.fixture()
def env(monkeypatch):
    app = create_app()
    app.config["TESTING"] = True
    app.config["PAYSTACK_SECRET_KEY"] = None
    app.config["SMILEID_PARTNER_ID"] = "1234"
    app.config["SMILEID_API_KEY"] = "smile-test-key"
    app.config["SMILEID_CALLBACK_URL"] = "https://api.example.test/webhooks/smileid"
    fake = FakeSmile()
    monkeypatch.setattr(smileid, "mint_token", fake.mint)
    monkeypatch.setattr(smileid, "job_status", fake.job_status)
    with app.app_context():
        db.drop_all()
        db.create_all()
        client = app.test_client()
        admin = make_user("admin", "0200000301")
        merchant = make_user(
            "merchant",
            "0200000302",
            business_name="Phone Hub",
            momo_number="0550000302",
        )
        customer = make_user(
            "customer",
            "0240000303",
            full_name="Ama Serwaa Mensah",
            dob="1990-01-15",
            national_id="GHA-123456789-0",
            kyc_status="pending",
        )
        product = Product(
            product_id="PRD0301",
            merchant_id=merchant.id,
            name="Phone",
            price=4000,
            stock_quantity=5,
            status="active",
        )
        db.session.add(product)
        db.session.commit()
        yield {
            "app": app,
            "client": client,
            "fake": fake,
            "admin": admin,
            "merchant": merchant,
            "customer": customer,
            "product": product,
            "admin_h": token(client, admin.phone),
            "cust_h": token(client, customer.phone),
        }
        db.session.remove()
        db.drop_all()


def signed_headers(when=None):
    ts = (when or datetime.now(timezone.utc)).isoformat()
    return {
        "Response-Timestamp": ts,
        "Response-Signature": smileid.expected_signature(ts),
        "Content-Type": "application/json",
    }


def webhook(env, status, id_fields=None, job_id=JOB, headers=None):
    body = {
        "status": status,
        "product": "biometric_kyc",
        "partner_params": {"job_id": job_id},
        "id_fields": id_fields,
        "message": "done",
    }
    return env["client"].post(
        "/webhooks/smileid", json=body, headers=headers or signed_headers()
    )


def start_and_submit(env):
    res = env["client"].post(
        "/customer/identity/start", headers=env["cust_h"], json={"consent": True}
    )
    assert res.status_code == 200, res.get_json()
    check_id = res.get_json()["check_id"]
    res = env["client"].post(
        f"/customer/identity/{check_id}/submitted",
        headers=env["cust_h"],
        json={"job_id": JOB},
    )
    assert res.status_code == 200, res.get_json()
    return IdentityCheck.query.get(check_id)


MATCHING = {
    "first_name": "Ama",
    "middle_name": "Serwaa",
    "last_name": "Mensah",
    "date_of_birth": "1990-01-15",
    "photo_url": "https://example.test/photo.jpg",
}


# ---------------------------------------------------------------- Smile ID signature


def test_webhook_signature(env):
    ts = datetime.now(timezone.utc).isoformat()
    good = smileid.expected_signature(ts)
    assert smileid.verify_webhook(ts, good)
    assert not smileid.verify_webhook(ts, good[:-2] + "xx")
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert not smileid.verify_webhook(old, smileid.expected_signature(old))  # replayed
    assert not smileid.verify_webhook(None, good)


# ---------------------------------------------------------------- starting a check


def test_start_binds_the_customers_details(env):
    assert (
        env["client"]
        .post("/customer/identity/start", headers=env["cust_h"], json={})
        .status_code
        == 400
    )
    res = env["client"].post(
        "/customer/identity/start", headers=env["cust_h"], json={"consent": True}
    )
    body = res.get_json()
    assert res.status_code == 200 and body["token"] == "tok_test"
    payload = env["fake"].tokens[0]["payload"]
    assert payload["id_number"] == "GHA-123456789-0" and payload["country"] == "GH"
    assert payload["given_names"] == "Ama Serwaa" and payload["last_name"] == "Mensah"
    assert (
        payload["phone_number"] == "+233240000303"
        and payload["consent"]["granted"] is True
    )
    assert body["fields"]["id_number"] == "GHA-123456789-0"
    assert IdentityCheck.query.one().status == IdentityCheck.STARTED


def test_start_needs_a_valid_ghana_card_and_full_name(env):
    c = env["customer"]
    c.national_id = "12345"
    db.session.commit()
    res = env["client"].post(
        "/customer/identity/start", headers=env["cust_h"], json={"consent": True}
    )
    assert res.status_code == 400 and "Ghana Card" in res.get_json()["error"]
    c.national_id, c.full_name = "GHA-123456789-0", "Ama"
    db.session.commit()
    res = env["client"].post(
        "/customer/identity/start", headers=env["cust_h"], json={"consent": True}
    )
    assert res.status_code == 400 and "full name" in res.get_json()["error"]


def test_invalid_job_id_rejected(env):
    res = env["client"].post(
        "/customer/identity/start", headers=env["cust_h"], json={"consent": True}
    )
    check_id = res.get_json()["check_id"]
    res = env["client"].post(
        f"/customer/identity/{check_id}/submitted",
        headers=env["cust_h"],
        json={"job_id": "../../etc"},
    )
    assert res.status_code == 400


# ---------------------------------------------------------------- results


def test_clear_result_with_matching_details_verifies_kyc(env):
    start_and_submit(env)
    assert webhook(env, "clear", MATCHING).status_code == 200
    check = IdentityCheck.query.one()
    user = User.query.get(env["customer"].id)
    assert check.status == IdentityCheck.CLEAR and check.name_match and check.dob_match
    assert user.kyc_status == "verified" and user.verification_level == "biometric"
    assert "photo_url" not in check.result_json  # no image links kept
    assert (
        RiskAssessment.query.filter_by(
            user_id=user.id, source=RiskAssessment.KYC_APPROVAL
        ).count()
        == 1
    )
    assert webhook(env, "clear", MATCHING).status_code == 200  # a retry changes nothing
    assert IdentityCheck.query.one().status == IdentityCheck.CLEAR


def test_name_mismatch_goes_to_review(env):
    start_and_submit(env)
    webhook(
        env,
        "clear",
        {**MATCHING, "first_name": "Kofi", "middle_name": "", "last_name": "Boateng"},
    )
    check = IdentityCheck.query.one()
    assert check.status == IdentityCheck.REVIEW and check.name_match is False
    assert User.query.get(env["customer"].id).kyc_status == "pending"
    assert any("name" in r for r in check.reasons)


def test_dob_mismatch_and_attention_go_to_review(env):
    start_and_submit(env)
    env["fake"].status = "attention"
    webhook(env, "attention", {**MATCHING, "date_of_birth": "1991-02-02"})
    check = IdentityCheck.query.one()
    assert check.status == IdentityCheck.REVIEW and check.dob_match is False


def test_block_rejects(env):
    start_and_submit(env)
    env["fake"].status = "block"
    webhook(env, "block", None)
    assert IdentityCheck.query.one().status == IdentityCheck.BLOCKED
    assert User.query.get(env["customer"].id).kyc_status == "rejected"


def test_unsigned_or_mismatched_webhook_ignored(env):
    start_and_submit(env)
    bad = {
        "Response-Timestamp": datetime.now(timezone.utc).isoformat(),
        "Response-Signature": "nope",
    }
    assert webhook(env, "clear", MATCHING, headers=bad).status_code == 401
    env["fake"].status = "block"  # Smile ID's own record disagrees with the body
    assert (
        webhook(env, "clear", MATCHING).get_json()["status"]
        == "status mismatch ignored"
    )
    assert IdentityCheck.query.one().status == IdentityCheck.SUBMITTED
    assert (
        webhook(
            env, "clear", MATCHING, job_id="job_unknownjob0000000000000000"
        ).status_code
        == 200
    )


def test_ghana_card_already_verified_elsewhere_is_blocked(env):
    make_user(
        "customer",
        "0240000399",
        full_name="Other Person",
        national_id="GHA 123456789 0",
        kyc_status="verified",
    )
    start_and_submit(env)
    webhook(env, "clear", MATCHING)
    assert IdentityCheck.query.one().status == IdentityCheck.BLOCKED
    signal = FraudSignal.query.filter_by(
        user_id=env["customer"].id, code="duplicate_ghana_card"
    ).one()
    assert signal.severity == FraudSignal.BLOCK


def test_admin_decides_a_review(env):
    start_and_submit(env)
    env["fake"].status = "attention"
    webhook(env, "attention", MATCHING)
    check = IdentityCheck.query.one()
    listing = (
        env["client"].get("/admin/identity-checks", headers=env["admin_h"]).get_json()
    )
    assert (
        listing["checks"][0]["id"] == check.id
        and listing["checks"][0]["id_fields"]["last_name"] == "Mensah"
    )
    url = f"/admin/identity-checks/{check.id}/decide"
    assert (
        env["client"]
        .post(url, headers=env["admin_h"], json={"approve": True})
        .status_code
        == 400
    )  # no note
    res = env["client"].post(
        url,
        headers=env["admin_h"],
        json={"approve": True, "note": "Called customer, ID matches"},
    )
    assert res.status_code == 200
    assert User.query.get(env["customer"].id).kyc_status == "verified"


def test_attempts_are_limited(env):
    env["fake"].status = "error"
    for _ in range(3):
        check = start_and_submit(env)
        webhook(env, "error", None, job_id=check.job_id)
        IdentityCheck.query.filter(IdentityCheck.id != check.id).update(
            {"job_id": None}
        )
        check.job_id = None  # free the job id for the next fake attempt
        db.session.commit()
    res = env["client"].post(
        "/customer/identity/start", headers=env["cust_h"], json={"consent": True}
    )
    assert res.status_code == 400 and "attempts" in res.get_json()["error"]


def test_documents_alone_cant_verify_once_smile_id_is_on(env):
    res = env["client"].put(
        f"/admin/kyc/customer/approve/{env['customer'].id}", headers=env["admin_h"]
    )
    assert res.status_code == 409
    assert User.query.get(env["customer"].id).kyc_status == "pending"


def test_manual_kyc_still_works_without_smile_id(env):
    env["app"].config["SMILEID_API_KEY"] = None
    res = env["client"].put(
        f"/admin/kyc/customer/approve/{env['customer'].id}",
        headers=env["admin_h"],
        json={"note": "Checked Ghana Card in person"},
    )
    assert res.status_code == 200, res.get_json()
    assert (
        IdentityCheck.query.filter_by(provider="manual").one().status
        == IdentityCheck.CLEAR
    )


def test_verified_customer_cant_change_name_or_ghana_card(env):
    c = env["customer"]
    c.kyc_status = "verified"
    db.session.commit()
    res = env["client"].put(
        "/customer/profile", headers=env["cust_h"], json={"full_name": "Someone Else"}
    )
    assert res.status_code == 409
    res = env["client"].put(
        "/customer/underwriting",
        headers=env["cust_h"],
        json={"national_id": "GHA-999999999-9"},
    )
    assert res.status_code == 409


# ---------------------------------------------------------------- employment verification (§9B)


def buyer(env, **extra):
    fields = {
        "full_name": "Kwame Buyer",
        "kyc_status": "verified",
        "employment_verified_at": None,
        **extra,
    }
    c = make_user("customer", "0240000350", **fields)
    return c, token(env["client"], c.phone)


def buy(env, headers, n=4, qty=1):
    return env["client"].post(
        "/customer/purchase",
        headers=headers,
        json={
            "accept_terms": True,
            "product_id": env["product"].id,
            "number_of_installments": n,
            "quantity": qty,
            "delivery_address": "Osu, Accra",
        },
    )


def test_first_credit_purchase_needs_employment_verification(env):
    c, h = buyer(env)
    res = buy(env, h)
    assert (
        res.status_code == 403
        and res.get_json()["code"] == "employment_verification_required"
    )
    assert buy(env, h, n=1).status_code == 201  # paying in full uses no credit

    url = f"/admin/customers/{c.id}/employment-verification"
    assert (
        env["client"]
        .put(url, headers=env["admin_h"], json={"verified": True, "method": "fax"})
        .status_code
        == 400
    )
    res = env["client"].put(
        url,
        headers=env["admin_h"],
        json={
            "verified": True,
            "method": "employer_call",
            "note": "HR at GCB confirmed, 26 Sep",
        },
    )
    assert res.status_code == 200 and res.get_json()["employment_verified"]
    assert buy(env, h).status_code == 201


def test_high_ticket_needs_employment_verification_even_for_repeat_customers(env):
    # A repeat customer (they already have a plan) with no employment verification on file
    c, h = buyer(env)
    from app.models.instalment import InstalmentPlan

    db.session.add(
        InstalmentPlan(
            plan_id="PLN-OLD",
            merchant_id=env["merchant"].id,
            customer_id=c.id,
            plan_name="Old",
            total_amount=100,
            number_of_installments=4,
            installment_amount=0,
            start_date=datetime(2026, 1, 1),
            end_date=datetime(2026, 4, 1),
            status="completed",
        )
    )
    db.session.commit()
    assert (
        buy(env, h, qty=1).get_json().get("code") != "employment_verification_required"
    )  # GHS 4,000
    res = buy(env, h, qty=3)  # GHS 12,000 >= the GHS 10,000 high-ticket threshold (D6)
    assert (
        res.status_code == 403
        and res.get_json()["code"] == "employment_verification_required"
    )


# ---------------------------------------------------------------- fraud signals (§9D/E)


def test_duplicate_ghana_card_at_signup_blocks_both(env):
    res = env["client"].post(
        "/register",
        json={
            "phone": "0240000377",
            "password": "Secret123!",
            "role": "customer",
            "full_name": "Copy Cat",
            "national_id": "gha-123456789-0",
            "email": "copy@example.test",
        },
    )
    assert res.status_code == 201, res.get_json()
    new = User.query.filter_by(phone="0240000377").one()
    assert new.national_id == "GHA-123456789-0"
    codes = {(s.user_id, s.code, s.severity) for s in FraudSignal.query.all()}
    assert (new.id, "duplicate_ghana_card", "block") in codes
    assert (env["customer"].id, "duplicate_ghana_card", "block") in codes


def test_blocked_customer_cant_buy_until_cleared(env):
    c, h = buyer(env, employment_verified_at=datetime.utcnow())
    signal = fraud.raise_signal(c, "test_block", "Test", severity=FraudSignal.BLOCK)
    db.session.commit()
    res = buy(env, h)
    assert res.status_code == 403 and "reviewed" in res.get_json()["error"]
    url = f"/admin/fraud-signals/{signal.id}"
    assert (
        env["client"]
        .put(url, headers=env["admin_h"], json={"status": "cleared"})
        .status_code
        == 400
    )
    assert (
        env["client"]
        .put(
            url,
            headers=env["admin_h"],
            json={"status": "cleared", "note": "Same person, old account closed"},
        )
        .status_code
        == 200
    )
    assert buy(env, h).status_code == 201


def test_customer_buying_from_their_own_shop_is_stopped(env):
    c, h = buyer(
        env, employment_verified_at=datetime.utcnow(), momo_number="0200000302"
    )  # the merchant's phone
    res = buy(env, h)
    assert res.status_code == 403
    m = env["merchant"]
    assert (
        FraudSignal.query.filter_by(
            user_id=m.id, code="self_dealing", severity="block"
        ).count()
        == 1
    )
    assert settlements.hold_reason(m).startswith("Fraud review")


def test_shared_devices(env):
    hdr = {"X-Device-Id": "dev-abcdef123456"}
    for phone in ("0240000361", "0240000362", "0240000363"):
        make_user("customer", phone, full_name="Device User")
        env["client"].post(
            "/login", json={"phone": phone, "password": "Secret123!"}, headers=hdr
        )
    assert DeviceSeen.query.filter_by(device_id="dev-abcdef123456").count() == 3
    assert (
        FraudSignal.query.filter_by(code="shared_device").count() == 1
    )  # the 3rd account
    env["client"].post(
        "/login",
        json={"phone": env["merchant"].phone, "password": "Secret123!"},
        headers=hdr,
    )
    assert (
        FraudSignal.query.filter_by(
            user_id=env["merchant"].id, code="merchant_customer_same_device"
        ).count()
        == 3
    )
    env["client"].post(
        "/login",
        json={"phone": "0240000361", "password": "Secret123!"},
        headers={"X-Device-Id": "dev-bot000000001", "X-Device-Flags": "webdriver"},
    )
    assert FraudSignal.query.filter_by(code="automated_browser").count() == 1
    # A bad device id is ignored, and the login still works
    res = env["client"].post(
        "/login",
        json={"phone": "0240000361", "password": "Secret123!"},
        headers={"X-Device-Id": "<script>"},
    )
    assert res.status_code == 200


def test_order_with_block_flag_cant_be_approved(env):
    c, h = buyer(env, employment_verified_at=datetime.utcnow())
    assert buy(env, h).status_code == 201
    order = PurchaseOrder.query.filter_by(customer_id=c.id).one()
    listing = env["client"].get("/admin/orders", headers=env["admin_h"]).get_json()
    assert listing["orders"][0]["fraud_flags"] == []
    fraud.raise_signal(
        env["merchant"], "test_block", "Test", severity=FraudSignal.BLOCK
    )
    db.session.commit()
    listing = env["client"].get("/admin/orders", headers=env["admin_h"]).get_json()
    assert listing["orders"][0]["fraud_flags"][0]["code"] == "test_block"
    res = env["client"].put(
        f"/admin/orders/{order.id}/approve",
        headers=env["admin_h"],
        json={"down_payment_reference": "MOMO-DP-9"},
    )
    assert res.status_code == 409


def test_fraud_hold_stops_merchant_payouts(env):
    m = env["merchant"]
    m.payout_method, m.payout_bank_code, m.momo_name = (
        "mobile_money",
        "MTN",
        "Phone Hub",
    )
    fraud.raise_signal(m, "test_block", "Test", severity=FraudSignal.BLOCK)
    db.session.commit()
    c = make_user("customer", "0240000371", full_name="Some Buyer")
    from app.models.instalment import InstalmentPlan

    plan = InstalmentPlan(
        plan_id="PLN-X",
        merchant_id=m.id,
        customer_id=c.id,
        plan_name="Phone",
        total_amount=4000,
        number_of_installments=4,
        installment_amount=0,
        start_date=datetime(2026, 1, 1),
        end_date=datetime(2026, 4, 1),
        status="active",
    )
    db.session.add(plan)
    db.session.flush()
    db.session.add(
        SettlementLine(
            merchant_id=m.id,
            plan_id=plan.id,
            line_type="sale",
            gross_pesewas=400000,
            fee_pesewas=40000,
            net_pesewas=360000,
            description="t",
        )
    )
    db.session.commit()
    batch = settlements.generate_batches(datetime.utcnow().date() + timedelta(days=8))[
        0
    ]
    assert batch.status == Settlement.ON_HOLD and "Fraud" in batch.hold_reason
