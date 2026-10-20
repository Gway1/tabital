"""Management Access (founder, 2026-09-27).

"All approvals, settings and rates, highly sensitive information and changes are reserved for
Management Access." Admin accounts have an admin_level:

- management: everything.
- operations: can read every admin queue (with personal data masked, services/pii.py), send
  payment reminders and add customer notes. Nothing else.

The rule is enforced once for every request (app/__init__.py register_access_control), so a new
endpoint is management-only unless it's added to OPERATIONS_WRITES. That keeps the safe default.
"""
import re

MANAGEMENT = "management"
OPERATIONS = "operations"
LEVELS = (MANAGEMENT, OPERATIONS)

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Changes an operations admin may make
OPERATIONS_WRITES = [
    re.compile(
        r"^/admin/collection/\d+/reminder$"
    ),  # collections reminder (SMS / in-app)
    re.compile(r"^/admin/customers/\d+/note$"),  # a note on the customer's file
    re.compile(r"^/notifications(/|$)"),  # their own notifications
]

# Reads that are highly sensitive: full personal data, business financials, rates, bulk exports
MANAGEMENT_READS = [
    re.compile(r"^/admin/pii/"),
    re.compile(r"^/admin/economics/"),
    re.compile(r"^/admin/reports/"),  # revenue and KPIs: business financials
    re.compile(r"^/admin/business-settings"),
    re.compile(r"^/admin/settings/"),
    re.compile(r"^/admin/team"),
    re.compile(r"/export$"),  # CSV exports aren't masked
    re.compile(r"^/admin/reports/download$"),
]

MESSAGE = "This needs Management Access. Ask a manager to do it."


def is_management(user) -> bool:
    return bool(user) and user.role == "admin" and user.admin_level == MANAGEMENT


def needs_management(method: str, path: str) -> bool:
    """For an admin who isn't management: is this request off limits?"""
    if method.upper() in WRITE_METHODS:
        return not any(p.search(path) for p in OPERATIONS_WRITES)
    return any(p.search(path) for p in MANAGEMENT_READS)
