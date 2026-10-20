"""Swagger/OpenAPI docs generated from the registered routes (app/docs.py)."""
import pytest

from app import create_app


@pytest.fixture()
def docs_app():
    app = create_app()
    app.config["TESTING"] = True
    return app


def test_spec_lists_every_resource_route(docs_app):
    res = docs_app.test_client().get("/swagger.json")
    assert res.status_code == 200
    spec = res.get_json()
    assert spec["openapi"].startswith("3.")
    rules = {
        r.rule
        for r in docs_app.url_map.iter_rules()
        if hasattr(docs_app.view_functions[r.endpoint], "view_class")
    }
    assert len(spec["paths"]) == len(
        {r.replace("<int:", "<").replace("<", "{").replace(">", "}") for r in rules}
    )


def test_path_params_and_security(docs_app):
    paths = docs_app.test_client().get("/swagger.json").get_json()["paths"]
    detail = paths["/admin/users/{user_id}"]["get"]
    assert detail["parameters"][0] == {
        "name": "user_id",
        "in": "path",
        "required": True,
        "schema": {"type": "integer"},
    }
    assert detail["security"] == [{"bearerAuth": []}]
    assert "security" not in paths["/login"]["post"]


def test_swagger_ui_served_when_enabled(docs_app):
    assert docs_app.test_client().get("/swagger-ui/").status_code == 200


def test_documented_endpoints_carry_body_and_responses(docs_app):
    paths = docs_app.test_client().get("/swagger.json").get_json()["paths"]
    login = paths["/login"]["post"]
    body = login["requestBody"]["content"]["application/json"]["schema"]
    assert body["required"] == ["phone", "password"]
    assert login["responses"]["200"]["content"]["application/json"]["schema"][
        "properties"
    ]["access_token"]
    assert login["responses"]["429"]["description"].startswith("Too many")
    signup = paths["/register"]["post"]
    assert signup["requestBody"]["content"]["application/json"]["schema"]["properties"][
        "role"
    ]["enum"]
    assert "409" in signup["responses"] and "201" in signup["responses"]
    for path in ("/forgot-password", "/api/verify-otp", "/reset-password"):
        assert paths[path]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]["required"]


def _spec(app):
    return app.test_client().get("/swagger.json").get_json()["paths"]


def test_money_and_onboarding_endpoints_are_documented(docs_app):
    paths = _spec(docs_app)
    body = lambda op: op["requestBody"]["content"]["application/json"]["schema"]

    assert body(paths["/customer/payments/paystack/initialize"]["post"])[
        "required"
    ] == ["plan_id"]
    assert "201" in paths["/customer/payments/paystack/initialize"]["post"]["responses"]
    assert body(paths["/customer/payments/make"]["post"])["properties"][
        "payment_method"
    ]["enum"]
    assert body(paths["/installment/calculate"]["post"])["required"] == [
        "product_price"
    ]
    assert "page" in {
        p["name"] for p in paths["/merchant/transactions"]["get"]["parameters"]
    }
    assert (
        "plans"
        in paths["/customer/instalments"]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]["properties"]
    )
    assert set(paths["/merchant/payment-links"]) == {"get", "post"}
    assert body(paths["/merchant/payout-account"]["put"])["properties"][
        "settlement_period_days"
    ]["enum"] == [3, 7, 30]
    assert body(paths["/admin/payment-claims/{claim_id}/reject"]["post"])[
        "required"
    ] == ["reason"]


def test_upload_and_download_content_types(docs_app):
    paths = _spec(docs_app)
    upload = paths["/merchant/documents/upload"]["post"]["requestBody"]["content"]
    assert list(upload) == ["multipart/form-data"]
    assert (
        upload["multipart/form-data"]["schema"]["properties"]["tax_document"]["format"]
        == "binary"
    )
    export = paths["/merchant/transactions/export"]["get"]["responses"]["200"][
        "content"
    ]
    assert list(export) == ["text/csv"]


def test_spec_is_valid_openapi(docs_app):
    validator = pytest.importorskip("openapi_spec_validator")
    validator.validate(docs_app.test_client().get("/swagger.json").get_json())


def test_undocumented_endpoints_are_read_from_the_code(docs_app):
    paths = _spec(docs_app)
    users = paths["/admin/users"]["get"]
    query = {p["name"]: p for p in users["parameters"]}
    assert {"page", "per_page", "search", "role", "status"} <= set(query)
    assert query["page"]["schema"] == {"type": "integer"}

    mark_paid = paths["/admin/instalments/payments/{payment_id}/mark-paid"]["put"]
    body = mark_paid["requestBody"]["content"]["application/json"]["schema"]
    assert {"amount_received", "payment_reference"} <= set(body["properties"])
    assert "Payment not found" in mark_paid["responses"]["404"]["description"]
    assert (
        "message"
        in mark_paid["responses"]["200"]["content"]["application/json"]["schema"][
            "properties"
        ]
    )


def test_hand_written_docs_are_not_overwritten_by_inference(docs_app):
    login = _spec(docs_app)["/login"]["post"]
    assert "description" in login and "read from the code" not in login["description"]
    assert login["responses"]["200"]["description"] == "Signed in"


def test_inference_survives_docstrings_that_break_dedent(docs_app):
    # These two handlers' docstrings/strings are indented less than the method, which used to leave
    # their source unparseable and the request body empty
    paths = _spec(docs_app)
    for path in ("/admin/kyc/reject/{merchant_id}", "/admin/kyc/document/reject/{document_id}"):
        body = paths[path]["put"]["requestBody"]["content"]["application/json"]["schema"]
        assert "rejection_reason" in body["properties"], path
