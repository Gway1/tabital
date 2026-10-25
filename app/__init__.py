from flask import Flask
from flask_cors import CORS
from flask_migrate import Migrate

from .config import Config
from .extensions import db, ma, guard, mail
from .models.user import User
from .models import ledger as _ledger_model  # noqa: F401  (registers the ledger table)
from .models import payment_intent as _payment_intent_model  # noqa: F401
from .models import risk_assessment as _risk_assessment_model  # noqa: F401
from .models import message_outbox as _message_outbox_model  # noqa: F401
from .models import payment_method as _payment_method_model  # noqa: F401
from .models import settlement as _settlement_model  # noqa: F401
from .models import identity as _identity_model  # noqa: F401
from .models import deferment as _deferment_model  # noqa: F401
from .models import pii_access as _pii_access_model  # noqa: F401
from .models import login_attempt as _login_attempt_model  # noqa: F401
from .models import part_payment as _part_payment_model  # noqa: F401
from .models import payment_claim as _payment_claim_model  # noqa: F401
from .models import gateway as _gateway_model  # noqa: F401
from .routes import register_routes


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    if not app.config.get("SECRET_KEY"):
        if app.config.get("DEBUG"):
            import secrets

            app.config["SECRET_KEY"] = secrets.token_urlsafe(64)
            app.logger.warning(
                "SECRET_KEY not set; using a random key for this debug session only"
            )
        else:
            raise RuntimeError("SECRET_KEY environment variable must be set")

    CORS(
        app,
        origins=app.config["CORS_ORIGINS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-Requested-With",
            "X-Device-Id",
            "X-Device-Flags",
        ],
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    )

    db.init_app(app)
    ma.init_app(app)
    mail.init_app(app)
    guard.init_app(app, User)
    if not app.config.get("TESTING"):
        from .services import smileid

        with app.app_context():
            for problem in smileid.config_warnings():
                app.logger.warning("Smile ID: %s", problem)
    Migrate(app, db)

    register_routes(app)
    register_commands(app)
    register_pii_masking(app)
    register_access_control(app)

    return app


def register_pii_masking(app):
    """Admin screens get masked personal data by default (services/pii.py).

    Applies to JSON responses under /admin/. The audited reveal endpoint (/admin/pii/…) is the
    only way to see a full value.
    """
    import json
    from flask import request
    from .services import pii

    @app.after_request
    def _mask_admin_pii(response):
        path = request.path or ""
        if not path.startswith("/admin/") or path.startswith("/admin/pii/"):
            return response
        if (
            response.status_code >= 400
            or not response.is_json
            or response.direct_passthrough
        ):
            return response
        data = response.get_json(silent=True)
        if data is None:
            return response
        response.set_data(json.dumps(pii.mask_payload(data)))
        return response


def register_access_control(app):
    """Management Access (services/access.py): checked once for every request an admin makes."""
    from flask import request
    from .services import access, auth_service

    @app.before_request
    def _management_access():
        if request.method == "OPTIONS" or "Authorization" not in request.headers:
            return None
        try:
            data = guard.extract_jwt_token(guard.read_token_from_header())
            user = User.query.get(data.get("id"))
        except Exception:  # bad or expired token: the endpoint's own auth check answers
            return None
        # A suspended, rejected or pending account loses access at once, not when its token expires
        # (flask_praetorian only checks is_valid when a token is issued or refreshed)
        if user is None or not user.is_valid():
            return {
                "error": "Your account isn't active. Please contact Tabital.",
                "code": "account_inactive",
            }, 401
        # A password reset or change signs out the sessions started before it
        if auth_service.session_predates_password_change(user, data.get("iat")):
            return {
                "error": "Your session has ended. Please sign in again.",
                "code": "session_expired",
            }, 401
        if user.role != "admin" or access.is_management(user):
            return None
        if access.needs_management(request.method, request.path):
            return {"error": access.MESSAGE, "code": "management_required"}, 403
        return None


def register_commands(app):
    import click

    @app.cli.command("create-admin")
    @click.option("--phone", required=True, help="Admin login phone number")
    @click.option("--name", default="Tabital Admin")
    @click.option(
        "--management",
        is_flag=True,
        help="Give Management Access (approvals, rates, reveals)",
    )
    @click.password_option()
    def create_admin(phone, name, management, password):
        """Create an admin account. Admins can't be created through the public API.

        New admins get operations (read-only) access unless --management is given.
        """
        from .services import access

        if User.query.filter_by(phone=phone).first():
            raise click.ClickException("A user with that phone number already exists")
        level = access.MANAGEMENT if management else access.OPERATIONS
        admin = User(
            phone=phone,
            full_name=name,
            role="admin",
            status="approved",
            admin_level=level,
            password=guard.hash_password(password),
        )
        db.session.add(admin)
        db.session.commit()
        click.echo(f"Admin {phone} created with {level} access")

    @app.cli.command("seed-data")
    @click.option(
        "--customers", default=12, show_default=True, help="Customers to create"
    )
    @click.option(
        "--merchants", default=3, show_default=True, help="Merchants to create"
    )
    @click.option(
        "--products", default=5, show_default=True, help="Products per merchant"
    )
    @click.option(
        "--seed",
        default=42,
        show_default=True,
        help="Random seed: the same seed gives the same data",
    )
    @click.option(
        "--password",
        default="Secret123!",
        show_default=True,
        help="Password for every seeded login",
    )
    @click.option(
        "--reset",
        is_flag=True,
        help="Delete the seeded users and everything that belongs to them",
    )
    @click.option(
        "--force",
        is_flag=True,
        help="Allow a database that isn't on this machine (asks to confirm)",
    )
    def seed_data(customers, merchants, products, seed, password, reset, force):
        """Fill a local database with test users, products, plans, payments and disputes.

        Safe to re-run: seeded accounts use phones 0200010000-0200019999 and are never duplicated.
        """
        from . import seed as seeding

        seeding.run(app, customers, merchants, products, seed, password, reset, force)

    @app.cli.command("set-admin-access")
    @click.option("--phone", required=True)
    @click.option(
        "--level", required=True, type=click.Choice(["management", "operations"])
    )
    def set_admin_access(phone, level):
        """Change an admin's access from the server (e.g. to set up the first manager)."""
        admin = User.query.filter_by(phone=phone, role="admin").first()
        if not admin:
            raise click.ClickException("No admin with that phone number")
        admin.admin_level = level
        db.session.commit()
        click.echo(f"{phone} now has {level} access")

    @app.cli.command("run-daily")
    @click.option(
        "--date",
        "run_date",
        default=None,
        help="Service as of this date (YYYY-MM-DD); default today",
    )
    @click.option("--no-reminders", is_flag=True)
    @click.option("--no-autopay", is_flag=True)
    def run_daily_command(run_date, no_reminders, no_autopay):
        """Daily servicing: late fees, DPD buckets, collections stages, reminders, autopay.

        Schedule once a day, e.g. a Render cron job at 06:00 Africa/Accra (06:00 UTC).
        """
        from datetime import datetime as _dt
        from .services import servicing

        day = _dt.strptime(run_date, "%Y-%m-%d").date() if run_date else None
        summary = servicing.run_daily(
            day, send_reminders=not no_reminders, run_autopay=not no_autopay
        )
        for key, value in summary.to_dict().items():
            click.echo(f"{key}: {value}")

        from .services import webhooks

        sent, failed = webhooks.deliver_due()
        click.echo(f"merchant_webhooks_sent: {sent}")

    @app.cli.command("deliver-webhooks")
    def deliver_webhooks_command():
        """Send queued merchant gateway webhooks (and retry failed ones). Run every minute."""
        from .services import webhooks

        sent, failed = webhooks.deliver_due()
        click.echo(f"sent: {sent}, failed or retrying: {failed}")

    @app.cli.command("ledger-backfill")
    @click.option(
        "--dry-run", is_flag=True, help="Report what would be written without saving"
    )
    def ledger_backfill(dry_run):
        """Create ledger entries for plans made before the ledger existed.

        Uses the recorded plan and payment rows: contract total, paid instalments and
        late fees. Plans that already have ledger entries are skipped.
        """
        from decimal import Decimal
        from .models.instalment import InstalmentPlan
        from .models.instalment_payment import InstalmentPayment
        from .services import ledger

        from .models.ledger import LedgerEntry
        from .models.transaction import Transaction

        created = 0
        no_fee = []
        for plan in InstalmentPlan.query.order_by(InstalmentPlan.id).all():
            if ledger.has_entries(plan):
                continue
            # Entries are dated when things happened, so reports put them in the right month
            opened = plan.created_at
            ledger.record(
                plan,
                "plan_opened",
                plan.total_amount,
                note="Backfill: contract total payable",
                at=opened,
            )
            # Merchant side, from the payout stored on the sale when it was approved
            sale = (
                Transaction.query.get(plan.transaction_id)
                if plan.transaction_id
                else None
            )
            if sale is not None and sale.payout_amount is not None:
                fee = Decimal(str(sale.amount or 0)) - Decimal(str(sale.payout_amount))
                ledger.record(
                    plan,
                    LedgerEntry.MERCHANT_FEE,
                    fee,
                    account=LedgerEntry.MERCHANT,
                    note="Backfill: merchant discount (MDR)",
                    at=opened,
                )
                ledger.record(
                    plan,
                    LedgerEntry.MERCHANT_PAYABLE,
                    sale.payout_amount,
                    account=LedgerEntry.MERCHANT,
                    note="Backfill: settlement owed to merchant",
                    at=opened,
                )
            else:
                no_fee.append(plan.id)
            payments = InstalmentPayment.query.filter_by(plan_id=plan.id).all()
            for p in payments:
                if p.late_fee and (p.late_fee_paid or p.status != "paid"):
                    ledger.record(
                        plan,
                        LedgerEntry.LATE_FEE_CHARGED,
                        p.late_fee,
                        payment=p,
                        note="Backfill: late fee",
                        at=p.late_fee_applied_date or p.due_date,
                    )
                if p.status == "paid":
                    paid = p.paid_amount or (
                        Decimal(str(p.amount)) + Decimal(str(p.late_fee or 0))
                    )
                    ledger.record(
                        plan,
                        LedgerEntry.PAYMENT_RECEIVED,
                        -Decimal(str(paid)),
                        payment=p,
                        reference=p.payment_reference or "backfill",
                        note=f"Backfill: payment {p.installment_number}",
                        at=p.paid_date or p.due_date,
                    )
            created += 1
        if no_fee:
            click.echo(
                f"{len(no_fee)} plan(s) had no stored merchant payout, so no merchant fee was written "
                f"(plan ids: {', '.join(map(str, no_fee[:50]))}{'…' if len(no_fee) > 50 else ''}). "
                "Their merchant fees are missing from revenue until entered by hand."
            )
        if dry_run:
            db.session.rollback()
            click.echo(f"Dry run: would backfill {created} plan(s)")
        else:
            db.session.commit()
            click.echo(f"Backfilled {created} plan(s)")
