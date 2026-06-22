"""Petrol Pump routes: landing page + pump setup (machines, nozzles, tanks).

Setup pages are restricted to Admin / Owner, Head Office Manager and Petrol
Pump Manager (note: Cashier can see the petrol pumps landing page but NOT the
setup pages). Records are never hard-deleted; toggle-status flips is_active.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user

from app.extensions import db
from app.auth.access import roles_for_module
from app.auth.decorators import role_required
from app.core.models import (
    BusinessUnit,
    BusinessUnitType,
    CashBankAccount,
    Customer,
    Driver,
    ExpenseCategory,
    PetrolPump,
    Product,
    ProductCategory,
    Vehicle,
    Vendor,
    VendorType,
)
from app.petrol_pumps.models import (
    CONSOLE_SHIFTS,
    CREDIT_CUSTOMER_METHOD,
    DEFAULT_SHIFT,
    SHIFT_DAY,
    SHIFT_NIGHT,
    LUBRICANT_PAYMENT_METHODS,
    LubricantSale,
    MachineReading,
    PumpDailyClosing,
    PumpExpense,
    PumpMachine,
    PumpNozzle,
    PumpPurchase,
    PumpPurchaseItem,
    PumpTank,
    PURCHASE_DELIVERY_STATUSES,
    StockMovement,
    STOCK_MOVEMENT_TYPES,
    MOVEMENT_PURCHASE_RECEIVED,
    MOVEMENT_RETAIL_SALE,
    MOVEMENT_STOCK_GAIN,
    MOVEMENT_STOCK_LOSS,
    StockAdjustment,
    ADJUSTMENT_STATUSES,
    ADJUSTMENT_STATUS_PENDING,
    ADJUSTMENT_STATUS_APPROVED,
    ADJUSTMENT_STATUS_REJECTED,
    PumpStaff,
    STAFF_DESIGNATIONS,
    STAFF_SHIFTS,
    PumpAttendance,
    PumpSalaryPayment,
    ATTENDANCE_STATUSES,
    SALARY_PAYMENT_TYPES,
    DailyChecklist,
    DailyChecklistItem,
    CHECKLIST_ITEMS,
    CHECKLIST_STATUSES,
    MaintenanceComplaint,
    MAINTENANCE_STATUSES,
    PumpCarriageCashFeed,
    PsoCardPayment,
    _utcnow,
)
from app.petrol_pumps import stock
from app.accounting import posting
from app.petrol_pumps.services import (
    calculate_daily_closing_summary,
    calculate_daily_expenses,
    calculate_daily_fuel_sales,
    calculate_daily_lubricant_sales,
    pump_current_cash,
)
from app.reports.exporters import VALID_FORMATS, export_response, fmt

petrol_pumps_bp = Blueprint("petrol_pumps", __name__)


def _export_format():
    """Return 'xlsx'/'pdf' if a valid ?export= is requested, else None."""
    requested = request.args.get("export", "").strip().lower()
    return requested if requested in VALID_FORMATS else None

# Roles that can manage pump setup (Cashier is intentionally excluded).
PUMP_SETUP_ROLES = (
    "Admin / Owner",
    "Head Office Manager",
    "Petrol Pump Manager",
)

# Roles that can enter machine readings (includes Cashier).
READING_ROLES = roles_for_module("petrol_pumps")

# Roles that can manage pump purchases (Accountant in, Cashier out).
PURCHASE_ROLES = (
    "Admin / Owner",
    "Head Office Manager",
    "Petrol Pump Manager",
    "Accountant",
)

FUEL_CATEGORY_NAME = "Fuel Products"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _active_pumps():
    return PetrolPump.query.filter_by(is_active=True).order_by(PetrolPump.name).all()


def _fuel_products():
    """Active products in the Fuel Products category (Petrol/Diesel/High Octane)."""
    return (
        Product.query.join(ProductCategory)
        .filter(
            ProductCategory.name == FUEL_CATEGORY_NAME,
            Product.is_active.is_(True),
        )
        .order_by(Product.name)
        .all()
    )


def _is_fuel_product(product):
    """True if the product belongs to the Fuel Products category."""
    return (
        product is not None
        and product.category is not None
        and product.category.name == FUEL_CATEGORY_NAME
    )


def _active_machines():
    return (
        PumpMachine.query.filter_by(is_active=True)
        .order_by(PumpMachine.machine_name)
        .all()
    )


def _int_or_none(raw):
    raw = (raw or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


# Key under which the currently-selected pump is remembered for the session.
_PUMP_SESSION_KEY = "pp_selected_pump"


def _selected_pump(pumps):
    """The current working pump, sticky across pages.

    An explicit ?pump= / ?petrol_pump_id= in the URL wins and is remembered for
    the rest of the session; otherwise the last remembered pump is used; only if
    nothing has ever been chosen do we fall back to the first pump. This is what
    keeps the pump the user picked on the dashboard selected on every inner page
    until they change it.
    """
    by_id = {p.id: p for p in pumps}
    explicit = (_int_or_none(request.args.get("pump"))
                or _int_or_none(request.args.get("petrol_pump_id")))
    if explicit in by_id:
        session[_PUMP_SESSION_KEY] = explicit
        return by_id[explicit]
    remembered = session.get(_PUMP_SESSION_KEY)
    if remembered in by_id:
        return by_id[remembered]
    return pumps[0] if pumps else None


def _selected_pump_id(pumps):
    """Just the id of `_selected_pump` (for prefilling form dicts)."""
    pump = _selected_pump(pumps)
    return pump.id if pump else None


def _parse_nonneg(raw_value, field_label, errors, default=None):
    """Parse an optional non-negative number. Appends to errors on problems."""
    raw_value = (raw_value or "").strip()
    if raw_value == "":
        return default
    try:
        value = Decimal(raw_value)
    except InvalidOperation:
        errors.append(f"{field_label} must be a number.")
        return default
    if value < 0:
        errors.append(f"{field_label} must not be negative.")
        return default
    return value


# --------------------------------------------------------------------------- #
# Landing + setup dashboard
# --------------------------------------------------------------------------- #
def _sum(amount_col, *filters):
    """COALESCE(SUM(col), 0) over the given filters → Decimal."""
    query = db.session.query(db.func.coalesce(db.func.sum(amount_col), 0))
    for f in filters:
        query = query.filter(f)
    return Decimal(str(query.scalar()))


def _pump_period_range(period, today):
    """(date_from, date_to, shift_mode) for a summary period key.

    shift_mode is 'day' / 'night' / None. Day/Night classify a reading by whether
    its shift label contains "night" (case-insensitive) — so ANY day-ish label
    (Day (12h), Full Day, halfday, blank) counts as Day and only an explicit
    night shift counts as Night. This keeps Sale robust no matter how the shift
    was typed on the classic form.
    """
    if period == "day":
        return today, today, "day"
    if period == "night":
        return today, today, "night"
    if period == "7days":
        return today - timedelta(days=6), today, None
    if period == "lastmonth":
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        return last_prev.replace(day=1), last_prev, None
    # default: this month
    return today.replace(day=1), today, None


def _apply_shift_mode(query, shift_mode):
    """Filter a MachineReading query by day/night shift mode (see above)."""
    if shift_mode == "night":
        return query.filter(db.func.lower(MachineReading.shift).like("%night%"))
    if shift_mode == "day":
        return query.filter(db.or_(
            MachineReading.shift.is_(None),
            ~db.func.lower(MachineReading.shift).like("%night%"),
        ))
    return query


PUMP_SUMMARY_PERIODS = [
    ("day", "Day"), ("night", "Night"), ("7days", "7 Days"),
    ("month", "This Month"), ("lastmonth", "Last Month"),
]


@petrol_pumps_bp.route("/")
@role_required(*READING_ROLES)
def index():
    """Petrol Pumps landing page with a per-pump quick-overview band."""
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    period = request.args.get("period", "day")
    if period not in dict(PUMP_SUMMARY_PERIODS):
        period = "day"

    summary = None
    if pump is not None:
        today = date.today()
        d_from, d_to, shift_mode = _pump_period_range(period, today)

        fuel_q = MachineReading.query.filter(
            MachineReading.petrol_pump_id == pump.id,
            MachineReading.is_active.is_(True),
            MachineReading.reading_date >= d_from,
            MachineReading.reading_date <= d_to,
        )
        fuel_q = _apply_shift_mode(fuel_q, shift_mode)
        # Fuel sale and its weighted-average COGS over the SAME readings, so the
        # profit figure matches the period/shift exactly.
        from app.petrol_pumps import costing
        _costs = costing.weighted_avg_costs(d_to)
        fuel_sale = Decimal("0")
        fuel_cogs = Decimal("0")
        uncosted_sale = Decimal("0")  # fuel sold for a product never purchased
        uncosted_products = set()
        for r in fuel_q.all():
            fuel_sale += r.sale_amount or Decimal("0")
            uc = costing.reading_unit_cost(r, _costs)
            if uc is not None:
                fuel_cogs += (r.sale_liters or Decimal("0")) * uc
            elif (r.sale_liters or 0) > 0:
                uncosted_sale += r.sale_amount or Decimal("0")
                if r.product is not None:
                    uncosted_products.add(r.product.name)

        lube_sale = _sum(
            LubricantSale.total_amount, LubricantSale.petrol_pump_id == pump.id,
            LubricantSale.is_active.is_(True),
            LubricantSale.sale_date >= d_from, LubricantSale.sale_date <= d_to,
        )
        expense_total = _sum(
            PumpExpense.amount, PumpExpense.petrol_pump_id == pump.id,
            PumpExpense.is_active.is_(True),
            PumpExpense.expense_date >= d_from, PumpExpense.expense_date <= d_to,
        )
        # Staff salaries/advances are paid out of pump cash too, so they count as
        # expenses on the dashboard (and against profit), matching the daily
        # closing and pump_current_cash.
        salary_total = _sum(
            PumpSalaryPayment.amount, PumpSalaryPayment.petrol_pump_id == pump.id,
            PumpSalaryPayment.is_active.is_(True),
            PumpSalaryPayment.payment_date >= d_from, PumpSalaryPayment.payment_date <= d_to,
        )
        expense_total = expense_total + salary_total
        current_stock = _sum(
            PumpTank.current_stock_liters, PumpTank.petrol_pump_id == pump.id,
            PumpTank.is_active.is_(True),
        )
        sale_total = fuel_sale + lube_sale
        # Profit = sale − cost of fuel sold − pump expenses (lubricant COGS isn't
        # tracked, so lubricant contributes at full margin — same basis the GL uses).
        profit = sale_total - fuel_cogs - expense_total
        summary = {
            "fuel_sale": fuel_sale, "lube_sale": lube_sale,
            "sale_total": sale_total, "expense": expense_total,
            "fuel_cogs": fuel_cogs, "profit": profit,
            "uncosted_sale": uncosted_sale,
            "uncosted_products": ", ".join(sorted(uncosted_products)),
            "current_cash": pump_current_cash(pump.id),
            "current_stock": current_stock, "from": d_from, "to": d_to,
        }

    return render_template(
        "petrol_pumps/index.html", pumps=pumps, pump=pump,
        period=period, periods=PUMP_SUMMARY_PERIODS, summary=summary,
    )


@petrol_pumps_bp.route("/setup")
@role_required(*PUMP_SETUP_ROLES)
def setup():
    """Petrol pump setup dashboard."""
    counts = {
        "machines": PumpMachine.query.count(),
        "nozzles": PumpNozzle.query.count(),
        "tanks": PumpTank.query.count(),
    }
    return render_template("petrol_pumps/setup/index.html", counts=counts)


# --------------------------------------------------------------------------- #
# Machines
# --------------------------------------------------------------------------- #
@petrol_pumps_bp.route("/setup/machines")
@role_required(*PUMP_SETUP_ROLES)
def machines_list():
    q = request.args.get("q", "").strip()
    query = PumpMachine.query.join(PetrolPump)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                PetrolPump.name.ilike(like),
                PumpMachine.machine_name.ilike(like),
                PumpMachine.machine_number.ilike(like),
            )
        )
    machines = query.order_by(PetrolPump.name, PumpMachine.machine_name).all()
    return render_template(
        "petrol_pumps/setup/machines/list.html", machines=machines, q=q
    )


def _active_tanks():
    """All active tanks (for the machine 'connected tank' selector)."""
    return (
        PumpTank.query.filter_by(is_active=True)
        .join(PumpTank.petrol_pump)
        .order_by(PetrolPump.name, PumpTank.tank_name)
        .all()
    )


def _read_machine_form():
    return {
        "tank_id": _int_or_none(request.form.get("tank_id")),
        "machine_name": request.form.get("machine_name", "").strip(),
        "machine_number": request.form.get("machine_number", "").strip(),
        "description": request.form.get("description", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_machine_form(form):
    """A machine is wired to one tank; its pump + fuel come from that tank."""
    errors = []
    tank = db.session.get(PumpTank, form["tank_id"]) if form["tank_id"] else None
    if tank is None or not tank.is_active:
        errors.append("Please select the tank this machine is connected to.")
    if not form["machine_name"]:
        errors.append("Machine name is required.")
    pump = tank.petrol_pump if tank is not None else None
    product = tank.product if tank is not None else None
    return errors, pump, product, tank


def _ensure_machine_nozzles(machine, count=2):
    """Guarantee a machine has `count` active nozzles of its own fuel.

    Nozzles are numbered sequentially PER PRODUCT across the pump, so the
    Reading Console shows p1/p2 for the first petrol machine, p3/p4 for the
    next, d1/d2 for the first diesel machine, etc. Safe to call repeatedly —
    it only tops up missing nozzles (used on create and as a self-heal on edit).
    """
    have = len(machine.nozzles)
    if have >= count:
        return 0
    # How many nozzles already exist for this fuel at this pump → continue the
    # numbering from there (e.g. a 2nd petrol machine starts at 3).
    used = (
        db.session.query(db.func.count(PumpNozzle.id))
        .filter(
            PumpNozzle.petrol_pump_id == machine.petrol_pump_id,
            PumpNozzle.product_id == machine.product_id,
        )
        .scalar()
    ) or 0
    created = 0
    for _ in range(count - have):
        used += 1
        db.session.add(PumpNozzle(
            petrol_pump_id=machine.petrol_pump_id,
            machine_id=machine.id,
            product_id=machine.product_id,
            nozzle_number=str(used),
            opening_reading=Decimal("0"),
            current_reading=Decimal("0"),
            is_active=True,
        ))
        created += 1
    return created


@petrol_pumps_bp.route("/setup/machines/create", methods=["GET", "POST"])
@role_required(*PUMP_SETUP_ROLES)
def machines_create():
    if request.method == "POST":
        form = _read_machine_form()
        errors, pump, product, tank = _validate_machine_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/setup/machines/form.html",
                form=form, tanks=_active_tanks(), mode="create",
            )
        machine = PumpMachine(
            petrol_pump=pump, product=product, tank=tank,
            machine_name=form["machine_name"],
            machine_number=form["machine_number"] or None,
            description=form["description"] or None,
            is_active=form["is_active"],
        )
        db.session.add(machine)
        db.session.flush()  # need machine.id before creating its nozzles
        _ensure_machine_nozzles(machine)  # auto-create its 2 nozzles
        db.session.commit()
        flash(f"Machine '{machine.machine_name}' created ({product.name}), wired to "
              f"'{tank.tank_name}', with 2 nozzles.", "success")
        return redirect(url_for("petrol_pumps.machines_view", machine_id=machine.id))

    form = {
        "tank_id": None, "machine_name": "",
        "machine_number": "", "description": "", "is_active": True,
    }
    return render_template(
        "petrol_pumps/setup/machines/form.html",
        form=form, tanks=_active_tanks(), mode="create",
    )


@petrol_pumps_bp.route("/setup/machines/<int:machine_id>")
@role_required(*PUMP_SETUP_ROLES)
def machines_view(machine_id):
    machine = db.get_or_404(PumpMachine, machine_id)
    return render_template("petrol_pumps/setup/machines/detail.html", machine=machine)


@petrol_pumps_bp.route("/setup/machines/<int:machine_id>/edit", methods=["GET", "POST"])
@role_required(*PUMP_SETUP_ROLES)
def machines_edit(machine_id):
    machine = db.get_or_404(PumpMachine, machine_id)
    if request.method == "POST":
        form = _read_machine_form()
        errors, pump, product, tank = _validate_machine_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/setup/machines/form.html",
                form=form, tanks=_active_tanks(), mode="edit", machine=machine,
            )
        product_changed = machine.product_id != product.id
        machine.petrol_pump = pump
        machine.product = product
        machine.tank = tank
        machine.machine_name = form["machine_name"]
        machine.machine_number = form["machine_number"] or None
        machine.description = form["description"] or None
        machine.is_active = form["is_active"]
        # Keep the rule intact: a machine's nozzles always match its fuel.
        realigned = 0
        if product_changed:
            for n in machine.nozzles:
                if n.product_id != product.id:
                    n.product_id = product.id
                    realigned += 1
        db.session.flush()
        added = _ensure_machine_nozzles(machine)  # self-heal any missing nozzles
        db.session.commit()
        msg = f"Machine '{machine.machine_name}' updated ({product.name}), wired to '{tank.tank_name}'."
        if realigned:
            msg += f" {realigned} nozzle(s) re-aligned to {product.name}."
        if added:
            msg += f" {added} missing nozzle(s) created."
        flash(msg, "success")
        return redirect(url_for("petrol_pumps.machines_view", machine_id=machine.id))

    form = {
        "tank_id": machine.tank_id,
        "machine_name": machine.machine_name,
        "machine_number": machine.machine_number or "",
        "description": machine.description or "",
        "is_active": machine.is_active,
    }
    return render_template(
        "petrol_pumps/setup/machines/form.html",
        form=form, tanks=_active_tanks(), mode="edit", machine=machine,
    )


@petrol_pumps_bp.route("/setup/machines/<int:machine_id>/toggle-status", methods=["POST"])
@role_required(*PUMP_SETUP_ROLES)
def machines_toggle_status(machine_id):
    machine = db.get_or_404(PumpMachine, machine_id)
    machine.is_active = not machine.is_active
    db.session.commit()
    state = "activated" if machine.is_active else "deactivated"
    flash(f"Machine '{machine.machine_name}' {state}.", "info")
    return redirect(url_for("petrol_pumps.machines_list"))


@petrol_pumps_bp.route("/setup/machines/<int:machine_id>/delete", methods=["POST"])
@role_required(*PUMP_SETUP_ROLES)
def machines_delete(machine_id):
    """Hard-delete a machine and its nozzles — blocked if it has sale history."""
    machine = db.get_or_404(PumpMachine, machine_id)
    back = request.form.get("return_url") or url_for("petrol_pumps.machines_list")
    nozzle_ids = [n.id for n in machine.nozzles]
    has_readings = (
        MachineReading.query.filter_by(machine_id=machine.id).first() is not None
        or (nozzle_ids and MachineReading.query.filter(
            MachineReading.nozzle_id.in_(nozzle_ids)).first() is not None)
    )
    if has_readings:
        flash(f"Cannot delete machine '{machine.machine_name}' — it has machine "
              "reading history. Deactivate it instead.", "danger")
        return redirect(back)
    name = machine.machine_name
    for n in list(machine.nozzles):  # remove its (auto-created) nozzles first
        db.session.delete(n)
    db.session.delete(machine)
    db.session.commit()
    flash(f"Machine '{name}' and its nozzles deleted.", "success")
    return redirect(back)


# --------------------------------------------------------------------------- #
# Nozzles
# --------------------------------------------------------------------------- #
@petrol_pumps_bp.route("/setup/nozzles")
@role_required(*PUMP_SETUP_ROLES)
def nozzles_list():
    q = request.args.get("q", "").strip()
    query = (
        PumpNozzle.query.join(PetrolPump, PumpNozzle.petrol_pump_id == PetrolPump.id)
        .join(PumpMachine, PumpNozzle.machine_id == PumpMachine.id)
        .join(Product, PumpNozzle.product_id == Product.id)
    )
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                PetrolPump.name.ilike(like),
                PumpMachine.machine_name.ilike(like),
                PumpNozzle.nozzle_number.ilike(like),
                Product.name.ilike(like),
            )
        )
    nozzles = query.order_by(PetrolPump.name, PumpNozzle.nozzle_number).all()
    return render_template(
        "petrol_pumps/setup/nozzles/list.html", nozzles=nozzles, q=q
    )


def _read_nozzle_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "machine_id": _int_or_none(request.form.get("machine_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "nozzle_number": request.form.get("nozzle_number", "").strip(),
        "opening_reading": request.form.get("opening_reading", "").strip(),
        "current_reading": request.form.get("current_reading", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _nozzle_number_taken(machine_id, nozzle_number, exclude_id=None):
    query = PumpNozzle.query.filter(
        PumpNozzle.machine_id == machine_id,
        db.func.lower(PumpNozzle.nozzle_number) == nozzle_number.lower(),
    )
    if exclude_id is not None:
        query = query.filter(PumpNozzle.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _validate_nozzle_form(form, exclude_id=None):
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    machine = None
    if form["machine_id"] is None:
        errors.append("Machine is required.")
    else:
        machine = db.session.get(PumpMachine, form["machine_id"])
        if machine is None:
            errors.append("Please select a valid machine.")
        elif pump is not None and machine.petrol_pump_id != pump.id:
            errors.append("The selected machine does not belong to that petrol pump.")

    # A nozzle's fuel is INHERITED from its machine (a machine is single-fuel);
    # it is never chosen per-nozzle.
    product = machine.product if machine is not None else None
    if machine is not None and product is None:
        errors.append("This machine has no fuel product set — set it on the machine first.")

    if not form["nozzle_number"]:
        errors.append("Nozzle number is required.")
    elif machine is not None and _nozzle_number_taken(
        machine.id, form["nozzle_number"], exclude_id=exclude_id
    ):
        errors.append("This nozzle number already exists on the selected machine.")

    opening = _parse_nonneg(form["opening_reading"], "Opening reading", errors, default=Decimal("0"))
    current = _parse_nonneg(form["current_reading"], "Current reading", errors, default=Decimal("0"))
    if opening is not None and current is not None and current < opening:
        errors.append("Current reading cannot be less than the opening reading.")

    return errors, pump, machine, product, opening, current


@petrol_pumps_bp.route("/setup/nozzles/create", methods=["GET", "POST"])
@role_required(*PUMP_SETUP_ROLES)
def nozzles_create():
    if request.method == "POST":
        form = _read_nozzle_form()
        errors, pump, machine, product, opening, current = _validate_nozzle_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/setup/nozzles/form.html",
                form=form, pumps=_active_pumps(), machines=_active_machines(),
                products=_fuel_products(), mode="create",
            )
        nozzle = PumpNozzle(
            petrol_pump=pump, machine=machine, product=product,
            nozzle_number=form["nozzle_number"],
            opening_reading=opening, current_reading=current,
            is_active=form["is_active"],
        )
        db.session.add(nozzle)
        db.session.commit()
        flash(f"Nozzle '{nozzle.nozzle_number}' created.", "success")
        return redirect(url_for("petrol_pumps.nozzles_view", nozzle_id=nozzle.id))

    form = {
        "petrol_pump_id": None, "machine_id": None, "product_id": None,
        "nozzle_number": "", "opening_reading": "0", "current_reading": "0",
        "is_active": True,
    }
    return render_template(
        "petrol_pumps/setup/nozzles/form.html",
        form=form, pumps=_active_pumps(), machines=_active_machines(),
        products=_fuel_products(), mode="create",
    )


@petrol_pumps_bp.route("/setup/nozzles/<int:nozzle_id>")
@role_required(*PUMP_SETUP_ROLES)
def nozzles_view(nozzle_id):
    nozzle = db.get_or_404(PumpNozzle, nozzle_id)
    return render_template("petrol_pumps/setup/nozzles/detail.html", nozzle=nozzle)


@petrol_pumps_bp.route("/setup/nozzles/<int:nozzle_id>/edit", methods=["GET", "POST"])
@role_required(*PUMP_SETUP_ROLES)
def nozzles_edit(nozzle_id):
    nozzle = db.get_or_404(PumpNozzle, nozzle_id)
    if request.method == "POST":
        form = _read_nozzle_form()
        errors, pump, machine, product, opening, current = _validate_nozzle_form(
            form, exclude_id=nozzle.id
        )
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/setup/nozzles/form.html",
                form=form, pumps=_active_pumps(), machines=_active_machines(),
                products=_fuel_products(), mode="edit", nozzle=nozzle,
            )
        nozzle.petrol_pump = pump
        nozzle.machine = machine
        nozzle.product = product
        nozzle.nozzle_number = form["nozzle_number"]
        nozzle.opening_reading = opening
        nozzle.current_reading = current
        nozzle.is_active = form["is_active"]
        db.session.commit()
        flash(f"Nozzle '{nozzle.nozzle_number}' updated.", "success")
        return redirect(url_for("petrol_pumps.nozzles_view", nozzle_id=nozzle.id))

    form = {
        "petrol_pump_id": nozzle.petrol_pump_id,
        "machine_id": nozzle.machine_id,
        "product_id": nozzle.product_id,
        "nozzle_number": nozzle.nozzle_number,
        "opening_reading": str(nozzle.opening_reading),
        "current_reading": str(nozzle.current_reading),
        "is_active": nozzle.is_active,
    }
    return render_template(
        "petrol_pumps/setup/nozzles/form.html",
        form=form, pumps=_active_pumps(), machines=_active_machines(),
        products=_fuel_products(), mode="edit", nozzle=nozzle,
    )


@petrol_pumps_bp.route("/setup/nozzles/<int:nozzle_id>/toggle-status", methods=["POST"])
@role_required(*PUMP_SETUP_ROLES)
def nozzles_toggle_status(nozzle_id):
    nozzle = db.get_or_404(PumpNozzle, nozzle_id)
    nozzle.is_active = not nozzle.is_active
    db.session.commit()
    state = "activated" if nozzle.is_active else "deactivated"
    flash(f"Nozzle '{nozzle.nozzle_number}' {state}.", "info")
    return redirect(url_for("petrol_pumps.nozzles_list"))


# --------------------------------------------------------------------------- #
# Tanks
# --------------------------------------------------------------------------- #
@petrol_pumps_bp.route("/setup/tanks")
@role_required(*PUMP_SETUP_ROLES)
def tanks_list():
    q = request.args.get("q", "").strip()
    query = (
        PumpTank.query.join(PetrolPump, PumpTank.petrol_pump_id == PetrolPump.id)
        .join(Product, PumpTank.product_id == Product.id)
    )
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                PetrolPump.name.ilike(like),
                PumpTank.tank_name.ilike(like),
                Product.name.ilike(like),
            )
        )
    tanks = query.order_by(PetrolPump.name, PumpTank.tank_name).all()
    return render_template("petrol_pumps/setup/tanks/list.html", tanks=tanks, q=q)


def _read_tank_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "tank_name": request.form.get("tank_name", "").strip(),
        "capacity_liters": request.form.get("capacity_liters", "").strip(),
        "opening_stock_liters": request.form.get("opening_stock_liters", "").strip(),
        "current_stock_liters": request.form.get("current_stock_liters", "").strip(),
        "dip_reading": request.form.get("dip_reading", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _tank_name_taken(petrol_pump_id, tank_name, exclude_id=None):
    query = PumpTank.query.filter(
        PumpTank.petrol_pump_id == petrol_pump_id,
        db.func.lower(PumpTank.tank_name) == tank_name.lower(),
    )
    if exclude_id is not None:
        query = query.filter(PumpTank.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _validate_tank_form(form, exclude_id=None):
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    product = None
    if form["product_id"] is None:
        errors.append("Product is required.")
    else:
        product = db.session.get(Product, form["product_id"])
        if product is None:
            errors.append("Please select a valid product.")
        elif not _is_fuel_product(product):
            errors.append("Tank product must be a fuel product (Petrol, Diesel, High Octane).")

    if not form["tank_name"]:
        errors.append("Tank name is required.")
    elif pump is not None and _tank_name_taken(
        pump.id, form["tank_name"], exclude_id=exclude_id
    ):
        errors.append("This tank name already exists at the selected petrol pump.")

    capacity = _parse_nonneg(form["capacity_liters"], "Capacity", errors, default=None)
    opening = _parse_nonneg(form["opening_stock_liters"], "Opening stock", errors, default=Decimal("0"))
    current = _parse_nonneg(form["current_stock_liters"], "Current stock", errors, default=Decimal("0"))
    dip = _parse_nonneg(form["dip_reading"], "Dip reading", errors, default=None)

    if capacity is not None and current is not None and current > capacity:
        errors.append("Current stock cannot exceed the tank capacity.")

    return errors, pump, product, capacity, opening, current, dip


@petrol_pumps_bp.route("/setup/tanks/create", methods=["GET", "POST"])
@role_required(*PUMP_SETUP_ROLES)
def tanks_create():
    if request.method == "POST":
        form = _read_tank_form()
        errors, pump, product, capacity, opening, current, dip = _validate_tank_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/setup/tanks/form.html",
                form=form, pumps=_active_pumps(), products=_fuel_products(),
                mode="create",
            )
        tank = PumpTank(
            petrol_pump=pump, product=product, tank_name=form["tank_name"],
            capacity_liters=capacity, opening_stock_liters=opening,
            current_stock_liters=current, dip_reading=dip,
            is_active=form["is_active"],
        )
        db.session.add(tank)
        db.session.commit()
        flash(f"Tank '{tank.tank_name}' created.", "success")
        return redirect(url_for("petrol_pumps.tanks_view", tank_id=tank.id))

    form = {
        "petrol_pump_id": None, "product_id": None, "tank_name": "",
        "capacity_liters": "", "opening_stock_liters": "0",
        "current_stock_liters": "0", "dip_reading": "", "is_active": True,
    }
    return render_template(
        "petrol_pumps/setup/tanks/form.html",
        form=form, pumps=_active_pumps(), products=_fuel_products(), mode="create",
    )


@petrol_pumps_bp.route("/setup/tanks/<int:tank_id>")
@role_required(*PUMP_SETUP_ROLES)
def tanks_view(tank_id):
    tank = db.get_or_404(PumpTank, tank_id)
    return render_template("petrol_pumps/setup/tanks/detail.html", tank=tank)


@petrol_pumps_bp.route("/setup/tanks/<int:tank_id>/edit", methods=["GET", "POST"])
@role_required(*PUMP_SETUP_ROLES)
def tanks_edit(tank_id):
    tank = db.get_or_404(PumpTank, tank_id)
    if request.method == "POST":
        form = _read_tank_form()
        errors, pump, product, capacity, opening, current, dip = _validate_tank_form(
            form, exclude_id=tank.id
        )
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/setup/tanks/form.html",
                form=form, pumps=_active_pumps(), products=_fuel_products(),
                mode="edit", tank=tank,
            )
        tank.petrol_pump = pump
        tank.product = product
        tank.tank_name = form["tank_name"]
        tank.capacity_liters = capacity
        tank.opening_stock_liters = opening
        tank.current_stock_liters = current
        tank.dip_reading = dip
        tank.is_active = form["is_active"]
        db.session.commit()
        flash(f"Tank '{tank.tank_name}' updated.", "success")
        return redirect(url_for("petrol_pumps.tanks_view", tank_id=tank.id))

    form = {
        "petrol_pump_id": tank.petrol_pump_id,
        "product_id": tank.product_id,
        "tank_name": tank.tank_name,
        "capacity_liters": "" if tank.capacity_liters is None else str(tank.capacity_liters),
        "opening_stock_liters": str(tank.opening_stock_liters),
        "current_stock_liters": str(tank.current_stock_liters),
        "dip_reading": "" if tank.dip_reading is None else str(tank.dip_reading),
        "is_active": tank.is_active,
    }
    return render_template(
        "petrol_pumps/setup/tanks/form.html",
        form=form, pumps=_active_pumps(), products=_fuel_products(),
        mode="edit", tank=tank,
    )


@petrol_pumps_bp.route("/setup/tanks/<int:tank_id>/toggle-status", methods=["POST"])
@role_required(*PUMP_SETUP_ROLES)
def tanks_toggle_status(tank_id):
    tank = db.get_or_404(PumpTank, tank_id)
    tank.is_active = not tank.is_active
    db.session.commit()
    state = "activated" if tank.is_active else "deactivated"
    flash(f"Tank '{tank.tank_name}' {state}.", "info")
    return redirect(url_for("petrol_pumps.tanks_list"))


@petrol_pumps_bp.route("/setup/tanks/<int:tank_id>/delete", methods=["POST"])
@role_required(*PUMP_SETUP_ROLES)
def tanks_delete(tank_id):
    """Hard-delete a tank — blocked if it carries stock or has any history."""
    tank = db.get_or_404(PumpTank, tank_id)
    back = request.form.get("return_url") or url_for("petrol_pumps.tanks_list")
    blockers = []
    if (tank.current_stock_liters or 0) != 0:
        blockers.append(f"it still holds {tank.current_stock_liters} L of stock")
    if MachineReading.query.filter_by(tank_id=tank.id).first():
        blockers.append("it has retail sale history")
    if PumpPurchaseItem.query.filter_by(tank_id=tank.id).first():
        blockers.append("it is used by purchases")
    if StockMovement.query.filter_by(tank_id=tank.id).first():
        blockers.append("it has stock movements")
    if StockAdjustment.query.filter_by(tank_id=tank.id).first():
        blockers.append("it has gain/loss adjustments")
    if blockers:
        flash(f"Cannot delete tank '{tank.tank_name}' — " + "; ".join(blockers)
              + ". Deactivate it instead.", "danger")
        return redirect(back)
    name = tank.tank_name
    db.session.delete(tank)
    db.session.commit()
    flash(f"Tank '{name}' deleted.", "success")
    return redirect(back)


# --------------------------------------------------------------------------- #
# Machine Readings (petrol pump RETAIL fuel sale)
# --------------------------------------------------------------------------- #
def _active_nozzles():
    return PumpNozzle.query.filter_by(is_active=True).order_by(PumpNozzle.id).all()


def _reading_stock_note(reading):
    """Trailing note for flash messages describing the stock effect."""
    if reading.stock_posted and reading.tank is not None:
        return f" Tank '{reading.tank.tank_name}' stock decreased by {reading.sale_liters}."
    if reading.is_active and (reading.sale_liters or 0) > 0:
        return (
            " (Stock not adjusted: this pump has no single active tank for the "
            "product.)"
        )
    return ""


def _normalize_shift(raw):
    """Blank shift becomes the default ('Full Day')."""
    raw = (raw or "").strip()
    return raw if raw else DEFAULT_SHIFT


def _reading_duplicate(nozzle_id, reading_date, shift, exclude_id=None):
    """True if a reading already exists for this nozzle/date/shift."""
    query = MachineReading.query.filter(
        MachineReading.nozzle_id == nozzle_id,
        MachineReading.reading_date == reading_date,
        MachineReading.shift == shift,
    )
    if exclude_id is not None:
        query = query.filter(MachineReading.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _read_reading_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "machine_id": _int_or_none(request.form.get("machine_id")),
        "nozzle_id": _int_or_none(request.form.get("nozzle_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "reading_date": request.form.get("reading_date", "").strip(),
        "shift": request.form.get("shift", "").strip(),
        "opening_reading": request.form.get("opening_reading", "").strip(),
        "closing_reading": request.form.get("closing_reading", "").strip(),
        "rate": request.form.get("rate", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_reading_form(form, exclude_id=None, exclude_reading=None):
    """Validate a reading. Returns (errors, context dict of resolved objects)."""
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    machine = None
    if form["machine_id"] is None:
        errors.append("Machine is required.")
    else:
        machine = db.session.get(PumpMachine, form["machine_id"])
        if machine is None:
            errors.append("Please select a valid machine.")
        elif pump is not None and machine.petrol_pump_id != pump.id:
            errors.append("The selected machine does not belong to that petrol pump.")

    nozzle = None
    if form["nozzle_id"] is None:
        errors.append("Nozzle is required.")
    else:
        nozzle = db.session.get(PumpNozzle, form["nozzle_id"])
        if nozzle is None:
            errors.append("Please select a valid nozzle.")
        elif machine is not None and nozzle.machine_id != machine.id:
            errors.append("The selected nozzle does not belong to that machine.")

    product = None
    if form["product_id"] is None:
        errors.append("Product is required.")
    else:
        product = db.session.get(Product, form["product_id"])
        if product is None:
            errors.append("Please select a valid product.")
        elif nozzle is not None and product.id != nozzle.product_id:
            errors.append("Product must match the selected nozzle's product.")

    reading_date = None
    if not form["reading_date"]:
        errors.append("Reading date is required.")
    else:
        try:
            reading_date = datetime.strptime(form["reading_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid reading date.")

    opening = _parse_nonneg(form["opening_reading"], "Opening reading", errors)
    if opening is None and "Opening reading must not be negative." not in errors:
        if form["opening_reading"] == "":
            errors.append("Opening reading is required.")
    closing = _parse_nonneg(form["closing_reading"], "Closing reading", errors)
    if closing is None and form["closing_reading"] == "":
        errors.append("Closing reading is required.")
    rate = _parse_nonneg(form["rate"], "Rate", errors)
    if rate is None and form["rate"] == "":
        errors.append("Rate is required.")

    if opening is not None and closing is not None and closing < opening:
        errors.append("Closing reading must be greater than or equal to the opening reading.")
    elif (opening is not None and closing is not None and product is not None
          and pump is not None):
        # Stock limit: can't sell more than the wired tank holds.
        tank = stock.tank_for_nozzle(nozzle) if nozzle is not None \
            else stock.resolve_tank(pump.id, product.id)
        msg = _insufficient_stock_for_tank(tank, product, closing - opening,
                                           exclude_reading=exclude_reading)
        if msg:
            errors.append(msg)

    shift = _normalize_shift(form["shift"])
    if nozzle is not None and reading_date is not None and _reading_duplicate(
        nozzle.id, reading_date, shift, exclude_id=exclude_id
    ):
        errors.append(
            f"A reading already exists for this nozzle on {reading_date} ({shift})."
        )

    context = {
        "pump": pump, "machine": machine, "nozzle": nozzle, "product": product,
        "reading_date": reading_date, "shift": shift,
        "opening": opening, "closing": closing, "rate": rate,
    }
    return errors, context


def _reading_form_choices():
    return {
        "pumps": _active_pumps(),
        "machines": _active_machines(),
        "nozzles": _active_nozzles(),
        "products": _fuel_products(),
    }


@petrol_pumps_bp.route("/machine-readings")
@role_required(*READING_ROLES)
def machine_readings_list():
    """List machine readings — scoped to the selected pump, default date today."""
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    f_machine = _int_or_none(request.args.get("machine_id"))
    f_nozzle = _int_or_none(request.args.get("nozzle_id"))
    f_product = _int_or_none(request.args.get("product_id"))

    query = MachineReading.query
    if f_date:
        try:
            query = query.filter(
                MachineReading.reading_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(MachineReading.petrol_pump_id == f_pump)
    if f_machine:
        query = query.filter(MachineReading.machine_id == f_machine)
    if f_nozzle:
        query = query.filter(MachineReading.nozzle_id == f_nozzle)
    if f_product:
        query = query.filter(MachineReading.product_id == f_product)

    readings = query.order_by(
        MachineReading.reading_date.desc(), MachineReading.id.desc()
    ).all()

    export = _export_format()
    if export:
        rows = [[
            r.reading_date.isoformat(),
            r.petrol_pump.name if r.petrol_pump else "",
            r.machine.machine_name if r.machine else "",
            r.nozzle.nozzle_number if r.nozzle else "",
            r.product.name if r.product else "",
            fmt(r.opening_reading), fmt(r.closing_reading),
            fmt(r.sale_liters), fmt(r.rate), fmt(r.sale_amount),
            "Yes" if r.is_active else "No",
        ] for r in readings]
        blocks = [{
            "headers": [
                "Date", "Pump", "Machine", "Nozzle", "Product", "Opening",
                "Closing", "Liters", "Rate", "Amount", "Active",
            ],
            "rows": rows,
        }]
        return export_response(export, "machine_readings", "Machine Readings", blocks)

    return render_template(
        "petrol_pumps/machine_readings/list.html",
        readings=readings, pump=pump,
        filters={
            "date": f_date, "petrol_pump_id": f_pump, "machine_id": f_machine,
            "nozzle_id": f_nozzle, "product_id": f_product,
        },
        **_reading_form_choices(),
    )


# --------------------------------------------------------------------------- #
# Reading Console — the simple per-machine entry experience.
#
# Pick a pump → tap a machine → enter the 12-hour shift's closing readings and
# the sale rate per nozzle → the sale is computed and saved in one go. Reuses
# the exact same MachineReading rows + stock sync as the classic form.
# --------------------------------------------------------------------------- #
def _insufficient_stock_msg(pump_id, product, liters, exclude_reading=None):
    """Return an error string if selling `liters` of `product` would exceed the
    pump's tank stock for that product, else None.

    Enforces the rule: you can't sell fuel that isn't in the tank — no Diesel
    sale if the pump has no Diesel stock, etc.
    """
    if product is None or liters is None or liters <= 0:
        return None
    tank = stock.resolve_tank(pump_id, product.id)
    pname = product.name
    if tank is None:
        return (f"Cannot sell {pname}: this pump has no single active {pname} tank. "
                f"Set up the tank and record a purchase first.")
    available = tank.current_stock_liters or Decimal("0")
    # On edit, the tank still holds this reading's old decrement — add it back so
    # the check measures the NET change.
    if (exclude_reading is not None and exclude_reading.stock_posted
            and exclude_reading.tank_id == tank.id):
        available += exclude_reading.sale_liters or Decimal("0")
    if liters > available:
        return (f"Not enough {pname} in '{tank.tank_name}': only "
                f"{available:,.2f} L in stock, but this sale is {liters:,.2f} L. "
                f"Record a {pname} purchase before selling.")
    return None


def _insufficient_stock_for_tank(tank, product, liters, exclude_reading=None):
    """Like _insufficient_stock_msg but for a SPECIFIC tank (a machine is wired
    to one tank, so its sale must fit that tank's stock)."""
    if liters is None or liters <= 0:
        return None
    pname = product.name if product else "fuel"
    if tank is None:
        return (f"Cannot sell {pname}: this machine has no tank wired (or its tank "
                f"is inactive). Connect it to a tank and record a purchase first.")
    available = tank.current_stock_liters or Decimal("0")
    if (exclude_reading is not None and exclude_reading.stock_posted
            and exclude_reading.tank_id == tank.id):
        available += exclude_reading.sale_liters or Decimal("0")
    if liters > available:
        return (f"Not enough {pname} in '{tank.tank_name}': only "
                f"{available:,.2f} L in stock, but this sale is {liters:,.2f} L. "
                f"Record a {pname} purchase before selling.")
    return None


def _nozzle_rate_prefill(nozzle):
    """Best-guess sale rate for a nozzle: its latest reading's rate, else the
    product's default sale rate, else blank."""
    last = (
        MachineReading.query.filter_by(nozzle_id=nozzle.id, is_active=True)
        .order_by(MachineReading.reading_date.desc(), MachineReading.id.desc())
        .first()
    )
    if last is not None and last.rate:
        return last.rate
    if nozzle.product is not None and nozzle.product.default_sale_rate:
        return nozzle.product.default_sale_rate
    return ""


def _product_letter(product):
    """Short nozzle-label prefix per product: p=Petrol, d=Diesel, h=High Octane."""
    name = (product.name if product else "").lower()
    if "octane" in name:
        return "h"
    if "diesel" in name:
        return "d"
    if "petrol" in name:
        return "p"
    return name[:1] or "x"


# Display order for products in the console (petrol, diesel, octane, then rest).
_PRODUCT_ORDER = {"p": 0, "d": 1, "h": 2}


def _product_rate_prefill(pump_id, product):
    """Last rate used for this product at this pump, else its default, else ''.
    The rate carries forward until the user changes it."""
    last = (
        MachineReading.query.filter_by(
            petrol_pump_id=pump_id, product_id=product.id, is_active=True
        )
        .order_by(MachineReading.reading_date.desc(), MachineReading.id.desc())
        .first()
    )
    if last is not None and last.rate:
        return last.rate
    if product.default_sale_rate:
        return product.default_sale_rate
    return ""


@petrol_pumps_bp.route("/machine-readings/console")
@role_required(*READING_ROLES)
def machine_readings_console():
    """Single-table nozzle entry: pick Day/Night, set one rate per product, type
    each nozzle's closing meter. The pump is fixed by the dashboard selection."""
    pumps = _active_pumps()
    pump = _selected_pump(pumps)

    today = date.today()
    # Shift + date come from the URL so switching them reloads with a fresh
    # opening meter (a nozzle's opening = the last saved closing). Default Day.
    sel_shift = request.args.get("shift")
    if sel_shift not in CONSOLE_SHIFTS:
        sel_shift = SHIFT_DAY
    sel_date = _parse_date_arg("reading_date") or today

    nozzles = []
    products = []
    done = {SHIFT_DAY: False, SHIFT_NIGHT: False}
    # When the selected shift is already entered, we show its SAVED readings
    # (locked) instead of a blank closing — so revisiting a done shift displays
    # exactly what was entered, not the running meter as a new opening.
    saved_by_nozzle = {}
    saved_rate_by_product = {}
    if pump is not None:
        for s in CONSOLE_SHIFTS:
            done[s] = db.session.query(
                MachineReading.query.filter(
                    MachineReading.petrol_pump_id == pump.id,
                    MachineReading.reading_date == sel_date,
                    MachineReading.shift == s,
                ).exists()
            ).scalar()

        if done.get(sel_shift):
            saved_rows = MachineReading.query.filter(
                MachineReading.petrol_pump_id == pump.id,
                MachineReading.reading_date == sel_date,
                MachineReading.shift == sel_shift,
                MachineReading.is_active.is_(True),
            ).all()
            for r in saved_rows:
                if r.nozzle_id is not None:
                    saved_by_nozzle[r.nozzle_id] = r
                if r.product_id is not None and r.rate:
                    saved_rate_by_product[r.product_id] = r.rate

        this_done = bool(done.get(sel_shift))

        rows = (
            PumpNozzle.query.filter_by(petrol_pump_id=pump.id, is_active=True).all()
        )
        rows.sort(key=lambda n: (
            _PRODUCT_ORDER.get(_product_letter(n.product), 9),
            (n.product.name or "") if n.product else "", n.id,
        ))
        counters = {}
        seen = set()
        for n in rows:
            letter = _product_letter(n.product)
            counters[letter] = counters.get(letter, 0) + 1
            saved = saved_by_nozzle.get(n.id)
            if this_done and saved is not None:
                # Locked display of the saved entry, exactly as recorded.
                opening = saved.opening_reading or Decimal("0")
                closing = saved.closing_reading
            else:
                # Opening auto-fills from the running meter (the last saved
                # closing); it is editable so it can be corrected.
                opening = n.current_reading or Decimal("0")
                closing = None
            nozzles.append({
                "n": n,
                "label": f"{letter}{counters[letter]}",
                "opening": opening,
                "closing": closing,
                "locked": this_done and saved is not None,
                "product_id": n.product_id,
                "product_name": n.product.name if n.product else "",
            })
            if n.product_id not in seen:
                seen.add(n.product_id)
                # When the shift is saved, show the rate actually used.
                rate = saved_rate_by_product.get(n.product_id)
                if rate is None:
                    rate = _product_rate_prefill(pump.id, n.product)
                products.append({
                    "id": n.product_id,
                    "name": n.product.name if n.product else "",
                    "rate": rate,
                })

    return render_template(
        "petrol_pumps/machine_readings/console.html",
        pumps=pumps, pump=pump, nozzles=nozzles, products=products, today=today,
        shift_day=SHIFT_DAY, shift_night=SHIFT_NIGHT, sel_shift=sel_shift,
        sel_date=sel_date, done_day=done[SHIFT_DAY], done_night=done[SHIFT_NIGHT],
    )


@petrol_pumps_bp.route("/machine-readings/console/save", methods=["POST"])
@role_required(*READING_ROLES)
def machine_readings_console_save():
    """Save a pump's whole-shift nozzle readings in one POST (one rate per product)."""
    pump = db.session.get(PetrolPump, _int_or_none(request.form.get("petrol_pump_id")))
    shift = (request.form.get("shift") or "").strip()
    raw_date = (request.form.get("reading_date") or "").strip()

    errors = []
    if pump is None:
        errors.append("Please select a valid petrol pump.")
    if shift not in CONSOLE_SHIFTS:
        errors.append("Please pick Day or Night.")
    reading_date = None
    try:
        reading_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        errors.append("Please enter a valid reading date.")

    # AJAX submit (the console): return errors as JSON so the typed readings are
    # NOT lost and the page does not reload — the form just shows the errors.
    is_ajax = request.headers.get("X-Requested-With") == "fetch"

    def _fail(errs):
        if is_ajax:
            return jsonify({"ok": False, "errors": errs}), 200
        for msg in errs:
            flash(msg, "danger")
        # Non-JS fallback: reload the same shift + date.
        return redirect(url_for(
            "petrol_pumps.machine_readings_console", pump=pump.id if pump else None,
            shift=shift if shift in CONSOLE_SHIFTS else None, reading_date=raw_date or None,
        ))

    if errors:
        return _fail(errors)

    nozzles = (
        PumpNozzle.query.filter_by(petrol_pump_id=pump.id, is_active=True).all()
    )
    # One rate per product (entered once at the top of the console).
    product_rate = {}
    for n in nozzles:
        if n.product_id not in product_rate:
            product_rate[n.product_id] = _parse_nonneg(
                request.form.get(f"rate_{n.product_id}"),
                f"{n.product.name if n.product else 'Product'} rate", errors,
            )

    # Parse every nozzle row first (all-or-nothing save).
    planned = []
    for nozzle in nozzles:
        sid = str(nozzle.id)
        closing_raw = (request.form.get(f"closing_{sid}") or "").strip()
        if closing_raw == "":
            continue  # nozzle untouched this shift
        label = f"Nozzle {nozzle.nozzle_number} ({nozzle.product.name if nozzle.product else ''})"
        opening = _parse_nonneg(request.form.get(f"opening_{sid}"), f"{label} opening", errors)
        closing = _parse_nonneg(closing_raw, f"{label} closing", errors)
        if opening is None:
            errors.append(f"{label}: opening reading is required.")
            continue
        if closing is None:
            continue  # parse error already recorded
        if closing < opening:
            errors.append(f"{label}: closing reading must not be less than opening ({opening}).")
            continue
        rate = product_rate.get(nozzle.product_id)
        liters = closing - opening
        if liters > 0 and (rate is None or rate <= 0):
            errors.append(f"{nozzle.product.name if nozzle.product else 'Product'}: sale rate is required.")
            continue
        if _reading_duplicate(nozzle.id, reading_date, shift):
            errors.append(f"{label}: a {shift} reading already exists for {reading_date}.")
            continue
        planned.append((nozzle, opening, closing, rate or Decimal("0"), liters))

    # Stock limit per TANK — a machine is wired to one tank, so each tank's
    # drawn liters can't exceed that tank's stock.
    by_tank = {}
    for nozzle, _o, _c, _r, liters in planned:
        tank = stock.tank_for_nozzle(nozzle)
        key = tank.id if tank is not None else f"none-{nozzle.product_id}"
        if key not in by_tank:
            by_tank[key] = [tank, nozzle.product, Decimal("0")]
        by_tank[key][2] += liters
    for tank, product, total in by_tank.values():
        msg = _insufficient_stock_for_tank(tank, product, total)
        if msg:
            errors.append(msg)

    if errors:
        return _fail(errors)
    if not planned:
        return _fail(["Nothing to save — enter at least one closing reading."])

    total_liters = Decimal("0")
    total_amount = Decimal("0")
    for nozzle, opening, closing, rate, liters in planned:
        amount = liters * rate
        reading = MachineReading(
            petrol_pump=pump, machine_id=nozzle.machine_id, nozzle=nozzle,
            product=nozzle.product, reading_date=reading_date, shift=shift,
            opening_reading=opening, closing_reading=closing,
            sale_liters=liters, rate=rate, sale_amount=amount,
            entered_by_id=current_user.id, is_active=True,
        )
        db.session.add(reading)
        nozzle.current_reading = closing
        db.session.flush()
        stock.sync_reading_stock(reading)
        total_liters += liters
        total_amount += amount

    db.session.commit()
    # Auto-advance to the OTHER shift for the same date so the next entry's
    # opening is already the closing just saved (day → night, night → day).
    next_shift = SHIFT_NIGHT if shift == SHIFT_DAY else SHIFT_DAY
    flash(
        f"{pump.name} · {shift} saved — {total_liters} liters, "
        f"sale PKR {total_amount}. Now entering {next_shift}.",
        "success",
    )
    next_url = url_for(
        "petrol_pumps.machine_readings_console", pump=pump.id,
        shift=next_shift, reading_date=reading_date.isoformat(),
    )
    if is_ajax:
        return jsonify({"ok": True, "redirect": next_url})
    return redirect(next_url)


@petrol_pumps_bp.route("/machine-readings/create", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def machine_readings_create():
    if request.method == "POST":
        form = _read_reading_form()
        errors, ctx = _validate_reading_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/machine_readings/form.html",
                form=form, mode="create", **_reading_form_choices(),
            )

        sale_liters = ctx["closing"] - ctx["opening"]
        sale_amount = sale_liters * ctx["rate"]

        reading = MachineReading(
            petrol_pump=ctx["pump"], machine=ctx["machine"], nozzle=ctx["nozzle"],
            product=ctx["product"], reading_date=ctx["reading_date"],
            shift=ctx["shift"], opening_reading=ctx["opening"],
            closing_reading=ctx["closing"], sale_liters=sale_liters,
            rate=ctx["rate"], sale_amount=sale_amount,
            entered_by_id=current_user.id, notes=form["notes"] or None,
            is_active=form["is_active"],
        )
        db.session.add(reading)
        # Advance the nozzle meter to this closing reading.
        ctx["nozzle"].current_reading = ctx["closing"]
        db.session.flush()  # populate reading.id before posting stock
        stock.sync_reading_stock(reading)
        db.session.commit()
        flash(
            f"Machine reading saved: {sale_liters} liters, amount {sale_amount}."
            + _reading_stock_note(reading),
            "success",
        )
        return redirect(url_for("petrol_pumps.machine_readings_view", reading_id=reading.id))

    form = {
        "petrol_pump_id": None, "machine_id": None, "nozzle_id": None,
        "product_id": None, "reading_date": date.today().isoformat(),
        "shift": "", "opening_reading": "", "closing_reading": "",
        "rate": "", "notes": "", "is_active": True,
    }
    return render_template(
        "petrol_pumps/machine_readings/form.html",
        form=form, mode="create", **_reading_form_choices(),
    )


@petrol_pumps_bp.route("/machine-readings/<int:reading_id>")
@role_required(*READING_ROLES)
def machine_readings_view(reading_id):
    reading = db.get_or_404(MachineReading, reading_id)
    return render_template(
        "petrol_pumps/machine_readings/detail.html", reading=reading
    )


@petrol_pumps_bp.route("/machine-readings/<int:reading_id>/edit", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def machine_readings_edit(reading_id):
    reading = db.get_or_404(MachineReading, reading_id)

    if request.method == "POST":
        form = _read_reading_form()
        errors, ctx = _validate_reading_form(form, exclude_id=reading.id, exclude_reading=reading)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/machine_readings/form.html",
                form=form, mode="edit", reading=reading, **_reading_form_choices(),
            )

        sale_liters = ctx["closing"] - ctx["opening"]
        sale_amount = sale_liters * ctx["rate"]

        reading.petrol_pump = ctx["pump"]
        reading.machine = ctx["machine"]
        reading.nozzle = ctx["nozzle"]
        reading.product = ctx["product"]
        reading.reading_date = ctx["reading_date"]
        reading.shift = ctx["shift"]
        reading.opening_reading = ctx["opening"]
        reading.closing_reading = ctx["closing"]
        reading.sale_liters = sale_liters
        reading.rate = ctx["rate"]
        reading.sale_amount = sale_amount
        reading.notes = form["notes"] or None
        reading.is_active = form["is_active"]
        ctx["nozzle"].current_reading = ctx["closing"]
        db.session.flush()
        stock.sync_reading_stock(reading)  # reverses old, re-posts new
        db.session.commit()
        flash(
            f"Machine reading updated: {sale_liters} liters, amount {sale_amount}."
            + _reading_stock_note(reading),
            "success",
        )
        return redirect(url_for("petrol_pumps.machine_readings_view", reading_id=reading.id))

    form = {
        "petrol_pump_id": reading.petrol_pump_id,
        "machine_id": reading.machine_id,
        "nozzle_id": reading.nozzle_id,
        "product_id": reading.product_id,
        "reading_date": reading.reading_date.isoformat(),
        "shift": reading.shift or "",
        "opening_reading": str(reading.opening_reading),
        "closing_reading": str(reading.closing_reading),
        "rate": str(reading.rate),
        "notes": reading.notes or "",
        "is_active": reading.is_active,
    }
    return render_template(
        "petrol_pumps/machine_readings/form.html",
        form=form, mode="edit", reading=reading, **_reading_form_choices(),
    )


@petrol_pumps_bp.route("/machine-readings/<int:reading_id>/toggle-status", methods=["POST"])
@role_required(*READING_ROLES)
def machine_readings_toggle_status(reading_id):
    reading = db.get_or_404(MachineReading, reading_id)
    reading.is_active = not reading.is_active
    stock.sync_reading_stock(reading)  # deactivate reverses, activate re-posts
    db.session.commit()
    state = "activated" if reading.is_active else "deactivated"
    flash(f"Machine reading {state}.{_reading_stock_note(reading)}", "info")
    return redirect(url_for("petrol_pumps.machine_readings_list"))


# --------------------------------------------------------------------------- #
# Daily Sale Summary (from machine readings only)
# --------------------------------------------------------------------------- #
@petrol_pumps_bp.route("/daily-sale-summary")
@role_required(*READING_ROLES)
def daily_sale_summary():
    """Product-wise retail sale totals from active machine readings. Scoped to
    the dashboard-selected pump (no pump picker) and defaulting to TODAY so the
    day's sale shows without clicking Filter; change the date to see another day."""
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None

    query = (
        db.session.query(
            Product.name.label("product_name"),
            db.func.sum(MachineReading.sale_liters).label("liters"),
            db.func.sum(MachineReading.sale_amount).label("amount"),
        )
        .join(Product, MachineReading.product_id == Product.id)
        .filter(MachineReading.is_active.is_(True))
    )
    if f_date:
        try:
            query = query.filter(
                MachineReading.reading_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(MachineReading.petrol_pump_id == f_pump)

    rows = query.group_by(Product.name).order_by(Product.name).all()

    total_liters = sum((r.liters or 0) for r in rows)
    total_amount = sum((r.amount or 0) for r in rows)

    export = _export_format()
    if export:
        data = [
            [r.product_name, fmt(r.liters or 0), fmt(r.amount or 0)] for r in rows
        ]
        data.append(["Total", fmt(total_liters), fmt(total_amount)])
        blocks = [{
            "headers": ["Product", "Sale Liters", "Sale Amount"], "rows": data,
        }]
        caption = f"Daily Sale Summary ({f_date or 'all dates'})"
        return export_response(export, "daily_sale_summary", caption, blocks)

    return render_template(
        "petrol_pumps/daily_sale_summary.html",
        rows=rows, total_liters=total_liters, total_amount=total_amount,
        filters={"date": f_date, "petrol_pump_id": f_pump},
        pump=pump, pumps=pumps,
    )


@petrol_pumps_bp.route("/nozzle-sale-summary")
@role_required(*READING_ROLES)
def nozzle_sale_summary():
    """Nozzle-wise retail sale totals — scoped to the selected pump, default today."""
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None

    query = (
        db.session.query(
            PetrolPump.name.label("pump"),
            PumpMachine.machine_name.label("machine"),
            PumpNozzle.nozzle_number.label("nozzle"),
            Product.name.label("product"),
            db.func.sum(MachineReading.sale_liters).label("liters"),
            db.func.sum(MachineReading.sale_amount).label("amount"),
        )
        .join(PetrolPump, MachineReading.petrol_pump_id == PetrolPump.id)
        .join(PumpMachine, MachineReading.machine_id == PumpMachine.id)
        .join(PumpNozzle, MachineReading.nozzle_id == PumpNozzle.id)
        .join(Product, MachineReading.product_id == Product.id)
        .filter(MachineReading.is_active.is_(True))
    )
    if f_date:
        try:
            query = query.filter(
                MachineReading.reading_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(MachineReading.petrol_pump_id == f_pump)

    rows = query.group_by(
        PetrolPump.name, PumpMachine.machine_name, PumpNozzle.nozzle_number, Product.name
    ).order_by(PetrolPump.name, PumpMachine.machine_name, PumpNozzle.nozzle_number).all()

    total_liters = sum((r.liters or 0) for r in rows)
    total_amount = sum((r.amount or 0) for r in rows)

    export = _export_format()
    if export:
        data = [[r.pump, r.machine, r.nozzle, r.product, fmt(r.liters or 0), fmt(r.amount or 0)] for r in rows]
        data.append(["Total", "", "", "", fmt(total_liters), fmt(total_amount)])
        blocks = [{
            "headers": ["Pump", "Machine", "Nozzle", "Product", "Sale Liters", "Sale Amount"],
            "rows": data,
        }]
        return export_response(export, "nozzle_sale_summary", f"Nozzle-wise Sale ({f_date or 'all dates'})", blocks)

    return render_template(
        "petrol_pumps/nozzle_sale_summary.html",
        rows=rows, total_liters=total_liters, total_amount=total_amount,
        filters={"date": f_date, "petrol_pump_id": f_pump},
        pump=pump, pumps=_active_pumps(),
    )


# --------------------------------------------------------------------------- #
# Stock: tank balances + movement ledger
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Stock Console — one premium screen for every pump's tank stock, movement
# ledger and gain/loss, date-filterable (mirrors the Reading/Purchase consoles).
# --------------------------------------------------------------------------- #
def _parse_date_arg(name):
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


@petrol_pumps_bp.route("/stock-console")
@role_required(*READING_ROLES)
def stock_console():
    pumps = _active_pumps()
    pump = _selected_pump(pumps)

    # Date range (default: this month to today). Movements/gain-loss respect it.
    today = date.today()
    date_to = _parse_date_arg("to") or today
    date_from = _parse_date_arg("from") or today.replace(day=1)

    tanks = []
    if pump is not None:
        for t in (
            PumpTank.query.filter_by(petrol_pump_id=pump.id, is_active=True)
            .order_by(PumpTank.id).all()
        ):
            moves = (
                StockMovement.query.filter(
                    StockMovement.tank_id == t.id,
                    StockMovement.movement_date >= date_from,
                    StockMovement.movement_date <= date_to,
                )
                .order_by(StockMovement.movement_date.desc(), StockMovement.id.desc())
                .all()
            )
            total_in = sum((m.quantity_liters for m in moves if (m.quantity_liters or 0) > 0), Decimal("0"))
            total_out = sum((-m.quantity_liters for m in moves if (m.quantity_liters or 0) < 0), Decimal("0"))
            adjustments = (
                StockAdjustment.query.filter(
                    StockAdjustment.tank_id == t.id,
                    StockAdjustment.is_active.is_(True),
                    StockAdjustment.adjustment_date >= date_from,
                    StockAdjustment.adjustment_date <= date_to,
                )
                .order_by(StockAdjustment.adjustment_date.desc(), StockAdjustment.id.desc())
                .all()
            )
            cap = t.capacity_liters or Decimal("0")
            cur = t.current_stock_liters or Decimal("0")
            pct = int(min(max((cur / cap * 100), 0), 100)) if cap > 0 else 0
            tanks.append({
                "t": t, "moves": moves, "total_in": total_in, "total_out": total_out,
                "adjustments": adjustments, "fill_pct": pct, "current": cur,
            })

    return render_template(
        "petrol_pumps/stock_console.html",
        pumps=pumps, pump=pump, tanks=tanks, today=today,
        date_from=date_from, date_to=date_to,
        MOVEMENT_PURCHASE_RECEIVED=MOVEMENT_PURCHASE_RECEIVED,
        MOVEMENT_RETAIL_SALE=MOVEMENT_RETAIL_SALE,
        MOVEMENT_STOCK_GAIN=MOVEMENT_STOCK_GAIN,
        MOVEMENT_STOCK_LOSS=MOVEMENT_STOCK_LOSS,
    )


@petrol_pumps_bp.route("/tank-stock")
@role_required(*READING_ROLES)
def tank_stock():
    """Current fuel tank stock balances — scoped to the selected pump."""
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    tanks = stock.tank_stock_rows(petrol_pump_id=f_pump)
    total = sum((t.current_stock_liters or Decimal("0")) for t in tanks)

    export = _export_format()
    if export:
        rows = [[
            t.petrol_pump.name if t.petrol_pump else "",
            t.tank_name,
            t.product.name if t.product else "",
            fmt(t.opening_stock_liters),
            fmt(t.current_stock_liters),
            fmt(t.capacity_liters) if t.capacity_liters is not None else "",
        ] for t in tanks]
        rows.append(["Total", "", "", "", fmt(total), ""])
        blocks = [{
            "headers": ["Pump", "Tank", "Product", "Opening", "Current", "Capacity"],
            "rows": rows,
        }]
        return export_response(export, "tank_stock", "Tank Stock", blocks)

    return render_template(
        "petrol_pumps/tank_stock.html",
        tanks=tanks, total=total, pump=pump,
        filters={"petrol_pump_id": f_pump}, pumps=pumps,
    )


@petrol_pumps_bp.route("/stock-movements")
@role_required(*READING_ROLES)
def stock_movements_list():
    """Audit ledger of fuel tank stock movements — scoped to the selected pump,
    default date today."""
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    f_product = _int_or_none(request.args.get("product_id"))
    f_type = request.args.get("movement_type", "").strip()

    query = StockMovement.query
    if f_date:
        try:
            query = query.filter(
                StockMovement.movement_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(StockMovement.petrol_pump_id == f_pump)
    if f_product:
        query = query.filter(StockMovement.product_id == f_product)
    if f_type:
        query = query.filter(StockMovement.movement_type == f_type)

    movements = query.order_by(
        StockMovement.movement_date.desc(), StockMovement.id.desc()
    ).all()

    export = _export_format()
    if export:
        rows = [[
            m.movement_date.isoformat(),
            m.petrol_pump.name if m.petrol_pump else "",
            m.tank.tank_name if m.tank else "",
            m.product.name if m.product else "",
            m.movement_type,
            fmt(m.quantity_liters),
            m.notes or "",
        ] for m in movements]
        blocks = [{
            "headers": ["Date", "Pump", "Tank", "Product", "Type", "Qty (L)", "Notes"],
            "rows": rows,
        }]
        return export_response(export, "stock_movements", "Stock Movements", blocks)

    return render_template(
        "petrol_pumps/stock_movements/list.html",
        movements=movements, pump=pump,
        filters={
            "date": f_date, "petrol_pump_id": f_pump,
            "product_id": f_product, "movement_type": f_type,
        },
        pumps=pumps, products=_fuel_products(),
        movement_types=STOCK_MOVEMENT_TYPES,
    )


# --------------------------------------------------------------------------- #
# Pump Staff / HR (BRD §6.9) — employee master
# --------------------------------------------------------------------------- #
# Staff records carry salary info, so they stay within the management roles
# (Cashier intentionally excluded, like pump setup).
STAFF_ROLES = PUMP_SETUP_ROLES


def _read_staff_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "employee_name": request.form.get("employee_name", "").strip(),
        "designation": request.form.get("designation", "").strip(),
        "shift": request.form.get("shift", "").strip(),
        "cnic": request.form.get("cnic", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip(),
        "emergency_contact": request.form.get("emergency_contact", "").strip(),
        "address": request.form.get("address", "").strip(),
        "monthly_salary": request.form.get("monthly_salary", "").strip(),
        "joining_date": request.form.get("joining_date", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_staff_form(form):
    """Validate a staff entry. Returns (errors, pump, salary, joining_date)."""
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump (duty station) is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    if not form["employee_name"]:
        errors.append("Employee name is required.")

    if not form["designation"]:
        errors.append("Designation is required.")
    elif form["designation"] not in STAFF_DESIGNATIONS:
        errors.append("Please select a valid designation.")

    if form["shift"] and form["shift"] not in STAFF_SHIFTS:
        errors.append("Please select a valid shift.")

    salary = _parse_nonneg(
        form["monthly_salary"], "Monthly salary", errors, default=Decimal("0")
    )

    joining_date = None
    if form["joining_date"]:
        try:
            joining_date = datetime.strptime(form["joining_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid joining date.")

    return errors, pump, salary, joining_date


def _staff_form_choices():
    return {
        "pumps": _active_pumps(),
        "designations": STAFF_DESIGNATIONS,
        "shifts": STAFF_SHIFTS,
    }


@petrol_pumps_bp.route("/staff")
@role_required(*STAFF_ROLES)
def staff_list():
    """List the SELECTED pump's employees (scoped to the dashboard pump — no
    'all pumps' view) with filters + Excel/PDF export."""
    q = request.args.get("q", "").strip()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    f_designation = request.args.get("designation", "").strip()

    query = PumpStaff.query.join(PetrolPump)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            PumpStaff.employee_name.ilike(like),
            PumpStaff.cnic.ilike(like),
            PumpStaff.phone_number.ilike(like),
        ))
    if f_pump:
        query = query.filter(PumpStaff.petrol_pump_id == f_pump)
    if f_designation:
        query = query.filter(PumpStaff.designation == f_designation)

    staff = query.order_by(PetrolPump.name, PumpStaff.employee_name).all()

    export = _export_format()
    if export:
        rows = [[
            s.employee_name,
            s.petrol_pump.name if s.petrol_pump else "",
            s.designation,
            s.shift or "",
            s.cnic or "",
            s.phone_number or "",
            fmt(s.monthly_salary),
            "Active" if s.is_active else "Inactive",
        ] for s in staff]
        blocks = [{
            "headers": [
                "Employee", "Duty Station", "Designation", "Shift", "CNIC",
                "Phone", "Monthly Salary", "Status",
            ],
            "rows": rows,
        }]
        return export_response(export, "pump_staff", "Pump Staff", blocks)

    return render_template(
        "petrol_pumps/staff/list.html",
        staff=staff, pump=pump,
        filters={"q": q, "petrol_pump_id": f_pump, "designation": f_designation},
        pumps=pumps, designations=STAFF_DESIGNATIONS,
    )


@petrol_pumps_bp.route("/staff/create", methods=["GET", "POST"])
@role_required(*STAFF_ROLES)
def staff_create():
    if request.method == "POST":
        form = _read_staff_form()
        errors, pump, salary, joining_date = _validate_staff_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/staff/form.html",
                form=form, mode="create", **_staff_form_choices(),
            )
        staff = PumpStaff(
            petrol_pump=pump, employee_name=form["employee_name"],
            designation=form["designation"], shift=form["shift"] or None,
            cnic=form["cnic"] or None, phone_number=form["phone_number"] or None,
            emergency_contact=form["emergency_contact"] or None,
            address=form["address"] or None, monthly_salary=salary,
            joining_date=joining_date, notes=form["notes"] or None,
            created_by_id=current_user.id, is_active=form["is_active"],
        )
        db.session.add(staff)
        db.session.commit()
        flash(f"Employee '{staff.employee_name}' added.", "success")
        return redirect(url_for("petrol_pumps.staff_view", staff_id=staff.id))

    form = {
        "petrol_pump_id": _selected_pump_id(_active_pumps()),
        "employee_name": "", "designation": "",
        "shift": "", "cnic": "", "phone_number": "", "emergency_contact": "",
        "address": "", "monthly_salary": "", "joining_date": "", "notes": "",
        "is_active": True,
    }
    return render_template(
        "petrol_pumps/staff/form.html",
        form=form, mode="create", **_staff_form_choices(),
    )


@petrol_pumps_bp.route("/staff/<int:staff_id>")
@role_required(*STAFF_ROLES)
def staff_view(staff_id):
    staff = db.get_or_404(PumpStaff, staff_id)
    return render_template("petrol_pumps/staff/detail.html", staff=staff)


@petrol_pumps_bp.route("/staff/<int:staff_id>/edit", methods=["GET", "POST"])
@role_required(*STAFF_ROLES)
def staff_edit(staff_id):
    staff = db.get_or_404(PumpStaff, staff_id)
    if request.method == "POST":
        form = _read_staff_form()
        errors, pump, salary, joining_date = _validate_staff_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/staff/form.html",
                form=form, mode="edit", staff=staff, **_staff_form_choices(),
            )
        staff.petrol_pump = pump
        staff.employee_name = form["employee_name"]
        staff.designation = form["designation"]
        staff.shift = form["shift"] or None
        staff.cnic = form["cnic"] or None
        staff.phone_number = form["phone_number"] or None
        staff.emergency_contact = form["emergency_contact"] or None
        staff.address = form["address"] or None
        staff.monthly_salary = salary
        staff.joining_date = joining_date
        staff.notes = form["notes"] or None
        staff.is_active = form["is_active"]
        db.session.commit()
        flash(f"Employee '{staff.employee_name}' updated.", "success")
        return redirect(url_for("petrol_pumps.staff_view", staff_id=staff.id))

    form = {
        "petrol_pump_id": staff.petrol_pump_id,
        "employee_name": staff.employee_name,
        "designation": staff.designation,
        "shift": staff.shift or "",
        "cnic": staff.cnic or "",
        "phone_number": staff.phone_number or "",
        "emergency_contact": staff.emergency_contact or "",
        "address": staff.address or "",
        "monthly_salary": str(staff.monthly_salary),
        "joining_date": staff.joining_date.isoformat() if staff.joining_date else "",
        "notes": staff.notes or "",
        "is_active": staff.is_active,
    }
    return render_template(
        "petrol_pumps/staff/form.html",
        form=form, mode="edit", staff=staff, **_staff_form_choices(),
    )


@petrol_pumps_bp.route("/staff/<int:staff_id>/toggle-status", methods=["POST"])
@role_required(*STAFF_ROLES)
def staff_toggle_status(staff_id):
    staff = db.get_or_404(PumpStaff, staff_id)
    staff.is_active = not staff.is_active
    db.session.commit()
    state = "activated" if staff.is_active else "deactivated"
    flash(f"Employee '{staff.employee_name}' {state}.", "info")
    return redirect(url_for("petrol_pumps.staff_list"))


@petrol_pumps_bp.route("/staff/<int:staff_id>/delete", methods=["POST"])
@role_required(*STAFF_ROLES)
def staff_delete(staff_id):
    """Permanently remove an employee record (no dependents yet)."""
    staff = db.get_or_404(PumpStaff, staff_id)
    name = staff.employee_name
    db.session.delete(staff)
    db.session.commit()
    flash(f"Employee '{name}' permanently deleted.", "success")
    return redirect(url_for("petrol_pumps.staff_list"))


# --------------------------------------------------------------------------- #
# Staff Attendance (BRD §6.9) — daily mark grid + report
# --------------------------------------------------------------------------- #
def _active_staff_for_pump(pump_id):
    return (
        PumpStaff.query.filter_by(petrol_pump_id=pump_id, is_active=True)
        .order_by(PumpStaff.employee_name)
        .all()
    )


@petrol_pumps_bp.route("/staff/attendance", methods=["GET", "POST"])
@role_required(*STAFF_ROLES)
def attendance_mark():
    """Mark daily attendance for all active staff at a pump (upsert grid)."""
    if request.method == "POST":
        pump_id = _int_or_none(request.form.get("petrol_pump_id"))
        raw_date = request.form.get("attendance_date", "").strip()
        pump = db.session.get(PetrolPump, pump_id) if pump_id else None
        try:
            att_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            att_date = None
        if pump is None or att_date is None:
            flash("Please select a valid pump and date.", "danger")
            return redirect(url_for("petrol_pumps.attendance_mark"))

        staff = _active_staff_for_pump(pump.id)
        marked = 0
        for member in staff:
            status = request.form.get(f"status_{member.id}", "").strip()
            if status not in ATTENDANCE_STATUSES:
                continue
            record = PumpAttendance.query.filter_by(
                staff_id=member.id, attendance_date=att_date
            ).first()
            if record is None:
                record = PumpAttendance(
                    staff_id=member.id, petrol_pump_id=pump.id,
                    attendance_date=att_date, created_by_id=current_user.id,
                )
                db.session.add(record)
            record.status = status
            record.petrol_pump_id = pump.id
            marked += 1
        db.session.commit()
        flash(f"Attendance saved for {marked} employee(s) on {att_date}.", "success")
        return redirect(url_for(
            "petrol_pumps.attendance_mark",
            petrol_pump_id=pump.id, date=att_date.isoformat(),
        ))

    # GET: show the grid for the chosen pump + date.
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    try:
        att_date = datetime.strptime(f_date, "%Y-%m-%d").date()
    except ValueError:
        att_date = date.today()

    staff_rows = []
    if f_pump:
        existing = {
            r.staff_id: r.status
            for r in PumpAttendance.query.filter_by(attendance_date=att_date)
            .filter(PumpAttendance.petrol_pump_id == f_pump).all()
        }
        for member in _active_staff_for_pump(f_pump):
            staff_rows.append({"staff": member, "status": existing.get(member.id, "Present")})

    return render_template(
        "petrol_pumps/staff/attendance_mark.html",
        pumps=_active_pumps(), statuses=ATTENDANCE_STATUSES,
        staff_rows=staff_rows,
        filters={"petrol_pump_id": f_pump, "date": att_date.isoformat()},
    )


@petrol_pumps_bp.route("/staff/attendance/report")
@role_required(*STAFF_ROLES)
def attendance_report():
    """Attendance records with filters + Excel/PDF export."""
    f_date = request.args.get("date", "").strip()
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))
    f_status = request.args.get("status", "").strip()

    query = PumpAttendance.query.join(PumpStaff)
    if f_date:
        try:
            query = query.filter(
                PumpAttendance.attendance_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(PumpAttendance.petrol_pump_id == f_pump)
    if f_status and f_status in ATTENDANCE_STATUSES:
        query = query.filter(PumpAttendance.status == f_status)

    records = query.order_by(
        PumpAttendance.attendance_date.desc(), PumpStaff.employee_name
    ).all()

    export = _export_format()
    if export:
        rows = [[
            r.attendance_date.isoformat(),
            r.staff.employee_name if r.staff else "",
            r.petrol_pump.name if r.petrol_pump else "",
            r.staff.designation if r.staff else "",
            r.status,
        ] for r in records]
        blocks = [{
            "headers": ["Date", "Employee", "Pump", "Designation", "Status"],
            "rows": rows,
        }]
        return export_response(export, "attendance", "Staff Attendance", blocks)

    return render_template(
        "petrol_pumps/staff/attendance_report.html",
        records=records,
        filters={"date": f_date, "petrol_pump_id": f_pump, "status": f_status},
        pumps=_active_pumps(), statuses=ATTENDANCE_STATUSES,
    )


# --------------------------------------------------------------------------- #
# Staff Salary / Advance Payments (BRD §6.9) — posts to cash/bank
# --------------------------------------------------------------------------- #
# Salary payments move money, so the Accountant is included here (unlike the
# rest of the staff master).
SALARY_ROLES = (
    "Admin / Owner",
    "Head Office Manager",
    "Petrol Pump Manager",
    "Accountant",
)


def _sync_salary(payment):
    """Salaries/advances are paid out of the pump's cash drawer — exactly like a
    pump expense — so they reduce services.pump_current_cash directly and do NOT
    post against a separate cash/bank account (that would double-count). Only the
    GL voucher is (re)built here."""
    posting.sync_salary_payment(payment)


def _active_accounts():
    return (
        CashBankAccount.query.filter_by(is_active=True)
        .order_by(CashBankAccount.name)
        .all()
    )


def _all_staff(pump_id=None):
    query = PumpStaff.query.filter_by(is_active=True).join(PetrolPump)
    if pump_id:
        query = query.filter(PumpStaff.petrol_pump_id == pump_id)
    return query.order_by(PetrolPump.name, PumpStaff.employee_name).all()


def _read_salary_form():
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


def _validate_salary_form(form):
    errors = []
    staff = None
    if form["staff_id"] is None:
        errors.append("Employee is required.")
    else:
        staff = db.session.get(PumpStaff, form["staff_id"])
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

    if not form["payment_type"]:
        errors.append("Payment type is required.")
    elif form["payment_type"] not in SALARY_PAYMENT_TYPES:
        errors.append("Please select a valid payment type.")

    amount = _parse_nonneg(form["amount"], "Amount", errors)
    if amount is None and form["amount"] == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")

    account = None
    if form["paid_from_account_id"] is not None:
        account = db.session.get(CashBankAccount, form["paid_from_account_id"])
        if account is None:
            errors.append("Please select a valid paid-from account.")

    return errors, staff, payment_date, amount, account


def _salary_form_choices(pump_id=None):
    return {
        "staff": _all_staff(pump_id),
        "accounts": _active_accounts(),
        "payment_types": SALARY_PAYMENT_TYPES,
    }


@petrol_pumps_bp.route("/staff/salary-payments")
@role_required(*SALARY_ROLES)
def salary_payments_list():
    f_date = request.args.get("date", "").strip()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    f_type = request.args.get("payment_type", "").strip()

    query = PumpSalaryPayment.query.join(PumpStaff)
    if f_date:
        try:
            query = query.filter(
                PumpSalaryPayment.payment_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(PumpSalaryPayment.petrol_pump_id == f_pump)
    if f_type and f_type in SALARY_PAYMENT_TYPES:
        query = query.filter(PumpSalaryPayment.payment_type == f_type)

    payments = query.order_by(
        PumpSalaryPayment.payment_date.desc(), PumpSalaryPayment.id.desc()
    ).all()
    total = sum((p.amount or Decimal("0")) for p in payments if p.is_active)

    export = _export_format()
    if export:
        rows = [[
            p.payment_date.isoformat(),
            p.staff.employee_name if p.staff else "",
            p.petrol_pump.name if p.petrol_pump else "",
            p.payment_type,
            p.for_month or "",
            fmt(p.amount),
            p.paid_from_account.name if p.paid_from_account else "",
            "Active" if p.is_active else "Cancelled",
        ] for p in payments]
        blocks = [{
            "headers": [
                "Date", "Employee", "Pump", "Type", "For Month", "Amount",
                "Paid From", "Status",
            ],
            "rows": rows,
        }]
        return export_response(export, "salary_payments", "Salary / Advance Payments", blocks)

    return render_template(
        "petrol_pumps/staff/salary_list.html",
        payments=payments, total=total, pump=pump,
        filters={"date": f_date, "petrol_pump_id": f_pump, "payment_type": f_type},
        pumps=pumps, payment_types=SALARY_PAYMENT_TYPES,
    )


@petrol_pumps_bp.route("/staff/salary-report")
@role_required(*SALARY_ROLES)
def salary_report():
    """Per-worker salary report for the SELECTED pump over a date range
    (default: last 30 days). For each employee: monthly salary due, salary paid,
    advance taken, and what's still owed = due − salary paid − advance."""
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    today = date.today()
    try:
        d_to = datetime.strptime(request.args.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        d_to = today
    try:
        d_from = datetime.strptime(request.args.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        d_from = d_to - timedelta(days=29)

    rows = []
    totals = {"due": Decimal("0"), "salary_paid": Decimal("0"),
              "advance": Decimal("0"), "paid_total": Decimal("0"),
              "remaining": Decimal("0")}
    if pump is not None:
        staff = (PumpStaff.query
                 .filter_by(petrol_pump_id=pump.id, is_active=True)
                 .order_by(PumpStaff.employee_name).all())
        for st in staff:
            salary_paid = _sum(
                PumpSalaryPayment.amount,
                PumpSalaryPayment.staff_id == st.id,
                PumpSalaryPayment.is_active.is_(True),
                PumpSalaryPayment.payment_type == "Salary",
                PumpSalaryPayment.payment_date >= d_from,
                PumpSalaryPayment.payment_date <= d_to,
            )
            advance = _sum(
                PumpSalaryPayment.amount,
                PumpSalaryPayment.staff_id == st.id,
                PumpSalaryPayment.is_active.is_(True),
                PumpSalaryPayment.payment_type == "Advance",
                PumpSalaryPayment.payment_date >= d_from,
                PumpSalaryPayment.payment_date <= d_to,
            )
            due = st.monthly_salary or Decimal("0")
            paid_total = salary_paid + advance
            remaining = due - paid_total
            rows.append({
                "staff": st, "due": due, "salary_paid": salary_paid,
                "advance": advance, "paid_total": paid_total, "remaining": remaining,
            })
            totals["due"] += due
            totals["salary_paid"] += salary_paid
            totals["advance"] += advance
            totals["paid_total"] += paid_total
            totals["remaining"] += remaining

    export = _export_format()
    if export:
        out = [[
            r["staff"].employee_name, r["staff"].designation,
            fmt(r["due"]), fmt(r["salary_paid"]), fmt(r["advance"]),
            fmt(r["paid_total"]), fmt(r["remaining"]),
        ] for r in rows]
        blocks = [{
            "headers": ["Employee", "Designation", "Monthly Salary",
                        "Salary Paid", "Advance Taken", "Total Paid", "Remaining"],
            "rows": out,
        }]
        title = f"Salary Report — {pump.name if pump else ''} ({d_from} to {d_to})"
        return export_response(export, "salary_report", title, blocks)

    return render_template(
        "petrol_pumps/staff/salary_report.html",
        pump=pump, pumps=pumps, rows=rows, totals=totals,
        d_from=d_from, d_to=d_to,
    )


@petrol_pumps_bp.route("/staff/salary-payments/create", methods=["GET", "POST"])
@role_required(*SALARY_ROLES)
def salary_payments_create():
    if request.method == "POST":
        form = _read_salary_form()
        errors, staff, payment_date, amount, account = _validate_salary_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/staff/salary_form.html",
                form=form, mode="create", **_salary_form_choices(_selected_pump_id(_active_pumps())),
            )
        payment = PumpSalaryPayment(
            staff=staff, petrol_pump_id=staff.petrol_pump_id,
            payment_date=payment_date, payment_type=form["payment_type"],
            amount=amount, for_month=form["for_month"] or None,
            paid_from_account=account, notes=form["notes"] or None,
            created_by_id=current_user.id, is_active=form["is_active"],
        )
        db.session.add(payment)
        db.session.flush()  # populate paid_from_account_id before posting
        _sync_salary(payment)
        db.session.commit()
        msg = (f"{payment.payment_type} of {payment.amount} recorded for "
               f"{staff.employee_name} — deducted from {payment.petrol_pump.name}'s cash.")
        flash(msg, "success")
        return redirect(url_for("petrol_pumps.salary_payments_view", payment_id=payment.id))

    form = {
        "staff_id": None, "payment_date": date.today().isoformat(),
        "payment_type": "Salary", "amount": "", "for_month": "",
        "paid_from_account_id": None, "notes": "", "is_active": True,
    }
    return render_template(
        "petrol_pumps/staff/salary_form.html",
        form=form, mode="create", **_salary_form_choices(_selected_pump_id(_active_pumps())),
    )


@petrol_pumps_bp.route("/staff/salary-payments/<int:payment_id>")
@role_required(*SALARY_ROLES)
def salary_payments_view(payment_id):
    payment = db.get_or_404(PumpSalaryPayment, payment_id)
    return render_template("petrol_pumps/staff/salary_detail.html", payment=payment)


@petrol_pumps_bp.route("/staff/salary-payments/<int:payment_id>/edit", methods=["GET", "POST"])
@role_required(*SALARY_ROLES)
def salary_payments_edit(payment_id):
    payment = db.get_or_404(PumpSalaryPayment, payment_id)
    if request.method == "POST":
        form = _read_salary_form()
        errors, staff, payment_date, amount, account = _validate_salary_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/staff/salary_form.html",
                form=form, mode="edit", payment=payment, **_salary_form_choices(payment.petrol_pump_id),
            )
        payment.staff = staff
        payment.petrol_pump_id = staff.petrol_pump_id
        payment.payment_date = payment_date
        payment.payment_type = form["payment_type"]
        payment.amount = amount
        payment.for_month = form["for_month"] or None
        payment.paid_from_account = account
        payment.notes = form["notes"] or None
        payment.is_active = form["is_active"]
        db.session.flush()
        _sync_salary(payment)
        db.session.commit()
        flash("Salary payment updated.", "success")
        return redirect(url_for("petrol_pumps.salary_payments_view", payment_id=payment.id))

    form = {
        "staff_id": payment.staff_id,
        "payment_date": payment.payment_date.isoformat(),
        "payment_type": payment.payment_type,
        "amount": str(payment.amount),
        "for_month": payment.for_month or "",
        "paid_from_account_id": payment.paid_from_account_id,
        "notes": payment.notes or "",
        "is_active": payment.is_active,
    }
    return render_template(
        "petrol_pumps/staff/salary_form.html",
        form=form, mode="edit", payment=payment, **_salary_form_choices(payment.petrol_pump_id),
    )


@petrol_pumps_bp.route("/staff/salary-payments/<int:payment_id>/toggle-status", methods=["POST"])
@role_required(*SALARY_ROLES)
def salary_payments_toggle_status(payment_id):
    payment = db.get_or_404(PumpSalaryPayment, payment_id)
    payment.is_active = not payment.is_active
    _sync_salary(payment)  # cancel reverses the balance; reactivate re-posts
    db.session.commit()
    state = "reactivated" if payment.is_active else "cancelled"
    flash(f"Salary payment {state}.", "info")
    return redirect(url_for("petrol_pumps.salary_payments_list"))


@petrol_pumps_bp.route("/staff/salary-payments/<int:payment_id>/delete", methods=["POST"])
@role_required(*SALARY_ROLES)
def salary_payments_delete(payment_id):
    payment = db.get_or_404(PumpSalaryPayment, payment_id)
    posting.clear_source(posting.SOURCE_SALARY_PAYMENT, payment.id)
    db.session.delete(payment)
    db.session.commit()
    flash("Salary payment deleted — the pump's cash is restored by that amount.", "success")
    return redirect(url_for("petrol_pumps.salary_payments_list"))


# --------------------------------------------------------------------------- #
# Daily Checklist (BRD §15.1)
# --------------------------------------------------------------------------- #
CHECKLIST_ROLES = PUMP_SETUP_ROLES


def _save_checklist_items(checklist):
    """(Re)build a checklist's item rows from the submitted grid."""
    checklist.items.clear()
    for idx, name in enumerate(CHECKLIST_ITEMS):
        status = request.form.get(f"status_{idx}", "OK").strip()
        if status not in CHECKLIST_STATUSES:
            status = "OK"
        note = request.form.get(f"note_{idx}", "").strip()
        checklist.items.append(DailyChecklistItem(
            item_name=name, status=status, note=note or None,
        ))


@petrol_pumps_bp.route("/checklists")
@role_required(*CHECKLIST_ROLES)
def checklists_list():
    f_date = request.args.get("date", "").strip()
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))

    query = DailyChecklist.query.join(PetrolPump)
    if f_date:
        try:
            query = query.filter(
                DailyChecklist.checklist_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(DailyChecklist.petrol_pump_id == f_pump)

    checklists = query.order_by(
        DailyChecklist.checklist_date.desc(), PetrolPump.name
    ).all()

    export = _export_format()
    if export:
        rows = [[
            ch.checklist_date.isoformat(),
            ch.petrol_pump.name if ch.petrol_pump else "",
            str(len(ch.items)),
            str(ch.issue_count),
            ch.checked_by.full_name if ch.checked_by else "",
        ] for ch in checklists]
        blocks = [{
            "headers": ["Date", "Pump", "Items", "Issues", "Checked By"],
            "rows": rows,
        }]
        return export_response(export, "daily_checklists", "Daily Checklists", blocks)

    return render_template(
        "petrol_pumps/checklists/list.html",
        checklists=checklists,
        filters={"date": f_date, "petrol_pump_id": f_pump},
        pumps=_active_pumps(),
    )


def _checklist_form_rows(existing=None):
    """Build the 12 standard rows, pre-filled from an existing checklist."""
    by_name = {}
    if existing is not None:
        by_name = {i.item_name: i for i in existing.items}
    rows = []
    for idx, name in enumerate(CHECKLIST_ITEMS):
        item = by_name.get(name)
        rows.append({
            "idx": idx, "name": name,
            "status": item.status if item else "OK",
            "note": (item.note or "") if item else "",
        })
    return rows


@petrol_pumps_bp.route("/checklists/create", methods=["GET", "POST"])
@role_required(*CHECKLIST_ROLES)
def checklists_create():
    if request.method == "POST":
        pump_id = _int_or_none(request.form.get("petrol_pump_id"))
        raw_date = request.form.get("checklist_date", "").strip()
        pump = db.session.get(PetrolPump, pump_id) if pump_id else None
        try:
            ch_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            ch_date = None
        if pump is None or ch_date is None:
            flash("Please select a valid pump and date.", "danger")
            return render_template(
                "petrol_pumps/checklists/form.html",
                mode="create", pumps=_active_pumps(), statuses=CHECKLIST_STATUSES,
                rows=_checklist_form_rows(),
                form={"petrol_pump_id": pump_id, "checklist_date": raw_date, "remarks": ""},
            )

        existing = DailyChecklist.query.filter_by(
            petrol_pump_id=pump.id, checklist_date=ch_date
        ).first()
        if existing is not None:
            flash("A checklist already exists for this pump and date — editing it.", "info")
            return redirect(url_for("petrol_pumps.checklists_edit", checklist_id=existing.id))

        checklist = DailyChecklist(
            petrol_pump=pump, checklist_date=ch_date,
            remarks=request.form.get("remarks", "").strip() or None,
            checked_by_id=current_user.id,
        )
        _save_checklist_items(checklist)
        db.session.add(checklist)
        db.session.commit()
        flash(f"Checklist saved ({checklist.issue_count} issue(s) flagged).", "success")
        return redirect(url_for("petrol_pumps.checklists_view", checklist_id=checklist.id))

    return render_template(
        "petrol_pumps/checklists/form.html",
        mode="create", pumps=_active_pumps(), statuses=CHECKLIST_STATUSES,
        rows=_checklist_form_rows(),
        form={"petrol_pump_id": None, "checklist_date": date.today().isoformat(), "remarks": ""},
    )


@petrol_pumps_bp.route("/checklists/<int:checklist_id>")
@role_required(*CHECKLIST_ROLES)
def checklists_view(checklist_id):
    checklist = db.get_or_404(DailyChecklist, checklist_id)
    return render_template("petrol_pumps/checklists/detail.html", checklist=checklist)


@petrol_pumps_bp.route("/checklists/<int:checklist_id>/edit", methods=["GET", "POST"])
@role_required(*CHECKLIST_ROLES)
def checklists_edit(checklist_id):
    checklist = db.get_or_404(DailyChecklist, checklist_id)
    if request.method == "POST":
        checklist.remarks = request.form.get("remarks", "").strip() or None
        _save_checklist_items(checklist)
        db.session.commit()
        flash("Checklist updated.", "success")
        return redirect(url_for("petrol_pumps.checklists_view", checklist_id=checklist.id))

    return render_template(
        "petrol_pumps/checklists/form.html",
        mode="edit", checklist=checklist, pumps=_active_pumps(),
        statuses=CHECKLIST_STATUSES, rows=_checklist_form_rows(checklist),
        form={
            "petrol_pump_id": checklist.petrol_pump_id,
            "checklist_date": checklist.checklist_date.isoformat(),
            "remarks": checklist.remarks or "",
        },
    )


@petrol_pumps_bp.route("/checklists/<int:checklist_id>/delete", methods=["POST"])
@role_required(*CHECKLIST_ROLES)
def checklists_delete(checklist_id):
    checklist = db.get_or_404(DailyChecklist, checklist_id)
    db.session.delete(checklist)
    db.session.commit()
    flash("Checklist deleted.", "success")
    return redirect(url_for("petrol_pumps.checklists_list"))


# --------------------------------------------------------------------------- #
# Maintenance Complaints (BRD §15.2)
# --------------------------------------------------------------------------- #
MAINTENANCE_ROLES = PUMP_SETUP_ROLES


def _read_maintenance_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "complaint_date": request.form.get("complaint_date", "").strip(),
        "complaint_type": request.form.get("complaint_type", "").strip(),
        "description": request.form.get("description", "").strip(),
        "assigned_vendor_id": _int_or_none(request.form.get("assigned_vendor_id")),
        "estimated_cost": request.form.get("estimated_cost", "").strip(),
        "actual_cost": request.form.get("actual_cost", "").strip(),
        "status": request.form.get("status", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_maintenance_form(form):
    errors = []
    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    complaint_date = None
    if not form["complaint_date"]:
        errors.append("Date is required.")
    else:
        try:
            complaint_date = datetime.strptime(form["complaint_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid date.")

    if not form["complaint_type"]:
        errors.append("Complaint type is required.")

    if form["status"] and form["status"] not in MAINTENANCE_STATUSES:
        errors.append("Please select a valid status.")

    vendor = None
    if form["assigned_vendor_id"] is not None:
        vendor = db.session.get(Vendor, form["assigned_vendor_id"])
        if vendor is None:
            errors.append("Please select a valid vendor.")

    est = _parse_nonneg(form["estimated_cost"], "Estimated cost", errors, default=None)
    act = _parse_nonneg(form["actual_cost"], "Actual cost", errors, default=None)

    return errors, pump, complaint_date, vendor, est, act


def _maintenance_form_choices():
    return {
        "pumps": _active_pumps(),
        "vendors": _active_vendors(),
        "statuses": MAINTENANCE_STATUSES,
    }


@petrol_pumps_bp.route("/maintenance")
@role_required(*MAINTENANCE_ROLES)
def maintenance_list():
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))
    f_status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()

    query = MaintenanceComplaint.query.join(PetrolPump)
    if f_pump:
        query = query.filter(MaintenanceComplaint.petrol_pump_id == f_pump)
    if f_status and f_status in MAINTENANCE_STATUSES:
        query = query.filter(MaintenanceComplaint.status == f_status)
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(
            MaintenanceComplaint.complaint_type.ilike(like),
            MaintenanceComplaint.description.ilike(like),
        ))

    complaints = query.order_by(
        MaintenanceComplaint.complaint_date.desc(), MaintenanceComplaint.id.desc()
    ).all()

    export = _export_format()
    if export:
        rows = [[
            c.complaint_date.isoformat(),
            c.petrol_pump.name if c.petrol_pump else "",
            c.complaint_type,
            c.assigned_vendor.name if c.assigned_vendor else "",
            fmt(c.estimated_cost) if c.estimated_cost is not None else "",
            fmt(c.actual_cost) if c.actual_cost is not None else "",
            c.status,
        ] for c in complaints]
        blocks = [{
            "headers": [
                "Date", "Pump", "Type", "Vendor", "Est. Cost", "Actual Cost", "Status",
            ],
            "rows": rows,
        }]
        return export_response(export, "maintenance", "Maintenance Complaints", blocks)

    return render_template(
        "petrol_pumps/maintenance/list.html",
        complaints=complaints,
        filters={"petrol_pump_id": f_pump, "status": f_status, "q": q},
        pumps=_active_pumps(), statuses=MAINTENANCE_STATUSES,
    )


@petrol_pumps_bp.route("/maintenance/create", methods=["GET", "POST"])
@role_required(*MAINTENANCE_ROLES)
def maintenance_create():
    if request.method == "POST":
        form = _read_maintenance_form()
        errors, pump, complaint_date, vendor, est, act = _validate_maintenance_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/maintenance/form.html",
                form=form, mode="create", **_maintenance_form_choices(),
            )
        complaint = MaintenanceComplaint(
            petrol_pump=pump, complaint_date=complaint_date,
            complaint_type=form["complaint_type"],
            description=form["description"] or None,
            assigned_vendor=vendor, estimated_cost=est, actual_cost=act,
            status=form["status"] or "Pending",
            notes=form["notes"] or None, created_by_id=current_user.id,
            is_active=form["is_active"],
        )
        db.session.add(complaint)
        db.session.commit()
        flash("Maintenance complaint logged.", "success")
        return redirect(url_for("petrol_pumps.maintenance_view", complaint_id=complaint.id))

    form = {
        "petrol_pump_id": None, "complaint_date": date.today().isoformat(),
        "complaint_type": "", "description": "", "assigned_vendor_id": None,
        "estimated_cost": "", "actual_cost": "", "status": "Pending",
        "notes": "", "is_active": True,
    }
    return render_template(
        "petrol_pumps/maintenance/form.html",
        form=form, mode="create", **_maintenance_form_choices(),
    )


@petrol_pumps_bp.route("/maintenance/<int:complaint_id>")
@role_required(*MAINTENANCE_ROLES)
def maintenance_view(complaint_id):
    complaint = db.get_or_404(MaintenanceComplaint, complaint_id)
    return render_template(
        "petrol_pumps/maintenance/detail.html",
        complaint=complaint, statuses=MAINTENANCE_STATUSES,
    )


@petrol_pumps_bp.route("/maintenance/<int:complaint_id>/edit", methods=["GET", "POST"])
@role_required(*MAINTENANCE_ROLES)
def maintenance_edit(complaint_id):
    complaint = db.get_or_404(MaintenanceComplaint, complaint_id)
    if request.method == "POST":
        form = _read_maintenance_form()
        errors, pump, complaint_date, vendor, est, act = _validate_maintenance_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/maintenance/form.html",
                form=form, mode="edit", complaint=complaint, **_maintenance_form_choices(),
            )
        complaint.petrol_pump = pump
        complaint.complaint_date = complaint_date
        complaint.complaint_type = form["complaint_type"]
        complaint.description = form["description"] or None
        complaint.assigned_vendor = vendor
        complaint.estimated_cost = est
        complaint.actual_cost = act
        complaint.status = form["status"] or "Pending"
        complaint.notes = form["notes"] or None
        complaint.is_active = form["is_active"]
        db.session.commit()
        flash("Maintenance complaint updated.", "success")
        return redirect(url_for("petrol_pumps.maintenance_view", complaint_id=complaint.id))

    form = {
        "petrol_pump_id": complaint.petrol_pump_id,
        "complaint_date": complaint.complaint_date.isoformat(),
        "complaint_type": complaint.complaint_type,
        "description": complaint.description or "",
        "assigned_vendor_id": complaint.assigned_vendor_id,
        "estimated_cost": "" if complaint.estimated_cost is None else str(complaint.estimated_cost),
        "actual_cost": "" if complaint.actual_cost is None else str(complaint.actual_cost),
        "status": complaint.status,
        "notes": complaint.notes or "",
        "is_active": complaint.is_active,
    }
    return render_template(
        "petrol_pumps/maintenance/form.html",
        form=form, mode="edit", complaint=complaint, **_maintenance_form_choices(),
    )


@petrol_pumps_bp.route("/maintenance/<int:complaint_id>/set-status", methods=["POST"])
@role_required(*MAINTENANCE_ROLES)
def maintenance_set_status(complaint_id):
    """Quick status transition from the detail page."""
    complaint = db.get_or_404(MaintenanceComplaint, complaint_id)
    new_status = request.form.get("status", "").strip()
    if new_status in MAINTENANCE_STATUSES:
        complaint.status = new_status
        db.session.commit()
        flash(f"Complaint marked '{new_status}'.", "success")
    else:
        flash("Invalid status.", "danger")
    return redirect(url_for("petrol_pumps.maintenance_view", complaint_id=complaint.id))


@petrol_pumps_bp.route("/maintenance/<int:complaint_id>/delete", methods=["POST"])
@role_required(*MAINTENANCE_ROLES)
def maintenance_delete(complaint_id):
    complaint = db.get_or_404(MaintenanceComplaint, complaint_id)
    db.session.delete(complaint)
    db.session.commit()
    flash("Maintenance complaint deleted.", "success")
    return redirect(url_for("petrol_pumps.maintenance_list"))


# --------------------------------------------------------------------------- #
# Stock Adjustments (gain/loss) — entry + approval workflow (BRD §12.3, §16)
# --------------------------------------------------------------------------- #
# Who may record a physical count (dip) entry.
ADJUSTMENT_ENTRY_ROLES = (
    "Admin / Owner",
    "Head Office Manager",
    "Petrol Pump Manager",
)
# Who may approve/reject (sensitive: posts to stock and affects profit).
ADJUSTMENT_APPROVAL_ROLES = (
    "Admin / Owner",
    "Head Office Manager",
)


def _can_approve_adjustments():
    return any(
        current_user.has_role(name) for name in ADJUSTMENT_APPROVAL_ROLES
    )


def _read_adjustment_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "tank_id": _int_or_none(request.form.get("tank_id")),
        "adjustment_date": request.form.get("adjustment_date", "").strip(),
        "physical_stock_liters": request.form.get("physical_stock_liters", "").strip(),
        "notes": request.form.get("notes", "").strip(),
    }


def _validate_adjustment_form(form):
    """Validate an adjustment entry. Returns (errors, pump, tank, date, physical)."""
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    tank = None
    if form["tank_id"] is None:
        errors.append("Tank is required.")
    else:
        tank = db.session.get(PumpTank, form["tank_id"])
        if tank is None:
            errors.append("Please select a valid tank.")
        elif pump is not None and tank.petrol_pump_id != pump.id:
            errors.append("The selected tank does not belong to that petrol pump.")

    adjustment_date = None
    if not form["adjustment_date"]:
        errors.append("Adjustment date is required.")
    else:
        try:
            adjustment_date = datetime.strptime(
                form["adjustment_date"], "%Y-%m-%d"
            ).date()
        except ValueError:
            errors.append("Please enter a valid adjustment date.")

    physical = _parse_nonneg(
        form["physical_stock_liters"], "Physical stock", errors
    )
    if physical is None and form["physical_stock_liters"] == "":
        errors.append("Physical stock (dip reading) is required.")

    return errors, pump, tank, adjustment_date, physical


def _adjustment_form_choices():
    return {"pumps": _active_pumps(), "tanks": _active_tanks()}


@petrol_pumps_bp.route("/stock-adjustments")
@role_required(*READING_ROLES)
def stock_adjustments_list():
    """List stock gain/loss adjustments (filters + Excel/PDF export)."""
    f_date = request.args.get("date", "").strip()
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))
    f_status = request.args.get("status", "").strip()

    query = StockAdjustment.query
    if f_date:
        try:
            query = query.filter(
                StockAdjustment.adjustment_date
                == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(StockAdjustment.petrol_pump_id == f_pump)
    if f_status and f_status in ADJUSTMENT_STATUSES:
        query = query.filter(StockAdjustment.status == f_status)

    adjustments = query.order_by(
        StockAdjustment.adjustment_date.desc(), StockAdjustment.id.desc()
    ).all()

    export = _export_format()
    if export:
        rows = [[
            a.adjustment_date.isoformat(),
            a.petrol_pump.name if a.petrol_pump else "",
            a.tank.tank_name if a.tank else "",
            a.product.name if a.product else "",
            fmt(a.system_stock_liters),
            fmt(a.physical_stock_liters),
            fmt(a.difference_liters),
            "Gain" if a.is_gain else ("Loss" if a.difference_liters < 0 else "—"),
            a.status,
            a.approved_by.full_name if a.approved_by else "",
        ] for a in adjustments]
        blocks = [{
            "headers": [
                "Date", "Pump", "Tank", "Product", "System (L)",
                "Physical (L)", "Difference (L)", "Gain/Loss", "Status",
                "Approved By",
            ],
            "rows": rows,
        }]
        return export_response(
            export, "stock_adjustments", "Stock Gain / Loss Adjustments", blocks
        )

    return render_template(
        "petrol_pumps/stock_adjustments/list.html",
        adjustments=adjustments,
        filters={"date": f_date, "petrol_pump_id": f_pump, "status": f_status},
        pumps=_active_pumps(), statuses=ADJUSTMENT_STATUSES,
        can_approve=_can_approve_adjustments(),
    )


@petrol_pumps_bp.route("/stock-adjustments/create", methods=["GET", "POST"])
@role_required(*ADJUSTMENT_ENTRY_ROLES)
def stock_adjustments_create():
    if request.method == "POST":
        form = _read_adjustment_form()
        errors, pump, tank, adjustment_date, physical = _validate_adjustment_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/stock_adjustments/form.html",
                form=form, mode="create", **_adjustment_form_choices(),
            )

        # Snapshot the system (book) stock at entry time.
        system_stock = tank.current_stock_liters or Decimal("0")
        difference = physical - system_stock

        # Approvals disabled (2026-06-21) — auto-approve and apply the stock
        # change immediately, no pending step.
        adjustment = StockAdjustment(
            petrol_pump=pump, tank=tank, product_id=tank.product_id,
            adjustment_date=adjustment_date,
            system_stock_liters=system_stock,
            physical_stock_liters=physical,
            difference_liters=difference,
            status=ADJUSTMENT_STATUS_APPROVED,
            approved_by_id=current_user.id, approved_at=_utcnow(),
            notes=form["notes"] or None,
            created_by_id=current_user.id,
        )
        db.session.add(adjustment)
        db.session.flush()
        stock.sync_adjustment_stock(adjustment)
        db.session.commit()

        kind = "gain" if difference > 0 else ("loss" if difference < 0 else "no difference")
        flash(
            f"Adjustment saved and applied: {difference} L ({kind}). "
            f"Tank stock updated.",
            "success",
        )
        return redirect(url_for(
            "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
        ))

    # Allow the Stock Console "Record Gain/Loss" button to preselect pump + tank.
    form = {
        "petrol_pump_id": _selected_pump_id(_active_pumps()),
        "tank_id": _int_or_none(request.args.get("tank")),
        "adjustment_date": date.today().isoformat(),
        "physical_stock_liters": "", "notes": "",
    }
    return render_template(
        "petrol_pumps/stock_adjustments/form.html",
        form=form, mode="create", **_adjustment_form_choices(),
    )


@petrol_pumps_bp.route("/stock-adjustments/<int:adjustment_id>")
@role_required(*READING_ROLES)
def stock_adjustments_view(adjustment_id):
    adjustment = db.get_or_404(StockAdjustment, adjustment_id)
    return render_template(
        "petrol_pumps/stock_adjustments/detail.html",
        adjustment=adjustment, can_approve=_can_approve_adjustments(),
    )


@petrol_pumps_bp.route("/stock-adjustments/<int:adjustment_id>/edit", methods=["GET", "POST"])
@role_required(*ADJUSTMENT_ENTRY_ROLES)
def stock_adjustments_edit(adjustment_id):
    adjustment = db.get_or_404(StockAdjustment, adjustment_id)
    if adjustment.status != ADJUSTMENT_STATUS_PENDING:
        flash("Only Pending adjustments can be edited. Reject/reopen it first.", "warning")
        return redirect(url_for(
            "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
        ))

    if request.method == "POST":
        form = _read_adjustment_form()
        errors, pump, tank, adjustment_date, physical = _validate_adjustment_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/stock_adjustments/form.html",
                form=form, mode="edit", adjustment=adjustment,
                **_adjustment_form_choices(),
            )

        # Re-snapshot book stock: the entry is still pending, so it should
        # always compare against the tank's CURRENT system figure.
        system_stock = tank.current_stock_liters or Decimal("0")
        adjustment.petrol_pump = pump
        adjustment.tank = tank
        adjustment.product_id = tank.product_id
        adjustment.adjustment_date = adjustment_date
        adjustment.system_stock_liters = system_stock
        adjustment.physical_stock_liters = physical
        adjustment.difference_liters = physical - system_stock
        adjustment.notes = form["notes"] or None
        db.session.commit()
        flash("Adjustment updated (still pending approval).", "success")
        return redirect(url_for(
            "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
        ))

    form = {
        "petrol_pump_id": adjustment.petrol_pump_id,
        "tank_id": adjustment.tank_id,
        "adjustment_date": adjustment.adjustment_date.isoformat(),
        "physical_stock_liters": str(adjustment.physical_stock_liters),
        "notes": adjustment.notes or "",
    }
    return render_template(
        "petrol_pumps/stock_adjustments/form.html",
        form=form, mode="edit", adjustment=adjustment,
        **_adjustment_form_choices(),
    )


@petrol_pumps_bp.route("/stock-adjustments/<int:adjustment_id>/approve", methods=["POST"])
@role_required(*ADJUSTMENT_APPROVAL_ROLES)
def stock_adjustments_approve(adjustment_id):
    adjustment = db.get_or_404(StockAdjustment, adjustment_id)
    if adjustment.status == ADJUSTMENT_STATUS_APPROVED:
        flash("This adjustment is already approved.", "info")
        return redirect(url_for(
            "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
        ))

    adjustment.status = ADJUSTMENT_STATUS_APPROVED
    adjustment.approved_by_id = current_user.id
    adjustment.approved_at = _utcnow()
    adjustment.rejection_reason = None
    stock.sync_adjustment_stock(adjustment)
    db.session.commit()

    if adjustment.stock_posted:
        verb = "increased" if adjustment.is_gain else "decreased"
        flash(
            f"Adjustment approved. Tank '{adjustment.tank.tank_name}' stock "
            f"{verb} by {abs(adjustment.difference_liters)} L.",
            "success",
        )
    else:
        flash("Adjustment approved (zero difference — no stock change).", "success")
    return redirect(url_for(
        "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
    ))


@petrol_pumps_bp.route("/stock-adjustments/<int:adjustment_id>/reject", methods=["POST"])
@role_required(*ADJUSTMENT_APPROVAL_ROLES)
def stock_adjustments_reject(adjustment_id):
    adjustment = db.get_or_404(StockAdjustment, adjustment_id)
    reason = request.form.get("rejection_reason", "").strip()

    # Rejecting an approved gain must not drive the tank negative.
    ok, tank = stock.can_unpost_adjustment(adjustment)
    if not ok:
        flash(
            f"Cannot reject: reversing this gain would make tank "
            f"'{tank.tank_name}' stock negative.",
            "danger",
        )
        return redirect(url_for(
            "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
        ))

    adjustment.status = ADJUSTMENT_STATUS_REJECTED
    adjustment.rejection_reason = reason or None
    stock.sync_adjustment_stock(adjustment)  # reverses any posted stock
    db.session.commit()
    flash("Adjustment rejected. Any posted stock has been reversed.", "info")
    return redirect(url_for(
        "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
    ))


@petrol_pumps_bp.route("/stock-adjustments/<int:adjustment_id>/reopen", methods=["POST"])
@role_required(*ADJUSTMENT_APPROVAL_ROLES)
def stock_adjustments_reopen(adjustment_id):
    adjustment = db.get_or_404(StockAdjustment, adjustment_id)
    if adjustment.status != ADJUSTMENT_STATUS_REJECTED:
        flash("Only rejected adjustments can be reopened.", "warning")
    else:
        adjustment.status = ADJUSTMENT_STATUS_PENDING
        adjustment.rejection_reason = None
        adjustment.approved_by_id = None
        adjustment.approved_at = None
        db.session.commit()
        flash("Adjustment reopened — it is pending approval again.", "info")
    return redirect(url_for(
        "petrol_pumps.stock_adjustments_view", adjustment_id=adjustment.id
    ))


# --------------------------------------------------------------------------- #
# Pump Fuel Purchases (+ tank stock increase)
# --------------------------------------------------------------------------- #
def _active_vendors():
    return Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()


def _active_tanks():
    return PumpTank.query.filter_by(is_active=True).order_by(PumpTank.tank_name).all()


def _purchase_should_post(purchase):
    """A purchase posts stock only when active, tank-received and Received."""
    return (
        purchase.is_active
        and purchase.tank_received
        and purchase.delivery_status == "Received"
    )


def _tank_deltas(purchase):
    """Total quantity per tank for items that target a tank."""
    deltas = {}
    for item in purchase.items:
        if item.tank_id is not None:
            deltas[item.tank_id] = deltas.get(item.tank_id, Decimal("0")) + (
                item.quantity_liters or Decimal("0")
            )
    return deltas


def _can_unpost(purchase):
    """Check that removing this purchase's stock won't make any tank negative."""
    for tank_id, qty in _tank_deltas(purchase).items():
        tank = db.session.get(PumpTank, tank_id)
        if tank is not None and (tank.current_stock_liters or Decimal("0")) - qty < 0:
            return False, tank
    return True, None


def _apply_stock(purchase, sign):
    """Add (sign=+1) or remove (sign=-1) this purchase's quantities from tanks."""
    for tank_id, qty in _tank_deltas(purchase).items():
        tank = db.session.get(PumpTank, tank_id)
        if tank is not None:
            tank.current_stock_liters = (
                tank.current_stock_liters or Decimal("0")
            ) + (sign * qty)


def _sync_stock(purchase):
    """Make the posted state match the desired state (idempotent).

    Keeps the StockMovement audit ledger in step with the tank balance.
    """
    desired = _purchase_should_post(purchase)
    if desired and not purchase.stock_posted:
        _apply_stock(purchase, 1)
        stock.record_purchase_movements(purchase)
        purchase.stock_posted = True
    elif not desired and purchase.stock_posted:
        _apply_stock(purchase, -1)
        stock.clear_movements(stock.SOURCE_PUMP_PURCHASE, purchase.id)
        purchase.stock_posted = False
    # Post the accounting entry (Dr Stock / Cr PSO Payable) — the payable exists
    # whenever the purchase is active, independent of whether fuel is received.
    posting.sync_pump_purchase(purchase)


def _invoice_taken(vendor_id, invoice_number, exclude_id=None):
    if not invoice_number:
        return False
    query = PumpPurchase.query.filter(
        PumpPurchase.vendor_id == vendor_id,
        db.func.lower(PumpPurchase.invoice_number) == invoice_number.lower(),
    )
    if exclude_id is not None:
        query = query.filter(PumpPurchase.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _read_purchase_header():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "vendor_id": _int_or_none(request.form.get("vendor_id")),
        "purchase_date": request.form.get("purchase_date", "").strip(),
        "invoice_number": request.form.get("invoice_number", "").strip(),
        "vehicle_number": request.form.get("vehicle_number", "").strip(),
        "driver_name": request.form.get("driver_name", "").strip(),
        "delivery_status": request.form.get("delivery_status", "").strip(),
        "tank_received": bool(request.form.get("tank_received")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _read_item_rows():
    """Rebuild submitted item rows (for re-rendering the form)."""
    products = request.form.getlist("item_product_id")
    tanks = request.form.getlist("item_tank_id")
    qtys = request.form.getlist("item_quantity")
    rates = request.form.getlist("item_rate")
    rows = []
    for i in range(max(len(products), 4)):
        rows.append({
            "product_id": products[i].strip() if i < len(products) else "",
            "tank_id": tanks[i].strip() if i < len(tanks) else "",
            "quantity": qtys[i].strip() if i < len(qtys) else "",
            "rate": rates[i].strip() if i < len(rates) else "",
        })
    return rows


def _validate_purchase(header, errors):
    """Validate header + items. Returns (pump, vendor, purchase_date, parsed_items)."""
    pump = None
    if header["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, header["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    vendor = None
    if header["vendor_id"] is None:
        errors.append("Vendor is required.")
    else:
        vendor = db.session.get(Vendor, header["vendor_id"])
        if vendor is None:
            errors.append("Please select a valid vendor.")

    purchase_date = None
    if not header["purchase_date"]:
        errors.append("Purchase date is required.")
    else:
        try:
            purchase_date = datetime.strptime(header["purchase_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid purchase date.")

    if not header["delivery_status"]:
        errors.append("Delivery status is required.")
    elif header["delivery_status"] not in PURCHASE_DELIVERY_STATUSES:
        errors.append("Please select a valid delivery status.")

    if header["invoice_number"] and vendor is not None and _invoice_taken(
        vendor.id, header["invoice_number"], exclude_id=header.get("_exclude_id")
    ):
        errors.append("This invoice number already exists for the selected vendor.")

    # --- Items ---
    products = request.form.getlist("item_product_id")
    tanks = request.form.getlist("item_tank_id")
    qtys = request.form.getlist("item_quantity")
    rates = request.form.getlist("item_rate")
    require_tank = header["tank_received"]

    parsed_items = []
    for i, raw_prod in enumerate(products):
        raw_prod = raw_prod.strip()
        if not raw_prod:
            continue
        line = i + 1

        product = db.session.get(Product, int(raw_prod)) if raw_prod.isdigit() else None
        if product is None:
            errors.append(f"Item {line}: invalid product.")
            continue
        if not _is_fuel_product(product):
            errors.append(f"Item {line}: product must be a fuel product.")

        raw_qty = (qtys[i] if i < len(qtys) else "").strip()
        raw_rate = (rates[i] if i < len(rates) else "").strip()
        try:
            quantity = Decimal(raw_qty) if raw_qty else Decimal("0")
        except InvalidOperation:
            errors.append(f"Item {line}: quantity must be a number.")
            quantity = Decimal("0")
        try:
            rate = Decimal(raw_rate) if raw_rate else Decimal("0")
        except InvalidOperation:
            errors.append(f"Item {line}: rate must be a number.")
            rate = Decimal("0")

        if quantity <= 0:
            errors.append(f"Item {line}: quantity must be greater than zero.")
        if rate < 0:
            errors.append(f"Item {line}: rate cannot be negative.")

        tank = None
        raw_tank = (tanks[i] if i < len(tanks) else "").strip()
        if require_tank:
            if not raw_tank:
                errors.append(f"Item {line}: tank is required when tank received is Yes.")
            else:
                tank = db.session.get(PumpTank, int(raw_tank)) if raw_tank.isdigit() else None
                if tank is None:
                    errors.append(f"Item {line}: invalid tank.")
                else:
                    if pump is not None and tank.petrol_pump_id != pump.id:
                        errors.append(f"Item {line}: tank does not belong to the selected pump.")
                    if product is not None and tank.product_id != product.id:
                        errors.append(f"Item {line}: tank product must match the item product.")
        # When tank not received, ignore any tank selection (no stock effect).

        parsed_items.append({
            "product": product,
            "tank": tank if require_tank else None,
            "quantity": quantity,
            "rate": rate,
            "total": (quantity * rate),
        })

    if not parsed_items:
        errors.append("At least one purchase item is required.")

    return pump, vendor, purchase_date, parsed_items


@petrol_pumps_bp.route("/purchases")
@role_required(*PURCHASE_ROLES)
def purchases_list():
    f_date = request.args.get("date", "").strip()
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))
    f_vendor = _int_or_none(request.args.get("vendor_id"))
    f_invoice = request.args.get("invoice_number", "").strip()
    f_status = request.args.get("delivery_status", "").strip()

    query = PumpPurchase.query
    if f_date:
        try:
            query = query.filter(
                PumpPurchase.purchase_date == datetime.strptime(f_date, "%Y-%m-%d").date()
            )
        except ValueError:
            pass
    if f_pump:
        query = query.filter(PumpPurchase.petrol_pump_id == f_pump)
    if f_vendor:
        query = query.filter(PumpPurchase.vendor_id == f_vendor)
    if f_invoice:
        query = query.filter(PumpPurchase.invoice_number.ilike(f"%{f_invoice}%"))
    if f_status:
        query = query.filter(PumpPurchase.delivery_status == f_status)

    purchases = query.order_by(
        PumpPurchase.purchase_date.desc(), PumpPurchase.id.desc()
    ).all()

    export = _export_format()
    if export:
        rows = [[
            p.purchase_date.isoformat(),
            p.petrol_pump.name if p.petrol_pump else "",
            p.vendor.name if p.vendor else "",
            p.invoice_number or "",
            p.delivery_status,
            "Yes" if p.tank_received else "No",
            "Yes" if p.stock_posted else "No",
            fmt(p.total_amount),
        ] for p in purchases]
        blocks = [{
            "headers": [
                "Date", "Pump", "Vendor", "Invoice", "Status",
                "Tank Recvd", "Stock Posted", "Total",
            ],
            "rows": rows,
        }]
        return export_response(export, "pump_purchases", "Pump Fuel Purchases", blocks)

    return render_template(
        "petrol_pumps/purchases/list.html",
        purchases=purchases,
        filters={
            "date": f_date, "petrol_pump_id": f_pump, "vendor_id": f_vendor,
            "invoice_number": f_invoice, "delivery_status": f_status,
        },
        pumps=_active_pumps(), vendors=_active_vendors(),
        statuses=PURCHASE_DELIVERY_STATUSES,
    )


def _purchase_form_choices():
    return {
        "pumps": _active_pumps(),
        "vendors": _active_vendors(),
        "products": _fuel_products(),
        "tanks": _active_tanks(),
        "statuses": PURCHASE_DELIVERY_STATUSES,
    }


# --------------------------------------------------------------------------- #
# Purchase Console — the simple per-tank entry experience (mirrors the Reading
# Console). Pick a pump → tap a fuel tank (or the Lubricants card) → enter
# quantity, rate, the delivery vehicle (chosen from the fleet) and driver. The
# vendor defaults to PSO; one PumpPurchase + item is created and tank stock
# posts exactly like the classic multi-line form.
# --------------------------------------------------------------------------- #
def _pso_vendor():
    """The PSO vendor fuel/lubricants are bought from (first active PSO)."""
    return (
        Vendor.query.filter_by(vendor_type=VendorType.PSO, is_active=True)
        .order_by(Vendor.id).first()
    )


def _active_vehicles_pp():
    return Vehicle.query.filter_by(is_active=True).order_by(Vehicle.vehicle_number).all()


def _tank_last_rate(tank):
    """Best-guess purchase rate for a tank: latest purchase-item rate for its
    product, else the product's default purchase rate, else blank."""
    item = (
        PumpPurchaseItem.query.filter_by(tank_id=tank.id)
        .order_by(PumpPurchaseItem.id.desc()).first()
    )
    if item is not None and item.rate:
        return item.rate
    if tank.product is not None and tank.product.default_purchase_rate:
        return tank.product.default_purchase_rate
    return ""


@petrol_pumps_bp.route("/purchases/console")
@role_required(*PURCHASE_ROLES)
def purchases_console():
    pumps = _active_pumps()
    pump = _selected_pump(pumps)

    today = date.today()
    tanks = []
    if pump is not None:
        for t in (
            PumpTank.query.filter_by(petrol_pump_id=pump.id, is_active=True)
            .order_by(PumpTank.id).all()
        ):
            received_today = db.session.query(
                db.func.coalesce(db.func.sum(PumpPurchaseItem.quantity_liters), 0)
            ).join(PumpPurchase, PumpPurchaseItem.pump_purchase_id == PumpPurchase.id).filter(
                PumpPurchaseItem.tank_id == t.id,
                PumpPurchase.purchase_date == today,
                PumpPurchase.is_active.is_(True),
            ).scalar()
            tanks.append({
                "t": t,
                "rate": _tank_last_rate(t),
                "received_today": received_today or 0,
            })

    return render_template(
        "petrol_pumps/purchases/console.html",
        pumps=pumps, pump=pump, tanks=tanks, today=today,
        lubricants=_lubricant_products(), vehicles=_active_vehicles_pp(),
        vendors=_active_vendors(), default_vendor=_pso_vendor(),
    )


@petrol_pumps_bp.route("/purchases/console/save", methods=["POST"])
@role_required(*PURCHASE_ROLES)
def purchases_console_save():
    """Save one fuel-tank OR one lubricant purchase from the console modal."""
    pump = db.session.get(PetrolPump, _int_or_none(request.form.get("petrol_pump_id")))
    mode = (request.form.get("mode") or "").strip()  # "tank" | "lubricant"
    vendor_id = _int_or_none(request.form.get("vendor_id"))
    vendor = db.session.get(Vendor, vendor_id) if vendor_id else None
    raw_date = (request.form.get("purchase_date") or "").strip()

    errors = []
    if pump is None:
        errors.append("Please select a valid petrol pump.")
    if vendor is None:
        errors.append("Select a vendor (e.g. PSO) — a purchase cannot be saved without one.")
    purchase_date = None
    try:
        purchase_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
    except ValueError:
        errors.append("Please enter a valid purchase date.")

    quantity = _parse_nonneg(request.form.get("quantity"), "Quantity", errors)
    rate = _parse_nonneg(request.form.get("rate"), "Purchase rate", errors)
    if quantity is None or quantity <= 0:
        errors.append("Quantity (liters) is required.")
    if rate is None or rate <= 0:
        errors.append("Purchase rate is required.")

    tank = None
    product = None
    if mode == "tank":
        tank = db.session.get(PumpTank, _int_or_none(request.form.get("tank_id")))
        if tank is None or (pump is not None and tank.petrol_pump_id != pump.id):
            errors.append("Please select a valid tank for this pump.")
        else:
            product = tank.product
    elif mode == "lubricant":
        product = db.session.get(Product, _int_or_none(request.form.get("product_id")))
        if product is None or not _is_lubricant_product(product):
            errors.append("Please select a valid lubricant product.")
    else:
        errors.append("Unknown purchase type.")

    # Vehicle from the fleet (optional); driver name prefilled but editable.
    vehicle_id = _int_or_none(request.form.get("vehicle_id"))
    vehicle = db.session.get(Vehicle, vehicle_id) if vehicle_id else None
    vehicle_number = vehicle.vehicle_number if vehicle is not None else (
        request.form.get("vehicle_number") or ""
    ).strip()
    driver_name = (request.form.get("driver_name") or "").strip()

    back = redirect(url_for(
        "petrol_pumps.purchases_console", pump=pump.id if pump else None,
    ))
    if errors:
        for msg in errors:
            flash(msg, "danger")
        return back

    total = quantity * rate
    purchase = PumpPurchase(
        petrol_pump=pump, vendor=vendor, purchase_date=purchase_date,
        vehicle_number=vehicle_number or None, driver_name=driver_name or None,
        delivery_status="Received", tank_received=(mode == "tank"),
        created_by_id=current_user.id, is_active=True, total_amount=total,
    )
    purchase.items.append(PumpPurchaseItem(
        product=product, tank=tank, quantity_liters=quantity, rate=rate,
        total_amount=total,
    ))
    db.session.add(purchase)
    db.session.flush()
    _sync_stock(purchase)
    db.session.commit()

    where = f"{tank.tank_name}" if tank is not None else product.name
    msg = f"Purchase saved · {where} — {quantity} @ {rate} = PKR {total}."
    if purchase.stock_posted:
        msg += " Tank stock increased."
    flash(msg, "success")
    return back


@petrol_pumps_bp.route("/purchases/create", methods=["GET", "POST"])
@role_required(*PURCHASE_ROLES)
def purchases_create():
    if request.method == "POST":
        header = _read_purchase_header()
        errors = []
        pump, vendor, purchase_date, items = _validate_purchase(header, errors)

        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/purchases/form.html",
                form=header, item_rows=_read_item_rows(), mode="create",
                **_purchase_form_choices(),
            )

        if vendor is not None and vendor.vendor_type != VendorType.PSO:
            flash("Note: the selected vendor is not PSO.", "warning")

        purchase = PumpPurchase(
            petrol_pump=pump, vendor=vendor, purchase_date=purchase_date,
            invoice_number=header["invoice_number"] or None,
            vehicle_number=header["vehicle_number"] or None,
            driver_name=header["driver_name"] or None,
            delivery_status=header["delivery_status"],
            tank_received=header["tank_received"],
            notes=header["notes"] or None,
            created_by_id=current_user.id,
            is_active=header["is_active"],
            total_amount=sum((it["total"] for it in items), Decimal("0")),
        )
        for it in items:
            purchase.items.append(PumpPurchaseItem(
                product=it["product"], tank=it["tank"],
                quantity_liters=it["quantity"], rate=it["rate"],
                total_amount=it["total"],
            ))
        db.session.add(purchase)
        db.session.flush()  # populate item.tank_id before posting stock
        _sync_stock(purchase)
        db.session.commit()

        msg = f"Purchase saved. Total {purchase.total_amount}."
        if purchase.stock_posted:
            msg += " Tank stock increased."
        flash(msg, "success")
        return redirect(url_for("petrol_pumps.purchases_view", purchase_id=purchase.id))

    form = {
        "petrol_pump_id": None, "vendor_id": None,
        "purchase_date": date.today().isoformat(), "invoice_number": "",
        "vehicle_number": "", "driver_name": "", "delivery_status": "Received",
        "tank_received": True, "notes": "", "is_active": True,
    }
    item_rows = [{"product_id": "", "tank_id": "", "quantity": "", "rate": ""} for _ in range(4)]
    return render_template(
        "petrol_pumps/purchases/form.html",
        form=form, item_rows=item_rows, mode="create", **_purchase_form_choices(),
    )


@petrol_pumps_bp.route("/purchases/<int:purchase_id>")
@role_required(*PURCHASE_ROLES)
def purchases_view(purchase_id):
    purchase = db.get_or_404(PumpPurchase, purchase_id)
    return render_template("petrol_pumps/purchases/detail.html", purchase=purchase)


@petrol_pumps_bp.route("/purchases/<int:purchase_id>/edit", methods=["GET", "POST"])
@role_required(*PURCHASE_ROLES)
def purchases_edit(purchase_id):
    purchase = db.get_or_404(PumpPurchase, purchase_id)

    if request.method == "POST":
        header = _read_purchase_header()
        header["_exclude_id"] = purchase.id
        errors = []
        pump, vendor, purchase_date, items = _validate_purchase(header, errors)

        # If this purchase already posted stock, make sure reversing the OLD
        # quantities will not drive any tank negative before we mutate anything.
        if purchase.stock_posted:
            ok, tank = _can_unpost(purchase)
            if not ok:
                errors.append(
                    f"Cannot edit: reversing stock would make tank "
                    f"'{tank.tank_name}' negative."
                )

        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/purchases/form.html",
                form=header, item_rows=_read_item_rows(), mode="edit",
                purchase=purchase, **_purchase_form_choices(),
            )

        if vendor is not None and vendor.vendor_type != VendorType.PSO:
            flash("Note: the selected vendor is not PSO.", "warning")

        # Reverse old stock posting first (using existing items), then rebuild.
        if purchase.stock_posted:
            _apply_stock(purchase, -1)
            stock.clear_movements(stock.SOURCE_PUMP_PURCHASE, purchase.id)
            purchase.stock_posted = False

        purchase.items.clear()
        for it in items:
            purchase.items.append(PumpPurchaseItem(
                product=it["product"], tank=it["tank"],
                quantity_liters=it["quantity"], rate=it["rate"],
                total_amount=it["total"],
            ))

        purchase.petrol_pump = pump
        purchase.vendor = vendor
        purchase.purchase_date = purchase_date
        purchase.invoice_number = header["invoice_number"] or None
        purchase.vehicle_number = header["vehicle_number"] or None
        purchase.driver_name = header["driver_name"] or None
        purchase.delivery_status = header["delivery_status"]
        purchase.tank_received = header["tank_received"]
        purchase.notes = header["notes"] or None
        purchase.is_active = header["is_active"]
        purchase.total_amount = sum((it["total"] for it in items), Decimal("0"))

        db.session.flush()  # populate new item.tank_id before posting stock
        _sync_stock(purchase)
        db.session.commit()
        flash("Purchase updated.", "success")
        return redirect(url_for("petrol_pumps.purchases_view", purchase_id=purchase.id))

    form = {
        "petrol_pump_id": purchase.petrol_pump_id,
        "vendor_id": purchase.vendor_id,
        "purchase_date": purchase.purchase_date.isoformat(),
        "invoice_number": purchase.invoice_number or "",
        "vehicle_number": purchase.vehicle_number or "",
        "driver_name": purchase.driver_name or "",
        "delivery_status": purchase.delivery_status,
        "tank_received": purchase.tank_received,
        "notes": purchase.notes or "",
        "is_active": purchase.is_active,
    }
    item_rows = []
    for item in purchase.items:
        item_rows.append({
            "product_id": str(item.product_id),
            "tank_id": str(item.tank_id) if item.tank_id else "",
            "quantity": str(item.quantity_liters),
            "rate": str(item.rate),
        })
    while len(item_rows) < 4:
        item_rows.append({"product_id": "", "tank_id": "", "quantity": "", "rate": ""})

    return render_template(
        "petrol_pumps/purchases/form.html",
        form=form, item_rows=item_rows, mode="edit", purchase=purchase,
        **_purchase_form_choices(),
    )


@petrol_pumps_bp.route("/purchases/<int:purchase_id>/toggle-status", methods=["POST"])
@role_required(*PURCHASE_ROLES)
def purchases_toggle_status(purchase_id):
    purchase = db.get_or_404(PumpPurchase, purchase_id)

    if purchase.is_active:
        # Deactivating: if it posted stock, make sure removing it is safe.
        if purchase.stock_posted:
            ok, tank = _can_unpost(purchase)
            if not ok:
                flash(
                    f"Cannot deactivate: tank '{tank.tank_name}' would go negative.",
                    "danger",
                )
                return redirect(url_for("petrol_pumps.purchases_list"))
        purchase.is_active = False
    else:
        purchase.is_active = True

    _sync_stock(purchase)
    db.session.commit()
    state = "activated" if purchase.is_active else "deactivated"
    flash(f"Purchase {state}.", "info")
    return redirect(url_for("petrol_pumps.purchases_list"))


# --------------------------------------------------------------------------- #
# Pump Expenses
# --------------------------------------------------------------------------- #
LUBRICANT_CATEGORY_NAME = "Lubricants"


def _active_accounts():
    return (
        CashBankAccount.query.filter_by(is_active=True)
        .order_by(CashBankAccount.name)
        .all()
    )


def _active_customers():
    return Customer.query.filter_by(is_active=True).order_by(Customer.name).all()


def _pump_expense_categories():
    """Active expense categories that are global or under Petrol Pump Retail."""
    return [
        c for c in ExpenseCategory.query.filter_by(is_active=True)
        .order_by(ExpenseCategory.name).all()
        if c.business_unit_id is None
        or (c.business_unit and c.business_unit.type == BusinessUnitType.PETROL_PUMP_RETAIL)
    ]


def _is_valid_expense_category(category):
    return category is not None and (
        category.business_unit_id is None
        or (
            category.business_unit
            and category.business_unit.type == BusinessUnitType.PETROL_PUMP_RETAIL
        )
    )


def _read_expense_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "expense_category_id": _int_or_none(request.form.get("expense_category_id")),
        "expense_date": request.form.get("expense_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "paid_from_account_id": _int_or_none(request.form.get("paid_from_account_id")),
        "vendor_id": _int_or_none(request.form.get("vendor_id")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_expense_form(form):
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    category = None
    if form["expense_category_id"] is None:
        errors.append("Expense category is required.")
    else:
        category = db.session.get(ExpenseCategory, form["expense_category_id"])
        if category is None:
            errors.append("Please select a valid expense category.")
        elif not _is_valid_expense_category(category):
            errors.append("Expense category must be global or a Petrol Pump Retail category.")

    expense_date = None
    if not form["expense_date"]:
        errors.append("Expense date is required.")
    else:
        try:
            expense_date = datetime.strptime(form["expense_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid expense date.")

    amount = _parse_nonneg(form["amount"], "Amount", errors)
    if amount is None and form["amount"] == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")

    account = None
    if form["paid_from_account_id"] is not None:
        account = db.session.get(CashBankAccount, form["paid_from_account_id"])
        if account is None or not account.is_active:
            errors.append("Please select an active paid-from account.")

    # Vendor was removed from pump expenses — it has no use here.
    vendor = None

    return errors, pump, category, expense_date, amount, account, vendor


def _expense_form_choices():
    return {
        "pumps": _active_pumps(),
        "categories": _pump_expense_categories(),
        "accounts": _active_accounts(),
    }


@petrol_pumps_bp.route("/expenses")
@role_required(*READING_ROLES)
def expenses_list():
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    f_cat = _int_or_none(request.args.get("expense_category_id"))

    query = PumpExpense.query
    if f_date:
        try:
            query = query.filter(PumpExpense.expense_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_pump:
        query = query.filter(PumpExpense.petrol_pump_id == f_pump)
    if f_cat:
        query = query.filter(PumpExpense.expense_category_id == f_cat)

    expenses = query.order_by(PumpExpense.expense_date.desc(), PumpExpense.id.desc()).all()
    return render_template(
        "petrol_pumps/expenses/list.html",
        expenses=expenses, pump=pump,
        filters={"date": f_date, "petrol_pump_id": f_pump, "expense_category_id": f_cat},
        **_expense_form_choices(),
    )


@petrol_pumps_bp.route("/expenses/create", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def expenses_create():
    if request.method == "POST":
        form = _read_expense_form()
        errors, pump, category, expense_date, amount, account, vendor = _validate_expense_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("petrol_pumps/expenses/form.html", form=form, mode="create", **_expense_form_choices())

        expense = PumpExpense(
            petrol_pump=pump, expense_category=category, expense_date=expense_date,
            amount=amount, paid_from_account=account, vendor=vendor,
            notes=form["notes"] or None, created_by_id=current_user.id,
            is_active=form["is_active"],
        )
        db.session.add(expense)
        db.session.commit()
        flash(f"Expense of {amount} saved.", "success")
        return redirect(url_for("petrol_pumps.expenses_view", expense_id=expense.id))

    form = {
        "petrol_pump_id": _selected_pump_id(_active_pumps()),
        "expense_category_id": None,
        "expense_date": date.today().isoformat(), "amount": "",
        "paid_from_account_id": None, "vendor_id": None, "notes": "", "is_active": True,
    }
    return render_template("petrol_pumps/expenses/form.html", form=form, mode="create", **_expense_form_choices())


@petrol_pumps_bp.route("/expenses/<int:expense_id>")
@role_required(*READING_ROLES)
def expenses_view(expense_id):
    expense = db.get_or_404(PumpExpense, expense_id)
    return render_template("petrol_pumps/expenses/detail.html", expense=expense)


@petrol_pumps_bp.route("/expenses/<int:expense_id>/edit", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def expenses_edit(expense_id):
    expense = db.get_or_404(PumpExpense, expense_id)
    if request.method == "POST":
        form = _read_expense_form()
        errors, pump, category, expense_date, amount, account, vendor = _validate_expense_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("petrol_pumps/expenses/form.html", form=form, mode="edit", expense=expense, **_expense_form_choices())

        expense.petrol_pump = pump
        expense.expense_category = category
        expense.expense_date = expense_date
        expense.amount = amount
        expense.paid_from_account = account
        expense.vendor = vendor
        expense.notes = form["notes"] or None
        expense.is_active = form["is_active"]
        db.session.commit()
        flash("Expense updated.", "success")
        return redirect(url_for("petrol_pumps.expenses_view", expense_id=expense.id))

    form = {
        "petrol_pump_id": expense.petrol_pump_id,
        "expense_category_id": expense.expense_category_id,
        "expense_date": expense.expense_date.isoformat(),
        "amount": str(expense.amount),
        "paid_from_account_id": expense.paid_from_account_id,
        "vendor_id": expense.vendor_id,
        "notes": expense.notes or "",
        "is_active": expense.is_active,
    }
    return render_template("petrol_pumps/expenses/form.html", form=form, mode="edit", expense=expense, **_expense_form_choices())


@petrol_pumps_bp.route("/expenses/<int:expense_id>/toggle-status", methods=["POST"])
@role_required(*READING_ROLES)
def expenses_toggle_status(expense_id):
    expense = db.get_or_404(PumpExpense, expense_id)
    expense.is_active = not expense.is_active
    db.session.commit()
    state = "activated" if expense.is_active else "deactivated"
    flash(f"Expense {state}.", "info")
    return redirect(url_for("petrol_pumps.expenses_list"))


# --------------------------------------------------------------------------- #
# Lubricant Sales
# --------------------------------------------------------------------------- #
def _lubricant_products():
    return (
        Product.query.join(ProductCategory)
        .filter(ProductCategory.name == LUBRICANT_CATEGORY_NAME, Product.is_active.is_(True))
        .order_by(Product.name)
        .all()
    )


def _is_lubricant_product(product):
    return (
        product is not None
        and product.category is not None
        and product.category.name == LUBRICANT_CATEGORY_NAME
    )


def _read_lubricant_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "sale_date": request.form.get("sale_date", "").strip(),
        "quantity": request.form.get("quantity", "").strip(),
        "rate": request.form.get("rate", "").strip(),
        "payment_method": request.form.get("payment_method", "").strip(),
        "customer_id": _int_or_none(request.form.get("customer_id")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _lubricant_stock(pump_id, product_id, exclude_sale_id=None):
    """Derived lubricant stock for a pump+product = purchased − sold.

    Lubricants have no tank; stock is purchases (PumpPurchaseItem lines for the
    lubricant) minus active lubricant sales.
    """
    purchased = (
        db.session.query(db.func.coalesce(db.func.sum(PumpPurchaseItem.quantity_liters), 0))
        .join(PumpPurchase, PumpPurchaseItem.pump_purchase_id == PumpPurchase.id)
        .filter(PumpPurchase.petrol_pump_id == pump_id,
                PumpPurchase.is_active.is_(True),
                PumpPurchaseItem.product_id == product_id)
        .scalar()
    )
    sold_q = LubricantSale.query.filter(
        LubricantSale.petrol_pump_id == pump_id,
        LubricantSale.product_id == product_id,
        LubricantSale.is_active.is_(True),
    )
    if exclude_sale_id is not None:
        sold_q = sold_q.filter(LubricantSale.id != exclude_sale_id)
    sold = sum((s.quantity or Decimal("0")) for s in sold_q.all())
    return Decimal(str(purchased)) - sold


def _lubricant_stock_rows(pump_id):
    """Per-product lubricant stock for a pump: purchased (Head Office buys) −
    sold. Only products with any purchase or sale are listed. This is what the
    lubricant-sale screen shows so staff can see what's in stock."""
    if not pump_id:
        return []
    rows = []
    total_avail = Decimal("0")
    for p in _lubricant_products():
        purchased = Decimal(str(
            db.session.query(db.func.coalesce(db.func.sum(PumpPurchaseItem.quantity_liters), 0))
            .join(PumpPurchase, PumpPurchaseItem.pump_purchase_id == PumpPurchase.id)
            .filter(PumpPurchase.petrol_pump_id == pump_id,
                    PumpPurchase.is_active.is_(True),
                    PumpPurchaseItem.product_id == p.id)
            .scalar()
        ))
        sold = Decimal(str(
            db.session.query(db.func.coalesce(db.func.sum(LubricantSale.quantity), 0))
            .filter(LubricantSale.petrol_pump_id == pump_id,
                    LubricantSale.product_id == p.id,
                    LubricantSale.is_active.is_(True))
            .scalar()
        ))
        if purchased or sold:
            available = purchased - sold
            total_avail += available
            rows.append({"product": p, "purchased": purchased, "sold": sold,
                         "available": available})
    return rows


def _validate_lubricant_form(form, exclude_sale_id=None):
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    product = None
    if form["product_id"] is None:
        errors.append("Product is required.")
    else:
        product = db.session.get(Product, form["product_id"])
        if product is None:
            errors.append("Please select a valid product.")
        elif not _is_lubricant_product(product):
            errors.append("Product must be a lubricant (fuel products are not allowed here).")

    sale_date = None
    if not form["sale_date"]:
        errors.append("Sale date is required.")
    else:
        try:
            sale_date = datetime.strptime(form["sale_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid sale date.")

    quantity = _parse_nonneg(form["quantity"], "Quantity", errors)
    if quantity is None and form["quantity"] == "":
        errors.append("Quantity is required.")
    elif quantity is not None and quantity <= 0:
        errors.append("Quantity must be greater than zero.")
    elif quantity is not None and pump is not None and product is not None:
        # Stock limit: can't sell more lubricant than was purchased.
        avail = _lubricant_stock(pump.id, product.id, exclude_sale_id=exclude_sale_id)
        if quantity > avail:
            errors.append(
                f"Not enough {product.name} in stock at this pump: only "
                f"{avail:,.2f} available, but the sale is {quantity:,.2f}. "
                f"Record a {product.name} purchase first."
            )

    rate = _parse_nonneg(form["rate"], "Rate", errors)
    if rate is None and form["rate"] == "":
        errors.append("Rate is required.")

    if not form["payment_method"]:
        errors.append("Payment method is required.")
    elif form["payment_method"] not in LUBRICANT_PAYMENT_METHODS:
        errors.append("Please select a valid payment method.")

    customer = None
    if form["payment_method"] == CREDIT_CUSTOMER_METHOD:
        if form["customer_id"] is None:
            errors.append("Customer is required for a Credit Customer sale.")
        else:
            customer = db.session.get(Customer, form["customer_id"])
            if customer is None:
                errors.append("Please select a valid customer.")
    elif form["customer_id"] is not None:
        customer = db.session.get(Customer, form["customer_id"])

    return errors, pump, product, sale_date, quantity, rate, customer


def _lubricant_form_choices():
    return {
        "pumps": _active_pumps(),
        "products": _lubricant_products(),
        "customers": _active_customers(),
        "payment_methods": LUBRICANT_PAYMENT_METHODS,
    }


@petrol_pumps_bp.route("/lubricant-sales")
@role_required(*READING_ROLES)
def lubricant_sales_list():
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    f_product = _int_or_none(request.args.get("product_id"))
    f_method = request.args.get("payment_method", "").strip()
    f_customer = _int_or_none(request.args.get("customer_id"))

    query = LubricantSale.query
    if f_date:
        try:
            query = query.filter(LubricantSale.sale_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_pump:
        query = query.filter(LubricantSale.petrol_pump_id == f_pump)
    if f_product:
        query = query.filter(LubricantSale.product_id == f_product)
    if f_method:
        query = query.filter(LubricantSale.payment_method == f_method)
    if f_customer:
        query = query.filter(LubricantSale.customer_id == f_customer)

    sales = query.order_by(LubricantSale.sale_date.desc(), LubricantSale.id.desc()).all()
    return render_template(
        "petrol_pumps/lubricant_sales/list.html",
        sales=sales, pump=pump, stock_rows=_lubricant_stock_rows(f_pump),
        filters={"date": f_date, "petrol_pump_id": f_pump, "product_id": f_product, "payment_method": f_method, "customer_id": f_customer},
        **_lubricant_form_choices(),
    )


@petrol_pumps_bp.route("/lubricant-sales/create", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def lubricant_sales_create():
    if request.method == "POST":
        form = _read_lubricant_form()
        errors, pump, product, sale_date, quantity, rate, customer = _validate_lubricant_form(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("petrol_pumps/lubricant_sales/form.html", form=form, mode="create", **_lubricant_form_choices())

        total = quantity * rate
        sale = LubricantSale(
            petrol_pump=pump, product=product, sale_date=sale_date,
            quantity=quantity, rate=rate, total_amount=total,
            payment_method=form["payment_method"], customer=customer,
            notes=form["notes"] or None, created_by_id=current_user.id,
            is_active=form["is_active"],
        )
        db.session.add(sale)
        db.session.commit()
        flash(f"Lubricant sale saved. Total {total}.", "success")
        return redirect(url_for("petrol_pumps.lubricant_sales_view", sale_id=sale.id))

    pump_id = _selected_pump_id(_active_pumps())
    form = {
        "petrol_pump_id": pump_id,
        "product_id": None, "sale_date": date.today().isoformat(),
        "quantity": "", "rate": "", "payment_method": "Cash", "customer_id": None,
        "notes": "", "is_active": True,
    }
    return render_template("petrol_pumps/lubricant_sales/form.html", form=form, mode="create", stock_rows=_lubricant_stock_rows(pump_id), **_lubricant_form_choices())


@petrol_pumps_bp.route("/lubricant-sales/<int:sale_id>")
@role_required(*READING_ROLES)
def lubricant_sales_view(sale_id):
    sale = db.get_or_404(LubricantSale, sale_id)
    return render_template("petrol_pumps/lubricant_sales/detail.html", sale=sale)


@petrol_pumps_bp.route("/lubricant-sales/<int:sale_id>/edit", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def lubricant_sales_edit(sale_id):
    sale = db.get_or_404(LubricantSale, sale_id)
    if request.method == "POST":
        form = _read_lubricant_form()
        errors, pump, product, sale_date, quantity, rate, customer = _validate_lubricant_form(form, exclude_sale_id=sale.id)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("petrol_pumps/lubricant_sales/form.html", form=form, mode="edit", sale=sale, stock_rows=_lubricant_stock_rows(sale.petrol_pump_id), **_lubricant_form_choices())

        sale.petrol_pump = pump
        sale.product = product
        sale.sale_date = sale_date
        sale.quantity = quantity
        sale.rate = rate
        sale.total_amount = quantity * rate
        sale.payment_method = form["payment_method"]
        sale.customer = customer
        sale.notes = form["notes"] or None
        sale.is_active = form["is_active"]
        db.session.commit()
        flash("Lubricant sale updated.", "success")
        return redirect(url_for("petrol_pumps.lubricant_sales_view", sale_id=sale.id))

    form = {
        "petrol_pump_id": sale.petrol_pump_id, "product_id": sale.product_id,
        "sale_date": sale.sale_date.isoformat(), "quantity": str(sale.quantity),
        "rate": str(sale.rate), "payment_method": sale.payment_method,
        "customer_id": sale.customer_id, "notes": sale.notes or "", "is_active": sale.is_active,
    }
    return render_template("petrol_pumps/lubricant_sales/form.html", form=form, mode="edit", sale=sale, stock_rows=_lubricant_stock_rows(sale.petrol_pump_id), **_lubricant_form_choices())


@petrol_pumps_bp.route("/lubricant-sales/<int:sale_id>/toggle-status", methods=["POST"])
@role_required(*READING_ROLES)
def lubricant_sales_toggle_status(sale_id):
    sale = db.get_or_404(LubricantSale, sale_id)
    sale.is_active = not sale.is_active
    db.session.commit()
    state = "activated" if sale.is_active else "deactivated"
    flash(f"Lubricant sale {state}.", "info")
    return redirect(url_for("petrol_pumps.lubricant_sales_list"))


# --------------------------------------------------------------------------- #
# Daily Closings
# --------------------------------------------------------------------------- #
# Only the NON-CASH amounts are entered by the accountant; cash received and
# cash submitted are derived (total sale − non-cash − expenses).
PAYMENT_INPUT_KEYS = [
    "bank_card_received", "pso_card_amount", "easypaisa_amount",
    "jazzcash_amount", "bank_transfer_amount", "credit_sale_amount",
]

# Non-cash methods that route the money to a chosen account. Each entry:
#   (amount_attr, account_attr, account_form_field, label, allowed-type-substrings)
# Credit sale is deliberately absent — it is a receivable, not a deposit.
# Note: PSO Card is NOT here — it does not land in a bank account. It is routed
# to the PSO vendor's card section (see _sync_closing_pso_card) and only reduces
# the PSO payable once an accountant verifies it.
CLOSING_DEPOSIT_METHODS = [
    ("bank_card_received", "bank_card_account_id", "bank_card_account_id", "Bank Card", ("bank",)),
    ("easypaisa_amount", "easypaisa_account_id", "easypaisa_account_id", "Easypaisa", ("wallet",)),
    ("jazzcash_amount", "jazzcash_account_id", "jazzcash_account_id", "JazzCash", ("wallet",)),
    ("bank_transfer_amount", "bank_transfer_account_id", "bank_transfer_account_id", "Bank Transfer", ("bank",)),
]


def _read_deposit_accounts():
    """Chosen account id per non-cash method, from the submitted form."""
    return {
        field: _int_or_none(request.form.get(field))
        for (_amt, _attr, field, _label, _types) in CLOSING_DEPOSIT_METHODS
    }


def _validate_deposit_accounts(pump_id, payment_inputs, account_ids, errors):
    """Each non-cash method with an amount must route to one of THIS pump's
    active accounts. Returns nothing; appends to `errors`."""
    for amount_attr, _attr, field, label, _types in CLOSING_DEPOSIT_METHODS:
        amount = payment_inputs.get(amount_attr) or Decimal("0")
        account_id = account_ids.get(field)
        if amount and amount > 0:
            if account_id is None:
                errors.append(f"{label}: choose which account the {amount:,.0f} went into.")
                continue
            acct = db.session.get(CashBankAccount, account_id)
            if acct is None or not acct.is_active or acct.petrol_pump_id != pump_id:
                errors.append(f"{label}: select one of this pump's accounts.")


def _apply_deposit_accounts(closing, account_ids):
    """Set each method's account FK on the closing (clearing it when the method
    has no amount, so a removed amount doesn't keep a stale account)."""
    for amount_attr, account_attr, field, _label, _types in CLOSING_DEPOSIT_METHODS:
        amount = getattr(closing, amount_attr) or Decimal("0")
        setattr(closing, account_attr, account_ids.get(field) if amount > 0 else None)


def _method_accounts(pump_id):
    """For the form: this pump's active accounts grouped by which methods may use
    them (by account type). Returns {form_field: [accounts]}."""
    accounts = _pump_accounts(pump_id)
    grouped = {}
    for _amt, _attr, field, _label, types in CLOSING_DEPOSIT_METHODS:
        grouped[field] = [
            a for a in accounts
            if any(t in (a.account_type or "").lower() for t in types)
        ]
    return grouped


def _pump_customers(pump_id):
    """This pump's active customers (for the daily-closing credit-sale selector)."""
    if not pump_id:
        return []
    return (
        Customer.query
        .filter_by(petrol_pump_id=pump_id, is_active=True)
        .order_by(Customer.name)
        .all()
    )


def _validate_credit_customer(pump_id, payment_inputs, customer_id, errors):
    """A credit sale MUST name the customer it was given to (one of this pump's
    customers), so it lands in that customer's ledger to clear later."""
    credit = payment_inputs.get("credit_sale_amount") or Decimal("0")
    if credit and credit > 0:
        if customer_id is None:
            errors.append("Credit Sale: choose which customer the credit was given to.")
            return
        cust = db.session.get(Customer, customer_id)
        if cust is None or not cust.is_active or cust.petrol_pump_id != pump_id:
            errors.append("Credit Sale: select one of this pump's customers.")


def _closing_duplicate(pump_id, closing_date, exclude_id=None):
    query = PumpDailyClosing.query.filter(
        PumpDailyClosing.petrol_pump_id == pump_id,
        PumpDailyClosing.closing_date == closing_date,
    )
    if exclude_id is not None:
        query = query.filter(PumpDailyClosing.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _read_payment_inputs(errors):
    inputs = {}
    for key in PAYMENT_INPUT_KEYS:
        inputs[key] = _parse_nonneg(request.form.get(key, "").strip(), key.replace("_", " ").title(), errors, default=Decimal("0"))
    return inputs


def _apply_summary_to_closing(closing, summary, payment_inputs):
    closing.fuel_sale_amount = summary["fuel_sale_amount"]
    closing.fuel_sale_liters = summary["fuel_sale_liters"]
    closing.lubricant_sale_amount = summary["lubricant_sale_amount"]
    closing.total_sale_amount = summary["total_sale_amount"]
    closing.expenses_paid = summary["expenses_paid"]
    closing.cash_received = summary["cash_received"]
    closing.bank_card_received = summary["bank_card_received"]
    closing.pso_card_amount = summary["pso_card_amount"]
    closing.easypaisa_amount = summary["easypaisa_amount"]
    closing.jazzcash_amount = summary["jazzcash_amount"]
    closing.bank_transfer_amount = summary["bank_transfer_amount"]
    closing.credit_sale_amount = summary["credit_sale_amount"]
    closing.cash_submitted_to_head_office = summary["cash_submitted_to_head_office"]
    closing.expected_cash_to_submit = summary["expected_cash_to_submit"]
    closing.difference_amount = summary["difference_amount"]


def _pump_accounts(pump_id):
    """Active cash/bank accounts that belong to a specific pump (for the daily
    closing's deposit selector — only that pump's accounts show)."""
    if not pump_id:
        return []
    return (
        CashBankAccount.query
        .filter_by(petrol_pump_id=pump_id, is_active=True)
        .order_by(CashBankAccount.name)
        .all()
    )


def _closing_deposit_plan(closing):
    """[(account_id, amount), ...] — one entry per non-cash method that has both
    an amount and a chosen destination account on this closing."""
    plan = []
    for amount_attr, account_attr, _field, _label, _types in CLOSING_DEPOSIT_METHODS:
        amount = getattr(closing, amount_attr) or Decimal("0")
        account_id = getattr(closing, account_attr)
        if account_id and amount > 0:
            plan.append((account_id, amount))
    return plan


def _sync_closing_bank(closing):
    """Idempotently credit EACH non-cash method's chosen account (bank card /
    PSO card / bank transfer -> bank; Easypaisa / JazzCash -> wallet). The exact
    prior postings are stored as a JSON snapshot and reversed first, so edits and
    deactivation always reconcile. Legacy single-account postings (older closings
    that used `bank_account_id`) are reversed once on the next sync."""
    # 1. Reverse the previous per-method snapshot.
    if closing.deposit_posted:
        try:
            previous = json.loads(closing.deposit_posted)
        except (ValueError, TypeError):
            previous = []
        for item in previous:
            acc = db.session.get(CashBankAccount, item.get("account_id"))
            if acc is not None:
                acc.current_balance = (
                    acc.current_balance or Decimal("0")
                ) - Decimal(str(item.get("amount") or 0))
        closing.deposit_posted = None

    # 1b. Reverse any legacy single-account posting (pre per-method closings).
    if closing.bank_posted_account_id and (closing.bank_posted_amount or Decimal("0")) != 0:
        legacy = db.session.get(CashBankAccount, closing.bank_posted_account_id)
        if legacy is not None:
            legacy.current_balance = (legacy.current_balance or Decimal("0")) - closing.bank_posted_amount
        closing.bank_posted_account_id = None
        closing.bank_posted_amount = Decimal("0")

    # 2. Apply the new per-method postings (only while the closing is active).
    snapshot = []
    if closing.is_active:
        for account_id, amount in _closing_deposit_plan(closing):
            acc = db.session.get(CashBankAccount, account_id)
            if acc is not None:
                acc.current_balance = (acc.current_balance or Decimal("0")) + amount
                snapshot.append({"account_id": account_id, "amount": str(amount)})
    closing.deposit_posted = json.dumps(snapshot) if snapshot else None


def _pso_vendor():
    """The PSO vendor (our fuel supplier). Prefer the Petrol Pump Retail one."""
    q = Vendor.query.filter(Vendor.vendor_type == VendorType.PSO)
    retail = BusinessUnit.query.filter_by(
        type=BusinessUnitType.PETROL_PUMP_RETAIL
    ).first()
    if retail is not None:
        v = q.filter(Vendor.business_unit_id == retail.id).first()
        if v is not None:
            return v
    return q.first()


def _sync_closing_pso_card(closing):
    """Mirror the closing's PSO card amount as a PsoCardPayment owed to the PSO
    vendor (one per closing). Idempotent: updates the existing row, or
    deactivates it when the closing is inactive / has no PSO card amount.

    The row stays PENDING (is_verified False) until an accountant verifies it —
    only then does it reduce the PSO payable (accounting.ledger). A pending row's
    amount tracks the closing; once verified it is left as-is so a later edit
    can't silently change an already-reconciled figure."""
    amount = closing.pso_card_amount or Decimal("0")
    existing = PsoCardPayment.query.filter_by(daily_closing_id=closing.id).first()
    vendor = _pso_vendor()

    want = bool(closing.is_active and amount > 0 and vendor is not None)
    if not want:
        if existing is not None:
            existing.is_active = False
        return

    if existing is None:
        db.session.add(PsoCardPayment(
            petrol_pump_id=closing.petrol_pump_id, vendor_id=vendor.id,
            daily_closing_id=closing.id, payment_date=closing.closing_date,
            amount=amount, is_active=True,
        ))
    else:
        existing.is_active = True
        existing.petrol_pump_id = closing.petrol_pump_id
        existing.vendor_id = vendor.id
        existing.payment_date = closing.closing_date
        if not existing.is_verified:
            existing.amount = amount


@petrol_pumps_bp.route("/daily-closings")
@role_required(*READING_ROLES)
def daily_closings_list():
    f_date = request.args.get("date", "").strip() or date.today().isoformat()
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    query = PumpDailyClosing.query
    if f_date:
        try:
            query = query.filter(PumpDailyClosing.closing_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_pump:
        query = query.filter(PumpDailyClosing.petrol_pump_id == f_pump)
    closings = query.order_by(PumpDailyClosing.closing_date.desc(), PumpDailyClosing.id.desc()).all()

    # Prepare the selected date's closing entry inline so it shows automatically
    # below the list (no "New Closing" click). If a closing already exists for
    # that date it's `entry_done`; otherwise `entry` carries the blank form +
    # auto-calculated preview.
    entry = None
    entry_done = None
    try:
        entry_date = datetime.strptime(f_date, "%Y-%m-%d").date()
    except ValueError:
        entry_date = date.today()
    if pump is not None:
        entry_done = PumpDailyClosing.query.filter_by(
            petrol_pump_id=pump.id, closing_date=entry_date
        ).first()
        if entry_done is None:
            eform = {
                "petrol_pump_id": pump.id, "closing_date": entry_date.isoformat(),
                "remarks": "", "manager_approved": False,
            }
            for key in PAYMENT_INPUT_KEYS:
                eform[key] = ""
            for (_a, _at, fld, _l, _t) in CLOSING_DEPOSIT_METHODS:
                eform[fld] = None
            eform["credit_customer_id"] = None
            entry = {
                "form": eform,
                "preview": calculate_daily_closing_summary(pump.id, entry_date, {}),
                "method_accounts": _method_accounts(pump.id),
                "credit_customers": _pump_customers(pump.id),
            }

    return render_template(
        "petrol_pumps/daily_closings/list.html",
        closings=closings, pump=pump,
        entry=entry, entry_done=entry_done, entry_date=entry_date,
        deposit_methods=CLOSING_DEPOSIT_METHODS,
        filters={"date": f_date, "petrol_pump_id": f_pump},
        pumps=pumps,
    )


@petrol_pumps_bp.route("/daily-closings/create", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def daily_closings_create():
    if request.method == "POST":
        pump_id = _int_or_none(request.form.get("petrol_pump_id"))
        raw_date = request.form.get("closing_date", "").strip()
        errors = []

        pump = db.session.get(PetrolPump, pump_id) if pump_id else None
        if pump is None:
            errors.append("Petrol pump is required.")
        closing_date = None
        if not raw_date:
            errors.append("Closing date is required.")
        else:
            try:
                closing_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                errors.append("Please enter a valid closing date.")

        payment_inputs = _read_payment_inputs(errors)
        account_ids = _read_deposit_accounts()
        credit_customer_id = _int_or_none(request.form.get("credit_customer_id"))
        if pump is not None:
            _validate_deposit_accounts(pump.id, payment_inputs, account_ids, errors)
            _validate_credit_customer(pump.id, payment_inputs, credit_customer_id, errors)

        if pump and closing_date and _closing_duplicate(pump.id, closing_date):
            errors.append("A daily closing already exists for this pump and date.")

        if pump and closing_date:
            if calculate_daily_closing_summary(pump.id, closing_date, payment_inputs)["payment_mismatch"]:
                errors.append("Non-cash amounts exceed the total sale — please check the figures.")

        if errors:
            for m in errors:
                flash(m, "danger")
            form = _closing_form_from_request()
            preview = None
            if pump and closing_date:
                preview = calculate_daily_closing_summary(pump.id, closing_date, payment_inputs)
            return render_template("petrol_pumps/daily_closings/form.html", form=form, mode="create", preview=preview, pumps=_active_pumps(), pump_accounts=_pump_accounts(pump.id if pump else None), method_accounts=_method_accounts(pump.id if pump else None), deposit_methods=CLOSING_DEPOSIT_METHODS, credit_customers=_pump_customers(pump.id if pump else None))

        summary = calculate_daily_closing_summary(pump.id, closing_date, payment_inputs)
        closing = PumpDailyClosing(
            petrol_pump=pump, closing_date=closing_date,
            remarks=request.form.get("remarks", "").strip() or None,
            manager_approved=bool(request.form.get("manager_approved")),
            created_by_id=current_user.id, is_active=True,
        )
        _apply_summary_to_closing(closing, summary, payment_inputs)
        _apply_deposit_accounts(closing, account_ids)
        closing.credit_customer_id = credit_customer_id if (payment_inputs.get("credit_sale_amount") or Decimal("0")) > 0 else None
        if closing.manager_approved:
            closing.approved_by_id = current_user.id
        db.session.add(closing)
        db.session.flush()
        posting.sync_daily_closing(closing)
        _sync_closing_bank(closing)
        _sync_closing_pso_card(closing)
        db.session.commit()
        if summary["payment_mismatch"]:
            flash("Warning: payment method totals do not equal total sale amount.", "warning")
        flash("Daily closing saved.", "success")
        return redirect(url_for("petrol_pumps.daily_closings_view", closing_id=closing.id))

    # GET: optional preview when pump + date provided as query args.
    pump_id = _selected_pump_id(_active_pumps())
    raw_date = request.args.get("closing_date", "").strip()
    preview = None
    pump = db.session.get(PetrolPump, pump_id) if pump_id else None
    closing_date = None
    if raw_date:
        try:
            closing_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            closing_date = None
    if pump and closing_date:
        preview = calculate_daily_closing_summary(pump.id, closing_date, {})

    form = {
        "petrol_pump_id": pump_id, "closing_date": raw_date or date.today().isoformat(),
        "remarks": "", "manager_approved": False,
    }
    for key in PAYMENT_INPUT_KEYS:
        form[key] = ""
    for (_amt, _attr, field, _label, _types) in CLOSING_DEPOSIT_METHODS:
        form[field] = None
    form["credit_customer_id"] = None
    return render_template("petrol_pumps/daily_closings/form.html", form=form, mode="create", preview=preview, pumps=_active_pumps(), pump_accounts=_pump_accounts(pump_id), method_accounts=_method_accounts(pump_id), deposit_methods=CLOSING_DEPOSIT_METHODS, credit_customers=_pump_customers(pump_id))


def _closing_form_from_request():
    form = {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "closing_date": request.form.get("closing_date", "").strip(),
        "remarks": request.form.get("remarks", "").strip(),
        "manager_approved": bool(request.form.get("manager_approved")),
        "credit_customer_id": _int_or_none(request.form.get("credit_customer_id")),
    }
    for key in PAYMENT_INPUT_KEYS:
        form[key] = request.form.get(key, "").strip()
    for (_amt, _attr, field, _label, _types) in CLOSING_DEPOSIT_METHODS:
        form[field] = _int_or_none(request.form.get(field))
    return form


@petrol_pumps_bp.route("/daily-closings/<int:closing_id>")
@role_required(*READING_ROLES)
def daily_closings_view(closing_id):
    closing = db.get_or_404(PumpDailyClosing, closing_id)
    payment_methods_total = (
        closing.cash_received + closing.bank_card_received + closing.pso_card_amount
        + closing.easypaisa_amount + closing.jazzcash_amount
        + closing.bank_transfer_amount + closing.credit_sale_amount
    )
    return render_template(
        "petrol_pumps/daily_closings/detail.html",
        closing=closing, payment_methods_total=payment_methods_total,
        payment_mismatch=(payment_methods_total != closing.total_sale_amount),
        deposit_methods=CLOSING_DEPOSIT_METHODS,
    )


@petrol_pumps_bp.route("/daily-closings/<int:closing_id>/edit", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def daily_closings_edit(closing_id):
    closing = db.get_or_404(PumpDailyClosing, closing_id)
    if request.method == "POST":
        errors = []
        payment_inputs = _read_payment_inputs(errors)
        account_ids = _read_deposit_accounts()
        credit_customer_id = _int_or_none(request.form.get("credit_customer_id"))
        _validate_deposit_accounts(closing.petrol_pump_id, payment_inputs, account_ids, errors)
        _validate_credit_customer(closing.petrol_pump_id, payment_inputs, credit_customer_id, errors)
        if calculate_daily_closing_summary(closing.petrol_pump_id, closing.closing_date, payment_inputs)["payment_mismatch"]:
            errors.append("Non-cash amounts exceed the total sale — please check the figures.")
        if errors:
            for m in errors:
                flash(m, "danger")
            form = _closing_form_from_request()
            form["petrol_pump_id"] = closing.petrol_pump_id
            form["closing_date"] = closing.closing_date.isoformat()
            preview = calculate_daily_closing_summary(closing.petrol_pump_id, closing.closing_date, payment_inputs)
            return render_template("petrol_pumps/daily_closings/form.html", form=form, mode="edit", closing=closing, preview=preview, pumps=_active_pumps(), pump_accounts=_pump_accounts(closing.petrol_pump_id), method_accounts=_method_accounts(closing.petrol_pump_id), deposit_methods=CLOSING_DEPOSIT_METHODS, credit_customers=_pump_customers(closing.petrol_pump_id))

        # Pump/date are fixed on edit; recompute from underlying records.
        summary = calculate_daily_closing_summary(closing.petrol_pump_id, closing.closing_date, payment_inputs)
        _apply_summary_to_closing(closing, summary, payment_inputs)
        _apply_deposit_accounts(closing, account_ids)
        closing.credit_customer_id = credit_customer_id if (payment_inputs.get("credit_sale_amount") or Decimal("0")) > 0 else None
        closing.remarks = request.form.get("remarks", "").strip() or None
        closing.manager_approved = bool(request.form.get("manager_approved"))
        closing.approved_by_id = current_user.id if closing.manager_approved else None
        posting.sync_daily_closing(closing)
        _sync_closing_bank(closing)
        _sync_closing_pso_card(closing)
        db.session.commit()
        if summary["payment_mismatch"]:
            flash("Warning: payment method totals do not equal total sale amount.", "warning")
        flash("Daily closing updated.", "success")
        return redirect(url_for("petrol_pumps.daily_closings_view", closing_id=closing.id))

    form = {
        "petrol_pump_id": closing.petrol_pump_id,
        "closing_date": closing.closing_date.isoformat(),
        "remarks": closing.remarks or "",
        "manager_approved": closing.manager_approved,
        "bank_card_received": str(closing.bank_card_received),
        "pso_card_amount": str(closing.pso_card_amount),
        "easypaisa_amount": str(closing.easypaisa_amount),
        "jazzcash_amount": str(closing.jazzcash_amount),
        "bank_transfer_amount": str(closing.bank_transfer_amount),
        "credit_sale_amount": str(closing.credit_sale_amount),
        "credit_customer_id": closing.credit_customer_id,
    }
    for (_amt, account_attr, field, _label, _types) in CLOSING_DEPOSIT_METHODS:
        form[field] = getattr(closing, account_attr)
    preview = calculate_daily_closing_summary(closing.petrol_pump_id, closing.closing_date, form)
    return render_template("petrol_pumps/daily_closings/form.html", form=form, mode="edit", closing=closing, preview=preview, pumps=_active_pumps(), pump_accounts=_pump_accounts(closing.petrol_pump_id), method_accounts=_method_accounts(closing.petrol_pump_id), deposit_methods=CLOSING_DEPOSIT_METHODS, credit_customers=_pump_customers(closing.petrol_pump_id))


@petrol_pumps_bp.route("/daily-closings/<int:closing_id>/toggle-status", methods=["POST"])
@role_required(*READING_ROLES)
def daily_closings_toggle_status(closing_id):
    closing = db.get_or_404(PumpDailyClosing, closing_id)
    closing.is_active = not closing.is_active
    posting.sync_daily_closing(closing)
    _sync_closing_bank(closing)
    _sync_closing_pso_card(closing)
    db.session.commit()
    state = "activated" if closing.is_active else "deactivated"
    flash(f"Daily closing {state}.", "info")
    return redirect(url_for("petrol_pumps.daily_closings_list"))


# --------------------------------------------------------------------------- #
# Cash Feed to Carriage — cash handed from a pump to the carriage business.
# One entry = "this pump fed PKR X to carriage". It reduces the pump's current
# cash and (when a carriage account is chosen) tops up that account's balance,
# so carriage can spend it on trips. Same idempotent balance-posting pattern as
# the head-office cash receipt.
# --------------------------------------------------------------------------- #
def _carriage_accounts():
    """Active cash/bank accounts of the Carriage business unit (fallback: all
    active accounts, so the feed still works before carriage accounts exist)."""
    from app.core.models import BusinessUnit, BusinessUnitType
    carriage = (
        CashBankAccount.query.join(BusinessUnit)
        .filter(
            BusinessUnit.type == BusinessUnitType.CARRIAGE,
            CashBankAccount.is_active.is_(True),
        )
        .order_by(CashBankAccount.name)
        .all()
    )
    return carriage or _active_accounts()


def _carriage_feed_should_post(feed):
    return feed.is_active and feed.into_account_id is not None


def _sync_carriage_feed(feed):
    """Idempotently credit the chosen carriage account with the fed cash."""
    desired = _carriage_feed_should_post(feed)
    account = feed.into_account
    if desired and not feed.is_posted:
        if account is not None:
            account.current_balance = (account.current_balance or Decimal("0")) + feed.amount
        feed.is_posted = True
    elif not desired and feed.is_posted:
        if account is not None:
            account.current_balance = (account.current_balance or Decimal("0")) - feed.amount
        feed.is_posted = False


@petrol_pumps_bp.route("/carriage-feeds")
@role_required(*READING_ROLES)
def carriage_feeds_list():
    pumps = _active_pumps()
    pump = _selected_pump(pumps)
    f_pump = pump.id if pump else None
    query = PumpCarriageCashFeed.query
    if f_pump:
        query = query.filter(PumpCarriageCashFeed.petrol_pump_id == f_pump)
    feeds = query.order_by(
        PumpCarriageCashFeed.feed_date.desc(), PumpCarriageCashFeed.id.desc()
    ).all()
    total = sum((f.amount or Decimal("0")) for f in feeds if f.is_active)
    return render_template(
        "petrol_pumps/carriage_feeds/list.html",
        feeds=feeds, total=total, pump=pump,
        filters={"petrol_pump_id": f_pump}, pumps=pumps,
    )


def _read_carriage_feed_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "feed_date": request.form.get("feed_date", "").strip(),
        "amount": request.form.get("amount", "").strip(),
        "into_account_id": _int_or_none(request.form.get("into_account_id")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_carriage_feed_form(form):
    errors = []
    pump = db.session.get(PetrolPump, form["petrol_pump_id"]) if form["petrol_pump_id"] else None
    if pump is None:
        errors.append("Please select a valid petrol pump.")
    feed_date = None
    if not form["feed_date"]:
        errors.append("Date is required.")
    else:
        try:
            feed_date = datetime.strptime(form["feed_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid date.")
    amount = _parse_nonneg(form["amount"], "Amount", errors)
    if amount is None and form["amount"] == "":
        errors.append("Amount is required.")
    elif amount is not None and amount <= 0:
        errors.append("Amount must be greater than zero.")
    account = None
    if form["into_account_id"] is not None:
        account = db.session.get(CashBankAccount, form["into_account_id"])
        if account is None or not account.is_active:
            errors.append("Please select a valid carriage account.")
    return errors, pump, feed_date, amount, account


@petrol_pumps_bp.route("/carriage-feeds/create", methods=["GET", "POST"])
@role_required(*READING_ROLES)
def carriage_feeds_create():
    if request.method == "POST":
        form = _read_carriage_feed_form()
        errors, pump, feed_date, amount, account = _validate_carriage_feed_form(form)
        if pump is not None and amount and amount > pump_current_cash(pump.id):
            errors.append(
                f"Pump only has PKR {pump_current_cash(pump.id):,.0f} cash now — "
                f"cannot feed PKR {amount:,.0f} to carriage."
            )
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template(
                "petrol_pumps/carriage_feeds/form.html", form=form, mode="create",
                pumps=_active_pumps(), accounts=_carriage_accounts(),
            )
        feed = PumpCarriageCashFeed(
            petrol_pump=pump, feed_date=feed_date, amount=amount,
            into_account=account, notes=form["notes"] or None,
            created_by_id=current_user.id, is_active=form["is_active"],
        )
        db.session.add(feed)
        db.session.flush()
        _sync_carriage_feed(feed)
        db.session.commit()
        flash(f"Fed PKR {amount:,.0f} from {pump.name} to carriage.", "success")
        return redirect(url_for("petrol_pumps.carriage_feeds_view", feed_id=feed.id))

    form = {
        "petrol_pump_id": _selected_pump_id(_active_pumps()),
        "feed_date": date.today().isoformat(), "amount": "",
        "into_account_id": None, "notes": "", "is_active": True,
    }
    return render_template(
        "petrol_pumps/carriage_feeds/form.html", form=form, mode="create",
        pumps=_active_pumps(), accounts=_carriage_accounts(),
    )


@petrol_pumps_bp.route("/carriage-feeds/<int:feed_id>")
@role_required(*READING_ROLES)
def carriage_feeds_view(feed_id):
    feed = db.get_or_404(PumpCarriageCashFeed, feed_id)
    return render_template("petrol_pumps/carriage_feeds/detail.html", feed=feed)


@petrol_pumps_bp.route("/carriage-feeds/<int:feed_id>/toggle-status", methods=["POST"])
@role_required(*READING_ROLES)
def carriage_feeds_toggle_status(feed_id):
    feed = db.get_or_404(PumpCarriageCashFeed, feed_id)
    feed.is_active = not feed.is_active
    _sync_carriage_feed(feed)   # reverses/re-applies the account credit
    db.session.commit()
    state = "activated" if feed.is_active else "deactivated"
    flash(f"Cash feed {state}.", "info")
    return redirect(url_for("petrol_pumps.carriage_feeds_list"))


@petrol_pumps_bp.route("/carriage-feeds/<int:feed_id>/delete", methods=["POST"])
@role_required(*READING_ROLES)
def carriage_feeds_delete(feed_id):
    feed = db.get_or_404(PumpCarriageCashFeed, feed_id)
    feed.is_active = False
    _sync_carriage_feed(feed)   # back out the account credit before removing
    db.session.delete(feed)
    db.session.commit()
    flash("Cash feed deleted.", "success")
    return redirect(url_for("petrol_pumps.carriage_feeds_list"))
