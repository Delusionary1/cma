"""Accounting routes: dashboard, Chart of Accounts CRUD, Journal Vouchers.

Access is restricted to the accounting module roles (Admin / Owner, Head Office
Manager, Accountant). Records are not hard-deleted; chart accounts use a
toggle-status flag.
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
from app.auth.access import roles_for_module
from app.auth.decorators import role_required
from app.core.models import BusinessUnit, CashBankAccount, Customer, Vendor
from app.accounting.models import (
    ACCOUNTING_ACCOUNT_TYPES,
    ChartOfAccount,
    CustomerReceipt,
    JournalEntry,
    RECEIPT_PAYMENT_METHODS,
    Voucher,
)
from app.accounting import posting
from app.accounting.services import (
    calculate_journal_totals,
    create_journal_entry,
    generate_voucher_number,
    validate_journal_entry_lines,
)
from app.accounting import ledger

accounting_bp = Blueprint("accounting", __name__)

# Roles allowed in accounting (Admin / Owner, Head Office Manager, Accountant).
ACC_ROLES = roles_for_module("accounting")


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _active_business_units():
    return (
        BusinessUnit.query.filter_by(is_active=True)
        .order_by(BusinessUnit.name)
        .all()
    )


def _active_accounts():
    return (
        ChartOfAccount.query.filter_by(is_active=True)
        .order_by(ChartOfAccount.code)
        .all()
    )


def _read_business_unit_id():
    raw = request.form.get("business_unit_id", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _account_code_taken(code, exclude_id=None):
    query = ChartOfAccount.query.filter(
        db.func.lower(ChartOfAccount.code) == code.lower()
    )
    if exclude_id is not None:
        query = query.filter(ChartOfAccount.id != exclude_id)
    return db.session.query(query.exists()).scalar()


# --------------------------------------------------------------------------- #
# Accounting dashboard
# --------------------------------------------------------------------------- #
@accounting_bp.route("/")
@role_required(*ACC_ROLES)
def index():
    """Accounting landing page."""
    counts = {
        "chart_of_accounts": ChartOfAccount.query.count(),
        "journal_vouchers": JournalEntry.query.count(),
        "customer_receipts": CustomerReceipt.query.count(),
        "total_receivables": ledger.total_receivables(),
        "total_payables": ledger.total_payables(),
    }
    return render_template("accounting/index.html", counts=counts)


# --------------------------------------------------------------------------- #
# Chart of Accounts
# --------------------------------------------------------------------------- #
@accounting_bp.route("/chart-of-accounts")
@role_required(*ACC_ROLES)
def chart_of_accounts_list():
    """List chart accounts, with optional search by code/name/type."""
    q = request.args.get("q", "").strip()
    query = ChartOfAccount.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                ChartOfAccount.code.ilike(like),
                ChartOfAccount.name.ilike(like),
                ChartOfAccount.account_type.ilike(like),
            )
        )
    accounts = query.order_by(ChartOfAccount.code).all()
    return render_template(
        "accounting/chart_of_accounts/list.html", accounts=accounts, q=q
    )


def _read_coa_form():
    return {
        "code": request.form.get("code", "").strip(),
        "name": request.form.get("name", "").strip(),
        "account_type": request.form.get("account_type", "").strip(),
        "business_unit_id": _read_business_unit_id(),
        "parent_id": (request.form.get("parent_id", "").strip() or None),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_coa_form(form, current_id=None):
    """Validate a chart-of-account form. Returns (errors, unit, parent_id)."""
    errors = []

    if not form["code"]:
        errors.append("Code is required.")
    elif _account_code_taken(form["code"], exclude_id=current_id):
        errors.append(f"Account code '{form['code']}' already exists.")

    if not form["name"]:
        errors.append("Name is required.")

    if not form["account_type"]:
        errors.append("Account type is required.")
    elif form["account_type"] not in ACCOUNTING_ACCOUNT_TYPES:
        errors.append("Please select a valid account type.")

    business_unit = None
    if form["business_unit_id"] is not None:
        business_unit = db.session.get(BusinessUnit, form["business_unit_id"])
        if business_unit is None:
            errors.append("Please select a valid business unit.")

    parent_id = None
    if form["parent_id"]:
        try:
            parent_id = int(form["parent_id"])
        except ValueError:
            errors.append("Please select a valid parent account.")
            parent_id = None
        if parent_id is not None:
            if current_id is not None and parent_id == current_id:
                errors.append("An account cannot be its own parent.")
            elif db.session.get(ChartOfAccount, parent_id) is None:
                errors.append("Please select a valid parent account.")

    return errors, business_unit, parent_id


@accounting_bp.route("/chart-of-accounts/create", methods=["GET", "POST"])
@role_required(*ACC_ROLES)
def chart_of_accounts_create():
    """Create a new chart account."""
    if request.method == "POST":
        form = _read_coa_form()
        errors, unit, parent_id = _validate_coa_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "accounting/chart_of_accounts/form.html",
                form=form, business_units=_active_business_units(),
                account_types=ACCOUNTING_ACCOUNT_TYPES,
                parent_choices=_active_accounts(), mode="create",
            )

        account = ChartOfAccount(
            business_unit=unit,
            code=form["code"],
            name=form["name"],
            account_type=form["account_type"],
            parent_id=parent_id,
            is_active=form["is_active"],
        )
        db.session.add(account)
        db.session.commit()
        flash(f"Account '{account.code} - {account.name}' created.", "success")
        return redirect(
            url_for("accounting.chart_of_accounts_view", account_id=account.id)
        )

    form = {
        "code": "", "name": "", "account_type": "",
        "business_unit_id": None, "parent_id": None, "is_active": True,
    }
    return render_template(
        "accounting/chart_of_accounts/form.html",
        form=form, business_units=_active_business_units(),
        account_types=ACCOUNTING_ACCOUNT_TYPES,
        parent_choices=_active_accounts(), mode="create",
    )


@accounting_bp.route("/chart-of-accounts/<int:account_id>")
@role_required(*ACC_ROLES)
def chart_of_accounts_view(account_id):
    """View a single chart account."""
    account = db.get_or_404(ChartOfAccount, account_id)
    return render_template(
        "accounting/chart_of_accounts/detail.html", account=account
    )


@accounting_bp.route(
    "/chart-of-accounts/<int:account_id>/edit", methods=["GET", "POST"]
)
@role_required(*ACC_ROLES)
def chart_of_accounts_edit(account_id):
    """Edit an existing chart account."""
    account = db.get_or_404(ChartOfAccount, account_id)

    if request.method == "POST":
        form = _read_coa_form()
        errors, unit, parent_id = _validate_coa_form(form, current_id=account.id)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "accounting/chart_of_accounts/form.html",
                form=form, business_units=_active_business_units(),
                account_types=ACCOUNTING_ACCOUNT_TYPES,
                parent_choices=_active_accounts(), mode="edit", account=account,
            )

        account.business_unit = unit
        account.code = form["code"]
        account.name = form["name"]
        account.account_type = form["account_type"]
        account.parent_id = parent_id
        account.is_active = form["is_active"]
        db.session.commit()
        flash(f"Account '{account.code} - {account.name}' updated.", "success")
        return redirect(
            url_for("accounting.chart_of_accounts_view", account_id=account.id)
        )

    form = {
        "code": account.code,
        "name": account.name,
        "account_type": account.account_type,
        "business_unit_id": account.business_unit_id,
        "parent_id": account.parent_id,
        "is_active": account.is_active,
    }
    return render_template(
        "accounting/chart_of_accounts/form.html",
        form=form, business_units=_active_business_units(),
        account_types=ACCOUNTING_ACCOUNT_TYPES,
        parent_choices=_active_accounts(), mode="edit", account=account,
    )


@accounting_bp.route(
    "/chart-of-accounts/<int:account_id>/toggle-status", methods=["POST"]
)
@role_required(*ACC_ROLES)
def chart_of_accounts_toggle_status(account_id):
    """Activate / deactivate a chart account (no hard delete)."""
    account = db.get_or_404(ChartOfAccount, account_id)
    account.is_active = not account.is_active
    db.session.commit()
    state = "activated" if account.is_active else "deactivated"
    flash(f"Account '{account.code}' {state}.", "info")
    return redirect(url_for("accounting.chart_of_accounts_list"))


# --------------------------------------------------------------------------- #
# Journal Vouchers
# --------------------------------------------------------------------------- #
@accounting_bp.route("/journal-vouchers")
@role_required(*ACC_ROLES)
def journal_vouchers_list():
    """List journal entries, with optional filters."""
    q = request.args.get("q", "").strip()
    bu_id = request.args.get("business_unit_id", "").strip()
    entry_date = request.args.get("date", "").strip()

    query = JournalEntry.query.outerjoin(Voucher)

    if q:
        query = query.filter(Voucher.voucher_number.ilike(f"%{q}%"))
    if bu_id:
        try:
            query = query.filter(JournalEntry.business_unit_id == int(bu_id))
        except ValueError:
            pass
    if entry_date:
        try:
            parsed = datetime.strptime(entry_date, "%Y-%m-%d").date()
            query = query.filter(JournalEntry.entry_date == parsed)
        except ValueError:
            pass

    entries = query.order_by(JournalEntry.id.desc()).all()
    return render_template(
        "accounting/journal_vouchers/list.html",
        entries=entries, q=q, bu_id=bu_id, entry_date=entry_date,
        business_units=_active_business_units(),
    )


def _parse_voucher_lines(errors):
    """Read the line rows from the form into a list of dicts (with Decimals).

    Only rows that have an account selected are considered. Bad numbers are
    reported via `errors`.
    """
    account_ids = request.form.getlist("account_id")
    debits = request.form.getlist("debit")
    credits = request.form.getlist("credit")
    descriptions = request.form.getlist("line_description")

    lines = []
    for i, raw_account in enumerate(account_ids):
        raw_account = raw_account.strip()
        if not raw_account:
            continue  # skip empty rows

        try:
            account_id = int(raw_account)
        except ValueError:
            errors.append(f"Line {i + 1}: invalid account.")
            continue
        if db.session.get(ChartOfAccount, account_id) is None:
            errors.append(f"Line {i + 1}: account does not exist.")
            continue

        def _amount(values, idx, label):
            raw = (values[idx] if idx < len(values) else "").strip()
            if raw == "":
                return Decimal("0")
            try:
                return Decimal(raw)
            except InvalidOperation:
                errors.append(f"Line {i + 1}: {label} must be a number.")
                return Decimal("0")

        lines.append({
            "account_id": account_id,
            "debit": _amount(debits, i, "debit"),
            "credit": _amount(credits, i, "credit"),
            "description": (
                descriptions[i].strip() if i < len(descriptions) else ""
            ),
        })

    return lines


@accounting_bp.route("/journal-vouchers/create", methods=["GET", "POST"])
@role_required(*ACC_ROLES)
def journal_vouchers_create():
    """Create a manual, balanced journal voucher."""
    accounts = _active_accounts()
    business_units = _active_business_units()

    if request.method == "POST":
        business_unit_id = _read_business_unit_id()
        raw_date = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()
        reference_number = request.form.get("reference_number", "").strip()

        errors = []

        business_unit = None
        if business_unit_id is None:
            errors.append("Business unit is required.")
        else:
            business_unit = db.session.get(BusinessUnit, business_unit_id)
            if business_unit is None:
                errors.append("Please select a valid business unit.")

        entry_date = None
        if not raw_date:
            errors.append("Date is required.")
        else:
            try:
                entry_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                errors.append("Please enter a valid date.")

        lines = _parse_voucher_lines(errors)
        # Only run the balancing rules if the lines themselves parsed cleanly.
        errors.extend(validate_journal_entry_lines(lines))

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "accounting/journal_vouchers/form.html",
                accounts=accounts, business_units=business_units,
                form={
                    "business_unit_id": business_unit_id,
                    "date": raw_date,
                    "description": description,
                    "reference_number": reference_number,
                },
                rows=_rows_from_request(),
            )

        voucher = Voucher(
            voucher_number=generate_voucher_number("Journal Voucher"),
            voucher_type="Journal Voucher",
            business_unit_id=business_unit_id,
            date=entry_date,
            description=description or None,
            reference_number=reference_number or None,
            created_by_id=current_user.id,
            is_posted=True,
        )
        db.session.add(voucher)

        create_journal_entry(
            business_unit_id=business_unit_id,
            entry_date=entry_date,
            lines=lines,
            description=description or None,
            created_by_id=current_user.id,
            voucher=voucher,
            is_posted=True,
        )
        db.session.commit()
        flash(f"Journal voucher '{voucher.voucher_number}' created.", "success")
        return redirect(
            url_for("accounting.journal_vouchers_view", entry_id=voucher.journal_entries[0].id)
        )

    # GET: blank form with 4 empty rows and today's date.
    form = {
        "business_unit_id": None,
        "date": date.today().isoformat(),
        "description": "",
        "reference_number": "",
    }
    rows = [{"account_id": "", "debit": "", "credit": "", "description": ""}
            for _ in range(4)]
    return render_template(
        "accounting/journal_vouchers/form.html",
        accounts=accounts, business_units=business_units, form=form, rows=rows,
    )


def _rows_from_request():
    """Rebuild the submitted line rows so the form can be redisplayed."""
    account_ids = request.form.getlist("account_id")
    debits = request.form.getlist("debit")
    credits = request.form.getlist("credit")
    descriptions = request.form.getlist("line_description")
    rows = []
    for i in range(max(len(account_ids), 4)):
        rows.append({
            "account_id": account_ids[i].strip() if i < len(account_ids) else "",
            "debit": debits[i].strip() if i < len(debits) else "",
            "credit": credits[i].strip() if i < len(credits) else "",
            "description": descriptions[i].strip() if i < len(descriptions) else "",
        })
    return rows


@accounting_bp.route("/journal-vouchers/<int:entry_id>")
@role_required(*ACC_ROLES)
def journal_vouchers_view(entry_id):
    """View a journal entry with its debit/credit lines and totals."""
    entry = db.get_or_404(JournalEntry, entry_id)
    total_debit, total_credit = calculate_journal_totals([
        {"debit": line.debit, "credit": line.credit} for line in entry.lines
    ])
    return render_template(
        "accounting/journal_vouchers/detail.html",
        entry=entry, total_debit=total_debit, total_credit=total_credit,
    )


# --------------------------------------------------------------------------- #
# Customer & Vendor Ledgers (derived receivables / payables)
# --------------------------------------------------------------------------- #
@accounting_bp.route("/customer-ledgers")
@role_required(*ACC_ROLES)
def customer_ledgers_list():
    q = request.args.get("q", "").strip()
    query = Customer.query
    if q:
        query = query.filter(Customer.name.ilike(f"%{q}%"))
    customers = query.order_by(Customer.name).all()
    rows = [{"customer": c, "balance": ledger.customer_balance(c)} for c in customers]
    total = sum((r["balance"] for r in rows), Decimal("0"))
    return render_template("accounting/ledgers/customers_list.html", rows=rows, total=total, q=q)


@accounting_bp.route("/customer-ledgers/<int:customer_id>")
@role_required(*ACC_ROLES)
def customer_ledger_view(customer_id):
    customer = db.get_or_404(Customer, customer_id)
    entries, closing = ledger.customer_ledger(customer)
    statement = ledger.customer_statement(customer)
    return render_template("accounting/ledgers/customer_detail.html",
                           customer=customer, entries=entries, closing=closing,
                           statement=statement)


@accounting_bp.route("/vendor-ledgers")
@role_required(*ACC_ROLES)
def vendor_ledgers_list():
    q = request.args.get("q", "").strip()
    query = Vendor.query
    if q:
        query = query.filter(Vendor.name.ilike(f"%{q}%"))
    vendors = query.order_by(Vendor.name).all()
    rows = [{"vendor": v, "balance": ledger.vendor_balance(v)} for v in vendors]
    total = sum((r["balance"] for r in rows), Decimal("0"))
    return render_template("accounting/ledgers/vendors_list.html", rows=rows, total=total, q=q)


@accounting_bp.route("/vendor-ledgers/<int:vendor_id>")
@role_required(*ACC_ROLES)
def vendor_ledger_view(vendor_id):
    vendor = db.get_or_404(Vendor, vendor_id)
    entries, closing = ledger.vendor_ledger(vendor)
    statement = ledger.vendor_statement(vendor)
    return render_template("accounting/ledgers/vendor_detail.html",
                           vendor=vendor, entries=entries, closing=closing,
                           statement=statement)


# --------------------------------------------------------------------------- #
# Customer Receipts (collections that reduce receivables)
# --------------------------------------------------------------------------- #
def _active_cash_accounts():
    return CashBankAccount.query.filter_by(is_active=True).order_by(CashBankAccount.name).all()


def _active_customers():
    return Customer.query.filter_by(is_active=True).order_by(Customer.name).all()


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


def _receipt_should_post(receipt):
    return receipt.is_active and receipt.received_into_account_id is not None


def _sync_receipt(receipt):
    """Increase the chosen account balance when posted (idempotent)."""
    desired = _receipt_should_post(receipt)
    if desired and not receipt.is_posted:
        receipt.received_into_account.current_balance = (
            receipt.received_into_account.current_balance or Decimal("0")
        ) + receipt.amount
        receipt.is_posted = True
    elif not desired and receipt.is_posted:
        receipt.received_into_account.current_balance = (
            receipt.received_into_account.current_balance or Decimal("0")
        ) - receipt.amount
        receipt.is_posted = False
    posting.sync_customer_receipt(receipt)


def _can_unpost_receipt(receipt):
    account = receipt.received_into_account
    return not (account is not None and (account.current_balance or Decimal("0")) - receipt.amount < 0)


@accounting_bp.route("/customer-receipts")
@role_required(*ACC_ROLES)
def customer_receipts_list():
    f_customer = request.args.get("customer_id", "").strip()
    query = CustomerReceipt.query
    if f_customer:
        try:
            query = query.filter(CustomerReceipt.customer_id == int(f_customer))
        except ValueError:
            pass
    receipts = query.order_by(CustomerReceipt.receipt_date.desc(), CustomerReceipt.id.desc()).all()
    return render_template("accounting/customer_receipts/list.html",
                           receipts=receipts, customers=_active_customers(),
                           filter_customer=f_customer)


def _read_receipt_form():
    def _int(name):
        raw = request.form.get(name, "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
    return {
        "customer_id": _int("customer_id"),
        "receipt_date": request.form.get("receipt_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "payment_method": request.form.get("payment_method", "").strip(),
        "received_into_account_id": _int("received_into_account_id"),
        "reference_number": request.form.get("reference_number", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_receipt_form(form):
    errors = []
    customer = None
    if form["customer_id"] is None:
        errors.append("Customer is required.")
    else:
        customer = db.session.get(Customer, form["customer_id"])
        if customer is None:
            errors.append("Please select a valid customer.")

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
    elif form["payment_method"] not in RECEIPT_PAYMENT_METHODS:
        errors.append("Please select a valid payment method.")

    account = None
    if form["received_into_account_id"] is not None:
        account = db.session.get(CashBankAccount, form["received_into_account_id"])
        if account is None or not account.is_active:
            errors.append("Please select an active received-into account.")

    return errors, customer, receipt_date, amount, account


def _receipt_choices():
    return {"customers": _active_customers(), "accounts": _active_cash_accounts(),
            "payment_methods": RECEIPT_PAYMENT_METHODS}


@accounting_bp.route("/customer-receipts/create", methods=["GET", "POST"])
@role_required(*ACC_ROLES)
def customer_receipts_create():
    if request.method == "POST":
        form = _read_receipt_form()
        errors, customer, receipt_date, amount, account = _validate_receipt_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("accounting/customer_receipts/form.html", form=form, mode="create", **_receipt_choices())
        receipt = CustomerReceipt(
            customer=customer, receipt_date=receipt_date, amount=amount,
            payment_method=form["payment_method"], received_into_account=account,
            reference_number=form["reference_number"] or None, notes=form["notes"] or None,
            created_by_id=current_user.id, is_active=form["is_active"],
        )
        db.session.add(receipt)
        db.session.flush()
        _sync_receipt(receipt)
        db.session.commit()
        flash(f"Receipt of {amount} from {customer.name} saved. New balance: {ledger.customer_balance(customer)}.", "success")
        return redirect(url_for("accounting.customer_receipts_view", receipt_id=receipt.id))

    form = {
        "customer_id": _to_int(request.args.get("customer_id")),
        "receipt_date": date.today().isoformat(), "amount": "",
        "payment_method": "Cash", "received_into_account_id": None,
        "reference_number": "", "notes": "", "is_active": True,
    }
    return render_template("accounting/customer_receipts/form.html", form=form, mode="create", **_receipt_choices())


def _to_int(raw):
    try:
        return int(raw) if raw else None
    except (ValueError, TypeError):
        return None


@accounting_bp.route("/customer-receipts/<int:receipt_id>")
@role_required(*ACC_ROLES)
def customer_receipts_view(receipt_id):
    receipt = db.get_or_404(CustomerReceipt, receipt_id)
    return render_template("accounting/customer_receipts/detail.html", receipt=receipt)


@accounting_bp.route("/customer-receipts/<int:receipt_id>/edit", methods=["GET", "POST"])
@role_required(*ACC_ROLES)
def customer_receipts_edit(receipt_id):
    receipt = db.get_or_404(CustomerReceipt, receipt_id)
    if request.method == "POST":
        form = _read_receipt_form()
        errors, customer, receipt_date, amount, account = _validate_receipt_form(form)
        if receipt.is_posted and not _can_unpost_receipt(receipt):
            errors.append(f"Cannot edit: reversing this receipt would make '{receipt.received_into_account.name}' negative.")
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("accounting/customer_receipts/form.html", form=form, mode="edit", receipt=receipt, **_receipt_choices())
        if receipt.is_posted:
            receipt.received_into_account.current_balance = (receipt.received_into_account.current_balance or Decimal("0")) - receipt.amount
            receipt.is_posted = False
        receipt.customer = customer
        receipt.receipt_date = receipt_date
        receipt.amount = amount
        receipt.payment_method = form["payment_method"]
        receipt.received_into_account = account
        receipt.reference_number = form["reference_number"] or None
        receipt.notes = form["notes"] or None
        receipt.is_active = form["is_active"]
        db.session.flush()
        _sync_receipt(receipt)
        db.session.commit()
        flash("Receipt updated.", "success")
        return redirect(url_for("accounting.customer_receipts_view", receipt_id=receipt.id))

    form = {
        "customer_id": receipt.customer_id, "receipt_date": receipt.receipt_date.isoformat(),
        "amount": str(receipt.amount), "payment_method": receipt.payment_method,
        "received_into_account_id": receipt.received_into_account_id,
        "reference_number": receipt.reference_number or "", "notes": receipt.notes or "",
        "is_active": receipt.is_active,
    }
    return render_template("accounting/customer_receipts/form.html", form=form, mode="edit", receipt=receipt, **_receipt_choices())


@accounting_bp.route("/customer-receipts/<int:receipt_id>/toggle-status", methods=["POST"])
@role_required(*ACC_ROLES)
def customer_receipts_toggle_status(receipt_id):
    receipt = db.get_or_404(CustomerReceipt, receipt_id)
    if receipt.is_active and receipt.is_posted and not _can_unpost_receipt(receipt):
        flash(f"Cannot deactivate: '{receipt.received_into_account.name}' would go negative.", "danger")
        return redirect(url_for("accounting.customer_receipts_list"))
    receipt.is_active = not receipt.is_active
    _sync_receipt(receipt)
    db.session.commit()
    state = "activated" if receipt.is_active else "deactivated"
    flash(f"Receipt {state}.", "info")
    return redirect(url_for("accounting.customer_receipts_list"))
