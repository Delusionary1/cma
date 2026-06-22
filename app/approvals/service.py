"""Approval workflow service (BRD §16).

A central place that decides which sensitive entries need approval, creates/
updates their Approval record idempotently, and resolves a queue link back to
the source record. Approval is an oversight/review layer recorded against the
entry — sensitive entries still operate, but managers can review, approve or
reject them, and rejected items are flagged for correction.
"""
from decimal import Decimal

from flask import url_for

from app.extensions import db
from app.approvals.models import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    Approval,
)

# Roles allowed to approve/reject.
APPROVAL_ROLES = ("Admin / Owner", "Head Office Manager")

# Which entity types require approval, and the amount threshold above which it
# kicks in. None = always require (regardless of amount).
APPROVAL_RULES = {
    "ho_expense": Decimal("50000"),
    "vendor_payment": Decimal("100000"),
    "bulk_sale": None,
    "agency_sale": None,
}

# Human labels + how to link to each source record's detail page.
APPROVAL_META = {
    "ho_expense": ("Head Office Expense", "head_office.expenses_view", "expense_id"),
    "vendor_payment": ("Vendor Payment", "head_office.vendor_payments_view", "payment_id"),
    "bulk_sale": ("Bulk Sale", "bulk_sale.sales_view", "sale_id"),
    "agency_sale": ("Agency Sale", "agency.sales_view", "sale_id"),
}


def label_for(entity_type):
    meta = APPROVAL_META.get(entity_type)
    return meta[0] if meta else entity_type


def view_url(approval):
    """URL of the source record's detail page, or None if unknown."""
    meta = APPROVAL_META.get(approval.entity_type)
    if not meta:
        return None
    _, endpoint, arg = meta
    return url_for(endpoint, **{arg: approval.entity_id})


def needs_approval(entity_type, amount):
    # APPROVALS DISABLED (2026-06-21, user request): nothing requires approval —
    # everything runs/posts automatically. `request_if_needed` therefore creates
    # no approval records (and clears any existing), so `is_blocked` is always
    # False. To re-enable, restore the threshold logic below.
    return False
    # --- original threshold logic (kept for easy re-enable) ---
    # if entity_type not in APPROVAL_RULES:
    #     return False
    # threshold = APPROVAL_RULES[entity_type]
    # if threshold is None:
    #     return True
    # return Decimal(str(amount or 0)) >= threshold


def for_entity(entity_type, entity_id):
    return Approval.query.filter_by(
        entity_type=entity_type, entity_id=entity_id
    ).first()


def is_blocked(entity_type, entity_id):
    """True when a sensitive entry is held by approval (a record exists and is
    not yet Approved — i.e. Pending or Rejected).

    Grandfathered by design: entries with NO approval record are NOT blocked, so
    pre-existing posted entries keep posting and only newly-created sensitive
    entries (which get a Pending approval at entry time) are gated out of the GL
    until a manager approves them.
    """
    if entity_id is None:
        return False
    appr = for_entity(entity_type, entity_id)
    return appr is not None and appr.status != APPROVAL_APPROVED


def request_if_needed(entity_type, entity_id, amount, requested_by_id, title=None):
    """Create/refresh a Pending approval when the entry is sensitive enough.

    Idempotent. If the entry drops below the threshold an existing approval is
    removed. If a previously-approved entry is edited to a different amount it is
    re-opened (Pending) so the change is re-reviewed.
    """
    existing = for_entity(entity_type, entity_id)
    amount = Decimal(str(amount or 0))

    if not needs_approval(entity_type, amount):
        if existing is not None:
            db.session.delete(existing)
        return

    if existing is None:
        db.session.add(Approval(
            entity_type=entity_type, entity_id=entity_id, title=title,
            amount=amount, status=APPROVAL_PENDING,
            requested_by_id=requested_by_id,
        ))
        return

    existing.title = title
    if existing.status == APPROVAL_APPROVED and existing.amount != amount:
        existing.status = APPROVAL_PENDING
        existing.approved_by_id = None
        existing.approved_at = None
        existing.rejection_reason = None
    existing.amount = amount


def approve(approval, user_id):
    from app.approvals.models import _utcnow
    approval.status = APPROVAL_APPROVED
    approval.approved_by_id = user_id
    approval.approved_at = _utcnow()
    approval.rejection_reason = None


def reject(approval, user_id, reason=None):
    from app.approvals.models import _utcnow
    approval.status = APPROVAL_REJECTED
    approval.approved_by_id = user_id
    approval.approved_at = _utcnow()
    approval.rejection_reason = reason or None


def reopen(approval):
    approval.status = APPROVAL_PENDING
    approval.approved_by_id = None
    approval.approved_at = None
    approval.rejection_reason = None


def pending_count():
    return Approval.query.filter_by(status=APPROVAL_PENDING).count()
