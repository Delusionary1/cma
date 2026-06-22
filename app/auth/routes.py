"""Authentication routes: login and logout.

Uses Flask-Login to manage the user session. The login form is plain
HTML/Jinja for now (Flask-WTF forms will come in a later task).
"""
from urllib.parse import urlparse

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.auth.models import User

auth_bp = Blueprint("auth", __name__)


def _is_safe_next(target):
    """Allow only same-site relative redirects (prevents open redirects)."""
    if not target:
        return False
    parsed = urlparse(target)
    # No scheme and no network location => a local path like "/reports".
    return parsed.scheme == "" and parsed.netloc == "" and target.startswith("/")


@auth_bp.route("/")
def index():
    """The /auth root just sends you to the login page."""
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate a user from a username/password form."""
    # Already logged in? Go home.
    if current_user.is_authenticated:
        return redirect(url_for("core.home"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        user = User.query.filter_by(username=username).first()

        if user and user.is_active and user.check_password(password):
            login_user(user)
            flash(f"Welcome back, {user.full_name}!", "success")

            next_page = request.args.get("next")
            if _is_safe_next(next_page):
                return redirect(next_page)
            return redirect(url_for("core.home"))

        # Same generic message whether the username or password was wrong.
        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    """Log the current user out and return to the login page."""
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
