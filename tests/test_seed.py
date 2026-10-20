"""`flask seed-data`: a repeatable test dataset built through the real endpoints."""
from decimal import Decimal

import pytest

from app import create_app
from app.extensions import db, guard
from app.models.instalment import InstalmentPlan
from app.models.instalment_payment import InstalmentPayment
from app.models.ledger import LedgerEntry
from app.models.payment_claim import PaymentClaim
from app.models.product import Product
from app.models.purchase_order import PurchaseOrder
from app.models.user import User
from app.models.dispute import Dispute
from app.models.document import Document
from app.models.identity import FraudSignal, IdentityCheck
from app.models.notifications import Notifications
from app.models.payment_method import PaymentMethod
from app.models.settlement import PaymentLink, Settlement
from app.models.support_ticket import SupportTicket
from app.seed import is_seed_phone
from app.services import ledger

AROUND_ORDERS = (Document, Notifications, PaymentMethod, SupportTicket, PaymentLink, Settlement, FraudSignal,
                 IdentityCheck)
SMALL = ["seed-data", "--customers", "10", "--merchants", "2", "--products", "3"]


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


def seed(app, *args):
    result = app.test_cli_runner().invoke(args=[*SMALL, *args])
    assert result.exit_code == 0, result.output
    return result


def counts():
    return {
        m.__name__: m.query.count()
        for m in (
            User,
            Product,
            PurchaseOrder,
            InstalmentPlan,
            InstalmentPayment,
            LedgerEntry,
            PaymentClaim,
            Dispute,
            *AROUND_ORDERS,
        )
    }


def test_seeds_every_kind_of_record(app_ctx):
    seed(app_ctx)
    n = counts()
    assert n["User"] == 2 + 2 + 10 and n["Product"] == 6
    assert n["PurchaseOrder"] >= 10 and n["InstalmentPlan"] >= 7
    assert (
        n["InstalmentPayment"] > n["InstalmentPlan"]
        and n["LedgerEntry"] > n["InstalmentPlan"]
    )
    assert n["PaymentClaim"] >= 1 and n["Dispute"] == 1
    assert {o.status for o in PurchaseOrder.query} >= {
        "approved",
        "pending",
        "rejected",
    }
    assert {u.status for u in User.query} >= {"approved", "pending", "restricted"}
    assert any(
        p.late_fee for p in InstalmentPayment.query
    )  # overdue plans were serviced


def test_seeds_the_records_around_the_orders(app_ctx):
    seed(app_ctx)
    n = counts()
    for model in AROUND_ORDERS:
        assert n[model.__name__] >= 1, model.__name__
    assert {d.status for d in Document.query} >= {"verified", "uploaded"}
    assert Settlement.query.filter_by(status="pending_approval").count() >= 1
    assert FraudSignal.query.one().severity == FraudSignal.REVIEW      # a flag to review, not a block


def test_ledger_agrees_with_the_schedule(app_ctx):
    seed(app_ctx)
    for plan in InstalmentPlan.query:
        unpaid = sum(
            (
                Decimal(str(p.get_total_due()))
                for p in InstalmentPayment.query.filter_by(plan_id=plan.id)
                if p.status != "paid"
            ),
            Decimal("0"),
        )
        assert ledger.customer_balance(plan) == unpaid.quantize(
            Decimal("0.01")
        ), plan.plan_id


def test_seeded_accounts_can_log_in(app_ctx):
    seed(app_ctx, "--password", "Another-Pass-1")
    client = app_ctx.test_client()
    for phone in ("0200010000", "0200010010", "0200010100"):
        res = client.post("/login", json={"phone": phone, "password": "Another-Pass-1"})
        assert res.status_code == 200, phone


def test_rerun_adds_nothing(app_ctx):
    seed(app_ctx)
    first = counts()
    seed(app_ctx)
    assert counts() == first


def test_same_seed_same_data_different_seed_different_data(app_ctx):
    seed(app_ctx, "--seed", "7")
    names = sorted(u.full_name for u in User.query if u.role == "customer")
    seed(app_ctx, "--seed", "7", "--reset", "--force")
    seed(app_ctx, "--seed", "7")
    assert sorted(u.full_name for u in User.query if u.role == "customer") == names
    seed(app_ctx, "--seed", "99", "--reset", "--force")
    seed(app_ctx, "--seed", "99")
    assert sorted(u.full_name for u in User.query if u.role == "customer") != names


def test_reset_removes_only_seed_data(app_ctx):
    other = User(
        phone="0241112222",
        role="customer",
        status="approved",
        password=guard.hash_password("x" * 8),
    )
    db.session.add(other)
    db.session.commit()
    seed(app_ctx)
    seed(app_ctx, "--reset", "--force")
    assert [u.phone for u in User.query] == ["0241112222"]
    left = counts()
    assert not any(
        left[name]
        for name in ("PurchaseOrder", "LedgerEntry", "Product")
        + tuple(m.__name__ for m in AROUND_ORDERS)
    ), left


def test_refuses_a_database_that_is_not_local(app_ctx):
    app_ctx.config[
        "SQLALCHEMY_DATABASE_URI"
    ] = "postgresql://user:pw@db.example.com/tabital"
    result = app_ctx.test_cli_runner().invoke(args=SMALL)
    assert result.exit_code != 0 and "db.example.com" in result.output
    assert User.query.count() == 0


def test_seed_phone_range():
    assert (
        is_seed_phone("0200010100")
        and not is_seed_phone("0241112222")
        and not is_seed_phone(None)
    )
