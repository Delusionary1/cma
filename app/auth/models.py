"""Authentication & authorization models.

Defines the foundation for role-based access:
  - User      : a person who can log in
  - Role      : a named role (Admin, Cashier, ...)
  - UserRole  : association object linking users and roles (many-to-many)

Passwords are never stored in plain text; only a salted hash is kept.
"""
from datetime import datetime, timezone

from flask_login import UserMixin
from sqlalchemy.ext.associationproxy import association_proxy
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def _utcnow():
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    """A user account that can authenticate into the ERP."""

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    username = db.Column(db.String(80), nullable=False, unique=True, index=True)
    email = db.Column(db.String(150), nullable=True, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # Association object rows linking this user to roles.
    user_roles = db.relationship(
        "UserRole", back_populates="user", cascade="all, delete-orphan"
    )
    # Convenience: treat user.roles as a list of Role objects directly.
    roles = association_proxy(
        "user_roles", "role", creator=lambda role: UserRole(role=role)
    )

    # --- Password handling ---
    def set_password(self, password):
        """Hash and store the given plain-text password."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Return True if the given plain-text password matches the hash."""
        return check_password_hash(self.password_hash, password)

    # --- Flask-Login ---
    def get_id(self):
        """Return the unique id used by Flask-Login (must be a string)."""
        return str(self.id)

    # --- Role helper ---
    def has_role(self, role_name):
        """Return True if this user has the named role."""
        return any(role.name == role_name for role in self.roles)

    def __repr__(self):
        return f"<User id={self.id} username={self.username!r}>"


class Role(db.Model):
    """A named role that grants a set of responsibilities."""

    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True, index=True)
    description = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user_roles = db.relationship(
        "UserRole", back_populates="role", cascade="all, delete-orphan"
    )
    users = association_proxy(
        "user_roles", "user", creator=lambda user: UserRole(user=user)
    )

    def __repr__(self):
        return f"<Role id={self.id} name={self.name!r}>"


class UserRole(db.Model):
    """Association object connecting a User to a Role (many-to-many)."""

    __tablename__ = "user_roles"
    __table_args__ = (
        db.UniqueConstraint("user_id", "role_id", name="uq_user_role"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    role_id = db.Column(
        db.Integer, db.ForeignKey("roles.id"), nullable=False, index=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    user = db.relationship("User", back_populates="user_roles")
    role = db.relationship("Role", back_populates="user_roles")

    def __repr__(self):
        return f"<UserRole user_id={self.user_id} role_id={self.role_id}>"
