"""Authorization helper functions.

Lightweight role checks for use in views and templates. Full permission
handling is intentionally out of scope for this task.
"""
from flask_login import current_user


def user_has_role(user, role_name):
    """Return True if the given user is logged in and has the named role."""
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return any(role.name == role_name for role in user.roles)


def current_user_has_role(role_name):
    """Return True if the currently logged-in user has the named role."""
    return user_has_role(current_user, role_name)
