"""Idempotent seeding for authentication data (roles + the admin user).

Safe to run multiple times:
  - roles are looked up by their unique name before inserting
  - the admin user is looked up by username before inserting
  - the password is only ever stored as a hash (never plain text)
"""
from app.extensions import db
from app.auth.models import Role, User

# The six roles from the requirements (role name is the unique key).
ROLE_NAMES = [
    "Admin / Owner",
    "Head Office Manager",
    "Petrol Pump Manager",
    "Cashier",
    "Accountant",
    "Transport Manager",
]

ADMIN_USERNAME = "admin"
ADMIN_EMAIL = "admin@example.com"
ADMIN_FULL_NAME = "Admin User"
ADMIN_DEFAULT_PASSWORD = "admin123"  # local development only
ADMIN_ROLE_NAME = "Admin / Owner"

# Test users for trying out each role locally (development only).
# (username, password, full_name, email, role_name)
TEST_USERS = [
    ("cashier", "cashier123", "Cashier User",
     "cashier@example.com", "Cashier"),
    ("accountant", "accountant123", "Accountant User",
     "accountant@example.com", "Accountant"),
    ("transport", "transport123", "Transport Manager User",
     "transport@example.com", "Transport Manager"),
    ("pumpmanager", "pump123", "Petrol Pump Manager User",
     "pumpmanager@example.com", "Petrol Pump Manager"),
    ("headoffice", "office123", "Head Office Manager User",
     "headoffice@example.com", "Head Office Manager"),
]


def _ensure_user(username, password, full_name, email, role_name, created):
    """Create a user with one role if the username does not already exist."""
    if User.query.filter_by(username=username).first() is not None:
        return  # already exists -> do not duplicate or change password

    user = User(full_name=full_name, username=username, email=email)
    user.set_password(password)  # only the hash is stored

    role = Role.query.filter_by(name=role_name).first()
    if role is not None:
        user.roles.append(role)

    db.session.add(user)
    created["users"] += 1


def seed_auth_data():
    """Insert roles, the admin user, and the test users (all idempotent).

    Returns a small summary dict so callers can print what happened.
    """
    created = {"roles": 0, "users": 0}

    # --- Roles ---
    for role_name in ROLE_NAMES:
        if Role.query.filter_by(name=role_name).first() is None:
            db.session.add(Role(name=role_name))
            created["roles"] += 1

    # Commit so roles have ids before we attach them to users.
    db.session.commit()

    # --- Admin user ---
    _ensure_user(
        ADMIN_USERNAME, ADMIN_DEFAULT_PASSWORD, ADMIN_FULL_NAME,
        ADMIN_EMAIL, ADMIN_ROLE_NAME, created,
    )

    # --- Role test users ---
    for username, password, full_name, email, role_name in TEST_USERS:
        _ensure_user(username, password, full_name, email, role_name, created)

    db.session.commit()
    return created
