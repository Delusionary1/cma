"""Application factory for the Oil & Gas Multi-Business ERP.

This module wires the whole application together:
  - creates the Flask app
  - loads configuration
  - initialises extensions (SQLAlchemy, Migrate)
  - registers every feature blueprint

No business logic or database models live here yet. This is only the
foundation that future modules will plug into.
"""
from flask import Flask

from .config import Config
from .extensions import db, migrate, login_manager


def create_app(config_class=Config):
    """Create and configure a Flask application instance."""
    app = Flask(__name__)
    app.config.from_object(config_class)

    # --- Initialise extensions ---
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    # --- Import models ---
    # Importing the models modules here ensures SQLAlchemy/Alembic are aware of
    # every table when running `flask db migrate`. (Imported for side effects.)
    from .core import models  # noqa: F401
    from .auth import models as auth_models  # noqa: F401
    from .accounting import models as accounting_models  # noqa: F401
    from .petrol_pumps import models as pump_models  # noqa: F401
    from .head_office import models as head_office_models  # noqa: F401
    from .bulk_sale import models as bulk_sale_models  # noqa: F401
    from .carriage import models as carriage_models  # noqa: F401
    from .agency import models as agency_models  # noqa: F401
    from .attachments import models as attachment_models  # noqa: F401
    from .approvals import models as approval_models  # noqa: F401

    # --- Flask-Login user loader ---
    # Tells Flask-Login how to reload a user object from the id stored in the
    # session cookie.
    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(auth_models.User, int(user_id))

    # --- Private application gate ---
    # This is an internal company system: EVERY page requires a signed-in
    # user. Only the auth pages and static assets are reachable anonymously,
    # so the sign-in screen is always the first page anyone sees.
    from flask import request
    from flask_login import current_user

    @app.before_request
    def require_login_everywhere():
        if current_user.is_authenticated:
            return None
        endpoint = request.endpoint or ""
        if endpoint == "static" or endpoint.startswith("auth."):
            return None
        return login_manager.unauthorized()

    # --- Template helpers ---
    # Make can_access_module() available in every template (used by the navbar
    # to show only the links the logged-in user is allowed to access).
    from .auth.access import can_access_module

    @app.context_processor
    def inject_access_helpers():
        from .attachments.routes import attachments_for
        from .attachments.models import ATTACHMENT_KINDS
        from .approvals import service as approval_service
        from flask_login import current_user

        # Pending-approval badge for the sidebar (only for approver roles).
        pending = 0
        can_approve = False
        if current_user.is_authenticated:
            can_approve = any(
                current_user.has_role(r) for r in approval_service.APPROVAL_ROLES
            )
            if can_approve:
                pending = approval_service.pending_count()

        return {
            "can_access_module": can_access_module,
            "entity_attachments": attachments_for,
            "attachment_kinds": ATTACHMENT_KINDS,
            "approval_for_entity": approval_service.for_entity,
            "can_approve_entries": can_approve,
            "approvals_pending": pending,
        }

    # --- Error handlers ---
    from flask import render_template

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html"), 403

    # --- Register CLI commands ---
    register_cli(app)

    # --- Register blueprints ---
    # Each future ERP module is its own blueprint. They are mounted under a
    # URL prefix so the routes stay neatly separated, exactly like the
    # business units they represent.
    from .core.routes import core_bp
    from .auth.routes import auth_bp
    from .master_data.routes import master_data_bp
    from .accounting.routes import accounting_bp
    from .petrol_pumps.routes import petrol_pumps_bp
    from .bulk_sale.routes import bulk_sale_bp
    from .carriage.routes import carriage_bp
    from .agency.routes import agency_bp
    from .head_office.routes import head_office_bp
    from .reports.routes import reports_bp
    from .attachments.routes import attachments_bp
    from .approvals.routes import approvals_bp

    app.register_blueprint(core_bp)  # mounted at "/"
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(master_data_bp, url_prefix="/master-data")
    app.register_blueprint(accounting_bp, url_prefix="/accounting")
    app.register_blueprint(petrol_pumps_bp, url_prefix="/petrol-pumps")
    app.register_blueprint(bulk_sale_bp, url_prefix="/bulk-sale")
    app.register_blueprint(carriage_bp, url_prefix="/carriage")
    app.register_blueprint(agency_bp, url_prefix="/agency")
    app.register_blueprint(head_office_bp, url_prefix="/head-office")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(attachments_bp, url_prefix="/attachments")
    app.register_blueprint(approvals_bp, url_prefix="/approvals")

    return app


def register_cli(app):
    """Register custom Flask CLI commands."""

    @app.cli.command("seed")
    def seed():
        """Seed the database with core + auth data (safe to re-run)."""
        from .core.seed import seed_data
        from .auth.seed import seed_auth_data
        from .accounting.seed import seed_accounting_data
        from .petrol_pumps.seed import seed_pump_setup_data

        created = {
            **seed_data(),
            **seed_auth_data(),
            **seed_accounting_data(),
            **seed_pump_setup_data(),
        }
        summary = ", ".join(f"{key}: {count}" for key, count in created.items())
        print(f"Seeding complete. New records -> {summary}")
