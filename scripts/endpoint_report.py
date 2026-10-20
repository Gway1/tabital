#!/usr/bin/env python
"""Call every API endpoint against freshly seeded data and write an Excel report.

    python scripts/endpoint_report.py [--out reports/run.xlsx] [--customers 12] [--seed 42] [--only /admin]

It never touches your real database: a throwaway SQLite file is created, seeded with `flask seed-data`,
and deleted afterwards (--keep-db to look at it). Outbound calls (Paystack, SMS, SMTP) are blocked.
Files the merchant upload endpoints write under uploads/ are removed again.

Each documented operation is called as the role its path implies (/admin as management admin, /merchant
as a merchant, /customer as a customer; other paths try each role). GETs run first, then POST/PUT/PATCH,
then DELETE. A second pass checks access control: every secured endpoint without a token, and every
/admin, /merchant and /customer endpoint with a role that does not belong there.

Outcomes: PASS (2xx/3xx), REJECTED (400/409/410/422... the endpoint answered and refused the sample
input), NOT FOUND (404), DENIED (401/403 for the role used) and FAIL (5xx or an exception).
Exit code is 1 when there is any FAIL or access-control breach.
"""
import argparse
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import traceback
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, REJECTED, RETIRED, UNAVAILABLE, NOT_FOUND, DENIED, FAIL = (
    "PASS",
    "REJECTED",
    "RETIRED",
    "UNAVAILABLE",
    "NOT FOUND",
    "DENIED",
    "FAIL",
)
OUTCOMES = (PASS, REJECTED, RETIRED, UNAVAILABLE, NOT_FOUND, DENIED, FAIL)
ORDER = ("get", "post", "put", "patch", "delete")
# Run first within their method: later calls (a document upload puts KYC back to pending) would
# make them fail for a reason that has nothing to do with the endpoint
FIRST = {"/customer/purchase"}
ROLE_BY_PREFIX = {
    "admin": ["admin"],
    "merchant": ["merchant"],
    "customer": ["customer"],
}
WRONG_ROLE = {"admin": "customer", "merchant": "customer", "customer": "merchant"}
# Open to any signed-in user although the path suggests otherwise (checked by hand, shown as SHARED)
SHARED = {
    "/admin/get_current_user": "returns the caller's own profile",
    "/customer/payments/config": "only says whether online payment is switched on",
}


# ---------------------------------------------------------------- environment


def isolate(db_file):
    """Point the app at a throwaway database and switch off every outbound integration. Must run
    before `app` is imported: Config reads the environment at import time."""
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{db_file}",
            "SECRET_KEY": os.environ.get("SECRET_KEY")
            or "endpoint-report-secret-key-0123456789abcdef",
            "FLASK_DEBUG": "0",
            "PAYSTACK_SECRET_KEY": "",
            "PAYSTACK_PUBLIC_KEY": "",
            "SMS_PROVIDER": "log",
            "MNOTIFY_API_KEY": "",
            "SMILEID_PARTNER_ID": "",
            "SMILEID_API_KEY": "",
            "MAIL_USERNAME": "",
            "MAIL_PASSWORD": "",
            "LOGIN_MAX_ACCOUNTS_PER_IP": "100000",
            "LOGIN_MAX_FAILURES": "100000",
            "SIGNUP_MAX_FAILURES_PER_IP": "100000",
            "OTP_MAX_ACCOUNTS_PER_IP": "100000",
            "OTP_MAX_FAILURES_PER_DAY": "100000",
            "RESET_REQUESTS_PER_IP_PER_HOUR": "100000",
            "RESET_CODES_PER_ACCOUNT_PER_DAY": "100000",
        }
    )


def block_network():
    import requests
    import smtplib

    def refuse(*args, **kwargs):
        raise requests.ConnectionError("network is disabled during the endpoint report")

    requests.sessions.Session.request = refuse
    smtplib.SMTP = smtplib.SMTP_SSL = refuse


class ErrorCapture(logging.Handler):
    """Remembers the last server-side exception so a 500 can be explained in the report."""

    def __init__(self):
        super().__init__(logging.ERROR)
        self.last = None

    def emit(self, record):
        if record.exc_info:
            etype, error, tb = record.exc_info
            frames = [
                f
                for f in traceback.extract_tb(tb)
                if "/app/" in f.filename.replace("\\", "/")
            ]
            where = (
                f" at {Path(frames[-1].filename).name}:{frames[-1].lineno}"
                if frames
                else ""
            )
            self.last = f"{etype.__name__}: {error}{where}"[:500]


# ---------------------------------------------------------------- test data


def pick_actors():
    from app.models.instalment import InstalmentPlan
    from app.models.user import User
    from app.seed import seed_phone, MERCHANT_AT, CUSTOMER_AT, ADMIN_AT
    from app.extensions import db

    admin = User.query.filter_by(phone=seed_phone(ADMIN_AT)).first()
    ops = User.query.filter_by(phone=seed_phone(ADMIN_AT + 1)).first()
    merchants = User.query.filter_by(role="merchant").order_by(User.id).all()
    customers = [
        c
        for c in User.query.filter_by(role="customer", status="approved").order_by(
            User.id
        )
        if c.phone >= seed_phone(CUSTOMER_AT)
    ]

    def plans_of(column, user_id):
        return InstalmentPlan.query.filter(column == user_id).count()

    from app.models.payment_method import PaymentMethod
    from app.models.support_ticket import SupportTicket

    def records_of(c):  # plans, tickets and saved cards the seed gave this customer
        return (
            plans_of(InstalmentPlan.customer_id, c.id)
            + SupportTicket.query.filter_by(user_id=c.id).count()
            + PaymentMethod.query.filter_by(customer_id=c.id).count()
        )

    customer = max(customers, key=records_of)
    merchant = max(merchants, key=lambda m: plans_of(InstalmentPlan.merchant_id, m.id))
    spare_customer = next(  # an approved customer, so status changes behave as for a real account
        (c for c in reversed(customers) if c.id != customer.id and c.kyc_status == "verified"),
        customer,
    )
    spare_merchant = next((m for m in merchants if m.id != merchant.id), merchant)
    return {
        "admin": admin,
        "ops": ops,
        "merchant": merchant,
        "customer": customer,
        "spare_customer": spare_customer,
        "spare_merchant": spare_merchant,
    }


def build_ids(actors):
    """Values for path parameters. User ids point at spare accounts so a delete or suspend can't
    remove the account that is making the calls."""
    from app.models.dispute import Dispute
    from app.models.document import Document
    from app.models.identity import FraudSignal, IdentityCheck
    from app.models.instalment import InstalmentPlan
    from app.models.instalment_payment import InstalmentPayment
    from app.models.notifications import Notifications
    from app.models.payment_claim import PaymentClaim
    from app.models.payment_method import PaymentMethod
    from app.models.product import Product
    from app.models.purchase_order import PurchaseOrder
    from app.models.settlement import PaymentLink, Settlement
    from app.models.support_ticket import SupportTicket
    from app.models.transaction import Transaction

    customer, merchant = actors["customer"], actors["merchant"]
    plan = (
        InstalmentPlan.query.filter_by(customer_id=customer.id)
        .order_by(InstalmentPlan.id)
        .first()
    )
    payment = (
        (
            InstalmentPayment.query.filter(
                InstalmentPayment.plan_id == plan.id, InstalmentPayment.status != "paid"
            )
            .order_by(InstalmentPayment.installment_number.desc())
            .first()
        )
        if plan
        else None
    )
    # Prefer a plan that still has something to pay, so the payment endpoints get a real instalment
    unpaid = (
        InstalmentPayment.query.join(
            InstalmentPlan, InstalmentPlan.id == InstalmentPayment.plan_id
        )
        .filter(
            InstalmentPlan.customer_id == customer.id, InstalmentPayment.status != "paid"
        )
        .order_by(InstalmentPayment.installment_number.desc(), InstalmentPayment.id)
        .first()
    )
    if unpaid:
        payment, plan = unpaid, InstalmentPlan.query.get(unpaid.plan_id)
    first = lambda model, **f: (
        model.query.filter_by(**f).order_by(model.id).first() or None
    )
    pick = lambda row, attr="id": getattr(row, attr, None) if row else None

    order = (
        first(PurchaseOrder, merchant_id=merchant.id, status="approved")
        or first(PurchaseOrder, merchant_id=merchant.id)
        or first(PurchaseOrder)
    )
    ids = {
        "user_id": actors["spare_customer"].id,
        "customer_id": actors["spare_customer"].id,
        "merchant_id": actors["spare_merchant"].id,
        "admin_id": actors["ops"].id,
        "plan_id": pick(plan),
        "payment_id": pick(payment),
        "order_id": pick(order),
        "transaction_id": pick(first(Transaction, customer_id=customer.id)),
        "product_id": pick(first(Product, merchant_id=merchant.id)),
        "claim_id": pick(first(PaymentClaim, status="pending") or first(PaymentClaim)),
        "dispute_id": pick(first(Dispute)),
        "document_id": pick(first(Document)),
        "link_id": pick(first(PaymentLink)),
        "method_id": pick(first(PaymentMethod)),
        "notification_id": pick(first(Notifications, user_id=customer.id)),
        "ticket_id": pick(first(SupportTicket)),
        "signal_id": pick(first(FraudSignal)),
        "check_id": pick(first(IdentityCheck)),
        "batch_id": pick(first(Settlement)),
        "settlement_id": pick(first(Settlement)),  # admin routes take the row id
        "token": pick(first(PaymentLink), "token") or "unknown-token",
        "reference": "SEED-UNKNOWN",
        "filename": "missing.pdf",
        "path": "missing.pdf",
    }
    ids = {k: (v if v is not None else 999999) for k, v in ids.items()}
    # The merchant settlement id is a week, "YYYY-WW" (Monday-start), as /merchant/settlements lists them
    latest = (
        Transaction.query.filter_by(merchant_id=merchant.id, status="completed")
        .order_by(Transaction.completion_date.desc())
        .first()
    )
    when = latest.completion_date if latest and latest.completion_date else datetime.now()
    ids["merchant_settlement_id"] = when.strftime("%Y-%W")

    # Records in the state an endpoint needs. Two of each where two endpoints would use one up
    # (confirm and reject the same claim; approve and reject the same order)
    def nth(rows, n):
        return (rows[n] if len(rows) > n else rows[0]).id if rows else 999999

    claims = PaymentClaim.query.filter_by(status="pending").order_by(PaymentClaim.id).all()
    orders = PurchaseOrder.query.filter_by(status="pending").order_by(PurchaseOrder.id).all()
    overdue = (
        InstalmentPayment.query.filter(
            InstalmentPayment.status == "overdue", InstalmentPayment.late_fee > 0
        ).first()
        or InstalmentPayment.query.filter_by(status="overdue").first()
    )
    deferrable = next(
        (
            p
            for p in InstalmentPlan.query.filter_by(
                customer_id=customer.id, status="active"
            ).order_by(InstalmentPlan.id)
            if not p.paused_at
            and not Dispute.query.filter_by(transaction_id=p.transaction_id).first()
            and InstalmentPayment.query.filter(
                InstalmentPayment.plan_id == p.id,
                InstalmentPayment.status == "pending",
                InstalmentPayment.installment_number > 1,
            ).first()
        ),
        plan,
    )
    ids.update(
        pending_claim_1=nth(claims, 0),
        pending_claim_2=nth(claims, 1),
        pending_order_1=nth(orders, 0),
        pending_order_2=nth(orders, 1),
        overdue_payment=pick(overdue) or ids["payment_id"],
        deferment_plan=pick(deferrable) or ids["plan_id"],
    )

    # Records each role owns: a customer can't read another customer's ticket, a merchant can't
    # edit someone else's payment link
    merchant_sale = first(Transaction, merchant_id=merchant.id)
    owned = {
        "customer": {
            "ticket_id": pick(first(SupportTicket, user_id=customer.id)),
            "method_id": pick(first(PaymentMethod, customer_id=customer.id)),
            "notification_id": pick(first(Notifications, user_id=customer.id)),
            "dispute_id": pick(first(Dispute, customer_id=customer.id)),
            "document_id": pick(first(Document, user_id=customer.id)),
            "transaction_id": pick(first(Transaction, customer_id=customer.id)),
            "order_id": pick(first(PurchaseOrder, customer_id=customer.id)),
        },
        "merchant": {
            "transaction_id": pick(merchant_sale),
            "customer_id": pick(merchant_sale, "customer_id"),
            "link_id": pick(first(PaymentLink, merchant_id=merchant.id)),
            "notification_id": pick(first(Notifications, user_id=merchant.id)),
            "dispute_id": pick(first(Dispute, merchant_id=merchant.id)),
            "document_id": pick(first(Document, user_id=merchant.id)),
            "batch_id": pick(first(Settlement, merchant_id=merchant.id)),
            "order_id": pick(order),
        },
    }
    ids["_owned"] = {
        role: {k: v for k, v in rows.items() if v is not None}
        for role, rows in owned.items()
    }
    return ids


NAME_HINTS = [  # (substring of the field name, sample value)
    ("email", "tester@example.com"),
    ("phone", "0241234567"),
    ("momo_number", "0241234567"),
    ("password", "Secret123!"),
    ("otp", "123456"),
    ("reason", "Endpoint report sample"),
    ("note", "Endpoint report sample"),
    ("description", "Endpoint report sample"),
    ("message", "Endpoint report sample"),
    ("comment", "Endpoint report sample"),
    ("title", "Endpoint report sample"),
    ("name", "Endpoint Tester"),
    ("address", "Osu, Accra"),
    ("reference", "SEED-REPORT-0001"),
    ("date", date.today().isoformat()),
    ("percentage", 10),
    ("amount", 100),
    ("price", 100),
    ("fee", 5),
    ("rate", 5),
    ("salary", 5000),
    ("quantity", 1),
    ("installments", 4),
    ("days", 7),
    ("months", 1),
    ("limit", 5),
    ("page", 1),
]


OMIT = object()  # leave the field out of the request
FIELD_OVERRIDES = {  # (path, field) -> value the handler accepts; the generic hints can't know these
    ("/admin/collection/{payment_id}/reminder", "reminder_type"): "sms",
    ("/admin/collection/{payment_id}/mark-received", "payment_method"): "mobile_money",
    ("/admin/pii/reveal", "field"): "momo_number",
    ("/customer/disputes", "reason"): "defective",
    ("/admin/customers/{customer_id}/employment-verification", "method"): "employer_call",
    ("/admin/customers/{customer_id}/status", "status"): "active",
    ("/admin/customers/{customer_id}", "status"): OMIT,  # status changes have their own endpoints
    ("/admin/disputes/{dispute_id}/resolve", "outcome"): "merchant_won",
    ("/admin/fraud-signals/{signal_id}", "status"): "cleared",
    ("/admin/merchants/{merchant_id}/fee-tier", "fee_tier"): "standard",
    ("/admin/products/{product_id}/status", "status"): "active",
    ("/admin/team/{admin_id}", "admin_level"): "operations",
    ("/merchant/quick-actions", "action"): "contact_support",
    ("/merchant/bank-details", "account_number"): "1234567890",
    ("/merchant/payout-account", "account_number"): "1234567890",
    ("/register", "national_id"): "GHA-123456789-1",
    ("/admin/business-settings", "changes"): {"late_fee_percentage": 12},
    ("/customer/profile", "full_name"): OMIT,  # verified against the Ghana Card
    ("/merchant/disputes/{dispute_id}", "status"): OMIT,  # only Tabital resolves disputes
    ("/customer/purchase", "payment_link"): OMIT,
}
# Multipart uploads whose required files the handler checks by name in a loop (not found by reading it)
EXTRA_FILES = {
    "/customer/documents/upload": ["front_image", "back_image", "salary_certificate", "bank_statement"],
}
# Fields a handler reads in a loop over a list, so reading its code can't find them. The phone is the
# one /register creates earlier in the run, so these also check that a clash answers 409, not 500
EXTRA_FIELDS = {
    "/admin/customers/{customer_id}": {"phone": "0241234567"},
    "/admin/merchants/{merchant_id}": {"phone": "0241234567", "business_email": "tester@example.com"},
}
_reference_counter = iter(range(1, 1_000_000))


def sample_value(name, schema, ids, path=""):
    lowered = name.lower()
    if (path, name) in FIELD_OVERRIDES:
        return FIELD_OVERRIDES[(path, name)]
    if "reference" in lowered:  # a reference may only be recorded once
        return f"SEED-REPORT-{next(_reference_counter):05d}"
    if lowered in ids:
        return ids[lowered]
    if schema.get("enum"):
        return schema["enum"][0]
    if lowered == "action":
        return "verify"
    if lowered in ("status", "delivery_status"):
        return "approved"
    if lowered.startswith(("is_", "has_", "can_", "enable")) or lowered in (
        "agree",
        "accept_terms",
    ):
        return True
    for hint, value in NAME_HINTS:
        if hint in lowered:
            return value
    kind = schema.get("type")
    return {
        "integer": 1,
        "number": 100,
        "boolean": True,
        "array": [],
        "object": {},
    }.get(kind, "test")


def build_request(operation, ids, path=""):
    """(json body | None, form/files dict | None, query dict) for one operation."""
    body = form = None
    content = (operation.get("requestBody") or {}).get("content", {})
    if "application/json" in content:
        props = (content["application/json"].get("schema") or {}).get("properties", {})
        body = {
            name: value
            for name, schema in props.items()
            if (value := sample_value(name, schema or {}, ids, path)) is not OMIT
        }
        body = {**body, **EXTRA_FIELDS.get(path, {})}
    elif "multipart/form-data" in content:
        props = content["multipart/form-data"]["schema"].get("properties", {})
        form = {}
        for name, schema in props.items():
            form[name] = (
                (io.BytesIO(b"%PDF-1.4 endpoint report"), "report.pdf")
                if schema.get("format") == "binary"
                else str(sample_value(name, schema or {}, ids, path))
            )
    if path in EXTRA_FILES:
        form = {
            **(form or {}),
            **{f: (io.BytesIO(b"%PDF-1.4 endpoint report"), "report.pdf") for f in EXTRA_FILES[path]},
        }
    query = {
        p["name"]: sample_value(p["name"], p.get("schema", {}), ids, path)
        for p in operation.get("parameters", [])
        if p["in"] == "query" and p["name"] in ("page", "limit")
    }
    return body, form, query


# Where one id won't do: an endpoint that needs a record in a particular state, or a second one
# when the first endpoint on the same record would already have used it up
PATH_IDS = {
    "/admin/payment-claims/{claim_id}/confirm": {"claim_id": "pending_claim_1"},
    "/admin/payment-claims/{claim_id}/reject": {"claim_id": "pending_claim_2"},
    "/admin/orders/{order_id}/approve": {"order_id": "pending_order_1"},
    "/admin/orders/{order_id}/reject": {"order_id": "pending_order_2"},
    "/customer/plans/{plan_id}/deferment": {"plan_id": "deferment_plan"},
}
for _p in (
    "/admin/instalments/payments/{payment_id}/apply-late-fee",
    "/admin/instalments/payments/{payment_id}/waive-late-fee",
    "/admin/collection/overdue/{payment_id}",
    "/admin/collection/{payment_id}/reminder",
    "/admin/collection/{payment_id}/mark-received",
    "/admin/collection/{payment_id}/payment-plan",
):
    PATH_IDS[_p] = {"payment_id": "overdue_payment"}


def fill_path(path, ids):
    def value(name):
        if name == "settlement_id" and area_of(path) == "merchant":
            return ids["merchant_settlement_id"]
        if name in PATH_IDS.get(path, {}):
            return ids[PATH_IDS[path][name]]
        # Records the signed-in role owns (its tickets, cards, links...); /notifications and the
        # other shared paths are called as a customer first
        area = area_of(path) if area_of(path) in ("customer", "merchant") else "customer"
        if name in ids["_owned"][area]:
            return ids["_owned"][area][name]
        return ids.get(name, 1)

    return re.sub(r"\{([^}]+)\}", lambda m: str(value(m.group(1))), path)


# ---------------------------------------------------------------- calling


class Runner:
    def __init__(self, app, actors):
        from app.extensions import db, guard

        self.app, self.capture, self.db = app, ErrorCapture(), db
        app.logger.handlers.clear()  # keep the console readable; failures go in the report
        app.logger.propagate = False
        app.logger.addHandler(self.capture)
        self.client = app.test_client()
        self.tokens = {
            "admin": guard.encode_jwt_token(actors["admin"]),
            "merchant": guard.encode_jwt_token(actors["merchant"]),
            "customer": guard.encode_jwt_token(actors["customer"]),
        }

    def call(self, method, url, role, body=None, form=None):
        headers = {"Authorization": f"Bearer {self.tokens[role]}"} if role else {}
        kwargs = {"headers": headers}
        if form is not None:
            kwargs.update(data=form, content_type="multipart/form-data")
        elif method in ("post", "put", "patch"):
            kwargs["json"] = body if body is not None else {}
        self.capture.last = None
        self.deliberate = False
        started = time.perf_counter()
        try:
            res = getattr(self.client, method)(url, **kwargs)
            status, error = res.status_code, None
            text = (
                res.get_data()
                .decode("utf-8", errors="replace")[:300]
                .replace("\n", " ")
            )
            payload = res.get_json(silent=True)
            self.deliberate = (
                status in (502, 503)
                and isinstance(payload, dict)
                and "error" in payload
            )
        except Exception as e:  # noqa: BLE001
            status, text, error = 0, "", f"{type(e).__name__}: {e}"[:500]
        elapsed = (time.perf_counter() - started) * 1000
        self.db.session.remove()  # as at the end of a real request: one failed write must not poison the next call
        if status >= 500 and not error and not self.deliberate:
            error = self.capture.last or "Server error (no traceback captured)"
        return status, text, error, elapsed


def classify(status, error, secured=True, deliberate=False):
    """deliberate: a 502/503 whose body is a JSON {"error": ...}, which the app sends on purpose when
    an integration (Paystack, SMS...) isn't configured. That is not a crash."""
    if error and not deliberate or status == 0:
        return FAIL
    if status >= 500:
        return UNAVAILABLE if deliberate else FAIL
    if status < 400:
        return PASS
    if status == 410:
        return RETIRED  # switched off on purpose; the body says what replaced it
    if status == 401 and not secured:
        return REJECTED  # login, OTP and webhook endpoints refuse bad credentials
    if status in (401, 403):
        return DENIED
    if status == 404:
        return NOT_FOUND
    return REJECTED


def area_of(path):
    first = path.strip("/").split("/")[0]
    return first if first in ROLE_BY_PREFIX else "other"


def roles_for(path, secured):
    if not secured:
        return [None]
    area = area_of(path)
    return ROLE_BY_PREFIX.get(area) or ["customer", "merchant", "admin"]


def run_endpoints(runner, spec, ids, only):
    results = []
    operations = [
        (path, method, op)
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if not only or path.startswith(only)
    ]
    operations.sort(
        key=lambda o: (
            ORDER.index(o[1]) if o[1] in ORDER else 9,
            o[0] not in FIRST,
            o[0].endswith(("clear-all", "mark-all-read")),  # after the ones that name a record
            o[0],
        )
    )
    for path, method, op in operations:
        secured = bool(op.get("security"))
        tried = None
        for role in roles_for(path, secured):
            body, form, query = build_request(
                op, ids, path
            )  # fresh file objects for every call
            url = fill_path(path, ids) + (
                "?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else ""
            )
            status, text, error, elapsed = runner.call(method, url, role, body, form)
            tried = role
            if status not in (401, 403):
                break
        outcome = classify(status, error, secured, runner.deliberate)
        documented = str(status) in op.get("responses", {}) if status else False
        results.append(
            {
                "area": area_of(path),
                "method": method.upper(),
                "path": path,
                "url": url,
                "role": tried or "none (public)",
                "secured": secured,
                "status": status,
                "outcome": outcome,
                "ms": round(elapsed, 1),
                "documented": "yes" if documented else "no",
                "request": json.dumps(
                    body
                    if body is not None
                    else {
                        k: "<file>" if isinstance(v, tuple) else v
                        for k, v in (form or {}).items()
                    },
                    default=str,
                )[:500]
                if (body is not None or form)
                else "",
                "response": text,
                "error": error or "",
                "summary": op.get("summary", ""),
            }
        )
    return results


def run_access_control(runner, spec, ids):
    checks = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if not op.get("security"):
                continue
            url = fill_path(path, ids)
            cases = [("no token", None, {401})]
            area = area_of(path)
            if area in WRONG_ROLE:
                cases.append(
                    (
                        f"{WRONG_ROLE[area]} token on {area} endpoint",
                        WRONG_ROLE[area],
                        {401, 403},
                    )
                )
            for label, role, expected in cases:
                body, form, _ = build_request(op, ids, path)
                status, text, error, _ = runner.call(method, url, role, body, form)
                if status in expected:
                    result = "OK"
                elif role and path in SHARED:
                    result = "SHARED"
                elif error or status == 0 or status >= 500:
                    result = "ERROR"
                elif status < 400:
                    result = "BREACH"
                else:
                    result = "NOTE"  # refused (400/404...) before the role check; nothing ran
                checks.append(
                    {
                        "check": label,
                        "method": method.upper(),
                        "path": path,
                        "expected": " or ".join(map(str, sorted(expected))),
                        "status": status,
                        "result": result,
                        "response": text,
                        "error": error or "",
                    }
                )
    return checks


# ---------------------------------------------------------------- excel


def write_workbook(path, results, checks, info):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.utils import get_column_letter

    fills = {
        PASS: "C6EFCE",
        REJECTED: "FFEB9C",
        RETIRED: "D9D9D9",
        UNAVAILABLE: "E4DFEC",
        NOT_FOUND: "DDEBF7",
        DENIED: "F8CBAD",
        FAIL: "FF9999",
        "OK": "C6EFCE",
        "BREACH": "FF9999",
        "ERROR": "FF9999",
        "NOTE": "FFEB9C",
        "SHARED": "DDEBF7",
    }
    head_fill, head_font = PatternFill("solid", fgColor="1F3864"), Font(
        bold=True, color="FFFFFF"
    )

    def table(ws, columns, rows, widths, colour_column=None):
        ws.append(columns)
        for cell in ws[1]:
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for row in rows:  # downloads can put control characters in a response preview
            ws.append(
                [
                    ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v
                    for v in row
                ]
            )
        for i, width in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = width
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            if colour_column is not None:
                colour = fills.get(row[colour_column].value)
                if colour:
                    row[colour_column].fill = PatternFill("solid", fgColor=colour)
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"

    counts = {
        o: sum(r["outcome"] == o for r in results)
        for o in OUTCOMES
    }
    breaches = [c for c in checks if c["result"] in ("BREACH", "ERROR")]
    summary.append(["Tabital API endpoint report"])
    summary["A1"].font = Font(bold=True, size=16)
    for label, value in [
        ("Run at", info["started"]),
        ("Duration (s)", info["seconds"]),
        ("Endpoints called", len(results)),
        ("Database", "throwaway SQLite, seeded with `flask seed-data`"),
        ("Seeded", info["seeded"]),
        ("Access-control checks", len(checks)),
        ("Access-control breaches / errors", len(breaches)),
        ("Shared on purpose (reviewed)", sum(c["result"] == "SHARED" for c in checks)),
        (
            "Path parameters without a real record (used 999999)",
            info["missing_ids"] or "none",
        ),
    ]:
        summary.append([label, value])
    summary.append([])
    summary.append(["Outcome", "Endpoints", "Meaning"])
    for cell in summary[summary.max_row]:
        cell.fill, cell.font = head_fill, head_font
    meaning = {
        PASS: "2xx/3xx: worked with the sample input",
        REJECTED: "400/409/422...: answered and refused the sample input or the record's state (reachable)",
        RETIRED: "410: switched off on purpose; the response names what replaced it",
        UNAVAILABLE: "502/503: an integration (Paystack, Smile ID...) isn't configured in this run",
        NOT_FOUND: "404: no record matched the sample id",
        DENIED: "401/403 for every role tried",
        FAIL: "5xx or exception: a bug to look at",
    }
    for outcome, n in counts.items():
        summary.append([outcome, n, meaning[outcome]])
        summary.cell(summary.max_row, 1).fill = PatternFill(
            "solid", fgColor=fills[outcome]
        )
    summary.append([])
    summary.append(
        ["Area", "Endpoints", *OUTCOMES, "Average ms"]
    )
    for cell in summary[summary.max_row]:
        cell.fill, cell.font = head_fill, head_font
    for area in sorted({r["area"] for r in results}):
        rows = [r for r in results if r["area"] == area]
        summary.append(
            [area, len(rows)]
            + [sum(r["outcome"] == o for r in rows) for o in counts]
            + [round(sum(r["ms"] for r in rows) / len(rows), 1)]
        )
    summary.append([])
    summary.append(["Slowest endpoints", "ms"])
    for cell in summary[summary.max_row]:
        cell.fill, cell.font = head_fill, head_font
    for r in sorted(results, key=lambda r: -r["ms"])[:10]:
        summary.append([f"{r['method']} {r['path']}", r["ms"]])
    summary.column_dimensions["A"].width = 52
    summary.column_dimensions["B"].width = 36
    summary.column_dimensions["C"].width = 70
    for col in "DEFGH":
        summary.column_dimensions[col].width = 12

    table(
        wb.create_sheet("Results"),
        [
            "Area",
            "Method",
            "Path",
            "URL called",
            "Role",
            "Secured",
            "Status",
            "Outcome",
            "ms",
            "Status in Swagger",
            "Summary",
            "Request body",
            "Response (first 300 chars)",
            "Server error",
        ],
        [
            [
                r["area"],
                r["method"],
                r["path"],
                r["url"],
                r["role"],
                "yes" if r["secured"] else "no",
                r["status"],
                r["outcome"],
                r["ms"],
                r["documented"],
                r["summary"],
                r["request"],
                r["response"],
                r["error"],
            ]
            for r in results
        ],
        [10, 8, 46, 46, 14, 9, 8, 12, 8, 10, 40, 40, 60, 50],
        colour_column=7,
    )

    issues = [
        [
            r["outcome"],
            r["method"],
            r["path"],
            r["role"],
            r["status"],
            r["error"] or r["response"],
            r["request"],
        ]
        for r in results
        if r["outcome"] in (FAIL, DENIED)
    ]
    issues += [
        [
            c["result"],
            c["method"],
            c["path"],
            c["check"],
            c["status"],
            c["error"] or c["response"] or SHARED.get(c["path"], ""),
            "",
        ]
        for c in checks
        if c["result"] in ("BREACH", "ERROR", "SHARED")
    ]
    table(
        wb.create_sheet("Issues"),
        ["Type", "Method", "Path", "Role / check", "Status", "Detail", "Request body"],
        issues,
        [12, 8, 46, 34, 8, 80, 40],
        colour_column=0,
    )

    table(
        wb.create_sheet("Access control"),
        [
            "Check",
            "Method",
            "Path",
            "Expected",
            "Got",
            "Result",
            "Response (first 300 chars)",
        ],
        [
            [
                c["check"],
                c["method"],
                c["path"],
                c["expected"],
                c["status"],
                c["result"],
                c["response"],
            ]
            for c in checks
        ],
        [38, 8, 46, 10, 8, 12, 70],
        colour_column=5,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--out",
        help="Excel file to write (default reports/endpoint_report_<time>.xlsx)",
    )
    parser.add_argument("--customers", type=int, default=12)
    parser.add_argument("--merchants", type=int, default=3)
    parser.add_argument("--products", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--only", help="Only endpoints whose path starts with this, e.g. /admin"
    )
    parser.add_argument(
        "--keep-db",
        action="store_true",
        help="Keep the throwaway database and print its path",
    )
    args = parser.parse_args()

    started = datetime.now()
    workdir = Path(tempfile.mkdtemp(prefix="tabital-report-"))
    isolate(workdir / "report.db")
    block_network()
    uploads = ROOT / "uploads"
    before = (
        {p for p in uploads.rglob("*") if p.is_file()} if uploads.exists() else set()
    )

    from app import create_app
    from app.docs import build_spec
    from app.extensions import db

    app = create_app()
    app.config["MAIL_SUPPRESS_SEND"] = True
    try:
        with app.app_context():
            db.create_all()
            seeded = app.test_cli_runner().invoke(
                args=[
                    "seed-data",
                    "--customers",
                    str(args.customers),
                    "--merchants",
                    str(args.merchants),
                    "--products",
                    str(args.products),
                    "--seed",
                    str(args.seed),
                ]
            )
            if seeded.exit_code != 0:
                sys.exit(f"Seeding failed:\n{seeded.output}")
            summary_line = next(
                (l for l in seeded.output.splitlines() if l.startswith("Seeded")), ""
            )

            actors = pick_actors()
            ids = build_ids(actors)
            spec = build_spec(app)
            runner = Runner(app, actors)
            print(
                f"Calling {sum(len(i) for i in spec['paths'].values())} operations..."
            )
            results = run_endpoints(runner, spec, ids, args.only)
            print("Checking access control...")
            checks = [] if args.only else run_access_control(runner, spec, ids)
        out = (
            Path(args.out)
            if args.out
            else ROOT / "reports" / f"endpoint_report_{started:%Y%m%d_%H%M%S}.xlsx"
        )
        info = {
            "started": started.strftime("%Y-%m-%d %H:%M:%S"),
            "seconds": round((datetime.now() - started).total_seconds(), 1),
            "seeded": summary_line,
            "missing_ids": ", ".join(sorted(k for k, v in ids.items() if v == 999999)),
        }
        write_workbook(out, results, checks, info)
    finally:
        for stray in (
            ({p for p in uploads.rglob("*") if p.is_file()} - before)
            if uploads.exists()
            else ()
        ):
            stray.unlink(missing_ok=True)
        if args.keep_db:
            print(f"Database kept at {workdir / 'report.db'}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    counts = {
        o: sum(r["outcome"] == o for r in results)
        for o in OUTCOMES
    }
    breaches = sum(c["result"] in ("BREACH", "ERROR") for c in checks)
    print(
        "  "
        + "   ".join(f"{k}: {v}" for k, v in counts.items())
        + f"   access breaches: {breaches}"
    )
    print(f"Report written to {out}")
    sys.exit(1 if counts[FAIL] or breaches else 0)


if __name__ == "__main__":
    main()
