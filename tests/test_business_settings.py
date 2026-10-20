"""Business settings page: validated, audited changes that the app actually uses."""
import pathlib
import re

import pytest

from app import create_app
from app.extensions import db
from app.models.system_settings import SettingChange, SystemSetting
from app.services import business_settings as bs, economics

from tests.test_ledger import make_user, token

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"
CALL = re.compile(
    r'(?:get_value|settings_get|_setting)\(\s*["\']([a-z0-9_]+)["\']\s*,\s*([^)]+?)\s*\)'
)


@pytest.fixture()
def env():
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        db.drop_all()
        db.create_all()
        client = app.test_client()
        admin = make_user("admin", "0200000501", full_name="Ops Admin")
        customer = make_user("customer", "0200000503", kyc_status="verified")
        yield {
            "client": client,
            "admin": admin,
            "admin_h": token(client, admin.phone),
            "cust_h": token(client, customer.phone),
        }
        db.session.remove()
        db.drop_all()


def _literal(text):
    text = text.strip()
    if text in ("True", "False"):
        return text == "True"
    if text.startswith(("'", '"')):
        return text.strip("'\"")
    try:
        return float(text)
    except ValueError:
        return None  # a variable or expression: nothing to compare


def test_registry_defaults_match_the_code():
    """A default shown on the page must be the default the code really uses."""
    seen = {}
    for path in APP_DIR.rglob("*.py"):
        for key, default in CALL.findall(path.read_text(encoding="utf-8")):
            if key in bs.BY_KEY:
                seen.setdefault(key, set()).add(_literal(default))
    for key, defaults in seen.items():
        registry = bs.BY_KEY[key]["default"]
        for d in defaults - {None}:
            assert (float(d) if isinstance(d, float) else d) == (
                float(registry) if isinstance(d, float) else registry
            ), key
    for key, (default, _, _) in economics.RATES.items():
        assert bs.BY_KEY[key]["default"] == default, key
    assert (
        bs.BY_KEY["autopay_retry_days"]["default"]
        == __import__("app.services.autopay", fromlist=["x"]).DEFAULT_RETRY_DAYS
    )


def test_page_lists_every_group_with_current_values(env):
    body = (
        env["client"].get("/admin/business-settings", headers=env["admin_h"]).get_json()
    )
    groups = {g["key"]: g for g in body["groups"]}
    assert set(groups) == {k for k, _ in bs.GROUPS}
    late = {s["key"]: s for s in groups["late_fees"]["settings"]}
    assert (
        late["late_fee_percentage"]["value"] == 10
        and late["late_fee_percentage"]["is_default"]
    )
    assert "existing plans" in late["late_fee_percentage"]["applies"]
    assert (
        env["client"].get("/admin/business-settings", headers=env["cust_h"]).status_code
        == 403
    )


def test_invalid_changes_save_nothing(env):
    c, h = env["client"], env["admin_h"]
    res = c.put(
        "/admin/business-settings",
        headers=h,
        json={
            "changes": {
                "late_fee_percentage": 80,
                "merchant_fee_percentage": "ten",
                "made_up_key": 1,
                "deferment_months": 1.5,
                "deferment_enabled": "yes",
            },
            "reason": "x",
        },
    )
    errors = res.get_json()["errors"]
    assert res.status_code == 400
    assert set(errors) == {
        "late_fee_percentage",
        "merchant_fee_percentage",
        "made_up_key",
        "deferment_months",
        "deferment_enabled",
        "reason",
    }
    assert SystemSetting.query.count() == 0 and SettingChange.query.count() == 0


def test_cap_cant_be_below_the_late_fee(env):
    res = env["client"].put(
        "/admin/business-settings",
        headers=env["admin_h"],
        json={
            "changes": {"late_fee_cap_percentage": 5},
            "reason": "Testing the cap rule",
        },
    )
    assert (
        res.status_code == 400 and "late_fee_cap_percentage" in res.get_json()["errors"]
    )


def test_changes_are_saved_audited_and_used(env):
    c, h = env["client"], env["admin_h"]
    res = c.put(
        "/admin/business-settings",
        headers=h,
        json={
            "changes": {
                "merchant_fee_percentage": 8,
                "deferment_enabled": False,
                "autopay_retry_days": [5, 0, 2, 2],
                "late_fee_percentage": 10,
            },
            "reason": "Premium merchant pilot, founder email 27 Sep",
        },
    )
    assert res.status_code == 200, res.get_json()
    saved = {s["key"]: s for s in res.get_json()["saved"]}
    assert set(saved) == {
        "merchant_fee_percentage",
        "deferment_enabled",
        "autopay_retry_days",
    }  # unchanged skipped
    assert (
        saved["merchant_fee_percentage"]["old"] == 10
        and saved["merchant_fee_percentage"]["new"] == 8
    )
    # The rest of the app reads the new values
    assert SystemSetting.get_value("merchant_fee_percentage", 10) == 8
    assert SystemSetting.get_value("deferment_enabled", True) is False
    assert SystemSetting.get_value("autopay_retry_days", None) == [0, 2, 5]
    quote = c.post(
        "/installment/calculate",
        headers=env["cust_h"],
        json={"product_price": 4000, "number_of_installments": 4},
    ).get_json()
    assert "merchant" not in quote  # customers never see merchant figures
    admin_quote = c.post(
        "/installment/calculate",
        headers=h,
        json={"product_price": 4000, "number_of_installments": 4},
    ).get_json()
    assert admin_quote["merchant"]["fee_amount"] == 320
    # Audit trail
    hist = c.get("/admin/business-settings/history", headers=h).get_json()["changes"]
    assert len(hist) == 3 and all(x["by"] == "Ops Admin" for x in hist)
    assert hist[0]["reason"].startswith("Premium merchant pilot")
    row = {
        s["key"]: s
        for g in c.get("/admin/business-settings", headers=h).get_json()["groups"]
        for s in g["settings"]
    }["merchant_fee_percentage"]
    assert (
        row["value"] == 8 and not row["is_default"] and row["updated_by"] == "Ops Admin"
    )


def test_old_unvalidated_endpoints_are_retired(env):
    c, h = env["client"], env["admin_h"]
    assert (
        c.put(
            "/admin/settings/charges", headers=h, json={"late_fee_grace_period_days": 3}
        ).status_code
        == 410
    )
    assert (
        c.put(
            "/admin/settings/installments", headers=h, json={"installment_options": []}
        ).status_code
        == 410
    )
    assert SystemSetting.query.count() == 0
