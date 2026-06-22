"""Attachment model (BRD §18).

A single polymorphic table holds files for ANY record: an attachment points at
its owner by (entity_type, entity_id) — e.g. ("pump_purchase", 42). The file
itself is stored on disk under a generated name; this row keeps the metadata
(original name, type, size, who uploaded it) plus a human label for the kind of
document.
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


# Friendly labels for the kinds of document the BRD lists (§18). Free-form;
# the entity itself usually implies the kind, so this is optional context.
ATTACHMENT_KINDS = [
    "Invoice",
    "PSO Invoice",
    "Delivery Challan",
    "Receipt",
    "Vehicle Document",
    "Maintenance Proof",
    "Photo",
    "Other",
]


class Attachment(db.Model):
    __tablename__ = "attachments"

    id = db.Column(db.Integer, primary_key=True)
    # Polymorphic owner.
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)

    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    content_type = db.Column(db.String(120), nullable=True)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    kind = db.Column(db.String(40), nullable=True)
    notes = db.Column(db.String(300), nullable=True)

    uploaded_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    uploaded_by = db.relationship("User")

    @property
    def is_image(self):
        return (self.content_type or "").startswith("image/")

    @property
    def size_label(self):
        size = self.file_size or 0
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def __repr__(self):
        return (
            f"<Attachment id={self.id} {self.entity_type}#{self.entity_id} "
            f"{self.original_filename!r}>"
        )
