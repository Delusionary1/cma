"""Attachment routes: upload, download and delete files for any record.

Every route requires a logged-in user (the app-wide gate enforces this), and
files live outside static/ so they are only reachable through the authenticated
download route. Uploads are validated by extension and stored under a generated
name to avoid collisions and path traversal.
"""
import os
import uuid
from urllib.parse import urlparse

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user
from werkzeug.utils import secure_filename

from app.extensions import db
from app.attachments.models import ATTACHMENT_KINDS, Attachment

attachments_bp = Blueprint("attachments", __name__)


def _allowed(filename):
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in current_app.config["ALLOWED_ATTACHMENT_EXTENSIONS"]


def _safe_next(target, fallback="/"):
    """Only allow same-site relative redirects (no open redirect)."""
    if not target:
        return fallback
    parsed = urlparse(target)
    if parsed.scheme == "" and parsed.netloc == "" and target.startswith("/"):
        return target
    return fallback


def _upload_dir():
    path = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(path, exist_ok=True)
    return path


def attachments_for(entity_type, entity_id):
    """All attachments for an owner, newest first (used by the template panel)."""
    return (
        Attachment.query.filter_by(entity_type=entity_type, entity_id=entity_id)
        .order_by(Attachment.created_at.desc())
        .all()
    )


@attachments_bp.route("/upload", methods=["POST"])
def upload():
    entity_type = request.form.get("entity_type", "").strip()
    entity_id = request.form.get("entity_id", "").strip()
    return_url = _safe_next(request.form.get("return_url"))

    if not entity_type or not entity_id.isdigit():
        flash("Invalid attachment target.", "danger")
        return redirect(return_url)

    file = request.files.get("file")
    if file is None or file.filename == "":
        flash("Please choose a file to upload.", "danger")
        return redirect(return_url)

    if not _allowed(file.filename):
        allowed = ", ".join(sorted(current_app.config["ALLOWED_ATTACHMENT_EXTENSIONS"]))
        flash(f"File type not allowed. Allowed: {allowed}.", "danger")
        return redirect(return_url)

    original = secure_filename(file.filename) or "file"
    ext = original.rsplit(".", 1)[1].lower() if "." in original else ""
    stored = f"{uuid.uuid4().hex}.{ext}" if ext else uuid.uuid4().hex
    dest = os.path.join(_upload_dir(), stored)
    file.save(dest)

    kind = request.form.get("kind", "").strip()
    record = Attachment(
        entity_type=entity_type, entity_id=int(entity_id),
        original_filename=original, stored_filename=stored,
        content_type=file.mimetype, file_size=os.path.getsize(dest),
        kind=kind if kind in ATTACHMENT_KINDS else None,
        notes=request.form.get("notes", "").strip() or None,
        uploaded_by_id=current_user.id,
    )
    db.session.add(record)
    db.session.commit()
    flash(f"Attachment '{original}' uploaded.", "success")
    return redirect(return_url)


@attachments_bp.route("/<int:attachment_id>/download")
def download(attachment_id):
    record = db.get_or_404(Attachment, attachment_id)
    inline = request.args.get("inline") == "1"
    return send_from_directory(
        _upload_dir(), record.stored_filename,
        as_attachment=not inline,
        download_name=record.original_filename,
    )


@attachments_bp.route("/<int:attachment_id>/delete", methods=["POST"])
def delete(attachment_id):
    record = db.get_or_404(Attachment, attachment_id)
    return_url = _safe_next(request.form.get("return_url"))
    path = os.path.join(_upload_dir(), record.stored_filename)
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass  # row goes regardless; an orphaned file is harmless
    db.session.delete(record)
    db.session.commit()
    flash("Attachment deleted.", "info")
    return redirect(return_url)
