"""Head Office routes: dashboard + cash received from petrol pumps.

Accessible by the head_office module roles (Admin / Owner, Head Office Manager,
Accountant). Recording a receipt into a cash/bank account increases that
account's balance, kept idempotent by an `is_posted` flag (same pattern as pump
purchase stock posting).
"""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from app.extensions import db
from app.accounting import posting
from app.approvals import service as approvals
from app.auth.access import roles_for_module
from app.auth.decorators import role_required
from app.core.models import (
    BusinessUnit,
    BusinessUnitType,
    CashBankAccount,
    ExpenseCategory,
    PetrolPump,
    Vendor,
)
from app.petrol_pumps.models import PumpDailyClosing
from app.head_office.models import (
    HEAD_OFFICE_PAYMENT_METHODS,
    HO_SALARY_PAYMENT_TYPES,
    CashTransfer,
    HeadOfficeCashReceipt,
    HeadOfficeExpense,
    HeadOfficeSalaryPayment,
    HeadOfficeStaff,
    VendorPayment,
)

head_office_bp = Blueprint("head_office", __name__)

HO_ROLES = roles_for_module("head_office")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _active_pumps():
    return PetrolPump.query.filter_by(is_active=True).order_by(PetrolPump.name).all()


def _active_accounts():
    return (
        CashBankAccount.query.filter_by(is_active=True)
        .order_by(CashBankAccount.name)
        .all()
    )


def _selectable_closings():
    """Active daily closings with cash submitted (for optional linking)."""
    return (
        PumpDailyClosing.query.filter(
            PumpDailyClosing.is_active.is_(True),
            PumpDailyClosing.cash_submitted_to_head_office > 0,
        )
        .order_by(PumpDailyClosing.closing_date.desc())
        .all()
    )


def _int_or_none(raw):
    raw = (raw or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _parse_amount(raw, label, errors):
    raw = (raw or "").strip()
    if raw == "":
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors.append(f"{label} must be a number.")
        return None
    if value < 0:
        errors.append(f"{label} must not be negative.")
        return None
    return value


# --- Idempotent account-balance posting ---
def _receipt_should_post(receipt):
    return receipt.is_active and receipt.received_into_account_id is not None


def _apply_receipt(receipt, sign):
    account = receipt.received_into_account
    if account is not None:
        account.current_balance = (
            account.current_balance or Decimal("0")
        ) + (sign * receipt.amount)


def _can_unpost_receipt(receipt):
    account = receipt.received_into_account
    if account is not None and (
        account.current_balance or Decimal("0")
    ) - receipt.amount < 0:
        return False
    return True


def _sync_receipt(receipt):
    desired = _receipt_should_post(receipt)
    if desired and not receipt.is_posted:
        _apply_receipt(receipt, 1)
        receipt.is_posted = True
    elif not desired and receipt.is_posted:
        _apply_receipt(receipt, -1)
        receipt.is_posted = False


def _closing_already_receipted(closing_id, exclude_id=None):
    query = HeadOfficeCashReceipt.query.filter(
        HeadOfficeCashReceipt.daily_closing_id == closing_id,
        HeadOfficeCashReceipt.is_active.is_(True),
    )
    if exclude_id is not None:
        query = query.filter(HeadOfficeCashReceipt.id != exclude_id)
    return db.session.query(query.exists()).scalar()


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@head_office_bp.route("/")
@role_required(*HO_ROLES)
def index():
    received_total = (
        db.session.query(db.func.coalesce(db.func.sum(HeadOfficeCashReceipt.amount), 0))
        .filter(HeadOfficeCashReceipt.is_active.is_(True))
        .scalar()
    )
    expense_total = (
        db.session.query(db.func.coalesce(db.func.sum(HeadOfficeExpense.amount), 0))
        .filter(HeadOfficeExpense.is_active.is_(True))
        .scalar()
    )
    # Head-office staff salaries/advances are head office expenses too.
    ho_salary_total = (
        db.session.query(db.func.coalesce(db.func.sum(HeadOfficeSalaryPayment.amount), 0))
        .filter(HeadOfficeSalaryPayment.is_active.is_(True))
        .scalar()
    )
    payment_total = (
        db.session.query(db.func.coalesce(db.func.sum(VendorPayment.amount), 0))
        .filter(VendorPayment.is_active.is_(True))
        .scalar()
    )
    # Pump fuel/lubricant purchases — Head Office buys for all pumps (BRD §10.5).
    from app.petrol_pumps.models import PumpPurchase
    purchase_total = (
        db.session.query(db.func.coalesce(db.func.sum(PumpPurchase.total_amount), 0))
        .filter(PumpPurchase.is_active.is_(True))
        .scalar()
    )

    counts = {
        "cash_receipts": HeadOfficeCashReceipt.query.count(),
        "cash_received_total": Decimal(str(received_total)),
        "expenses": HeadOfficeExpense.query.count(),
        "expense_total": Decimal(str(expense_total)) + Decimal(str(ho_salary_total)),
        "staff": HeadOfficeStaff.query.filter_by(is_active=True).count(),
        "ho_salary_total": Decimal(str(ho_salary_total)),
        "vendor_payments": VendorPayment.query.count(),
        "payment_total": Decimal(str(payment_total)),
        "pump_purchases": PumpPurchase.query.filter_by(is_active=True).count(),
        "pump_purchase_total": Decimal(str(purchase_total)),
    }

    # Cash & bank position — where the company's money sits right now (BRD §10.6).
    cash_accounts, bank_accounts, wallet_accounts = [], [], []
    for acc in (CashBankAccount.query.filter_by(is_active=True)
                .order_by(CashBankAccount.name).all()):
        kind = (acc.account_type or "").lower()
        if "bank" in kind:
            bank_accounts.append(acc)
        elif "wallet" in kind:
            wallet_accounts.append(acc)
        elif "cash" in kind:
            cash_accounts.append(acc)
    cash_total = sum((a.current_balance or Decimal("0")) for a in cash_accounts)
    bank_total = sum((a.current_balance or Decimal("0")) for a in bank_accounts)
    wallet_total = sum((a.current_balance or Decimal("0")) for a in wallet_accounts)
    treasury = {
        "cash_accounts": cash_accounts, "bank_accounts": bank_accounts,
        "wallet_accounts": wallet_accounts,
        "cash_total": cash_total, "bank_total": bank_total,
        "wallet_total": wallet_total,
        "grand_total": cash_total + bank_total + wallet_total,
    }

    # Cash still sitting at the pumps. `cash` is the live physical cash (display
    # only); `to_collect` is what head office may actually receive now — only the
    # cash confirmed via that pump's daily closing. Until the closing is entered
    # to_collect is 0, so the cash shows but cannot be collected/utilised yet.
    from app.petrol_pumps.services import pump_current_cash, pump_cash_to_collect
    pumps_cash = []
    for p in _active_pumps():
        c = pump_current_cash(p.id)
        collect = pump_cash_to_collect(p.id)
        if c != 0 or collect != 0:
            pumps_cash.append({"pump": p, "cash": c, "to_collect": collect})
    pumps_cash.sort(key=lambda r: r["cash"], reverse=True)
    pumps_cash_total = sum((r["cash"] for r in pumps_cash), Decimal("0"))
    pumps_collect_total = sum((r["to_collect"] for r in pumps_cash), Decimal("0"))

    # Who do we owe, and how much? (outstanding vendor/PSO payables — BRD §10.6)
    from app.accounting import ledger
    from app.core.models import Vendor
    payables = []
    for v in Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all():
        bal = ledger.vendor_balance(v)
        if bal > 0:
            payables.append({"vendor": v, "balance": bal})
    payables.sort(key=lambda r: r["balance"], reverse=True)
    payables_total = sum((r["balance"] for r in payables), Decimal("0"))

    return render_template(
        "head_office/index.html", counts=counts,
        payables=payables, payables_total=payables_total, treasury=treasury,
        pumps_cash=pumps_cash, pumps_cash_total=pumps_cash_total,
        pumps_collect_total=pumps_collect_total,
    )


# --------------------------------------------------------------------------- #
# Cash Receipts
# --------------------------------------------------------------------------- #
@head_office_bp.route("/cash-receipts")
@role_required(*HO_ROLES)
def cash_receipts_list():
    f_date = request.args.get("date", "").strip()
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))
    f_account = _int_or_none(request.args.get("received_into_account_id"))

    query = HeadOfficeCashReceipt.query
    if f_date:
        try:
            query = query.filter(
                HeadOfficeCashReceipt.receipt_date
                == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(HeadOfficeCashReceipt.petrol_pump_id == f_pump)
    if f_account:
        query = query.filter(
            HeadOfficeCashReceipt.received_into_account_id == f_account
        )

    receipts = query.order_by(
        HeadOfficeCashReceipt.receipt_date.desc(), HeadOfficeCashReceipt.id.desc()
    ).all()
    return render_template(
        "head_office/cash_receipts/list.html",
        receipts=receipts,
        filters={"date": f_date, "petrol_pump_id": f_pump, "received_into_account_id": f_account},
        pumps=_active_pumps(), accounts=_active_accounts(),
    )


def _read_receipt_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "daily_closing_id": _int_or_none(request.form.get("daily_closing_id")),
        "receipt_date": request.form.get("receipt_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "payment_method": request.form.get("payment_method", "").strip(),
        "received_into_account_id": _int_or_none(request.form.get("received_into_account_id")),
        "reference_number": request.form.get("reference_number", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_receipt_form(form, exclude_id=None):
    errors = []
    warnings = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    receipt_date = None
    if not form["receipt_date"]:
        errors.append("Receipt date is required.")
    else:
        try:
            receipt_date = datetime.strptime(form["receipt_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid receipt date.")

    amount = _parse_amount(form["amount"], "Amount", errors)
    if amount is None and form["amount"] == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")

    if not form["payment_method"]:
        errors.append("Payment method is required.")
    elif form["payment_method"] not in HEAD_OFFICE_PAYMENT_METHODS:
        errors.append("Please select a valid payment method.")

    account = None
    if form["received_into_account_id"] is not None:
        account = db.session.get(CashBankAccount, form["received_into_account_id"])
        if account is None or not account.is_active:
            errors.append("Please select an active received-into account.")

    closing = None
    if form["daily_closing_id"] is not None:
        closing = db.session.get(PumpDailyClosing, form["daily_closing_id"])
        if closing is None:
            errors.append("Please select a valid daily closing.")
        else:
            if pump is not None and closing.petrol_pump_id != pump.id:
                errors.append("The selected closing does not belong to that petrol pump.")
            if _closing_already_receipted(closing.id, exclude_id=exclude_id):
                errors.append("This daily closing has already been receipted.")
            if amount is not None and amount != closing.cash_submitted_to_head_office:
                warnings.append(
                    "Amount differs from the cash submitted on the linked closing "
                    f"({closing.cash_submitted_to_head_office})."
                )

    return errors, warnings, pump, receipt_date, amount, account, closing


def _receipt_form_choices():
    return {
        "pumps": _active_pumps(),
        "accounts": _active_accounts(),
        "closings": _selectable_closings(),
        "payment_methods": HEAD_OFFICE_PAYMENT_METHODS,
    }


# --------------------------------------------------------------------------- #
# Cash Receive Console — graphical, per-pump. Shows each pump's current cash,
# and receiving splits it into a bank account and/or head-office cash in hand.
# No free-typed amounts: you can only receive what the pump actually holds.
# --------------------------------------------------------------------------- #
def _ho_cash_account():
    """The head-office cash-in-hand account (receive remainder lands here)."""
    acc = CashBankAccount.query.filter_by(name="Head Office Cash").first()
    if acc is not None:
        return acc
    ho = BusinessUnit.query.filter_by(type=BusinessUnitType.HEAD_OFFICE).first()
    q = CashBankAccount.query.filter(CashBankAccount.is_active.is_(True))
    if ho is not None:
        cash = q.filter(CashBankAccount.business_unit_id == ho.id,
                        CashBankAccount.account_type.ilike("%cash%")).first()
        if cash is not None:
            return cash
    return q.filter(CashBankAccount.account_type.ilike("%cash%")).first()


def _bank_accounts():
    return (CashBankAccount.query.filter(
        CashBankAccount.is_active.is_(True),
        CashBankAccount.account_type.ilike("%bank%"),
    ).order_by(CashBankAccount.name).all())


@head_office_bp.route("/cash-receipts/console")
@role_required(*HO_ROLES)
def cash_receipts_console():
    from app.petrol_pumps.services import pump_current_cash, pump_cash_to_collect
    pumps = []
    for p in _active_pumps():
        pumps.append({
            "p": p,
            "cash": pump_current_cash(p.id),
            # Only daily-closing-confirmed cash can actually be received.
            "to_collect": pump_cash_to_collect(p.id),
        })
    return render_template(
        "head_office/cash_receipts/console.html",
        pumps=pumps, banks=_bank_accounts(), ho_cash=_ho_cash_account(),
        today=date.today(),
    )


@head_office_bp.route("/cash-receipts/console/save", methods=["POST"])
@role_required(*HO_ROLES)
def cash_receipts_console_save():
    """Receive a pump's cash, split into a bank account and/or HO cash in hand."""
    from app.petrol_pumps.services import pump_cash_to_collect
    pump = db.session.get(PetrolPump, _int_or_none(request.form.get("petrol_pump_id")))
    raw_date = (request.form.get("receipt_date") or "").strip()
    bank = db.session.get(CashBankAccount, _int_or_none(request.form.get("bank_account_id"))) \
        if _int_or_none(request.form.get("bank_account_id")) else None
    ho_cash = _ho_cash_account()

    errors = []
    if pump is None:
        errors.append("Invalid pump.")
    receipt_date = None
    try:
        receipt_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        errors.append("Please enter a valid date.")
    receive = _parse_amount(request.form.get("receive_amount"), "Receive amount", errors)
    to_bank = _parse_amount(request.form.get("bank_amount"), "Amount to bank", errors) \
        if (request.form.get("bank_amount") or "").strip() else Decimal("0")

    # Head office can only collect cash the pump has CONFIRMED via daily closing.
    available = pump_cash_to_collect(pump.id) if pump is not None else Decimal("0")
    if receive is not None:
        if receive <= 0:
            errors.append("Receive amount must be greater than zero.")
        elif available <= 0:
            errors.append(
                f"{pump.name if pump else 'This pump'} has no cash ready to collect yet — "
                f"enter the pump's daily closing first. Cash is only received once the "
                f"closing is done."
            )
        elif receive > available:
            errors.append(f"Cannot receive {receive} — only {available} has been confirmed "
                          f"via {pump.name if pump else 'this pump'}'s daily closing so far.")
    if to_bank is None:
        to_bank = Decimal("0")
    if to_bank < 0:
        errors.append("Amount to bank must not be negative.")
    if receive is not None and to_bank > receive:
        errors.append("Amount to bank cannot exceed the amount received.")
    if to_bank > 0 and bank is None:
        errors.append("Choose the bank account the money goes into.")
    if ho_cash is None and receive is not None and (receive - (to_bank or 0)) > 0:
        errors.append("No 'Head Office Cash' account found for the cash-in-hand remainder.")

    back = redirect(url_for("head_office.cash_receipts_console"))
    if errors:
        for m in errors:
            flash(m, "danger")
        return back

    to_cash = receive - to_bank
    made = []
    if to_bank > 0:
        r = HeadOfficeCashReceipt(
            petrol_pump=pump, receipt_date=receipt_date, amount=to_bank,
            payment_method="Bank Transfer", received_into_account=bank,
            notes=f"Received from {pump.name} → {bank.name}",
            received_by_id=current_user.id, created_by_id=current_user.id, is_active=True)
        db.session.add(r); db.session.flush(); _sync_receipt(r)
        made.append(f"{to_bank} → {bank.name}")
    if to_cash > 0:
        r = HeadOfficeCashReceipt(
            petrol_pump=pump, receipt_date=receipt_date, amount=to_cash,
            payment_method="Cash", received_into_account=ho_cash,
            notes=f"Received from {pump.name} → cash in hand",
            received_by_id=current_user.id, created_by_id=current_user.id, is_active=True)
        db.session.add(r); db.session.flush(); _sync_receipt(r)
        made.append(f"{to_cash} → {ho_cash.name}")
    db.session.commit()
    flash(f"Received {receive} from {pump.name}: " + ", ".join(made) + ".", "success")
    return back


@head_office_bp.route("/cash-receipts/create", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def cash_receipts_create():
    if request.method == "POST":
        form = _read_receipt_form()
        errors, warnings, pump, receipt_date, amount, account, closing = _validate_receipt_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/cash_receipts/form.html", form=form, mode="create", **_receipt_form_choices())
        for w in warnings:
            flash(w, "warning")

        receipt = HeadOfficeCashReceipt(
            petrol_pump=pump, daily_closing=closing, receipt_date=receipt_date,
            amount=amount, payment_method=form["payment_method"],
            received_into_account=account,
            reference_number=form["reference_number"] or None,
            notes=form["notes"] or None,
            received_by_id=current_user.id, created_by_id=current_user.id,
            is_active=form["is_active"],
        )
        db.session.add(receipt)
        db.session.flush()  # populate received_into_account_id before posting
        _sync_receipt(receipt)
        db.session.commit()
        msg = f"Cash receipt of {amount} saved."
        if receipt.is_posted:
            msg += f" Posted to {account.name}."
        flash(msg, "success")
        return redirect(url_for("head_office.cash_receipts_view", receipt_id=receipt.id))

    # GET: optionally prefill from a daily closing.
    form = {
        "petrol_pump_id": None, "daily_closing_id": None,
        "receipt_date": date.today().isoformat(), "amount": "",
        "payment_method": "Cash", "received_into_account_id": None,
        "reference_number": "", "notes": "", "is_active": True,
    }
    closing_id = _int_or_none(request.args.get("daily_closing_id"))
    if closing_id:
        closing = db.session.get(PumpDailyClosing, closing_id)
        if closing is not None:
            form["daily_closing_id"] = closing.id
            form["petrol_pump_id"] = closing.petrol_pump_id
            form["receipt_date"] = closing.closing_date.isoformat()
            form["amount"] = str(closing.cash_submitted_to_head_office)
    return render_template("head_office/cash_receipts/form.html", form=form, mode="create", **_receipt_form_choices())


@head_office_bp.route("/cash-receipts/<int:receipt_id>")
@role_required(*HO_ROLES)
def cash_receipts_view(receipt_id):
    receipt = db.get_or_404(HeadOfficeCashReceipt, receipt_id)
    return render_template("head_office/cash_receipts/detail.html", receipt=receipt)


@head_office_bp.route("/cash-receipts/<int:receipt_id>/edit", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def cash_receipts_edit(receipt_id):
    receipt = db.get_or_404(HeadOfficeCashReceipt, receipt_id)
    if request.method == "POST":
        form = _read_receipt_form()
        errors, warnings, pump, receipt_date, amount, account, closing = _validate_receipt_form(form, exclude_id=receipt.id)

        # Guard: reversing the old posting must not drive the account negative.
        if receipt.is_posted and not _can_unpost_receipt(receipt):
            errors.append(
                f"Cannot edit: reversing this receipt would make "
                f"'{receipt.received_into_account.name}' negative."
            )

        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/cash_receipts/form.html", form=form, mode="edit", receipt=receipt, **_receipt_form_choices())
        for w in warnings:
            flash(w, "warning")

        # Reverse the old posting, update, then re-post.
        if receipt.is_posted:
            _apply_receipt(receipt, -1)
            receipt.is_posted = False

        receipt.petrol_pump = pump
        receipt.daily_closing = closing
        receipt.receipt_date = receipt_date
        receipt.amount = amount
        receipt.payment_method = form["payment_method"]
        receipt.received_into_account = account
        receipt.reference_number = form["reference_number"] or None
        receipt.notes = form["notes"] or None
        receipt.is_active = form["is_active"]
        db.session.flush()  # populate received_into_account_id before posting
        _sync_receipt(receipt)
        db.session.commit()
        flash("Cash receipt updated.", "success")
        return redirect(url_for("head_office.cash_receipts_view", receipt_id=receipt.id))

    form = {
        "petrol_pump_id": receipt.petrol_pump_id,
        "daily_closing_id": receipt.daily_closing_id,
        "receipt_date": receipt.receipt_date.isoformat(),
        "amount": str(receipt.amount),
        "payment_method": receipt.payment_method,
        "received_into_account_id": receipt.received_into_account_id,
        "reference_number": receipt.reference_number or "",
        "notes": receipt.notes or "",
        "is_active": receipt.is_active,
    }
    return render_template("head_office/cash_receipts/form.html", form=form, mode="edit", receipt=receipt, **_receipt_form_choices())


@head_office_bp.route("/cash-receipts/<int:receipt_id>/toggle-status", methods=["POST"])
@role_required(*HO_ROLES)
def cash_receipts_toggle_status(receipt_id):
    receipt = db.get_or_404(HeadOfficeCashReceipt, receipt_id)
    if receipt.is_active:
        if receipt.is_posted and not _can_unpost_receipt(receipt):
            flash(
                f"Cannot deactivate: '{receipt.received_into_account.name}' would go negative.",
                "danger",
            )
            return redirect(url_for("head_office.cash_receipts_list"))
        receipt.is_active = False
    else:
        receipt.is_active = True
    _sync_receipt(receipt)
    db.session.commit()
    state = "activated" if receipt.is_active else "deactivated"
    flash(f"Cash receipt {state}.", "info")
    return redirect(url_for("head_office.cash_receipts_list"))


@head_office_bp.route("/cash-receipts/<int:receipt_id>/delete", methods=["POST"])
@role_required(*HO_ROLES)
def cash_receipts_delete(receipt_id):
    receipt = db.get_or_404(HeadOfficeCashReceipt, receipt_id)
    if receipt.is_posted and not _can_unpost_receipt(receipt):
        flash(
            f"Cannot delete: '{receipt.received_into_account.name}' would go negative. "
            "Reduce or reverse other transactions first.",
            "danger",
        )
        return redirect(url_for("head_office.cash_receipts_list"))
    if receipt.is_posted:
        _apply_receipt(receipt, -1)
    db.session.delete(receipt)
    db.session.commit()
    flash("Cash receipt deleted (any balance effect reversed).", "success")
    return redirect(url_for("head_office.cash_receipts_list"))


# --------------------------------------------------------------------------- #
# Head Office Expenses
# --------------------------------------------------------------------------- #
def _head_office_expense_categories():
    """Active expense categories that are global or under Head Office."""
    return [
        c for c in ExpenseCategory.query.filter_by(is_active=True)
        .order_by(ExpenseCategory.name).all()
        if c.business_unit_id is None
        or (c.business_unit and c.business_unit.type == BusinessUnitType.HEAD_OFFICE)
    ]


def _is_valid_ho_category(category):
    return category is not None and (
        category.business_unit_id is None
        or (category.business_unit and category.business_unit.type == BusinessUnitType.HEAD_OFFICE)
    )


def _active_vendors():
    return Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()


def _expense_should_post(expense):
    return expense.is_active and expense.paid_from_account_id is not None


def _sync_expense(expense):
    """Post (decrease balance) or reverse (increase) idempotently.

    Also (re)builds the double-entry journal voucher for this expense.
    """
    desired = _expense_should_post(expense)
    if desired and not expense.is_posted:
        expense.paid_from_account.current_balance = (
            expense.paid_from_account.current_balance or Decimal("0")
        ) - expense.amount
        expense.is_posted = True
    elif not desired and expense.is_posted:
        expense.paid_from_account.current_balance = (
            expense.paid_from_account.current_balance or Decimal("0")
        ) + expense.amount
        expense.is_posted = False
    cat = expense.expense_category.name if expense.expense_category else "expense"
    approvals.request_if_needed(
        "ho_expense", expense.id, expense.amount, expense.created_by_id,
        title=f"{cat} — {expense.amount}",
    )
    # Request approval first so the GL posting below honours the gate (a newly
    # created over-threshold expense is held out of the GL until approved).
    posting.sync_head_office_expense(expense)


@head_office_bp.route("/expenses")
@role_required(*HO_ROLES)
def expenses_list():
    f_date = request.args.get("date", "").strip()
    f_cat = _int_or_none(request.args.get("expense_category_id"))
    f_account = _int_or_none(request.args.get("paid_from_account_id"))
    f_vendor = _int_or_none(request.args.get("vendor_id"))

    query = HeadOfficeExpense.query
    if f_date:
        try:
            query = query.filter(HeadOfficeExpense.expense_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_cat:
        query = query.filter(HeadOfficeExpense.expense_category_id == f_cat)
    if f_account:
        query = query.filter(HeadOfficeExpense.paid_from_account_id == f_account)
    if f_vendor:
        query = query.filter(HeadOfficeExpense.vendor_id == f_vendor)

    expenses = query.order_by(HeadOfficeExpense.expense_date.desc(), HeadOfficeExpense.id.desc()).all()
    return render_template(
        "head_office/expenses/list.html",
        expenses=expenses,
        filters={"date": f_date, "expense_category_id": f_cat, "paid_from_account_id": f_account, "vendor_id": f_vendor},
        categories=_head_office_expense_categories(), accounts=_active_accounts(), vendors=_active_vendors(),
    )


def _read_expense_form():
    return {
        "expense_category_id": _int_or_none(request.form.get("expense_category_id")),
        "expense_date": request.form.get("expense_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "paid_from_account_id": _int_or_none(request.form.get("paid_from_account_id")),
        "vendor_id": _int_or_none(request.form.get("vendor_id")),
        "reference_number": request.form.get("reference_number", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_expense_form(form, existing=None):
    errors = []

    category = None
    if form["expense_category_id"] is None:
        errors.append("Expense category is required.")
    else:
        category = db.session.get(ExpenseCategory, form["expense_category_id"])
        if category is None:
            errors.append("Please select a valid expense category.")
        elif not _is_valid_ho_category(category):
            errors.append("Expense category must be global or a Head Office category.")

    expense_date = None
    if not form["expense_date"]:
        errors.append("Expense date is required.")
    else:
        try:
            expense_date = datetime.strptime(form["expense_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid expense date.")

    amount = _parse_amount(form["amount"], "Amount", errors)
    if amount is None and form["amount"] == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")

    account = _check_paid_from(form["paid_from_account_id"], amount, errors, existing)

    vendor = None
    if form["vendor_id"] is not None:
        vendor = db.session.get(Vendor, form["vendor_id"])
        if vendor is None:
            errors.append("Please select a valid vendor.")

    return errors, category, expense_date, amount, account, vendor


def _expense_form_choices():
    return {
        "categories": _head_office_expense_categories(),
        "accounts": _active_accounts(),
        "vendors": _active_vendors(),
    }


def _warn_if_negative(account):
    if account is not None and (account.current_balance or Decimal("0")) < 0:
        flash(f"Warning: account '{account.name}' balance is now negative.", "warning")


def _available_balance(account, existing=None):
    """How much can be spent from `account` right now. On edit, the account still
    holds the record's own prior (posted) deduction, so add it back to measure the
    NET change — otherwise re-saving an unchanged payment would falsely fail."""
    bal = account.current_balance or Decimal("0")
    if (existing is not None and getattr(existing, "is_posted", False)
            and getattr(existing, "paid_from_account_id", None) == account.id):
        bal += existing.amount or Decimal("0")
    return bal


def _check_paid_from(form_account_id, amount, errors, existing=None):
    """Shared 'paid from' resolution for expenses & vendor payments: the account
    is REQUIRED and the amount may not exceed its available balance (no going
    negative). Returns the resolved account or None."""
    if form_account_id is None:
        errors.append("Paid-from account is required — choose the cash/bank account the money comes out of.")
        return None
    account = db.session.get(CashBankAccount, form_account_id)
    if account is None or not account.is_active:
        errors.append("Please select an active paid-from account.")
        return None
    if amount is not None and amount > 0:
        available = _available_balance(account, existing)
        if amount > available:
            errors.append(
                f"Not enough balance in '{account.name}': only {available:,.2f} available, "
                f"but you are paying {amount:,.2f}. Reduce the amount or use another account."
            )
    return account


@head_office_bp.route("/expenses/create", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def expenses_create():
    if request.method == "POST":
        form = _read_expense_form()
        errors, category, expense_date, amount, account, vendor = _validate_expense_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/expenses/form.html", form=form, mode="create", **_expense_form_choices())

        expense = HeadOfficeExpense(
            expense_category=category, expense_date=expense_date, amount=amount,
            paid_from_account=account, vendor=vendor,
            reference_number=form["reference_number"] or None,
            notes=form["notes"] or None, created_by_id=current_user.id,
            is_active=form["is_active"],
        )
        db.session.add(expense)
        db.session.flush()  # populate paid_from_account_id before posting
        _sync_expense(expense)
        db.session.commit()
        if expense.is_posted:
            _warn_if_negative(account)
            flash(f"Expense of {amount} saved and paid from {account.name}.", "success")
        else:
            flash(f"Expense of {amount} saved.", "success")
        return redirect(url_for("head_office.expenses_view", expense_id=expense.id))

    form = {
        "expense_category_id": None, "expense_date": date.today().isoformat(),
        "amount": "", "paid_from_account_id": None, "vendor_id": None,
        "reference_number": "", "notes": "", "is_active": True,
    }
    return render_template("head_office/expenses/form.html", form=form, mode="create", **_expense_form_choices())


@head_office_bp.route("/expenses/<int:expense_id>")
@role_required(*HO_ROLES)
def expenses_view(expense_id):
    expense = db.get_or_404(HeadOfficeExpense, expense_id)
    return render_template("head_office/expenses/detail.html", expense=expense)


@head_office_bp.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def expenses_edit(expense_id):
    expense = db.get_or_404(HeadOfficeExpense, expense_id)
    if request.method == "POST":
        form = _read_expense_form()
        errors, category, expense_date, amount, account, vendor = _validate_expense_form(form, existing=expense)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/expenses/form.html", form=form, mode="edit", expense=expense, **_expense_form_choices())

        # Reverse old posting (give the money back), update, then re-post.
        if expense.is_posted:
            expense.paid_from_account.current_balance = (
                expense.paid_from_account.current_balance or Decimal("0")
            ) + expense.amount
            expense.is_posted = False

        expense.expense_category = category
        expense.expense_date = expense_date
        expense.amount = amount
        expense.paid_from_account = account
        expense.vendor = vendor
        expense.reference_number = form["reference_number"] or None
        expense.notes = form["notes"] or None
        expense.is_active = form["is_active"]
        db.session.flush()
        _sync_expense(expense)
        db.session.commit()
        if expense.is_posted:
            _warn_if_negative(account)
        flash("Expense updated.", "success")
        return redirect(url_for("head_office.expenses_view", expense_id=expense.id))

    form = {
        "expense_category_id": expense.expense_category_id,
        "expense_date": expense.expense_date.isoformat(),
        "amount": str(expense.amount),
        "paid_from_account_id": expense.paid_from_account_id,
        "vendor_id": expense.vendor_id,
        "reference_number": expense.reference_number or "",
        "notes": expense.notes or "",
        "is_active": expense.is_active,
    }
    return render_template("head_office/expenses/form.html", form=form, mode="edit", expense=expense, **_expense_form_choices())


@head_office_bp.route("/expenses/<int:expense_id>/toggle-status", methods=["POST"])
@role_required(*HO_ROLES)
def expenses_toggle_status(expense_id):
    expense = db.get_or_404(HeadOfficeExpense, expense_id)
    # Reactivating re-deducts the money — block it if the account can't cover it.
    if not expense.is_active and expense.paid_from_account is not None:
        avail = expense.paid_from_account.current_balance or Decimal("0")
        if (expense.amount or Decimal("0")) > avail:
            flash(f"Cannot activate: '{expense.paid_from_account.name}' only has {avail:,.2f}, "
                  f"but this expense is {expense.amount:,.2f}.", "danger")
            return redirect(url_for("head_office.expenses_list"))
    expense.is_active = not expense.is_active
    _sync_expense(expense)
    db.session.commit()
    state = "activated" if expense.is_active else "deactivated"
    flash(f"Expense {state}.", "info")
    return redirect(url_for("head_office.expenses_list"))


@head_office_bp.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@role_required(*HO_ROLES)
def expenses_delete(expense_id):
    from app.approvals.models import Approval
    from app.attachments.models import Attachment

    expense = db.get_or_404(HeadOfficeExpense, expense_id)
    if expense.is_posted and expense.paid_from_account is not None:
        expense.paid_from_account.current_balance = (
            expense.paid_from_account.current_balance or Decimal("0")
        ) + expense.amount
    posting.clear_source(posting.SOURCE_HO_EXPENSE, expense.id)
    Approval.query.filter_by(entity_type="ho_expense", entity_id=expense.id).delete()
    Attachment.query.filter_by(entity_type="ho_expense", entity_id=expense.id).delete()
    db.session.delete(expense)
    db.session.commit()
    flash("Expense deleted (any balance effect reversed).", "success")
    return redirect(url_for("head_office.expenses_list"))


# --------------------------------------------------------------------------- #
# Vendor / PSO Payments
# --------------------------------------------------------------------------- #
def _sync_payment(payment):
    """Post (decrease balance) or reverse (increase) idempotently."""
    desired = payment.is_active and payment.paid_from_account_id is not None
    if desired and not payment.is_posted:
        payment.paid_from_account.current_balance = (
            payment.paid_from_account.current_balance or Decimal("0")
        ) - payment.amount
        payment.is_posted = True
    elif not desired and payment.is_posted:
        payment.paid_from_account.current_balance = (
            payment.paid_from_account.current_balance or Decimal("0")
        ) + payment.amount
        payment.is_posted = False
    vname = payment.vendor.name if payment.vendor else "vendor"
    approvals.request_if_needed(
        "vendor_payment", payment.id, payment.amount, payment.created_by_id,
        title=f"{vname} — {payment.amount}",
    )
    # Request approval first so the GL posting honours the gate.
    posting.sync_vendor_payment(payment)


@head_office_bp.route("/vendor-payments")
@role_required(*HO_ROLES)
def vendor_payments_list():
    f_date = request.args.get("date", "").strip()
    f_vendor = _int_or_none(request.args.get("vendor_id"))
    f_account = _int_or_none(request.args.get("paid_from_account_id"))

    query = VendorPayment.query
    if f_date:
        try:
            query = query.filter(VendorPayment.payment_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_vendor:
        query = query.filter(VendorPayment.vendor_id == f_vendor)
    if f_account:
        query = query.filter(VendorPayment.paid_from_account_id == f_account)

    payments = query.order_by(VendorPayment.payment_date.desc(), VendorPayment.id.desc()).all()
    return render_template(
        "head_office/vendor_payments/list.html",
        payments=payments,
        filters={"date": f_date, "vendor_id": f_vendor, "paid_from_account_id": f_account},
        vendors=_active_vendors(), accounts=_active_accounts(),
    )


def _read_payment_form():
    return {
        "vendor_id": _int_or_none(request.form.get("vendor_id")),
        "payment_date": request.form.get("payment_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "paid_from_account_id": _int_or_none(request.form.get("paid_from_account_id")),
        "payment_method": request.form.get("payment_method", "").strip(),
        "reference_number": request.form.get("reference_number", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_payment_form(form, existing=None):
    errors = []

    vendor = None
    if form["vendor_id"] is None:
        errors.append("Vendor is required.")
    else:
        vendor = db.session.get(Vendor, form["vendor_id"])
        if vendor is None:
            errors.append("Please select a valid vendor.")

    payment_date = None
    if not form["payment_date"]:
        errors.append("Payment date is required.")
    else:
        try:
            payment_date = datetime.strptime(form["payment_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid payment date.")

    amount = _parse_amount(form["amount"], "Amount", errors)
    if amount is None and form["amount"] == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")

    if not form["payment_method"]:
        errors.append("Payment method is required.")
    elif form["payment_method"] not in HEAD_OFFICE_PAYMENT_METHODS:
        errors.append("Please select a valid payment method.")

    account = _check_paid_from(form["paid_from_account_id"], amount, errors, existing)

    return errors, vendor, payment_date, amount, account


def _payment_form_choices():
    from app.accounting import ledger
    vendors = _active_vendors()
    # Outstanding balance per vendor, so the form can show how much is owed.
    vendor_balances = {v.id: ledger.vendor_balance(v) for v in vendors}
    return {
        "vendors": vendors,
        "vendor_balances": vendor_balances,
        "accounts": _active_accounts(),
        "payment_methods": HEAD_OFFICE_PAYMENT_METHODS,
    }


@head_office_bp.route("/vendor-payments/create", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def vendor_payments_create():
    if request.method == "POST":
        form = _read_payment_form()
        errors, vendor, payment_date, amount, account = _validate_payment_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/vendor_payments/form.html", form=form, mode="create", **_payment_form_choices())

        payment = VendorPayment(
            vendor=vendor, payment_date=payment_date, amount=amount,
            paid_from_account=account, payment_method=form["payment_method"],
            reference_number=form["reference_number"] or None,
            notes=form["notes"] or None, created_by_id=current_user.id,
            is_active=form["is_active"],
        )
        db.session.add(payment)
        db.session.flush()  # populate paid_from_account_id before posting
        _sync_payment(payment)
        db.session.commit()
        if payment.is_posted:
            _warn_if_negative(account)
            flash(f"Payment of {amount} to {vendor.name} saved and paid from {account.name}.", "success")
        else:
            flash(f"Payment of {amount} to {vendor.name} saved.", "success")
        return redirect(url_for("head_office.vendor_payments_view", payment_id=payment.id))

    # Allow "Pay" links from the dashboard to preselect a vendor (?vendor_id=).
    preselect = _int_or_none(request.args.get("vendor_id"))
    form = {
        "vendor_id": preselect, "payment_date": date.today().isoformat(), "amount": "",
        "paid_from_account_id": None, "payment_method": "Cash",
        "reference_number": "", "notes": "", "is_active": True,
    }
    return render_template("head_office/vendor_payments/form.html", form=form, mode="create", **_payment_form_choices())


@head_office_bp.route("/vendor-payments/<int:payment_id>")
@role_required(*HO_ROLES)
def vendor_payments_view(payment_id):
    payment = db.get_or_404(VendorPayment, payment_id)
    return render_template("head_office/vendor_payments/detail.html", payment=payment)


@head_office_bp.route("/vendor-payments/<int:payment_id>/edit", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def vendor_payments_edit(payment_id):
    payment = db.get_or_404(VendorPayment, payment_id)
    if request.method == "POST":
        form = _read_payment_form()
        errors, vendor, payment_date, amount, account = _validate_payment_form(form, existing=payment)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/vendor_payments/form.html", form=form, mode="edit", payment=payment, **_payment_form_choices())

        # Reverse old posting (refund), update, then re-post.
        if payment.is_posted:
            payment.paid_from_account.current_balance = (
                payment.paid_from_account.current_balance or Decimal("0")
            ) + payment.amount
            payment.is_posted = False

        payment.vendor = vendor
        payment.payment_date = payment_date
        payment.amount = amount
        payment.paid_from_account = account
        payment.payment_method = form["payment_method"]
        payment.reference_number = form["reference_number"] or None
        payment.notes = form["notes"] or None
        payment.is_active = form["is_active"]
        db.session.flush()
        _sync_payment(payment)
        db.session.commit()
        if payment.is_posted:
            _warn_if_negative(account)
        flash("Payment updated.", "success")
        return redirect(url_for("head_office.vendor_payments_view", payment_id=payment.id))

    form = {
        "vendor_id": payment.vendor_id,
        "payment_date": payment.payment_date.isoformat(),
        "amount": str(payment.amount),
        "paid_from_account_id": payment.paid_from_account_id,
        "payment_method": payment.payment_method,
        "reference_number": payment.reference_number or "",
        "notes": payment.notes or "",
        "is_active": payment.is_active,
    }
    return render_template("head_office/vendor_payments/form.html", form=form, mode="edit", payment=payment, **_payment_form_choices())


@head_office_bp.route("/vendor-payments/<int:payment_id>/toggle-status", methods=["POST"])
@role_required(*HO_ROLES)
def vendor_payments_toggle_status(payment_id):
    payment = db.get_or_404(VendorPayment, payment_id)
    # Reactivating re-deducts the money — block it if the account can't cover it.
    if not payment.is_active and payment.paid_from_account is not None:
        avail = payment.paid_from_account.current_balance or Decimal("0")
        if (payment.amount or Decimal("0")) > avail:
            flash(f"Cannot activate: '{payment.paid_from_account.name}' only has {avail:,.2f}, "
                  f"but this payment is {payment.amount:,.2f}.", "danger")
            return redirect(url_for("head_office.vendor_payments_list"))
    payment.is_active = not payment.is_active
    _sync_payment(payment)
    db.session.commit()
    state = "activated" if payment.is_active else "deactivated"
    flash(f"Payment {state}.", "info")
    return redirect(url_for("head_office.vendor_payments_list"))


@head_office_bp.route("/vendor-payments/<int:payment_id>/delete", methods=["POST"])
@role_required(*HO_ROLES)
def vendor_payments_delete(payment_id):
    from app.approvals.models import Approval

    payment = db.get_or_404(VendorPayment, payment_id)
    if payment.is_posted and payment.paid_from_account is not None:
        payment.paid_from_account.current_balance = (
            payment.paid_from_account.current_balance or Decimal("0")
        ) + payment.amount
    posting.clear_source(posting.SOURCE_VENDOR_PAYMENT, payment.id)
    Approval.query.filter_by(entity_type="vendor_payment", entity_id=payment.id).delete()
    db.session.delete(payment)
    db.session.commit()
    flash("Payment deleted (any balance effect reversed).", "success")
    return redirect(url_for("head_office.vendor_payments_list"))


# --------------------------------------------------------------------------- #
# Cash Transfers (BRD §11.2) — move money between accounts
# --------------------------------------------------------------------------- #
from app.reports.exporters import VALID_FORMATS, export_response, fmt  # noqa: E402


def _ht_export_format():
    requested = request.args.get("export", "").strip().lower()
    return requested if requested in VALID_FORMATS else None


def _transfer_should_post(t):
    return bool(
        t.is_active and t.from_account_id and t.to_account_id
        and t.from_account_id != t.to_account_id
    )


def _sync_transfer(t):
    """Decrement source + increment destination idempotently; post the journal."""
    desired = _transfer_should_post(t)
    if desired and not t.is_posted:
        t.from_account.current_balance = (t.from_account.current_balance or Decimal("0")) - t.amount
        t.to_account.current_balance = (t.to_account.current_balance or Decimal("0")) + t.amount
        t.is_posted = True
    elif not desired and t.is_posted:
        t.from_account.current_balance = (t.from_account.current_balance or Decimal("0")) + t.amount
        t.to_account.current_balance = (t.to_account.current_balance or Decimal("0")) - t.amount
        t.is_posted = False
    posting.sync_cash_transfer(t)


def _read_transfer_form():
    return {
        "from_account_id": _int_or_none(request.form.get("from_account_id")),
        "to_account_id": _int_or_none(request.form.get("to_account_id")),
        "transfer_date": request.form.get("transfer_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "reference_number": request.form.get("reference_number", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_transfer_form(form):
    errors = []
    from_acc = db.session.get(CashBankAccount, form["from_account_id"]) if form["from_account_id"] else None
    to_acc = db.session.get(CashBankAccount, form["to_account_id"]) if form["to_account_id"] else None
    if from_acc is None:
        errors.append("From account is required.")
    if to_acc is None:
        errors.append("To account is required.")
    if from_acc and to_acc and from_acc.id == to_acc.id:
        errors.append("From and To accounts must be different.")

    transfer_date = None
    if not form["transfer_date"]:
        errors.append("Transfer date is required.")
    else:
        try:
            transfer_date = datetime.strptime(form["transfer_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid transfer date.")

    amount = _parse_amount(form["amount"], "Amount", errors)
    if amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")

    return errors, from_acc, to_acc, transfer_date, amount


@head_office_bp.route("/cash-transfers/cash-to-bank", methods=["POST"])
@role_required(*HO_ROLES)
def cash_to_bank_transfer():
    """Quick transfer of cash-in-hand into a bank account (from the dashboard
    Cash modal). Reuses the CashTransfer machinery; cannot overdraw the cash."""
    from_acc = db.session.get(CashBankAccount, _int_or_none(request.form.get("from_account_id")))
    to_acc = db.session.get(CashBankAccount, _int_or_none(request.form.get("to_account_id")))
    errors = []
    if from_acc is None or not from_acc.is_active:
        errors.append("Choose a valid cash account to transfer from.")
    if to_acc is None or not to_acc.is_active:
        errors.append("Choose the bank account to transfer into.")
    if from_acc and to_acc and from_acc.id == to_acc.id:
        errors.append("From and To accounts must be different.")
    amount = _parse_amount(request.form.get("amount"), "Amount", errors)
    if amount is None and (request.form.get("amount") or "").strip() == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")
    if from_acc and amount and amount > (from_acc.current_balance or Decimal("0")):
        errors.append(
            f"Not enough cash in '{from_acc.name}': only "
            f"{(from_acc.current_balance or Decimal('0')):,.2f} available."
        )
    if errors:
        for m in errors:
            flash(m, "danger")
        return redirect(url_for("head_office.index"))

    transfer = CashTransfer(
        from_account=from_acc, to_account=to_acc, transfer_date=date.today(),
        amount=amount, notes=f"Cash in hand → {to_acc.name}",
        created_by_id=current_user.id, is_active=True,
    )
    db.session.add(transfer)
    db.session.flush()
    _sync_transfer(transfer)
    db.session.commit()
    flash(f"Transferred {amount:,.2f} from {from_acc.name} to {to_acc.name}.", "success")
    return redirect(url_for("head_office.index"))


@head_office_bp.route("/cash-transfers")
@role_required(*HO_ROLES)
def cash_transfers_list():
    f_date = request.args.get("date", "").strip()
    f_account = _int_or_none(request.args.get("account_id"))

    query = CashTransfer.query
    if f_date:
        try:
            query = query.filter(CashTransfer.transfer_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_account:
        query = query.filter(db.or_(
            CashTransfer.from_account_id == f_account,
            CashTransfer.to_account_id == f_account,
        ))
    transfers = query.order_by(CashTransfer.transfer_date.desc(), CashTransfer.id.desc()).all()
    total = sum((t.amount or Decimal("0")) for t in transfers if t.is_active)

    export = _ht_export_format()
    if export:
        rows = [[
            t.transfer_date.isoformat(),
            t.from_account.name if t.from_account else "",
            t.to_account.name if t.to_account else "",
            fmt(t.amount),
            t.reference_number or "",
            "Active" if t.is_active else "Cancelled",
        ] for t in transfers]
        blocks = [{"headers": ["Date", "From", "To", "Amount", "Reference", "Status"], "rows": rows}]
        return export_response(export, "cash_transfers", "Cash Transfers", blocks)

    return render_template(
        "head_office/cash_transfers/list.html",
        transfers=transfers, total=total,
        filters={"date": f_date, "account_id": f_account},
        accounts=_active_accounts(),
    )


@head_office_bp.route("/cash-transfers/create", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def cash_transfers_create():
    if request.method == "POST":
        form = _read_transfer_form()
        errors, from_acc, to_acc, transfer_date, amount = _validate_transfer_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/cash_transfers/form.html", form=form, mode="create", accounts=_active_accounts())
        transfer = CashTransfer(
            from_account=from_acc, to_account=to_acc, transfer_date=transfer_date,
            amount=amount, reference_number=form["reference_number"] or None,
            notes=form["notes"] or None, created_by_id=current_user.id,
            is_active=form["is_active"],
        )
        db.session.add(transfer)
        db.session.flush()
        _sync_transfer(transfer)
        db.session.commit()
        if transfer.is_posted:
            _warn_if_negative(from_acc)
            flash(f"Transferred {amount} from {from_acc.name} to {to_acc.name}.", "success")
        else:
            flash("Cash transfer saved.", "success")
        return redirect(url_for("head_office.cash_transfers_view", transfer_id=transfer.id))

    form = {
        "from_account_id": None, "to_account_id": None,
        "transfer_date": date.today().isoformat(), "amount": "",
        "reference_number": "", "notes": "", "is_active": True,
    }
    return render_template("head_office/cash_transfers/form.html", form=form, mode="create", accounts=_active_accounts())


@head_office_bp.route("/cash-transfers/<int:transfer_id>")
@role_required(*HO_ROLES)
def cash_transfers_view(transfer_id):
    transfer = db.get_or_404(CashTransfer, transfer_id)
    return render_template("head_office/cash_transfers/detail.html", transfer=transfer)


@head_office_bp.route("/cash-transfers/<int:transfer_id>/edit", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def cash_transfers_edit(transfer_id):
    transfer = db.get_or_404(CashTransfer, transfer_id)
    if request.method == "POST":
        form = _read_transfer_form()
        errors, from_acc, to_acc, transfer_date, amount = _validate_transfer_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/cash_transfers/form.html", form=form, mode="edit", transfer=transfer, accounts=_active_accounts())
        if transfer.is_posted:
            transfer.from_account.current_balance = (transfer.from_account.current_balance or Decimal("0")) + transfer.amount
            transfer.to_account.current_balance = (transfer.to_account.current_balance or Decimal("0")) - transfer.amount
            transfer.is_posted = False
        transfer.from_account = from_acc
        transfer.to_account = to_acc
        transfer.transfer_date = transfer_date
        transfer.amount = amount
        transfer.reference_number = form["reference_number"] or None
        transfer.notes = form["notes"] or None
        transfer.is_active = form["is_active"]
        db.session.flush()
        _sync_transfer(transfer)
        db.session.commit()
        if transfer.is_posted:
            _warn_if_negative(from_acc)
        flash("Cash transfer updated.", "success")
        return redirect(url_for("head_office.cash_transfers_view", transfer_id=transfer.id))

    form = {
        "from_account_id": transfer.from_account_id,
        "to_account_id": transfer.to_account_id,
        "transfer_date": transfer.transfer_date.isoformat(),
        "amount": str(transfer.amount),
        "reference_number": transfer.reference_number or "",
        "notes": transfer.notes or "",
        "is_active": transfer.is_active,
    }
    return render_template("head_office/cash_transfers/form.html", form=form, mode="edit", transfer=transfer, accounts=_active_accounts())


@head_office_bp.route("/cash-transfers/<int:transfer_id>/toggle-status", methods=["POST"])
@role_required(*HO_ROLES)
def cash_transfers_toggle_status(transfer_id):
    transfer = db.get_or_404(CashTransfer, transfer_id)
    transfer.is_active = not transfer.is_active
    _sync_transfer(transfer)
    db.session.commit()
    state = "reactivated" if transfer.is_active else "cancelled"
    flash(f"Cash transfer {state}.", "info")
    return redirect(url_for("head_office.cash_transfers_list"))


@head_office_bp.route("/cash-transfers/<int:transfer_id>/delete", methods=["POST"])
@role_required(*HO_ROLES)
def cash_transfers_delete(transfer_id):
    transfer = db.get_or_404(CashTransfer, transfer_id)
    if transfer.is_posted:
        transfer.from_account.current_balance = (transfer.from_account.current_balance or Decimal("0")) + transfer.amount
        transfer.to_account.current_balance = (transfer.to_account.current_balance or Decimal("0")) - transfer.amount
    posting.clear_source(posting.SOURCE_CASH_TRANSFER, transfer.id)
    db.session.delete(transfer)
    db.session.commit()
    flash("Cash transfer deleted (any balance effect reversed).", "success")
    return redirect(url_for("head_office.cash_transfers_list"))


# --------------------------------------------------------------------------- #
# Head Office Staff + salary/advance payments (paid as a head office expense)
# --------------------------------------------------------------------------- #
def _all_ho_staff():
    return HeadOfficeStaff.query.order_by(HeadOfficeStaff.employee_name).all()


@head_office_bp.route("/staff")
@role_required(*HO_ROLES)
def staff_list():
    q = request.args.get("q", "").strip()
    query = HeadOfficeStaff.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            HeadOfficeStaff.employee_name.ilike(like),
            HeadOfficeStaff.cnic.ilike(like),
            HeadOfficeStaff.phone_number.ilike(like),
        ))
    staff = query.order_by(HeadOfficeStaff.employee_name).all()
    return render_template("head_office/staff/list.html", staff=staff, q=q)


def _read_ho_staff_form():
    return {
        "employee_name": request.form.get("employee_name", "").strip(),
        "designation": request.form.get("designation", "").strip(),
        "cnic": request.form.get("cnic", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip(),
        "monthly_salary": request.form.get("monthly_salary", "").strip(),
        "joining_date": request.form.get("joining_date", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_ho_staff_form(form):
    errors = []
    if not form["employee_name"]:
        errors.append("Employee name is required.")
    salary = _parse_amount(form["monthly_salary"], "Monthly salary", errors)
    if salary is None:
        salary = Decimal("0")
    joining = None
    if form["joining_date"]:
        try:
            joining = datetime.strptime(form["joining_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid joining date.")
    return errors, salary, joining


@head_office_bp.route("/staff/create", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def staff_create():
    if request.method == "POST":
        form = _read_ho_staff_form()
        errors, salary, joining = _validate_ho_staff_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/staff/form.html", form=form, mode="create")
        staff = HeadOfficeStaff(
            employee_name=form["employee_name"], designation=form["designation"] or None,
            cnic=form["cnic"] or None, phone_number=form["phone_number"] or None,
            monthly_salary=salary, joining_date=joining, notes=form["notes"] or None,
            is_active=form["is_active"],
        )
        db.session.add(staff)
        db.session.commit()
        flash(f"Employee '{staff.employee_name}' added.", "success")
        return redirect(url_for("head_office.staff_view", staff_id=staff.id))

    form = {
        "employee_name": "", "designation": "", "cnic": "", "phone_number": "",
        "monthly_salary": "", "joining_date": "", "notes": "", "is_active": True,
    }
    return render_template("head_office/staff/form.html", form=form, mode="create")


@head_office_bp.route("/staff/<int:staff_id>")
@role_required(*HO_ROLES)
def staff_view(staff_id):
    staff = db.get_or_404(HeadOfficeStaff, staff_id)
    return render_template("head_office/staff/detail.html", staff=staff)


@head_office_bp.route("/staff/<int:staff_id>/edit", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def staff_edit(staff_id):
    staff = db.get_or_404(HeadOfficeStaff, staff_id)
    if request.method == "POST":
        form = _read_ho_staff_form()
        errors, salary, joining = _validate_ho_staff_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/staff/form.html", form=form, mode="edit", staff=staff)
        staff.employee_name = form["employee_name"]
        staff.designation = form["designation"] or None
        staff.cnic = form["cnic"] or None
        staff.phone_number = form["phone_number"] or None
        staff.monthly_salary = salary
        staff.joining_date = joining
        staff.notes = form["notes"] or None
        staff.is_active = form["is_active"]
        db.session.commit()
        flash("Employee updated.", "success")
        return redirect(url_for("head_office.staff_view", staff_id=staff.id))

    form = {
        "employee_name": staff.employee_name, "designation": staff.designation or "",
        "cnic": staff.cnic or "", "phone_number": staff.phone_number or "",
        "monthly_salary": str(staff.monthly_salary),
        "joining_date": staff.joining_date.isoformat() if staff.joining_date else "",
        "notes": staff.notes or "", "is_active": staff.is_active,
    }
    return render_template("head_office/staff/form.html", form=form, mode="edit", staff=staff)


@head_office_bp.route("/staff/<int:staff_id>/toggle-status", methods=["POST"])
@role_required(*HO_ROLES)
def staff_toggle_status(staff_id):
    staff = db.get_or_404(HeadOfficeStaff, staff_id)
    staff.is_active = not staff.is_active
    db.session.commit()
    flash(f"Employee {'activated' if staff.is_active else 'deactivated'}.", "info")
    return redirect(url_for("head_office.staff_list"))


@head_office_bp.route("/staff/<int:staff_id>/delete", methods=["POST"])
@role_required(*HO_ROLES)
def staff_delete(staff_id):
    staff = db.get_or_404(HeadOfficeStaff, staff_id)
    n = HeadOfficeSalaryPayment.query.filter_by(staff_id=staff_id).count()
    if n:
        flash(f"Cannot delete '{staff.employee_name}': {n} salary payment(s) reference it. Deactivate instead.", "danger")
        return redirect(url_for("head_office.staff_view", staff_id=staff_id))
    db.session.delete(staff)
    db.session.commit()
    flash("Employee deleted.", "success")
    return redirect(url_for("head_office.staff_list"))


# --- HO salary/advance payments (a head office expense) ---
def _sync_ho_salary(payment):
    """Decrease (post) / restore (reverse) the paid-from balance idempotently,
    then (re)build the GL voucher (Dr Head Office Expense / Cr account)."""
    desired = payment.is_active and payment.paid_from_account_id is not None
    if desired and not payment.is_posted:
        payment.paid_from_account.current_balance = (
            payment.paid_from_account.current_balance or Decimal("0")
        ) - payment.amount
        payment.is_posted = True
    elif not desired and payment.is_posted:
        payment.paid_from_account.current_balance = (
            payment.paid_from_account.current_balance or Decimal("0")
        ) + payment.amount
        payment.is_posted = False
    posting.sync_ho_salary_payment(payment)


def _read_ho_salary_form():
    return {
        "staff_id": _int_or_none(request.form.get("staff_id")),
        "payment_date": request.form.get("payment_date", "").strip(),
        "payment_type": request.form.get("payment_type", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "for_month": request.form.get("for_month", "").strip(),
        "paid_from_account_id": _int_or_none(request.form.get("paid_from_account_id")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_ho_salary_form(form, existing=None):
    errors = []
    staff = None
    if form["staff_id"] is None:
        errors.append("Employee is required.")
    else:
        staff = db.session.get(HeadOfficeStaff, form["staff_id"])
        if staff is None:
            errors.append("Please select a valid employee.")
    payment_date = None
    if not form["payment_date"]:
        errors.append("Payment date is required.")
    else:
        try:
            payment_date = datetime.strptime(form["payment_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid payment date.")
    if not form["payment_type"] or form["payment_type"] not in HO_SALARY_PAYMENT_TYPES:
        errors.append("Please select a valid payment type.")
    amount = _parse_amount(form["amount"], "Amount", errors)
    if amount is None and form["amount"] == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")
    account = _check_paid_from(form["paid_from_account_id"], amount, errors, existing)
    return errors, staff, payment_date, amount, account


def _ho_salary_choices():
    return {"staff": _all_ho_staff(), "accounts": _active_accounts(),
            "payment_types": HO_SALARY_PAYMENT_TYPES}


@head_office_bp.route("/staff/salary-payments")
@role_required(*HO_ROLES)
def ho_salary_list():
    f_type = request.args.get("payment_type", "").strip()
    query = HeadOfficeSalaryPayment.query.join(HeadOfficeStaff)
    if f_type and f_type in HO_SALARY_PAYMENT_TYPES:
        query = query.filter(HeadOfficeSalaryPayment.payment_type == f_type)
    payments = query.order_by(
        HeadOfficeSalaryPayment.payment_date.desc(), HeadOfficeSalaryPayment.id.desc()
    ).all()
    total = sum((p.amount or Decimal("0")) for p in payments if p.is_active)
    return render_template(
        "head_office/staff/salary_list.html", payments=payments, total=total,
        filters={"payment_type": f_type}, payment_types=HO_SALARY_PAYMENT_TYPES,
    )


@head_office_bp.route("/staff/salary-payments/create", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def ho_salary_create():
    if request.method == "POST":
        form = _read_ho_salary_form()
        errors, staff, payment_date, amount, account = _validate_ho_salary_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/staff/salary_form.html", form=form, mode="create", **_ho_salary_choices())
        payment = HeadOfficeSalaryPayment(
            staff=staff, payment_date=payment_date, payment_type=form["payment_type"],
            amount=amount, for_month=form["for_month"] or None, paid_from_account=account,
            notes=form["notes"] or None, created_by_id=current_user.id, is_active=form["is_active"],
        )
        db.session.add(payment)
        db.session.flush()
        _sync_ho_salary(payment)
        db.session.commit()
        _warn_if_negative(account)
        flash(f"{payment.payment_type} of {amount} paid to {staff.employee_name} from {account.name}.", "success")
        return redirect(url_for("head_office.ho_salary_list"))

    form = {
        "staff_id": None, "payment_date": date.today().isoformat(),
        "payment_type": "Salary", "amount": "", "for_month": "",
        "paid_from_account_id": None, "notes": "", "is_active": True,
    }
    return render_template("head_office/staff/salary_form.html", form=form, mode="create", **_ho_salary_choices())


@head_office_bp.route("/staff/salary-payments/<int:payment_id>/edit", methods=["GET", "POST"])
@role_required(*HO_ROLES)
def ho_salary_edit(payment_id):
    payment = db.get_or_404(HeadOfficeSalaryPayment, payment_id)
    if request.method == "POST":
        form = _read_ho_salary_form()
        errors, staff, payment_date, amount, account = _validate_ho_salary_form(form, existing=payment)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("head_office/staff/salary_form.html", form=form, mode="edit", payment=payment, **_ho_salary_choices())
        # Reverse old posting, update, re-post.
        if payment.is_posted:
            payment.paid_from_account.current_balance = (
                payment.paid_from_account.current_balance or Decimal("0")
            ) + payment.amount
            payment.is_posted = False
        payment.staff = staff
        payment.payment_date = payment_date
        payment.payment_type = form["payment_type"]
        payment.amount = amount
        payment.for_month = form["for_month"] or None
        payment.paid_from_account = account
        payment.notes = form["notes"] or None
        payment.is_active = form["is_active"]
        db.session.flush()
        _sync_ho_salary(payment)
        db.session.commit()
        _warn_if_negative(account)
        flash("Salary payment updated.", "success")
        return redirect(url_for("head_office.ho_salary_list"))

    form = {
        "staff_id": payment.staff_id, "payment_date": payment.payment_date.isoformat(),
        "payment_type": payment.payment_type, "amount": str(payment.amount),
        "for_month": payment.for_month or "", "paid_from_account_id": payment.paid_from_account_id,
        "notes": payment.notes or "", "is_active": payment.is_active,
    }
    return render_template("head_office/staff/salary_form.html", form=form, mode="edit", payment=payment, **_ho_salary_choices())


@head_office_bp.route("/staff/salary-payments/<int:payment_id>/toggle-status", methods=["POST"])
@role_required(*HO_ROLES)
def ho_salary_toggle_status(payment_id):
    payment = db.get_or_404(HeadOfficeSalaryPayment, payment_id)
    if not payment.is_active and payment.paid_from_account is not None:
        avail = payment.paid_from_account.current_balance or Decimal("0")
        if (payment.amount or Decimal("0")) > avail:
            flash(f"Cannot activate: '{payment.paid_from_account.name}' only has {avail:,.2f}, "
                  f"but this payment is {payment.amount:,.2f}.", "danger")
            return redirect(url_for("head_office.ho_salary_list"))
    payment.is_active = not payment.is_active
    _sync_ho_salary(payment)
    db.session.commit()
    flash(f"Salary payment {'activated' if payment.is_active else 'cancelled'}.", "info")
    return redirect(url_for("head_office.ho_salary_list"))


@head_office_bp.route("/staff/salary-payments/<int:payment_id>/delete", methods=["POST"])
@role_required(*HO_ROLES)
def ho_salary_delete(payment_id):
    payment = db.get_or_404(HeadOfficeSalaryPayment, payment_id)
    if payment.is_posted and payment.paid_from_account is not None:
        payment.paid_from_account.current_balance = (
            payment.paid_from_account.current_balance or Decimal("0")
        ) + payment.amount
    posting.clear_source(posting.SOURCE_HO_SALARY, payment.id)
    db.session.delete(payment)
    db.session.commit()
    flash("Salary payment deleted (any balance effect reversed).", "success")
    return redirect(url_for("head_office.ho_salary_list"))
