"""Deactivating accounts. Accounts are never deleted (money, KYC, fraud and audit records must stay),
and can't be deactivated while money is still owed either way: a suspended customer can't sign in to
pay, and a suspended merchant's settlements would be stuck."""
from ..extensions import db


def deactivation_blocker(user):
    """Why this account can't be deactivated now, or None."""
    from ..models.instalment import InstalmentPlan
    from . import ledger

    if user.role == "customer":
        # Every plan that can still owe money, charged-off (defaulted) ones included
        plans = InstalmentPlan.query.filter(
            InstalmentPlan.customer_id == user.id,
            InstalmentPlan.status.notin_(("cancelled", "completed")),
        ).all()
        owed = (
            sum((b["outstanding"] for b in ledger.plan_balances(plans).values()), 0)
            if plans
            else 0
        )
        if owed > 0:
            return (
                f"This customer still owes GHS {float(owed):,.2f} on {len(plans)} plan(s). They must be able "
                "to sign in and pay, so the account can't be deactivated until that's settled."
            )
    elif user.role == "merchant":
        from ..models.settlement import Settlement, SettlementLine

        unbatched = (
            db.session.query(
                db.func.coalesce(db.func.sum(SettlementLine.net_pesewas), 0)
            )
            .filter(
                SettlementLine.merchant_id == user.id,
                SettlementLine.settlement_id.is_(None),
            )
            .scalar()
            or 0
        )
        unpaid = Settlement.query.filter(
            Settlement.merchant_id == user.id,
            Settlement.status != Settlement.PAID,
            Settlement.net_pesewas != 0,
        ).count()
        if unbatched != 0 or unpaid:
            return (
                "This merchant still has settlements to be paid (or clawbacks to be taken). "
                "Deactivate them once those are settled."
            )
    return None


SIGN_IN_STATUSES = ("approved", "active", "restricted")  # User.is_valid


def status_change_error(user, new_status):
    """Why an admin can't set this account status, or None.

    - A status change only reinstates an account. New accounts are approved through their own
      checks (customer approval / KYC, merchant KYB), never by setting the status.
    - A merchant can't be active without KYB.
    - Any status that blocks sign-in is refused while money is owed either way.
    """
    if user.role == "admin":
        return "Admins are managed on Settings > Team and access."
    if new_status in ("approved", "active"):
        if user.status in (None, "pending", "rejected"):
            return "New accounts are approved through " + (
                "Merchant KYB."
                if user.role == "merchant"
                else "customer approval and KYC."
            )
        if user.role == "merchant" and user.kyc_status != "verified":
            return "This merchant hasn't passed KYB, so they can't be made active."
    if new_status not in SIGN_IN_STATUSES:
        blocked = deactivation_blocker(user)
        if blocked:
            return (
                blocked + " Use 'restricted' meanwhile: they can still sign in and pay."
            )
    return None


def approve_after_checks(user):
    """KYC/KYB passed: approve a new or rejected account, but never lift a restriction or suspension."""
    if user.status in (None, "pending", "rejected"):
        user.status = "approved"


def deactivate(user):
    """Suspend the account (no sign-in, no new purchases or sales). Raises ValueError if money is owed."""
    reason = deactivation_blocker(user)
    if reason:
        raise ValueError(reason)
    user.status = "suspended"
    db.session.commit()
