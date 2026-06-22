"""Role-Based Access Control (RBAC) access map.

This is the SINGLE SOURCE OF TRUTH for "which role can access which module".
Route decorators, the navbar, and the verification tests all read from here,
so access rules never drift apart.

Module keys (used in URLs, decorators and the navbar):
    master_data, accounting, petrol_pumps, bulk_sale,
    carriage, agency, head_office, reports
"""
from flask_login import current_user

# The role that can access everything.
ADMIN_ROLE_NAME = "Admin / Owner"

# All module keys the ERP exposes (order matters for the navbar).
MODULE_KEYS = [
    "master_data",
    "accounting",
    "petrol_pumps",
    "bulk_sale",
    "carriage",
    "agency",
    "head_office",
    "reports",
]

# Role name -> set of module keys that role may access.
# Admin / Owner gets every module automatically.
ROLE_MODULE_ACCESS = {
    ADMIN_ROLE_NAME: set(MODULE_KEYS),
    "Head Office Manager": {
        "master_data",
        "accounting",
        "petrol_pumps",
        "bulk_sale",
        "carriage",
        "agency",
        "head_office",
        "reports",
    },
    "Petrol Pump Manager": {"petrol_pumps", "reports"},
    "Cashier": {"petrol_pumps"},
    "Accountant": {"accounting", "head_office", "reports"},
    "Transport Manager": {"carriage", "reports"},
}


def roles_for_module(module_name):
    """Return the list of role names allowed to access the given module.

    (Includes Admin / Owner, which is harmless because admin is always allowed
    by the decorators anyway.)
    """
    return [
        role_name
        for role_name, modules in ROLE_MODULE_ACCESS.items()
        if module_name in modules
    ]


def can_access_module(module_name, user=None):
    """Return True if the user may access the given module.

    Defaults to the currently logged-in user, which makes it convenient to call
    straight from Jinja templates: can_access_module('reports').
    """
    if user is None:
        user = current_user

    if not getattr(user, "is_authenticated", False):
        return False

    # Admin can access everything.
    if user.has_role(ADMIN_ROLE_NAME):
        return True

    return any(user.has_role(role) for role in roles_for_module(module_name))
