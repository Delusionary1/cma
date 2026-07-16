"""Carriage / Transport routes: dashboard + vehicle/tanker trips.

Accessible by the carriage module roles (Admin / Owner, Head Office Manager,
Transport Manager). Carriage has its own profit/loss; it does not post to the
pump/bulk/agency businesses it may serve.
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
from app.auth.access import roles_for_module
from app.auth.decorators import role_required
from app.core.models import (
    BusinessUnit,
    BusinessUnitType,
    CashBankAccount,
    Driver,
    ExpenseCategory,
    Product,
    Vehicle,
    Vendor,
)
from app.carriage.models import (
    BUSINESS_REFERENCES,
    CARRIAGE_OWNERSHIP,
    CarriageExpense,
    CarriageTrip,
    FREIGHT_PAID_BY_OPTIONS,
    TRIP_STATUSES,
    TRIP_TYPES,
)

from app.carriage import reports as carriage_reports
from app.reports.exporters import VALID_FORMATS, export_response, fmt

carriage_bp = Blueprint("carriage", __name__)

CARRIAGE_ROLES = roles_for_module("carriage")


def _cr_date_range():
    def parse(name):
        raw = request.args.get(name, "").strip()
        if not raw:
            return None, raw
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date(), raw
        except ValueError:
            return None, raw
    df, rf = parse("from")
    dt, rt = parse("to")
    return df, dt, {"from": rf, "to": rt}


@carriage_bp.route("/reports")
@role_required(*CARRIAGE_ROLES)
def reports():
    """Vehicle-wise and driver-wise carriage profit/loss (BRD §13.3)."""
    date_from, date_to, filters = _cr_date_range()
    vehicles = carriage_reports.vehicle_pl(date_from, date_to)
    drivers = carriage_reports.driver_pl(date_from, date_to)
    delivery = carriage_reports.delivery_pl(date_from, date_to)
    expenses = carriage_reports.expense_breakdown(date_from, date_to)
    rented = carriage_reports.rented_vehicle_payable(date_from, date_to)

    export = request.args.get("export", "").strip().lower()
    if export in VALID_FORMATS:
        def block(title, data, key_label):
            rows = [[
                r["label"], str(r["trips"]), fmt(r["freight"]),
                fmt(r["expenses"]), fmt(r["net"]),
            ] for r in data["rows"]]
            t = data["totals"]
            rows.append(["TOTAL", str(t["trips"]), fmt(t["freight"]), fmt(t["expenses"]), fmt(t["net"])])
            return {"title": title,
                    "headers": [key_label, "Trips", "Freight", "Expenses", "Net Profit"],
                    "rows": rows}
        erows = [[r["label"], str(r["trips"]), fmt(r["amount"])] for r in expenses["rows"]]
        erows.append(["TOTAL", str(expenses["totals"]["trips"]), fmt(expenses["totals"]["amount"])])
        rrows = [[r["label"], str(r["trips"]), fmt(r["rent"])] for r in rented["rows"]]
        rrows.append(["TOTAL", str(rented["totals"]["trips"]), fmt(rented["totals"]["rent"])])
        blocks = [
            block("Vehicle-wise Profit / Loss", vehicles, "Vehicle"),
            block("Driver-wise Profit / Loss", drivers, "Driver"),
            block("Delivery Report (by trip type)", delivery, "Trip Type"),
            {"title": "Carriage Expense Breakdown (by category)", "headers": ["Category", "Trips", "Amount"], "rows": erows},
            {"title": "Rented Vehicle Payable", "headers": ["Provider", "Trips", "Rent Payable"], "rows": rrows},
        ]
        return export_response(export, "carriage_pl", "Carriage Profit / Loss", blocks)

    return render_template(
        "carriage/reports.html", vehicles=vehicles, drivers=drivers,
        delivery=delivery, expenses=expenses, rented=rented, filters=filters,
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _active_vehicles():
    return Vehicle.query.filter_by(is_active=True).order_by(Vehicle.vehicle_number).all()


def _active_drivers():
    return Driver.query.filter_by(is_active=True).order_by(Driver.name).all()


def _active_vendors():
    return Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()


def _active_products():
    return Product.query.filter_by(is_active=True).order_by(Product.name).all()


def _active_accounts():
    return (
        CashBankAccount.query.filter_by(is_active=True)
        .order_by(CashBankAccount.name)
        .all()
    )


def _int_or_none(raw):
    raw = (raw or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _parse_nonneg(raw, label, errors, default=Decimal("0")):
    raw = (raw or "").strip()
    if raw == "":
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        errors.append(f"{label} must be a number.")
        return default
    if value < 0:
        errors.append(f"{label} must not be negative.")
        return default
    return value


def _generate_trip_number():
    count = CarriageTrip.query.count()
    number = count + 1
    while CarriageTrip.query.filter_by(trip_number=f"TRIP-{number:04d}").first() is not None:
        number += 1
    return f"TRIP-{number:04d}"


def _form_choices():
    return {
        "vehicles": _active_vehicles(),
        "drivers": _active_drivers(),
        "vendors": _active_vendors(),
        "products": _active_products(),
        "accounts": _active_accounts(),
        "trip_types": TRIP_TYPES,
        "ownerships": CARRIAGE_OWNERSHIP,
        "freight_paid_by_options": FREIGHT_PAID_BY_OPTIONS,
        "trip_statuses": TRIP_STATUSES,
        "business_references": BUSINESS_REFERENCES,
    }


EXPENSE_FIELDS = [
    "rent_amount", "fuel_expense", "toll_tax", "loading_unloading_expense",
    "driver_expense", "maintenance_expense", "other_expense",
]


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@carriage_bp.route("/")
@role_required(*CARRIAGE_ROLES)
def index():
    active = CarriageTrip.query.filter_by(is_active=True)
    freight = db.session.query(db.func.coalesce(db.func.sum(CarriageTrip.total_freight_amount), 0)).filter(CarriageTrip.is_active.is_(True)).scalar()
    profit = db.session.query(db.func.coalesce(db.func.sum(CarriageTrip.net_profit), 0)).filter(CarriageTrip.is_active.is_(True)).scalar()
    # Cash fed to carriage from petrol pumps (all-time, active feeds).
    from app.petrol_pumps.models import PumpCarriageCashFeed
    from app.core.models import BusinessUnit, BusinessUnitType
    fed = (
        db.session.query(db.func.coalesce(db.func.sum(PumpCarriageCashFeed.amount), 0))
        .filter(PumpCarriageCashFeed.is_active.is_(True))
        .scalar()
    )
    # Carriage cash/bank on hand (its own accounts' balances; pump feeds land here).
    carriage_accounts = (
        CashBankAccount.query.join(BusinessUnit)
        .filter(
            BusinessUnit.type == BusinessUnitType.CARRIAGE,
            CashBankAccount.is_active.is_(True),
        )
        .order_by(CashBankAccount.name)
        .all()
    )
    cash_balance = sum((a.current_balance or Decimal("0")) for a in carriage_accounts)

    expenses_total = (
        db.session.query(db.func.coalesce(db.func.sum(CarriageExpense.amount), 0))
        .filter(CarriageExpense.is_active.is_(True))
        .scalar()
    )

    totals = {
        "count": CarriageTrip.query.count(),
        "own": active.filter(CarriageTrip.vehicle_ownership == "Own Vehicle").count(),
        "rented": active.filter(CarriageTrip.vehicle_ownership == "Rented Vehicle").count(),
        "customer": active.filter(CarriageTrip.vehicle_ownership == "Customer Vehicle").count(),
        "freight": Decimal(str(freight)),
        "profit": Decimal(str(profit)),
        "fed_from_pumps": Decimal(str(fed)),
        "cash_balance": cash_balance,
        "expenses_total": Decimal(str(expenses_total)),
    }
    return render_template(
        "carriage/index.html", totals=totals, carriage_accounts=carriage_accounts,
    )


# --------------------------------------------------------------------------- #
# Trips CRUD
# --------------------------------------------------------------------------- #
@carriage_bp.route("/trips")
@role_required(*CARRIAGE_ROLES)
def trips_list():
    f_date = request.args.get("date", "").strip()
    f_type = request.args.get("trip_type", "").strip()
    f_ownership = request.args.get("vehicle_ownership", "").strip()
    f_status = request.args.get("trip_status", "").strip()
    f_vehicle = _int_or_none(request.args.get("vehicle_id"))

    query = CarriageTrip.query
    if f_date:
        try:
            query = query.filter(CarriageTrip.trip_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_type:
        query = query.filter(CarriageTrip.trip_type == f_type)
    if f_ownership:
        query = query.filter(CarriageTrip.vehicle_ownership == f_ownership)
    if f_status:
        query = query.filter(CarriageTrip.trip_status == f_status)
    if f_vehicle:
        query = query.filter(CarriageTrip.vehicle_id == f_vehicle)

    trips = query.order_by(CarriageTrip.trip_date.desc(), CarriageTrip.id.desc()).all()
    return render_template(
        "carriage/trips/list.html",
        trips=trips,
        filters={"date": f_date, "trip_type": f_type, "vehicle_ownership": f_ownership,
                 "trip_status": f_status, "vehicle_id": f_vehicle},
        **_form_choices(),
    )


def _read_form():
    form = {
        "trip_date": request.form.get("trip_date", "").strip(),
        "trip_type": request.form.get("trip_type", "").strip(),
        "business_reference": request.form.get("business_reference", "").strip(),
        "vehicle_ownership": request.form.get("vehicle_ownership", "").strip(),
        "vehicle_id": _int_or_none(request.form.get("vehicle_id")),
        "vehicle_number": request.form.get("vehicle_number", "").strip(),
        "driver_id": _int_or_none(request.form.get("driver_id")),
        "driver_name": request.form.get("driver_name", "").strip(),
        "driver_contact": request.form.get("driver_contact", "").strip(),
        "rented_vehicle_vendor_id": _int_or_none(request.form.get("rented_vehicle_vendor_id")),
        "paid_from_account_id": _int_or_none(request.form.get("paid_from_account_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "quantity_loaded": request.form.get("quantity_loaded", "").strip(),
        "quantity_delivered": request.form.get("quantity_delivered", "").strip(),
        "loading_point": request.form.get("loading_point", "").strip(),
        "unloading_point": request.form.get("unloading_point", "").strip(),
        "party_name": request.form.get("party_name", "").strip(),
        "freight_rate": request.form.get("freight_rate", "").strip(),
        "total_freight_amount": request.form.get("total_freight_amount", "").strip(),
        "freight_paid_by": request.form.get("freight_paid_by", "").strip(),
        "trip_status": request.form.get("trip_status", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }
    for f in EXPENSE_FIELDS:
        form[f] = request.form.get(f, "").strip()
    return form


def _validate(form):
    errors = []
    nums = {}

    if not form["trip_date"]:
        errors.append("Trip date is required.")
        trip_date = None
    else:
        try:
            trip_date = datetime.strptime(form["trip_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid trip date.")
            trip_date = None

    if not form["trip_type"]:
        errors.append("Trip type is required.")
    elif form["trip_type"] not in TRIP_TYPES:
        errors.append("Please select a valid trip type.")

    if not form["vehicle_ownership"]:
        errors.append("Vehicle ownership is required.")
    elif form["vehicle_ownership"] not in CARRIAGE_OWNERSHIP:
        errors.append("Please select a valid vehicle ownership.")

    if not form["trip_status"]:
        errors.append("Trip status is required.")
    elif form["trip_status"] not in TRIP_STATUSES:
        errors.append("Please select a valid trip status.")

    if form["business_reference"] and form["business_reference"] not in BUSINESS_REFERENCES:
        errors.append("Please select a valid business reference.")
    if form["freight_paid_by"] and form["freight_paid_by"] not in FREIGHT_PAID_BY_OPTIONS:
        errors.append("Please select a valid 'freight paid by'.")

    qty_loaded = _parse_nonneg(form["quantity_loaded"], "Quantity loaded", errors)
    qty_delivered = _parse_nonneg(form["quantity_delivered"], "Quantity delivered", errors)
    if qty_delivered is not None and qty_loaded is not None and qty_delivered > qty_loaded:
        errors.append("Quantity delivered cannot exceed quantity loaded.")
    nums["quantity_loaded"] = qty_loaded
    nums["quantity_delivered"] = qty_delivered

    nums["freight_rate"] = _parse_nonneg(form["freight_rate"], "Freight rate", errors)
    nums["total_freight_amount"] = _parse_nonneg(form["total_freight_amount"], "Total freight amount", errors)
    for f in EXPENSE_FIELDS:
        nums[f] = _parse_nonneg(form[f], f.replace("_", " ").title(), errors)

    # Optional FK references (validate existence if given).
    vehicle = db.session.get(Vehicle, form["vehicle_id"]) if form["vehicle_id"] else None
    if form["vehicle_id"] and vehicle is None:
        errors.append("Please select a valid vehicle.")
    driver = db.session.get(Driver, form["driver_id"]) if form["driver_id"] else None
    if form["driver_id"] and driver is None:
        errors.append("Please select a valid driver.")
    vendor = db.session.get(Vendor, form["rented_vehicle_vendor_id"]) if form["rented_vehicle_vendor_id"] else None
    if form["rented_vehicle_vendor_id"] and vendor is None:
        errors.append("Please select a valid rented vehicle provider.")
    account = db.session.get(CashBankAccount, form["paid_from_account_id"]) if form["paid_from_account_id"] else None
    if form["paid_from_account_id"] and account is None:
        errors.append("Please select a valid paid-from account.")
    product = db.session.get(Product, form["product_id"]) if form["product_id"] else None
    if form["product_id"] and product is None:
        errors.append("Please select a valid product.")

    refs = {"trip_date": trip_date, "vehicle": vehicle, "driver": driver,
            "vendor": vendor, "account": account, "product": product}
    return errors, nums, refs


def _apply(trip, form, nums, refs):
    trip.trip_date = refs["trip_date"]
    trip.trip_type = form["trip_type"]
    trip.business_reference = form["business_reference"] or None
    trip.vehicle_ownership = form["vehicle_ownership"]
    trip.vehicle = refs["vehicle"]
    trip.vehicle_number = form["vehicle_number"] or None
    trip.driver = refs["driver"]
    trip.driver_name = form["driver_name"] or None
    trip.driver_contact = form["driver_contact"] or None
    trip.rented_vehicle_vendor = refs["vendor"]
    trip.paid_from_account = refs["account"]
    trip.product = refs["product"]
    trip.quantity_loaded = nums["quantity_loaded"]
    trip.quantity_delivered = nums["quantity_delivered"]
    trip.loading_point = form["loading_point"] or None
    trip.unloading_point = form["unloading_point"] or None
    trip.party_name = form["party_name"] or None
    trip.freight_rate = nums["freight_rate"]
    trip.total_freight_amount = nums["total_freight_amount"]
    trip.freight_paid_by = form["freight_paid_by"] or None
    for f in EXPENSE_FIELDS:
        setattr(trip, f, nums[f])
    trip.trip_status = form["trip_status"]
    trip.notes = form["notes"] or None
    trip.is_active = form["is_active"]
    total_expenses = sum(nums[f] for f in EXPENSE_FIELDS)
    trip.net_profit = nums["total_freight_amount"] - total_expenses


@carriage_bp.route("/trips/create", methods=["GET", "POST"])
@role_required(*CARRIAGE_ROLES)
def trips_create():
    if request.method == "POST":
        form = _read_form()
        errors, nums, refs = _validate(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("carriage/trips/form.html", form=form, mode="create", **_form_choices())

        trip = CarriageTrip(trip_number=_generate_trip_number(), created_by_id=current_user.id)
        _apply(trip, form, nums, refs)
        db.session.add(trip)
        db.session.flush()
        posting.sync_carriage_trip(trip)
        db.session.commit()
        flash(f"Trip {trip.trip_number} saved. Net profit {trip.net_profit}.", "success")
        return redirect(url_for("carriage.trips_view", trip_id=trip.id))

    form = {
        "trip_date": date.today().isoformat(), "trip_type": "", "business_reference": "",
        "vehicle_ownership": "Own Vehicle", "vehicle_id": None, "vehicle_number": "",
        "driver_id": None, "driver_name": "", "driver_contact": "",
        "rented_vehicle_vendor_id": None, "paid_from_account_id": None, "product_id": None,
        "quantity_loaded": "", "quantity_delivered": "", "loading_point": "",
        "unloading_point": "", "party_name": "", "freight_rate": "0",
        "total_freight_amount": "0", "freight_paid_by": "Company",
        "trip_status": "Planned", "notes": "", "is_active": True,
    }
    for f in EXPENSE_FIELDS:
        form[f] = "0"
    return render_template("carriage/trips/form.html", form=form, mode="create", **_form_choices())


@carriage_bp.route("/trips/<int:trip_id>")
@role_required(*CARRIAGE_ROLES)
def trips_view(trip_id):
    trip = db.get_or_404(CarriageTrip, trip_id)
    return render_template("carriage/trips/detail.html", trip=trip)


@carriage_bp.route("/trips/<int:trip_id>/edit", methods=["GET", "POST"])
@role_required(*CARRIAGE_ROLES)
def trips_edit(trip_id):
    trip = db.get_or_404(CarriageTrip, trip_id)
    if request.method == "POST":
        form = _read_form()
        errors, nums, refs = _validate(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("carriage/trips/form.html", form=form, mode="edit", trip=trip, **_form_choices())

        _apply(trip, form, nums, refs)
        posting.sync_carriage_trip(trip)
        db.session.commit()
        flash(f"Trip {trip.trip_number} updated. Net profit {trip.net_profit}.", "success")
        return redirect(url_for("carriage.trips_view", trip_id=trip.id))

    form = {
        "trip_date": trip.trip_date.isoformat(), "trip_type": trip.trip_type,
        "business_reference": trip.business_reference or "",
        "vehicle_ownership": trip.vehicle_ownership, "vehicle_id": trip.vehicle_id,
        "vehicle_number": trip.vehicle_number or "", "driver_id": trip.driver_id,
        "driver_name": trip.driver_name or "", "driver_contact": trip.driver_contact or "",
        "rented_vehicle_vendor_id": trip.rented_vehicle_vendor_id,
        "paid_from_account_id": trip.paid_from_account_id, "product_id": trip.product_id,
        "quantity_loaded": str(trip.quantity_loaded), "quantity_delivered": str(trip.quantity_delivered),
        "loading_point": trip.loading_point or "", "unloading_point": trip.unloading_point or "",
        "party_name": trip.party_name or "", "freight_rate": str(trip.freight_rate),
        "total_freight_amount": str(trip.total_freight_amount),
        "freight_paid_by": trip.freight_paid_by or "Company",
        "trip_status": trip.trip_status, "notes": trip.notes or "", "is_active": trip.is_active,
    }
    for f in EXPENSE_FIELDS:
        form[f] = str(getattr(trip, f))
    return render_template("carriage/trips/form.html", form=form, mode="edit", trip=trip, **_form_choices())


@carriage_bp.route("/trips/<int:trip_id>/toggle-status", methods=["POST"])
@role_required(*CARRIAGE_ROLES)
def trips_toggle_status(trip_id):
    trip = db.get_or_404(CarriageTrip, trip_id)
    trip.is_active = not trip.is_active
    posting.sync_carriage_trip(trip)
    db.session.commit()
    state = "activated" if trip.is_active else "deactivated"
    flash(f"Trip {trip.trip_number} {state}.", "info")
    return redirect(url_for("carriage.trips_list"))


@carriage_bp.route("/trips/<int:trip_id>/delete", methods=["POST"])
@role_required(*CARRIAGE_ROLES)
def trips_delete(trip_id):
    trip = db.get_or_404(CarriageTrip, trip_id)
    trip.is_active = False
    posting.sync_carriage_trip(trip)  # reverse GL before removing
    trip_number = trip.trip_number
    db.session.delete(trip)
    db.session.commit()
    flash(f"Trip {trip_number} permanently deleted.", "success")
    return redirect(url_for("carriage.trips_list"))


# --------------------------------------------------------------------------- #
# Carriage expenses (standalone business expenses, separate from trip costs)
# --------------------------------------------------------------------------- #
def _carriage_expense_categories():
    """Active expense categories that are global or under the Carriage unit."""
    return [
        c for c in ExpenseCategory.query.filter_by(is_active=True)
        .order_by(ExpenseCategory.name).all()
        if c.business_unit_id is None
        or (c.business_unit and c.business_unit.type == BusinessUnitType.CARRIAGE)
    ]


def _carriage_pay_accounts():
    """Carriage cash/bank accounts (fallback: all active accounts)."""
    carriage = (
        CashBankAccount.query.join(BusinessUnit)
        .filter(
            BusinessUnit.type == BusinessUnitType.CARRIAGE,
            CashBankAccount.is_active.is_(True),
        )
        .order_by(CashBankAccount.name)
        .all()
    )
    if carriage:
        return carriage
    return CashBankAccount.query.filter_by(is_active=True).order_by(CashBankAccount.name).all()


def _expense_form_choices():
    return {
        "categories": _carriage_expense_categories(),
        "accounts": _carriage_pay_accounts(),
    }


def _carriage_expense_should_post(expense):
    return expense.is_active and expense.paid_from_account_id is not None


def _sync_carriage_expense(expense):
    """Decrease (or reverse) the paid-from account balance idempotently, and
    (re)build the double-entry GL voucher."""
    desired = _carriage_expense_should_post(expense)
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
    posting.sync_carriage_expense(expense)


@carriage_bp.route("/expenses")
@role_required(*CARRIAGE_ROLES)
def expenses_list():
    f_date = request.args.get("date", "").strip()
    f_cat = _int_or_none(request.args.get("expense_category_id"))
    query = CarriageExpense.query
    if f_date:
        try:
            query = query.filter(CarriageExpense.expense_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_cat:
        query = query.filter(CarriageExpense.expense_category_id == f_cat)
    expenses = query.order_by(CarriageExpense.expense_date.desc(), CarriageExpense.id.desc()).all()
    total = sum((e.amount or Decimal("0")) for e in expenses if e.is_active)
    return render_template(
        "carriage/expenses/list.html",
        expenses=expenses, total=total,
        filters={"date": f_date, "expense_category_id": f_cat},
        **_expense_form_choices(),
    )


def _read_expense_form():
    return {
        "expense_category_id": _int_or_none(request.form.get("expense_category_id")),
        "expense_date": request.form.get("expense_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "paid_from_account_id": _int_or_none(request.form.get("paid_from_account_id")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_expense_form(form):
    errors = []
    category = None
    if form["expense_category_id"] is not None:
        category = db.session.get(ExpenseCategory, form["expense_category_id"])
        if category is None:
            errors.append("Please select a valid expense category.")
    expense_date = None
    if not form["expense_date"]:
        errors.append("Date is required.")
    else:
        try:
            expense_date = datetime.strptime(form["expense_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid date.")
    amount = None
    raw = form["amount"]
    if raw == "":
        errors.append("Amount is required.")
    else:
        try:
            amount = Decimal(raw)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (InvalidOperation, ValueError):
            errors.append("Amount must be a number.")
    account = None
    if form["paid_from_account_id"] is not None:
        account = db.session.get(CashBankAccount, form["paid_from_account_id"])
        if account is None or not account.is_active:
            errors.append("Please select a valid paid-from account.")
    return errors, category, expense_date, amount, account


@carriage_bp.route("/expenses/create", methods=["GET", "POST"])
@role_required(*CARRIAGE_ROLES)
def expenses_create():
    if request.method == "POST":
        form = _read_expense_form()
        errors, category, expense_date, amount, account = _validate_expense_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("carriage/expenses/form.html", form=form, mode="create", **_expense_form_choices())
        expense = CarriageExpense(
            expense_category=category, expense_date=expense_date, amount=amount,
            paid_from_account=account, notes=form["notes"] or None,
            created_by_id=current_user.id, is_active=form["is_active"],
        )
        db.session.add(expense)
        db.session.flush()
        _sync_carriage_expense(expense)
        db.session.commit()
        flash(f"Carriage expense of {amount} saved.", "success")
        return redirect(url_for("carriage.expenses_view", expense_id=expense.id))

    form = {
        "expense_category_id": None, "expense_date": date.today().isoformat(),
        "amount": "", "paid_from_account_id": None, "notes": "", "is_active": True,
    }
    return render_template("carriage/expenses/form.html", form=form, mode="create", **_expense_form_choices())


@carriage_bp.route("/expenses/<int:expense_id>")
@role_required(*CARRIAGE_ROLES)
def expenses_view(expense_id):
    expense = db.get_or_404(CarriageExpense, expense_id)
    return render_template("carriage/expenses/detail.html", expense=expense)


@carriage_bp.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@role_required(*CARRIAGE_ROLES)
def expenses_edit(expense_id):
    expense = db.get_or_404(CarriageExpense, expense_id)
    if request.method == "POST":
        form = _read_expense_form()
        errors, category, expense_date, amount, account = _validate_expense_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("carriage/expenses/form.html", form=form, mode="edit", expense=expense, **_expense_form_choices())
        # reverse the old posting first, then re-apply with new values
        if expense.is_posted and expense.paid_from_account:
            expense.paid_from_account.current_balance = (
                expense.paid_from_account.current_balance or Decimal("0")
            ) + expense.amount
            expense.is_posted = False
        expense.expense_category = category
        expense.expense_date = expense_date
        expense.amount = amount
        expense.paid_from_account = account
        expense.notes = form["notes"] or None
        expense.is_active = form["is_active"]
        db.session.flush()
        _sync_carriage_expense(expense)
        db.session.commit()
        flash("Carriage expense updated.", "success")
        return redirect(url_for("carriage.expenses_view", expense_id=expense.id))

    form = {
        "expense_category_id": expense.expense_category_id,
        "expense_date": expense.expense_date.isoformat(),
        "amount": str(expense.amount),
        "paid_from_account_id": expense.paid_from_account_id,
        "notes": expense.notes or "", "is_active": expense.is_active,
    }
    return render_template("carriage/expenses/form.html", form=form, mode="edit", expense=expense, **_expense_form_choices())


@carriage_bp.route("/expenses/<int:expense_id>/toggle-status", methods=["POST"])
@role_required(*CARRIAGE_ROLES)
def expenses_toggle_status(expense_id):
    expense = db.get_or_404(CarriageExpense, expense_id)
    expense.is_active = not expense.is_active
    _sync_carriage_expense(expense)
    db.session.commit()
    state = "activated" if expense.is_active else "deactivated"
    flash(f"Carriage expense {state}.", "info")
    return redirect(url_for("carriage.expenses_list"))


@carriage_bp.route("/expenses/<int:expense_id>/delete", methods=["POST"])
@role_required(*CARRIAGE_ROLES)
def expenses_delete(expense_id):
    expense = db.get_or_404(CarriageExpense, expense_id)
    expense.is_active = False
    _sync_carriage_expense(expense)   # reverse balance + clear GL before removing
    db.session.delete(expense)
    db.session.commit()
    flash("Carriage expense deleted.", "success")
    return redirect(url_for("carriage.expenses_list"))
