"""Approvals queue: a manager inbox to review, approve or reject sensitive
entries (BRD §16). Restricted to the approval roles.
"""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.auth.decorators import role_required
from app.accounting import posting
from app.approvals import service
from app.approvals.models import APPROVAL_STATUSES, Approval

approvals_bp = Blueprint("approvals", __name__)

APPROVAL_ROLES = service.APPROVAL_ROLES


@approvals_bp.route("/")
@role_required(*APPROVAL_ROLES)
def index():
    f_status = request.args.get("status", "Pending").strip()
    f_type = request.args.get("type", "").strip()

    query = Approval.query
    if f_status and f_status in APPROVAL_STATUSES:
        query = query.filter(Approval.status == f_status)
    if f_type and f_type in service.APPROVAL_META:
        query = query.filter(Approval.entity_type == f_type)
    items = query.order_by(Approval.created_at.desc()).all()

    rows = [{"a": a, "label": service.label_for(a.entity_type),
             "url": service.view_url(a)} for a in items]
    counts = {s: Approval.query.filter_by(status=s).count() for s in APPROVAL_STATUSES}

    return render_template(
        "approvals/index.html", rows=rows, counts=counts,
        filters={"status": f_status, "type": f_type},
        statuses=APPROVAL_STATUSES, types=service.APPROVAL_META,
    )


@approvals_bp.route("/<int:approval_id>/approve", methods=["POST"])
@role_required(*APPROVAL_ROLES)
def approve(approval_id):
    approval = db.get_or_404(Approval, approval_id)
    service.approve(approval, current_user.id)
    posting.resync_for_approval(approval.entity_type, approval.entity_id)
    db.session.commit()
    flash(f"{service.label_for(approval.entity_type)} approved.", "success")
    return redirect(request.form.get("return_url") or url_for("approvals.index"))


@approvals_bp.route("/<int:approval_id>/reject", methods=["POST"])
@role_required(*APPROVAL_ROLES)
def reject(approval_id):
    approval = db.get_or_404(Approval, approval_id)
    service.reject(approval, current_user.id, request.form.get("rejection_reason", "").strip())
    posting.resync_for_approval(approval.entity_type, approval.entity_id)
    db.session.commit()
    flash(f"{service.label_for(approval.entity_type)} rejected.", "info")
    return redirect(request.form.get("return_url") or url_for("approvals.index"))


@approvals_bp.route("/<int:approval_id>/reopen", methods=["POST"])
@role_required(*APPROVAL_ROLES)
def reopen(approval_id):
    approval = db.get_or_404(Approval, approval_id)
    service.reopen(approval)
    posting.resync_for_approval(approval.entity_type, approval.entity_id)
    db.session.commit()
    flash("Approval reopened (pending again).", "info")
    return redirect(request.form.get("return_url") or url_for("approvals.index"))
