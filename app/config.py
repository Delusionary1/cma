"""Application configuration.

Values are read from environment variables (loaded from a .env file via
python-dotenv) so secrets never live in the codebase. Sensible defaults are
provided for local development.
"""
import os

from dotenv import load_dotenv

# Load variables from a .env file in the project root, if present.
load_dotenv()

# Absolute path to the project root (one level up from this app/ package).
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Config:
    """Base configuration shared across environments."""

    # Used by Flask to sign sessions and CSRF tokens. Override in production!
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    # Database connection. Defaults to a local SQLite file for easy first setup.
    # In production set DATABASE_URL to a PostgreSQL URI, e.g.
    #   postgresql://user:password@localhost:5432/oil_gas_erp
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(BASE_DIR, "app.db"),
    )

    # Disable a feature we don't need that adds overhead.
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- File attachments (BRD §18) ---
    # Files are stored OUTSIDE static/ and served only through an authenticated
    # download route, so attachments stay private to logged-in users.
    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads")
    )
    # Max upload size (applies to the whole request). 10 MB.
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    # Allowed file extensions for attachments (invoices, receipts, photos, docs).
    ALLOWED_ATTACHMENT_EXTENSIONS = {
        "pdf", "png", "jpg", "jpeg", "gif", "webp", "heic",
        "doc", "docx", "xls", "xlsx", "csv", "txt",
    }
