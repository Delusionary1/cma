"""Approval model (BRD §16).

One polymorphic table holds an approval record for any sensitive entry, pointed
at its source by (entity_type, entity_id) — exactly like attachments. It tracks
the review workflow: who requested it, the current status, and who approved or
rejected it (with a reason). Unique per source record.
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


APPROVAL_PENDING = "Pending"
APPROVAL_APPROVED = "Approved"
APPROVAL_REJECTED = "Rejected"
APPROVAL_STATUSES = [APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_REJECTED]


class Approval(db.Model):
    __tablename__ = "approvals"
    __table_args__ = (
        db.UniqueConstraint("entity_type", "entity_id", name="uq_approval_entity"),
    )

    id = db.Column(db.Integer, primary_key=True)
    entity_type = db.Column(db.String(50), nullable=False, index=True)
    entity_id = db.Column(db.Integer, nullable=False, index=True)

    title = db.Column(db.String(200), nullable=True)
    amount = db.Column(db.Numeric(16, 2), nullable=True)
    status = db.Column(db.String(20), nullable=False, default=APPROVAL_PENDING, index=True)

    requested_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    approved_at = db.Column(db.DateTime, nullable=True)
    rejection_reason = db.Column(db.String(300), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    requested_by = db.relationship("User", foreign_keys=[requested_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def __repr__(self):
        return (
            f"<Approval {self.entity_type}#{self.entity_id} {self.status!r}>"
        )
