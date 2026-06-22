"""Flask extension instances.

These are created here (un-attached to any app) so they can be imported
anywhere without causing circular imports. They are bound to the actual
application later, inside the create_app() factory.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager

# Database ORM
db = SQLAlchemy()

# Database migrations (Alembic under the hood)
migrate = Migrate()

# User session / authentication management
login_manager = LoginManager()
# Where to send anonymous users who hit a @login_required route.
login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "warning"
