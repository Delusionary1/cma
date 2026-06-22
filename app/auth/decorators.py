"""Role-based access control decorators.

Use these on view functions to restrict access by role:

    @role_required("Cashier", "Petrol Pump Manager")
    @admin_required()
    @roles_required_any("Accountant", "Head Office Manager")

Rules applied by every decorator here:
  * Not logged in        -> redirect to the login page (with a flash message).
  * Logged in, no access  -> clean 403 Forbidden page.
  * Admin / Owner         -> always allowed.
"""
from functools import wraps

from flask import abort
from flask_login import current_user

from app.extensions import login_manager
from app.auth.access import ADMIN_ROLE_NAME


def _ensure_roles(required_role_names):
    """Check the current user against the required roles.

    Returns a redirect response if the user must log in first, otherwise calls
    abort(403) on denial, or returns None when access is granted.
    """
    # 1. Must be logged in. Reuse Flask-Login's standard "unauthorized" handler
    #    so the user is redirected to the login page with the usual ?next=...
    if not current_user.is_authenticated:
        return login_manager.unauthorized()

    # 2. Admin can do anything.
    if current_user.has_role(ADMIN_ROLE_NAME):
        return None

    # 3. Otherwise the user needs at least one of the required roles.
    if any(current_user.has_role(name) for name in required_role_names):
        return None

    # 4. Logged in but not permitted.
    abort(403)


def role_required(*role_names):
    """Allow access only to users having at least one of the given roles."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            response = _ensure_roles(role_names)
            if response is not None:
                return response
            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def roles_required_any(*role_names):
    """Alias of role_required: access if the user has ANY of these roles."""
    return role_required(*role_names)


def admin_required():
    """Allow access only to Admin / Owner users."""
    return role_required(ADMIN_ROLE_NAME)
