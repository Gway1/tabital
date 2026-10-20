"""`flask seed-data`: a believable test dataset for a local database.

Users and products are inserted directly. Orders, plans, payments, claims, deliveries and disputes go
through the real endpoints (app.test_client), so the schedule, ledger and settlement lines are exactly
what the app produces. Everything is keyed to a reserved phone range, so re-running adds nothing twice
and --reset removes only what this command made.
"""
import random
from datetime import date, datetime, timedelta
from decimal import Decimal

import click
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from .extensions import db, guard

BASE_PHONE = 200010000  # seed phones are 0200010000 + n
ADMIN_AT, MERCHANT_AT, CUSTOMER_AT = 0, 10, 100
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

FIRST_NAMES = [
    "Kwame",
    "Ama",
    "Kofi",
    "Akosua",
    "Yaw",
    "Abena",
    "Kwesi",
    "Efua",
    "Nana",
    "Adwoa",
    "Kojo",
    "Esi",
    "Fiifi",
    "Maame",
    "Kwabena",
    "Afia",
    "Yaa",
    "Kweku",
    "Araba",
    "Selorm",
]
LAST_NAMES = [
    "Mensah",
    "Owusu",
    "Boateng",
    "Asante",
    "Appiah",
    "Addo",
    "Quaye",
    "Tetteh",
    "Ofori",
    "Amoah",
    "Darko",
    "Acheampong",
    "Agyeman",
    "Nkrumah",
    "Adjei",
    "Bonsu",
    "Danso",
    "Sarpong",
]
CITIES = [
    ("Accra", "Osu"),
    ("Kumasi", "Adum"),
    ("Takoradi", "Market Circle"),
    ("Tamale", "Gumani"),
    ("Cape Coast", "Abura"),
    ("Tema", "Community 4"),
    ("Ho", "Bankoe"),
    ("Sunyani", "Penkwase"),
]
JOBS = [
    ("Teacher", "Ghana Education Service"),
    ("Nurse", "Korle Bu Teaching Hospital"),
    ("Accountant", "Ecobank Ghana"),
    ("Engineer", "Ghana Water Company"),
    ("Sales Executive", "MTN Ghana"),
    ("Civil Servant", "Controller and Accountant-General"),
]
SHOPS = [
    ("Kantamanto Gadgets", "Electronics"),
    ("Accra Home Centre", "Home appliances"),
    ("Makola Mobile Hub", "Phones"),
    ("Kumasi Tech Plaza", "Electronics"),
    ("Osu Style House", "Fashion"),
]
CATALOGUE = [
    ("Samsung Galaxy A15", "Phones", "Samsung", 1800),
    ("iPhone 13 (128GB)", "Phones", "Apple", 5200),
    ("Tecno Spark 20", "Phones", "Tecno", 1500),
    ("HP 15 Laptop", "Laptops", "HP", 4200),
    ("Lenovo IdeaPad 3", "Laptops", "Lenovo", 3900),
    ('Hisense 43" Smart TV', "Electronics", "Hisense", 2800),
    ("LG Double-door Fridge", "Appliances", "LG", 4400),
    ("Nasco Standing Fan", "Appliances", "Nasco", 450),
    ("Binatone Blender", "Appliances", "Binatone", 320),
    ("JBL Flip 6 Speaker", "Electronics", "JBL", 1300),
    ("Wax Print Dress Set", "Fashion", "Local", 600),
    ("Nike Air Force 1", "Fashion", "Nike", 1100),
]
REFERENCE_PREFIX = "SEED"
# One order is driven per scenario; extra eligible customers get further scenarios round-robin
SCENARIOS = [
    "current",
    "paid_some",
    "completed",
    "claim_pending",
    "claim_pending",  # a second, so one can be confirmed and another rejected
    "claim_confirmed",
    "awaiting_approval",
    "awaiting_approval",  # likewise: one to approve, one to reject
    "rejected",
    "dispute",
    "approved",  # approved but not yet delivered, for the merchant's delivery screen
    # Last: a customer with a late plan isn't eligible for another purchase
    "overdue_15",
    "overdue_45",
    "overdue_75",
]


class SeedError(click.ClickException):
    pass


def seed_phone(n):
    return f"0{BASE_PHONE + n}"


def is_seed_phone(phone):
    try:
        return (
            phone is not None
            and len(phone) == 10
            and phone.startswith("0")
            and BASE_PHONE <= int(phone) < BASE_PHONE + 10000
        )
    except ValueError:
        return False


def check_local(app, force):
    """Refuse a database that isn't on this machine unless --force and a confirmation say otherwise."""
    url = make_url(app.config["SQLALCHEMY_DATABASE_URI"])
    if url.get_backend_name() == "sqlite" or url.host in LOCAL_HOSTS or not url.host:
        return
    if not force:
        raise SeedError(
            f"{url.host} isn't a local database. Seed data is for local development; "
            "pass --force to seed it anyway."
        )
    click.confirm(
        f"Seed {url.host}/{url.database}? This adds test users and orders.", abort=True
    )


class Api:
    """The test client with a bearer token per user. Tokens are minted directly: logging in 30 users
    from one address would trip the sign-in spraying limit."""

    def __init__(self, app):
        self.client = app.test_client()

    def _headers(self, user):
        return {"Authorization": f"Bearer {guard.encode_jwt_token(user)}"}

    def call(self, method, path, user, expect, **body):
        res = getattr(self.client, method)(path, headers=self._headers(user), json=body)
        if res.status_code not in expect:
            raise SeedError(
                f"{method.upper()} {path} as {user.phone} gave {res.status_code}: {res.get_json()}"
            )
        return res


def run(app, customers, merchants, products, seed, password, reset, force):
    check_local(app, force)
    if reset:
        if not force and not click.confirm(
            "Delete every seeded user and the records that belong to them?"
        ):
            raise click.Abort()
        removed = remove_seed_data()
        click.echo(f"Removed {removed} seeded rows")
        return

    from .services.system_defaults import init_system_settings

    init_system_settings()

    rng = random.Random(seed)
    created = {"users": 0, "products": 0, "orders": 0}
    admins = make_admins(password, created)
    shops = [make_merchant(i, rng, password, created) for i in range(merchants)]
    shoppers = [make_customer(i, rng, password, created) for i in range(customers)]
    catalogue = [
        make_products(m, i, products, rng, created) for i, m in enumerate(shops)
    ]
    db.session.commit()

    eligible = [c for c, kind in shoppers if kind == "eligible"]
    if _already_ordered(eligible):
        click.echo("Orders already seeded; skipping them")
    else:
        drive_orders(app, rng, admins["management"], eligible, catalogue, created)

    seed_extras(
        app, rng, admins["management"], shops, shoppers, catalogue, created
    )

    click.echo(
        f"Seeded {created['users']} users, {created['products']} products, {created['orders']} orders"
    )
    extras = ", ".join(f"{n} {name}" for name, n in created.items() if name not in ("users", "products", "orders") and n)
    if extras:
        click.echo(f"Also: {extras}")
    print_credentials(admins, shops, shoppers, password)


# ---------------------------------------------------------------- users and products


def make_admins(password, created):
    from .models.user import User

    out = {}
    for n, (level, name) in enumerate(
        [("management", "Seed Manager"), ("operations", "Seed Operator")]
    ):
        phone = seed_phone(ADMIN_AT + n)
        user = User.query.filter_by(phone=phone).first()
        if not user:
            user = User(
                phone=phone,
                full_name=name,
                role="admin",
                status="approved",
                admin_level=level,
                password=guard.hash_password(password),
            )
            db.session.add(user)
            db.session.flush()
            created["users"] += 1
        out[level] = user
    return out


def make_merchant(i, rng, password, created):
    from .models.user import User

    phone = seed_phone(MERCHANT_AT + i)
    user = User.query.filter_by(phone=phone).first()
    if user:
        return user
    name, kind = SHOPS[i % len(SHOPS)]
    city, area = CITIES[i % len(CITIES)]
    owner = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    user = User(
        phone=phone,
        password=guard.hash_password(password),
        role="merchant",
        status="approved",
        kyc_status="verified",
        verified=True,
        kyc_completed_on=datetime.utcnow() - timedelta(days=60),
        business_name=f"{name} {i + 1}" if i >= len(SHOPS) else name,
        owner_name=owner,
        business_email=f"merchant{i + 1}@seed.tabitalpay.test",
        business_phone=f"0302{i:06d}"[:10],
        business_type="Retail",
        product_type=kind,
        city=city,
        address=f"{area}, {city}",
        business_address=f"{area}, {city}",
        agree=True,
        merchant_fee_tier=["standard", "premium", "high_risk"][i % 3],
        settlement_period_days=[7, 3, 30][i % 3],
        payout_method="mobile_money",
        payout_bank_code="MTN",
        momo_name=owner,
        momo_number=f"0550{i:06d}",
        account_name=owner,
        account_number=f"1001{i:08d}",
        bank_name="GCB Bank",
    )
    db.session.add(user)
    db.session.flush()
    user.merchant_id = user.generate_merchant_id()
    db.session.flush()
    created["users"] += 1
    return user


def make_customer(i, rng, password, created):
    """Returns (user, kind). Most are eligible; the rest cover the states underwriting and KYC produce."""
    from .models.user import User

    kind = "eligible"
    if i % 6 == 4:
        kind = "pending"
    elif i % 6 == 5:
        kind = ["restricted", "unverified_salary", "rejected"][(i // 6) % 3]
    phone = seed_phone(CUSTOMER_AT + i)
    user = User.query.filter_by(phone=phone).first()
    if user:
        return user, kind
    first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
    city, area = rng.choice(CITIES)
    job, company = rng.choice(JOBS)
    status = {
        "pending": "pending",
        "restricted": "restricted",
        "rejected": "rejected",
    }.get(kind, "approved")
    verified = kind in ("eligible", "restricted")
    user = User(
        phone=phone,
        password=guard.hash_password(password),
        role="customer",
        status=status,
        full_name=f"{first} {last}",
        business_email=f"{first}.{last}{i + 1}@seed.tabitalpay.test".lower(),
        dob=f"{rng.randint(1975, 2000)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
        city=city,
        address=f"{area}, {city}",
        designation=job,
        company=company,
        agree=True,
        ref_name=f"{rng.choice(FIRST_NAMES)} {last}",
        ref_phone=f"0244{rng.randint(100000, 999999)}",
        ref_relationship="Sibling",
        kyc_status="verified" if verified else "pending",
        national_id=f"GHA-{phone[-9:]}-1",
        momo_number=phone,
        monthly_salary=Decimal(rng.randrange(9000, 20000, 500)),
        employment_start_date=date.today() - timedelta(days=rng.randint(400, 2500)),
        salary_paid_to_bank=True,
        salary_verified=kind != "unverified_salary",
        employment_verified_at=datetime.utcnow() - timedelta(days=30)
        if kind == "eligible"
        else None,
    )
    db.session.add(user)
    db.session.flush()
    if status in ("approved", "restricted"):
        user.customer_id = user.generate_customer_id()
        db.session.flush()
    created["users"] += 1
    return user, kind


def make_products(merchant, index, count, rng, created):
    from .models.product import Product

    items = []
    picks = [CATALOGUE[(index * 3 + j) % len(CATALOGUE)] for j in range(count)]
    for j, (name, category, brand, price) in enumerate(picks):
        sku = f"SEED-M{index + 1}-P{j + 1}"
        product = Product.query.filter_by(sku=sku).first()
        if not product:
            product = Product(
                product_id=Product.generate_product_id(Product),
                merchant_id=merchant.id,
                name=name,
                category=category,
                brand=brand,
                sku=sku,
                description=f"{name} from {merchant.business_name}",
                price=float(price),
                stock_quantity=rng.randint(5, 40),
                status="active",
                is_featured=j == 0,
                is_new=j == 1,
            )
            db.session.add(product)
            db.session.flush()
            created["products"] += 1
        items.append(product)
    return items


# ---------------------------------------------------------------- orders


def _already_ordered(customers):
    from .models.purchase_order import PurchaseOrder

    ids = [c.id for c in customers]
    return (
        bool(ids)
        and PurchaseOrder.query.filter(PurchaseOrder.customer_id.in_(ids)).count() > 0
    )


def drive_orders(app, rng, admin, eligible, catalogue, created):
    from .models.instalment import InstalmentPlan
    from .models.instalment_payment import InstalmentPayment
    from .models.purchase_order import PurchaseOrder
    from .services import servicing, settlements

    if not eligible or not catalogue:
        return
    api = Api(app)
    refs = iter(range(1, 10_000))
    ref = lambda tag: f"{REFERENCE_PREFIX}-{tag}-{next(refs):04d}"
    orders = max(len(SCENARIOS), len(eligible))
    skipped = 0

    def pay(payment, user=admin):
        api.call(
            "put",
            f"/admin/instalments/payments/{payment.id}/mark-paid",
            user,
            (200,),
            payment_reference=ref("PAY"),
            payment_method="mobile_money",
        )

    def schedule(plan):
        return (
            InstalmentPayment.query.filter_by(plan_id=plan.id)
            .order_by(InstalmentPayment.installment_number)
            .all()
        )

    for n in range(orders):
        scenario = SCENARIOS[n % len(SCENARIOS)]
        customer = eligible[n % len(eligible)]
        merchant_products = rng.choice(catalogue)
        product = rng.choice(merchant_products)
        terms = rng.choice([4, 4, 3, 2])

        res = api.call(
            "post",
            "/customer/purchase",
            customer,
            (201, 400, 403, 409),
            accept_terms=True,
            product_id=product.id,
            number_of_installments=terms,
            delivery_address=customer.address or "Osu, Accra",
        )
        if res.status_code != 201:
            skipped += 1
            click.echo(
                f"  skipped {scenario} for {customer.phone}: {res.get_json().get('error')}"
            )
            continue
        order = (
            PurchaseOrder.query.filter_by(customer_id=customer.id)
            .order_by(PurchaseOrder.id.desc())
            .first()
        )
        created["orders"] += 1
        if scenario == "awaiting_approval":
            continue
        if scenario == "rejected":
            api.call(
                "put",
                f"/admin/orders/{order.id}/reject",
                admin,
                (200,),
                reason="Seed: rejected for review",
            )
            continue

        api.call(
            "put",
            f"/admin/orders/{order.id}/approve",
            admin,
            (200,),
            down_payment_reference=ref("DP"),
            down_payment_method="mobile_money",
        )
        db.session.expire_all()
        plan = (
            InstalmentPlan.query.filter_by(customer_id=customer.id)
            .order_by(InstalmentPlan.id.desc())
            .first()
        )
        payments = schedule(plan)
        later = [p for p in payments if p.installment_number > 1]

        if scenario in (
            "current",
            "paid_some",
            "completed",
            "claim_pending",
            "claim_confirmed",
        ) or scenario.startswith("overdue"):
            api.call(
                "put",
                f"/merchant/orders/{order.id}/delivery",
                product.merchant,
                (200,),
                delivery_status="delivered",
            )

        if scenario == "paid_some" and later:
            pay(later[0])
        elif scenario == "completed":
            for p in later:
                pay(p)
        elif scenario.startswith("overdue") and later:
            later[0].due_date = datetime.utcnow() - timedelta(
                days=int(scenario.split("_")[1])
            )
            db.session.commit()
        elif scenario in ("claim_pending", "claim_confirmed") and later:
            claim = api.call(
                "post",
                "/customer/payments/make",
                customer,
                (202,),
                plan_id=plan.id,
                payment_method="mobile_money",
                payment_reference=ref("CLAIM"),
            ).get_json()
            if scenario == "claim_confirmed":
                api.call(
                    "post",
                    f"/admin/payment-claims/{claim['claim_id']}/confirm",
                    admin,
                    (200,),
                )
        elif scenario == "dispute":
            api.call(
                "post",
                "/customer/disputes",
                customer,
                (201,),
                plan_id=plan.id,
                reason="not_as_described",
                description="The product doesn't match the photos on the listing.",
            )

    db.session.expire_all()
    servicing.run_daily(
        send_reminders=False, run_autopay=False
    )  # late fees, days past due, collections
    settlements.generate_batches()
    if skipped:
        click.echo(f"{skipped} order(s) skipped (see above)")


# ---------------------------------------------------------------- everything around the orders

def seed_extras(app, rng, admin, shops, shoppers, catalogue, created):
    """Documents, notifications, saved cards, a fraud flag and identity check, support tickets,
    payment links and settlement batches, so the screens and endpoints that read them have data.
    Each block checks for its own earlier rows first, so a re-run adds nothing."""
    from .models.identity import FraudSignal, IdentityCheck
    from .models.notifications import Notifications
    from .models.payment_method import PaymentMethod
    from .models.document import Document
    from .models.settlement import PaymentLink
    from .models.support_ticket import SupportTicket
    from .services import fraud, settlements

    api = Api(app)
    eligible = [c for c, kind in shoppers if kind == "eligible"]
    pending = [c for c, kind in shoppers if kind == "pending"]
    count = lambda key, n=1: created.__setitem__(key, created.get(key, 0) + n)

    # KYC documents (rows only: no files are written). Merchants are verified, pending customers wait
    kyc = [(m, ["business_registration", "tax_document", "bank_statement"], "verified") for m in shops]
    kyc += [(c, ["kyc_front", "kyc_back", "passport_photo"], "uploaded") for c in pending]
    kyc += [(c, ["kyc_front", "kyc_back"], "verified") for c in eligible[:2]]
    for user, kinds, status in kyc:
        if Document.query.filter_by(user_id=user.id).first():
            continue
        for kind in kinds:
            db.session.add(Document(
                document_id=Document.generate_document_id(Document), user_id=user.id,
                document_type=kind, document_name=kind.replace("_", " ").title(),
                file_name=f"seed-{kind}.pdf", file_size=48_000, mime_type="application/pdf",
                status=status, uploaded_by=user.id,
                verified_by=admin.id if status == "verified" else None,
                verified_at=datetime.utcnow() - timedelta(days=20) if status == "verified" else None,
            ))
            db.session.flush()
            count("documents")

    # Notifications
    for user in [*shops, *eligible]:
        if Notifications.query.filter_by(user_id=user.id, title="Welcome to Tabital Pay").first():
            continue
        for title, message, kind, link in [
            ("Welcome to Tabital Pay", "Your account is ready.", "system", "/"),
            ("Payment reminder", "An instalment is due soon.", "payment", "/customer/instalments"),
            ("Order update", "There is an update on one of your orders.", "order", "/customer/orders"),
        ]:
            Notifications.create_notification(
                user_id=user.id, user_role=user.role, title=title, message=message, type=kind,
                link=link, action_text="View",
            )
            count("notifications")

    # Saved cards (not chargeable: the authorization codes are made up)
    for n, customer in enumerate(eligible[:2]):
        code = f"AUTH_SEED_{customer.id}"
        if PaymentMethod.query.filter_by(authorization_code=code).first():
            continue
        db.session.add(PaymentMethod(
            customer_id=customer.id, provider="paystack", authorization_code=code,
            signature=f"SIG_SEED_{customer.id}", email=customer.business_email or f"{customer.phone}@seed.tabitalpay.test",
            channel="card", card_type=["visa", "mastercard"][n % 2], bank="GTBank",
            last4=f"{4242 + n:04d}", exp_month="12", exp_year="2030", reusable=True,
            autopay_enabled=False, is_default=True,
        ))
        count("saved cards")

    # A fraud flag for review and an identity check waiting for an admin, on a sign-up still pending
    subject = pending[0] if pending else (eligible[-1] if eligible else None)
    if subject and len(eligible) > 1:
        other = next(c for c in eligible if c.id != subject.id)
        before = FraudSignal.query.count()
        fraud.raise_signal(subject, "shared_momo", "Another customer uses the same Mobile Money number",
                           related=other, key="SEED-MOMO")
        count("fraud flags", FraudSignal.query.count() - before)
        if not IdentityCheck.query.filter_by(reference=f"SEED-IDC-{subject.id}").first():
            db.session.add(IdentityCheck(
                user_id=subject.id, provider="smileid", product="biometric_kyc",
                reference=f"SEED-IDC-{subject.id}", id_type="GHANA_CARD", id_number=subject.national_id,
                status=IdentityCheck.REVIEW, provider_status="attention",
                provider_message="Date of birth differs from the card", name_match=True, dob_match=False,
                reasons_json='["dob_mismatch"]', completed_at=datetime.utcnow(),
            ))
            count("identity checks")
    db.session.commit()

    # Support tickets, through the endpoints
    if eligible and not SupportTicket.query.filter(SupportTicket.subject.like("[Seed]%")).first():
        for customer, (subject_line, category, priority) in zip(eligible, [
            ("[Seed] Payment not showing on my plan", "payment", "high"),
            ("[Seed] When will my order be delivered?", "delivery", "medium"),
            ("[Seed] Update my Mobile Money number", "account", "low"),
        ]):
            res = api.call("post", "/customer/support/ticket", customer, (201,), subject=subject_line,
                           message="Seeded ticket for testing the support screens.", category=category,
                           priority=priority).get_json()
            count("support tickets")
            if category == "payment":
                api.call("post", f"/customer/support/tickets/{res['id']}/reply", customer, (200,),
                         message="Adding my MoMo receipt reference: SEED-RECEIPT-1.")

    # Payment links, through the endpoint
    for merchant, products in zip(shops, catalogue):
        if PaymentLink.query.filter_by(merchant_id=merchant.id).first() or not products:
            continue
        api.call("post", "/merchant/payment-links", merchant, (201,), product_id=products[0].id,
                 quantity=1, expires_in_hours=72, note="Seed link")
        count("payment links")

    # Settlement batches: judged as of five weeks on, so every merchant's cycle has ended
    batches = settlements.generate_batches(date.today() + timedelta(days=35))
    count("settlement batches", len(batches))


# ---------------------------------------------------------------- reset


def remove_seed_data():
    """Delete seeded users and, table by table, the rows that point at them (or at rows being deleted)."""
    from .models.user import User

    seed_ids = {u.id for u in User.query.all() if is_seed_phone(u.phone)}
    if not seed_ids:
        return 0
    doomed = {"users": seed_ids}
    refs = {}  # table name -> [(column, parent table name)]
    for table in db.metadata.sorted_tables:
        refs[table.name] = [
            (fk.parent, fk.column.table.name) for fk in table.foreign_keys
        ]

    for table in db.metadata.sorted_tables:  # parents first
        if table.name == "users" or "id" not in table.c:
            continue
        conditions = [
            col.in_(doomed[parent])
            for col, parent in refs[table.name]
            if doomed.get(parent) and parent != table.name
        ]
        if conditions:
            ids = {
                row[0]
                for row in db.session.execute(
                    sa.select(table.c.id).where(sa.or_(*conditions))
                )
            }
            if ids:
                doomed[table.name] = ids

    total = 0
    for table in reversed(db.metadata.sorted_tables):  # children first
        ids = doomed.get(table.name)
        if ids and "id" in table.c:
            total += db.session.execute(
                sa.delete(table).where(table.c.id.in_(ids))
            ).rowcount
    # Link tables without an id column
    for table in db.metadata.sorted_tables:
        if "id" in table.c:
            continue
        conditions = [
            col.in_(doomed[parent])
            for col, parent in refs[table.name]
            if doomed.get(parent)
        ]
        if conditions:
            total += db.session.execute(
                sa.delete(table).where(sa.or_(*conditions))
            ).rowcount
    db.session.commit()
    return total


# ---------------------------------------------------------------- output


def print_credentials(admins, shops, shoppers, password):
    click.echo("\nLogin (password: %s)" % password)
    rows = [(f"admin ({level})", u.phone, "") for level, u in admins.items()]
    rows += [(f"merchant", m.phone, m.business_name) for m in shops]
    rows += [("customer", c.phone, f"{c.full_name} ({kind})") for c, kind in shoppers]
    for role, phone, note in rows:
        click.echo(f"  {role:<20} {phone}  {note}")
