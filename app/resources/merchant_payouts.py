"""Phase 5 endpoints: merchant payout account, settlement cycle, settlements and statements,
admin settlement batches, and payment links (in-store / WhatsApp sales)."""
import csv
import io
import secrets
from datetime import datetime, timedelta

from flask import Response, current_app
from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request

from ..docs import BOOL, INT, MESSAGE, NUM, STR, api_doc, obj
from ..extensions import db
from ..models.product import Product
from ..models.settlement import PaymentLink, Settlement, SettlementLine
from ..services import paystack, settlements

PAYOUT_FIELDS = (
    "payout_method",
    "payout_bank_code",
    "bank_name",
    "account_name",
    "account_number",
    "branch_name",
    "momo_name",
    "momo_number",
)


_PAYOUT_VIEW = obj(
    {
        "payout_method": STR,
        "payout_bank_code": STR,
        "bank_name": STR,
        "account_name": STR,
        "account_number": STR,
        "momo_name": STR,
        "momo_number": STR,
        "settlement_period_days": INT,
        "payout_hold_until": {**STR, "nullable": True},
        "payouts_ready": BOOL,
        "payouts_blocked_reason": {**STR, "nullable": True},
        "message": {**STR, "description": "Only after a change that paused payouts"},
    }
)
_LINK_VIEW = obj(
    {
        "id": INT,
        "token": STR,
        "url": STR,
        "status": STR,
        "usable": BOOL,
        "product_id": INT,
        "product_name": STR,
        "price": NUM,
        "quantity": INT,
        "note": STR,
        "expires_at": STR,
        "created_at": STR,
        "qr_svg": {**STR, "description": "Only on create and single-link reads"},
    }
)


class PayoutError(ValueError):
    status = 400


class PayoutConflict(PayoutError):
    """The account or Mobile Money number is already linked to another account."""

    status = 409


def apply_payout_details(merchant, data, by_admin=False):
    """Validate and save payout details. Any real change triggers the payout hold. No commit.

    Returns True if something changed.
    """
    changes = {}
    # A masked value from an admin screen means 'unchanged' (services/pii.py)
    from ..services.pii import is_masked

    data = {k: v for k, v in data.items() if not is_masked(v)}
    if "payout_method" in data:
        method = (data.get("payout_method") or "").strip()
        if method not in ("mobile_money", "bank"):
            raise PayoutError("payout_method must be mobile_money or bank")
        changes["payout_method"] = method
    for key in (
        "payout_bank_code",
        "bank_name",
        "account_name",
        "branch_name",
        "momo_name",
    ):
        if key in data:
            changes[key] = (data.get(key) or "").strip() or None
    if "account_number" in data:
        value = (data.get("account_number") or "").replace(" ", "").strip()
        if value and (not value.isdigit() or not 6 <= len(value) <= 20):
            raise PayoutError("Bank account number must be 6-20 digits")
        changes["account_number"] = value or None
    if "momo_number" in data:
        value = (data.get("momo_number") or "").replace(" ", "").strip()
        if value and (not value.isdigit() or len(value) != 10):
            raise PayoutError("Mobile Money number must be 10 digits, e.g. 0241234567")
        changes["momo_number"] = value or None

    # Both columns are unique. Say so, rather than let the database refuse the save (a 500)
    from ..models.user import User

    for key, label in (
        ("account_number", "bank account number"),
        ("momo_number", "Mobile Money number"),
    ):
        value = changes.get(key)
        if (
            value
            and getattr(merchant, key) != value
            and User.query.filter(getattr(User, key) == value, User.id != merchant.id).first()
        ):
            raise PayoutConflict(f"This {label} is already linked to another account.")

    changed = False
    for key, value in changes.items():
        if getattr(merchant, key) != value:
            setattr(merchant, key, value)
            changed = True
    if changed:
        settlements.payout_details_changed(merchant, by_admin=by_admin)
    return changed


def payout_view(merchant):
    reason = settlements.hold_reason(merchant)
    return {
        "payout_method": merchant.payout_method,
        "payout_bank_code": merchant.payout_bank_code,
        "bank_name": merchant.bank_name,
        "account_name": merchant.account_name,
        "account_number": merchant.account_number,
        "momo_name": merchant.momo_name,
        "momo_number": merchant.momo_number,
        "settlement_period_days": merchant.settlement_period_days
        or settlements.DEFAULT_PERIOD,
        "payout_hold_until": merchant.payout_hold_until.isoformat()
        if merchant.payout_hold_until
        else None,
        "payouts_ready": reason is None,
        "payouts_blocked_reason": reason,
    }


def _merchant():
    user = current_user()
    return user if user.role == "merchant" else None


def _admin():
    user = current_user()
    return user if user.role == "admin" else None


# ---------------------------------------------------------------- merchant: payout account & cycle


class MerchantPayoutAccountResource(Resource):
    @api_doc(
        "Where the merchant's payouts go, the settlement cycle, and whether payouts are ready",
        responses={200: ("Payout account", _PAYOUT_VIEW), 403: "Not a merchant"},
    )
    @auth_required
    def get(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        return payout_view(merchant), 200

    @api_doc(
        "Change payout details or the settlement cycle",
        description="A real change to the payout details pauses payouts for 48 hours.",
        body=obj(
            {
                "payout_method": {"type": "string", "enum": ["mobile_money", "bank"]},
                "payout_bank_code": {
                    **STR,
                    "description": "Paystack code from /merchant/payout-banks",
                },
                "bank_name": STR,
                "account_name": STR,
                "account_number": {**STR, "description": "6-20 digits"},
                "branch_name": STR,
                "momo_name": STR,
                "momo_number": {**STR, "description": "10 digits"},
                "settlement_period_days": {"type": "integer", "enum": [3, 7, 30]},
            }
        ),
        responses={
            200: ("Saved; `message` is set when payouts were paused", _PAYOUT_VIEW),
            400: "Details failed validation",
            403: "Not a merchant",
        },
    )
    @auth_required
    def put(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        try:
            changed = apply_payout_details(merchant, data)
        except PayoutError as e:
            return {"error": str(e)}, e.status
        if "settlement_period_days" in data:
            try:
                period = int(data["settlement_period_days"])
            except (TypeError, ValueError):
                period = None
            if period not in settlements.ALLOWED_PERIODS:
                return {"error": "Settlement period must be 3, 7 or 30 days"}, 400
            merchant.settlement_period_days = period
        db.session.commit()
        body = payout_view(merchant)
        if changed:
            body[
                "message"
            ] = "Payout details saved. For your security, payouts are paused for 48 hours."
        return body, 200


class MerchantPayoutBanksResource(Resource):
    @api_doc(
        "Banks or Mobile Money providers (with Paystack codes) for the payout form",
        query={
            "method": (
                {"type": "string", "enum": ["bank", "mobile_money"]},
                "Default `bank`",
            )
        },
        responses={
            200: ("The list", obj({"banks": {"type": "array", "items": obj()}})),
            403: "Not a merchant or admin",
            502: "Paystack couldn't supply the list",
            503: "Payouts aren't configured yet",
        },
    )
    @auth_required
    def get(self):
        """Banks or MoMo providers (with Paystack codes) for the payout form."""
        if not _merchant() and not _admin():
            return {"error": "Unauthorized"}, 403
        method = request.args.get("method", "bank")
        if not paystack.is_configured():
            return {"error": "Payouts aren't configured yet"}, 503
        try:
            return {"banks": paystack.list_banks(method)}, 200
        except paystack.PaystackError as e:
            return {"error": f"Couldn't load the list from Paystack: {e}"}, 502


# ---------------------------------------------------------------- merchant: settlements & statement


class MerchantSettlementBatchesResource(Resource):
    @auth_required
    def get(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        batches = (
            Settlement.query.filter_by(merchant_id=merchant.id)
            .order_by(Settlement.period_end.desc(), Settlement.id.desc())
            .limit(100)
            .all()
        )
        pending = SettlementLine.query.filter_by(
            merchant_id=merchant.id, settlement_id=None
        ).all()
        return {
            "settlements": [b.to_dict() for b in batches],
            "next_settlement": {
                "lines": len(pending),
                "net": round(sum(l.net_pesewas for l in pending) / 100, 2),
                "period_days": merchant.settlement_period_days
                or settlements.DEFAULT_PERIOD,
            },
            "payout_account": payout_view(merchant),
        }, 200


class MerchantSettlementBatchResource(Resource):
    @auth_required
    def get(self, batch_id):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        batch = Settlement.query.filter_by(id=batch_id, merchant_id=merchant.id).first()
        if not batch:
            return {"error": "Settlement not found"}, 404
        return batch.to_dict(with_lines=True), 200


class MerchantStatementResource(Resource):
    @auth_required
    def get(self):
        """Sales, fees, clawbacks and payouts in a date range. ?format=csv to download."""
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        try:
            start = (
                datetime.strptime(request.args.get("from"), "%Y-%m-%d")
                if request.args.get("from")
                else datetime.utcnow() - timedelta(days=30)
            )
            end = (
                datetime.strptime(request.args.get("to"), "%Y-%m-%d")
                + timedelta(days=1)
                if request.args.get("to")
                else datetime.utcnow() + timedelta(days=1)
            )
        except ValueError:
            return {"error": "Dates must be YYYY-MM-DD"}, 400

        lines = (
            SettlementLine.query.filter(
                SettlementLine.merchant_id == merchant.id,
                SettlementLine.created_at >= start,
                SettlementLine.created_at < end,
            )
            .order_by(SettlementLine.created_at)
            .all()
        )
        paid = Settlement.query.filter(
            Settlement.merchant_id == merchant.id,
            Settlement.status == Settlement.PAID,
            Settlement.paid_at >= start,
            Settlement.paid_at < end,
        ).all()
        rows = [
            {
                "date": l.created_at.date().isoformat(),
                "type": l.line_type,
                "reference": l.plan.plan_id if l.plan else "",
                "description": l.description,
                "gross": round(l.gross_pesewas / 100, 2),
                "fee": round(l.fee_pesewas / 100, 2),
                "net": round(l.net_pesewas / 100, 2),
                "settlement": l.settlement.settlement_id
                if l.settlement
                else "Next settlement",
            }
            for l in lines
        ]
        rows += [
            {
                "date": b.paid_at.date().isoformat(),
                "type": "payout",
                "reference": b.transfer_reference or "",
                "description": f"Settlement {b.settlement_id} paid",
                "gross": 0,
                "fee": 0,
                "net": -round(b.net_pesewas / 100, 2),
                "settlement": b.settlement_id,
            }
            for b in paid
        ]
        rows.sort(key=lambda r: r["date"])
        totals = {
            "sales": round(
                sum(l.gross_pesewas for l in lines if l.line_type == "sale") / 100, 2
            ),
            "fees": round(
                sum(l.fee_pesewas for l in lines if l.line_type == "sale") / 100, 2
            ),
            "clawbacks": round(
                sum(l.net_pesewas for l in lines if l.line_type == "clawback") / 100, 2
            ),
            "paid_out": round(sum(b.net_pesewas for b in paid) / 100, 2),
        }

        if request.args.get("format") == "csv":
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(
                [
                    "Date",
                    "Type",
                    "Reference",
                    "Description",
                    "Gross (GHS)",
                    "Fee (GHS)",
                    "Net (GHS)",
                    "Settlement",
                ]
            )
            for r in rows:
                writer.writerow(
                    [
                        r["date"],
                        r["type"],
                        r["reference"],
                        r["description"],
                        f'{r["gross"]:.2f}',
                        f'{r["fee"]:.2f}',
                        f'{r["net"]:.2f}',
                        r["settlement"],
                    ]
                )
            return Response(
                out.getvalue(),
                mimetype="text/csv",
                headers={
                    "Content-Disposition": f"attachment; filename=tabital_statement_{start:%Y%m%d}_{(end - timedelta(days=1)):%Y%m%d}.csv"
                },
            )
        return {
            "from": start.date().isoformat(),
            "to": (end - timedelta(days=1)).date().isoformat(),
            "rows": rows,
            "totals": totals,
        }, 200


# ---------------------------------------------------------------- admin: settlement batches


class AdminSettlementBatchesResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        q = Settlement.query
        if request.args.get("status"):
            q = q.filter(Settlement.status == request.args["status"])
        batches = q.order_by(Settlement.created_at.desc()).limit(200).all()
        return {"settlements": [b.to_dict() for b in batches]}, 200


class AdminGenerateSettlementsResource(Resource):
    @auth_required
    def post(self):
        """Create batches for merchants whose cycle has ended (the daily job does this too)."""
        if not _admin():
            return {"error": "Unauthorized"}, 403
        created = settlements.generate_batches()
        return {"created": [b.to_dict() for b in created]}, 200


class AdminSettlementBatchResource(Resource):
    @auth_required
    def get(self, batch_id):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        batch = Settlement.query.get(batch_id)
        if not batch:
            return {"error": "Settlement not found"}, 404
        body = batch.to_dict(with_lines=True)
        body["payout_account"] = payout_view(batch.merchant)
        return body, 200


class AdminApproveSettlementResource(Resource):
    @auth_required
    def post(self, batch_id):
        """Approve a batch and send the money through Paystack Transfers."""
        admin = _admin()
        if not admin:
            return {"error": "Unauthorized"}, 403
        batch = Settlement.query.get(batch_id)
        if not batch:
            return {"error": "Settlement not found"}, 404
        outcome = settlements.approve_and_pay(batch, admin)
        messages = {
            "paid": "Paid to the merchant.",
            "processing": "Transfer started; Paystack will confirm it shortly.",
            "on_hold": f"On hold: {batch.hold_reason}",
            "failed": f"Transfer failed: {batch.failure_reason}",
            "paystack_not_configured": "Paystack isn't configured, so no transfer was made.",
            "nothing_to_pay": "Nothing to pay in this settlement.",
        }
        status = 200 if outcome in ("paid", "processing") else 400
        return {
            "outcome": outcome,
            "message": messages.get(outcome, outcome),
            "settlement": batch.to_dict(),
        }, status


# ---------------------------------------------------------------- payment links (in-store / WhatsApp)


def _link_url(token):
    base = (current_app.config.get("FRONTEND_URL") or "http://localhost:4200").rstrip(
        "/"
    )
    return f"{base}/customer/pay-link/{token}"


def _qr_svg(url):
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(
        url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2
    )
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _link_view(link, with_qr=False):
    body = {
        "id": link.id,
        "token": link.token,
        "url": _link_url(link.token),
        "status": link.status,
        "usable": link.is_usable(),
        "product_id": link.product_id,
        "product_name": link.product.name if link.product else None,
        "price": float(link.product.price) if link.product else None,
        "quantity": link.quantity,
        "note": link.note,
        "expires_at": link.expires_at.isoformat(),
        "created_at": link.created_at.isoformat() if link.created_at else None,
    }
    if with_qr:
        body["qr_svg"] = _qr_svg(body["url"])
    return body


class MerchantPaymentLinksResource(Resource):
    @api_doc(
        "The merchant's 100 most recent payment links",
        responses={
            200: (
                "Links",
                obj({"payment_links": {"type": "array", "items": _LINK_VIEW}}),
            ),
            403: "Not a merchant",
        },
    )
    @auth_required
    def get(self):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        links = (
            PaymentLink.query.filter_by(merchant_id=merchant.id)
            .order_by(PaymentLink.created_at.desc())
            .limit(100)
            .all()
        )
        return {"payment_links": [_link_view(l) for l in links]}, 200

    @api_doc(
        "Create a one-use checkout link and QR code for a product (in-store or WhatsApp sales)",
        body=obj(
            {
                "product_id": {
                    **INT,
                    "description": "One of the merchant's active products",
                },
                "quantity": {**INT, "default": 1},
                "expires_in_hours": {
                    **INT,
                    "default": 24,
                    "minimum": 1,
                    "maximum": 168,
                },
                "note": {**STR, "maxLength": 200},
            },
            required=["product_id"],
        ),
        responses={
            201: ("Created, with `qr_svg`", _LINK_VIEW),
            400: "Product, quantity or expiry isn't valid",
            403: "Not a merchant",
        },
    )
    @auth_required
    def post(self):
        """Create a one-use checkout link + QR code for a product (expires after 24 hours by default)."""
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        data = request.get_json() or {}
        product = Product.query.filter_by(
            id=data.get("product_id"), merchant_id=merchant.id
        ).first()
        if not product or product.status != "active":
            return {"error": "Choose one of your active products"}, 400
        try:
            quantity = int(data.get("quantity", 1))
            hours = int(data.get("expires_in_hours", 24))
        except (TypeError, ValueError):
            return {"error": "quantity and expires_in_hours must be numbers"}, 400
        if quantity < 1 or quantity > (product.stock_quantity or 0):
            return {"error": "Quantity isn't in stock"}, 400
        if not 1 <= hours <= 168:
            return {"error": "Links can last between 1 hour and 7 days"}, 400
        link = PaymentLink(
            token=secrets.token_urlsafe(16),
            merchant_id=merchant.id,
            product_id=product.id,
            quantity=quantity,
            note=(data.get("note") or "")[:200] or None,
            expires_at=datetime.utcnow() + timedelta(hours=hours),
        )
        db.session.add(link)
        db.session.commit()
        return _link_view(link, with_qr=True), 201


class MerchantPaymentLinkResource(Resource):
    @api_doc(
        "One payment link, with its QR code",
        responses={
            200: ("The link", _LINK_VIEW),
            403: "Not a merchant",
            404: "Link not found",
        },
    )
    @auth_required
    def get(self, link_id):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        link = PaymentLink.query.filter_by(id=link_id, merchant_id=merchant.id).first()
        if not link:
            return {"error": "Link not found"}, 404
        return _link_view(link, with_qr=True), 200

    @api_doc(
        "Cancel an active payment link",
        responses={
            200: ("Cancelled", MESSAGE),
            403: "Not a merchant",
            404: "Link not found",
        },
    )
    @auth_required
    def delete(self, link_id):
        merchant = _merchant()
        if not merchant:
            return {"error": "Unauthorized"}, 403
        link = PaymentLink.query.filter_by(id=link_id, merchant_id=merchant.id).first()
        if not link:
            return {"error": "Link not found"}, 404
        if link.status == PaymentLink.ACTIVE:
            link.status = PaymentLink.CANCELLED
            db.session.commit()
        return {"message": "Link cancelled"}, 200


class CustomerPaymentLinkResource(Resource):
    @api_doc(
        "What a customer sees after scanning the QR code or opening a link",
        responses={
            200: (
                "The product on offer",
                obj(
                    {
                        "token": STR,
                        "merchant_name": STR,
                        "note": STR,
                        "quantity": INT,
                        "expires_at": STR,
                        "product": obj(
                            {
                                "id": INT,
                                "name": STR,
                                "description": STR,
                                "price": NUM,
                                "image": STR,
                                "category": STR,
                            }
                        ),
                    }
                ),
            ),
            403: "Not signed in as a customer",
            404: "Link not found",
            410: "Expired or already used",
        },
    )
    @auth_required
    def get(self, token):
        """What a customer sees after scanning the QR / opening the link."""
        user = current_user()
        if user.role != "customer":
            return {"error": "Please sign in as a customer to use this link"}, 403
        link = PaymentLink.query.filter_by(token=token).first()
        if not link:
            return {"error": "Link not found"}, 404
        if not link.is_usable():
            return {
                "error": "This link has expired or was already used. Ask the merchant for a new one."
            }, 410
        p = link.product
        return {
            "token": link.token,
            "merchant_name": link.merchant.business_name or link.merchant.full_name,
            "note": link.note,
            "quantity": link.quantity,
            "product": {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "price": float(p.price),
                "image": p.main_image,
                "category": p.category,
            },
            "expires_at": link.expires_at.isoformat(),
        }, 200
