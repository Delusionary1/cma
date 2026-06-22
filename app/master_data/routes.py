"""Master Data routes: Business Unit and Petrol Pump management (CRUD).

Access is restricted to the roles allowed for the master_data module
(Admin / Owner and Head Office Manager). Records are never hard-deleted; the
toggle-status routes flip the is_active flag instead.

Forms are plain HTML/Jinja (no Flask-WTF yet). Validation is done here and
errors are shown to the user with flash messages.
"""
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from decimal import Decimal, InvalidOperation

from app.extensions import db
from app.auth.access import roles_for_module
from app.auth.decorators import role_required
from app.core.models import (
    ACCOUNT_TYPES,
    BusinessUnit,
    BusinessUnitType,
    CashBankAccount,
    Customer,
    Driver,
    ExpenseCategory,
    PetrolPump,
    Product,
    ProductCategory,
    PRODUCT_UNITS,
    Vehicle,
    VEHICLE_TYPES,
    OWNERSHIP_TYPES,
    Vendor,
    VendorType,
)

master_data_bp = Blueprint("master_data", __name__)

# Roles allowed to manage master data (Admin / Owner + Head Office Manager).
MANAGE_ROLES = roles_for_module("master_data")

# Vehicles and drivers are also manageable by the Transport Manager.
VEHICLE_DRIVER_ROLES = (
    "Admin / Owner",
    "Head Office Manager",
    "Transport Manager",
)

# Cash/bank accounts and expense categories are also manageable by the Accountant.
ACCOUNT_ROLES = (
    "Admin / Owner",
    "Head Office Manager",
    "Accountant",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _active_retail_units():
    """Active business units of type PETROL_PUMP_RETAIL (for pump dropdowns)."""
    return (
        BusinessUnit.query.filter_by(
            type=BusinessUnitType.PETROL_PUMP_RETAIL, is_active=True
        )
        .order_by(BusinessUnit.name)
        .all()
    )


def _business_unit_name_taken(name, exclude_id=None):
    """True if another business unit already uses this name (case-insensitive)."""
    query = BusinessUnit.query.filter(
        db.func.lower(BusinessUnit.name) == name.lower()
    )
    if exclude_id is not None:
        query = query.filter(BusinessUnit.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _petrol_pump_name_taken(name, exclude_id=None):
    """True if another petrol pump already uses this name (case-insensitive)."""
    query = PetrolPump.query.filter(
        db.func.lower(PetrolPump.name) == name.lower()
    )
    if exclude_id is not None:
        query = query.filter(PetrolPump.id != exclude_id)
    return db.session.query(query.exists()).scalar()


# --------------------------------------------------------------------------- #
# Master Data dashboard
# --------------------------------------------------------------------------- #
@master_data_bp.route("/")
@role_required(*MANAGE_ROLES)
def index():
    """Master Data landing page with links to each sub-section."""
    counts = {
        "business_units": BusinessUnit.query.count(),
        "petrol_pumps": PetrolPump.query.count(),
        "product_categories": ProductCategory.query.count(),
        "products": Product.query.count(),
        "customers": Customer.query.count(),
        "vendors": Vendor.query.count(),
        "drivers": Driver.query.count(),
        "vehicles": Vehicle.query.count(),
        "cash_bank_accounts": CashBankAccount.query.count(),
        "expense_categories": ExpenseCategory.query.count(),
    }
    return render_template("master_data/index.html", counts=counts)


# --------------------------------------------------------------------------- #
# Business Units
# --------------------------------------------------------------------------- #
@master_data_bp.route("/business-units")
@role_required(*MANAGE_ROLES)
def business_units_list():
    """List business units, with optional search by name or type."""
    q = request.args.get("q", "").strip()
    query = BusinessUnit.query

    if q:
        like = f"%{q}%"
        # Match type by its stored value (e.g. "PETROL_PUMP_RETAIL").
        matching_types = [
            t for t in BusinessUnitType if q.lower() in t.value.lower()
        ]
        conditions = [BusinessUnit.name.ilike(like)]
        if matching_types:
            conditions.append(BusinessUnit.type.in_(matching_types))
        query = query.filter(db.or_(*conditions))

    units = query.order_by(BusinessUnit.name).all()
    return render_template(
        "master_data/business_units/list.html", units=units, q=q
    )


@master_data_bp.route("/business-units/create", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def business_units_create():
    """Create a new business unit."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        type_value = request.form.get("type", "").strip()
        description = request.form.get("description", "").strip()
        is_active = bool(request.form.get("is_active"))

        # Keep submitted values so the form can be re-rendered on error.
        form = {
            "name": name,
            "type": type_value,
            "description": description,
            "is_active": is_active,
        }

        errors = []
        if not name:
            errors.append("Name is required.")
        if not type_value:
            errors.append("Type is required.")

        selected_type = None
        if type_value:
            try:
                selected_type = BusinessUnitType[type_value]
            except KeyError:
                errors.append("Please select a valid type.")

        if name and _business_unit_name_taken(name):
            errors.append(f"A business unit named '{name}' already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/business_units/form.html",
                form=form,
                types=list(BusinessUnitType),
                mode="create",
            )

        unit = BusinessUnit(
            name=name,
            type=selected_type,
            description=description or None,
            is_active=is_active,
        )
        db.session.add(unit)
        db.session.commit()
        flash(f"Business unit '{unit.name}' created.", "success")
        return redirect(url_for("master_data.business_units_view", unit_id=unit.id))

    # GET: blank form (new units default to active).
    form = {"name": "", "type": "", "description": "", "is_active": True}
    return render_template(
        "master_data/business_units/form.html",
        form=form,
        types=list(BusinessUnitType),
        mode="create",
    )


@master_data_bp.route("/business-units/<int:unit_id>")
@role_required(*MANAGE_ROLES)
def business_units_view(unit_id):
    """View a single business unit."""
    unit = db.get_or_404(BusinessUnit, unit_id)
    return render_template("master_data/business_units/detail.html", unit=unit)


@master_data_bp.route(
    "/business-units/<int:unit_id>/edit", methods=["GET", "POST"]
)
@role_required(*MANAGE_ROLES)
def business_units_edit(unit_id):
    """Edit an existing business unit."""
    unit = db.get_or_404(BusinessUnit, unit_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        type_value = request.form.get("type", "").strip()
        description = request.form.get("description", "").strip()
        is_active = bool(request.form.get("is_active"))

        form = {
            "name": name,
            "type": type_value,
            "description": description,
            "is_active": is_active,
        }

        errors = []
        if not name:
            errors.append("Name is required.")
        if not type_value:
            errors.append("Type is required.")

        selected_type = None
        if type_value:
            try:
                selected_type = BusinessUnitType[type_value]
            except KeyError:
                errors.append("Please select a valid type.")

        if name and _business_unit_name_taken(name, exclude_id=unit.id):
            errors.append(f"A business unit named '{name}' already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/business_units/form.html",
                form=form,
                types=list(BusinessUnitType),
                mode="edit",
                unit=unit,
            )

        unit.name = name
        unit.type = selected_type
        unit.description = description or None
        unit.is_active = is_active
        db.session.commit()
        flash(f"Business unit '{unit.name}' updated.", "success")
        return redirect(url_for("master_data.business_units_view", unit_id=unit.id))

    # GET: pre-fill from the existing record.
    form = {
        "name": unit.name,
        "type": unit.type.name,
        "description": unit.description or "",
        "is_active": unit.is_active,
    }
    return render_template(
        "master_data/business_units/form.html",
        form=form,
        types=list(BusinessUnitType),
        mode="edit",
        unit=unit,
    )


@master_data_bp.route(
    "/business-units/<int:unit_id>/toggle-status", methods=["POST"]
)
@role_required(*MANAGE_ROLES)
def business_units_toggle_status(unit_id):
    """Activate / deactivate a business unit (no hard delete)."""
    unit = db.get_or_404(BusinessUnit, unit_id)
    unit.is_active = not unit.is_active
    db.session.commit()
    state = "activated" if unit.is_active else "deactivated"
    flash(f"Business unit '{unit.name}' {state}.", "info")
    return redirect(url_for("master_data.business_units_list"))


# --------------------------------------------------------------------------- #
# Petrol Pumps Console — one place to add a pump and set up its tanks & machines
# (replaces the separate "Petrol Pumps" list + "Pump Setup" screens).
# --------------------------------------------------------------------------- #
def _fuel_products_md():
    """Active fuel products (Petrol / Diesel / High Octane)."""
    return (
        Product.query.join(ProductCategory)
        .filter(ProductCategory.name == "Fuel Products", Product.is_active.is_(True))
        .order_by(Product.id).all()
    )


def _int_or_none_md(raw):
    raw = (raw or "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _pump_setup_context(pump):
    """A pump's tanks + machines, for the edit page's manage section."""
    from app.petrol_pumps.models import PumpTank, PumpMachine
    return {
        "pump_tanks": (PumpTank.query.filter_by(petrol_pump_id=pump.id)
                       .order_by(PumpTank.id).all()),
        "pump_machines": (PumpMachine.query.filter_by(petrol_pump_id=pump.id)
                          .order_by(PumpMachine.id).all()),
    }


@master_data_bp.route("/pumps-console")
@role_required(*MANAGE_ROLES)
def pumps_console():
    from app.petrol_pumps.models import PumpTank, PumpMachine, PumpNozzle  # noqa
    pumps = PetrolPump.query.order_by(PetrolPump.id).all()
    cards = []
    for p in pumps:
        tanks = (PumpTank.query.filter_by(petrol_pump_id=p.id, is_active=True)
                 .order_by(PumpTank.id).all())
        machines = (PumpMachine.query.filter_by(petrol_pump_id=p.id, is_active=True)
                    .order_by(PumpMachine.id).all())
        cards.append({"p": p, "tanks": tanks, "machines": machines})
    return render_template(
        "master_data/pumps_console.html",
        cards=cards, fuels=_fuel_products_md(),
        retail_unit=_active_retail_units()[0] if _active_retail_units() else None,
    )


@master_data_bp.route("/pumps-console/add-pump", methods=["POST"])
@role_required(*MANAGE_ROLES)
def pumps_console_add_pump():
    """Add a pump — asks only name + location; category is fixed to Pump Retail."""
    name = request.form.get("name", "").strip()
    location = request.form.get("location", "").strip()
    units = _active_retail_units()
    bu = units[0] if units else None

    errors = []
    if not name:
        errors.append("Pump name is required.")
    elif _petrol_pump_name_taken(name):
        errors.append(f"A petrol pump named '{name}' already exists.")
    if bu is None:
        errors.append("No active 'Petrol Pump Retail' business unit exists — create one first.")

    if errors:
        for m in errors:
            flash(m, "danger")
        return redirect(url_for("master_data.pumps_console"))

    pump = PetrolPump(business_unit=bu, name=name, location=location or None, is_active=True)
    db.session.add(pump)
    db.session.commit()
    flash(f"Petrol pump '{pump.name}' added. Now set up its tanks and machines below.", "success")
    return redirect(url_for("master_data.pumps_console", pump=pump.id))


@master_data_bp.route("/pumps-console/add-tank", methods=["POST"])
@role_required(*MANAGE_ROLES)
def pumps_console_add_tank():
    from app.petrol_pumps.models import PumpTank
    pump = db.session.get(PetrolPump, _int_or_none_md(request.form.get("pump_id")))
    product = db.session.get(Product, _int_or_none_md(request.form.get("product_id")))
    raw_cap = (request.form.get("capacity") or "").strip()

    errors = []
    if pump is None:
        errors.append("Invalid pump.")
    if product is None:
        errors.append("Select the tank's fuel (Petrol / Diesel / High Octane).")
    capacity = None
    if raw_cap:
        try:
            capacity = Decimal(raw_cap)
            if capacity < 0:
                errors.append("Capacity must not be negative.")
        except (InvalidOperation, ValueError):
            errors.append("Capacity must be a number.")
    if errors:
        for m in errors:
            flash(m, "danger")
        return redirect(url_for("master_data.pumps_console", pump=pump.id if pump else None))

    # Auto-name the tank by fuel, numbered if a same-fuel tank already exists.
    existing = PumpTank.query.filter_by(petrol_pump_id=pump.id, product_id=product.id).count()
    name = f"{product.name} Tank" + ("" if existing == 0 else f" {existing + 1}")
    db.session.add(PumpTank(
        petrol_pump_id=pump.id, product_id=product.id, tank_name=name,
        capacity_liters=capacity, opening_stock_liters=Decimal("0"),
        current_stock_liters=Decimal("0"), is_active=True,
    ))
    db.session.commit()
    flash(f"Tank added to {pump.name}: {name}"
          + (f" ({capacity} L capacity)." if capacity is not None else "."), "success")
    return redirect(url_for("master_data.pumps_console", pump=pump.id))


@master_data_bp.route("/pumps-console/add-machine", methods=["POST"])
@role_required(*MANAGE_ROLES)
def pumps_console_add_machine():
    """Add a machine wired to ONE tank; auto-creates its 2 nozzles (the tank's
    fuel). Sales from this machine decrement that tank."""
    from app.petrol_pumps.models import PumpMachine, PumpNozzle, PumpTank
    pump = db.session.get(PetrolPump, _int_or_none_md(request.form.get("pump_id")))
    tank = db.session.get(PumpTank, _int_or_none_md(request.form.get("tank_id")))

    errors = []
    if pump is None:
        errors.append("Invalid pump.")
    if tank is None or (pump is not None and tank.petrol_pump_id != pump.id):
        errors.append("Select a valid tank for this pump (add a tank first if there are none).")
    if errors:
        for m in errors:
            flash(m, "danger")
        return redirect(url_for("master_data.pumps_console", pump=pump.id if pump else None))

    product = tank.product  # machine fuel = its tank's fuel
    seq = PumpMachine.query.filter_by(petrol_pump_id=pump.id).count() + 1
    machine = PumpMachine(
        petrol_pump_id=pump.id, product_id=tank.product_id, tank_id=tank.id,
        machine_name=f"Machine {seq}", is_active=True,
    )
    db.session.add(machine)
    db.session.flush()
    # Every machine has 2 nozzles, both of the machine's fuel. Number them
    # sequentially per fuel across the pump (1st petrol machine → 1,2; 2nd
    # petrol machine → 3,4) so the Reading Console labels them p1,p2 / p3,p4…
    used = (
        db.session.query(db.func.count(PumpNozzle.id))
        .filter(PumpNozzle.petrol_pump_id == pump.id,
                PumpNozzle.product_id == product.id)
        .scalar()
    ) or 0
    for _ in (1, 2):
        used += 1
        db.session.add(PumpNozzle(
            petrol_pump_id=pump.id, machine_id=machine.id, product_id=product.id,
            nozzle_number=str(used), opening_reading=Decimal("0"),
            current_reading=Decimal("0"), is_active=True,
        ))
    db.session.commit()
    flash(f"{machine.machine_name} ({product.name}) added to {pump.name}, wired to "
          f"{tank.tank_name}, with 2 nozzles.", "success")
    return redirect(url_for("master_data.pumps_console", pump=pump.id))


# --------------------------------------------------------------------------- #
# Petrol Pumps (classic list/CRUD — kept as advanced/fallback)
# --------------------------------------------------------------------------- #
@master_data_bp.route("/petrol-pumps")
@role_required(*MANAGE_ROLES)
def petrol_pumps_list():
    """List petrol pumps, with optional search by name/location/manager."""
    q = request.args.get("q", "").strip()
    query = PetrolPump.query

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                PetrolPump.name.ilike(like),
                PetrolPump.location.ilike(like),
                PetrolPump.manager_name.ilike(like),
            )
        )

    pumps = query.order_by(PetrolPump.name).all()
    return render_template(
        "master_data/petrol_pumps/list.html", pumps=pumps, q=q
    )


def _validate_pump_form(form, exclude_id=None):
    """Shared validation for create/edit. Returns (errors, business_unit)."""
    errors = []

    name = form["name"]
    if not name:
        errors.append("Name is required.")
    elif _petrol_pump_name_taken(name, exclude_id=exclude_id):
        errors.append(f"A petrol pump named '{name}' already exists.")

    business_unit = None
    bu_id = form["business_unit_id"]
    if not bu_id:
        errors.append("Business unit is required.")
    else:
        business_unit = db.session.get(BusinessUnit, bu_id)
        if (
            business_unit is None
            or business_unit.type != BusinessUnitType.PETROL_PUMP_RETAIL
        ):
            errors.append(
                "Please select a valid Petrol Pump Retail business unit."
            )

    return errors, business_unit


def _read_pump_form():
    """Read pump fields from the submitted form into a plain dict."""
    raw_bu = request.form.get("business_unit_id", "").strip()
    try:
        bu_id = int(raw_bu) if raw_bu else None
    except ValueError:
        bu_id = None
    return {
        "name": request.form.get("name", "").strip(),
        "business_unit_id": bu_id,
        "location": request.form.get("location", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


@master_data_bp.route("/petrol-pumps/create", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def petrol_pumps_create():
    """Create a new petrol pump (only under a PETROL_PUMP_RETAIL unit)."""
    if request.method == "POST":
        form = _read_pump_form()
        errors, business_unit = _validate_pump_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/petrol_pumps/form.html",
                form=form,
                retail_units=_active_retail_units(),
                mode="create",
            )

        pump = PetrolPump(
            business_unit=business_unit,
            name=form["name"],
            location=form["location"] or None,
            is_active=form["is_active"],
        )
        db.session.add(pump)
        db.session.commit()
        flash(f"Petrol pump '{pump.name}' created.", "success")
        return redirect(url_for("master_data.petrol_pumps_view", pump_id=pump.id))

    form = {
        "name": "",
        "business_unit_id": None,
        "location": "",
        "pso_account_name": "",
        "cash_account_name": "",
        "bank_account_name": "",
        "manager_name": "",
        "is_active": True,
    }
    return render_template(
        "master_data/petrol_pumps/form.html",
        form=form,
        retail_units=_active_retail_units(),
        mode="create",
    )


@master_data_bp.route("/petrol-pumps/<int:pump_id>")
@role_required(*MANAGE_ROLES)
def petrol_pumps_view(pump_id):
    """View a single petrol pump."""
    pump = db.get_or_404(PetrolPump, pump_id)
    return render_template("master_data/petrol_pumps/detail.html", pump=pump)


@master_data_bp.route("/petrol-pumps/<int:pump_id>/edit", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def petrol_pumps_edit(pump_id):
    """Edit an existing petrol pump."""
    pump = db.get_or_404(PetrolPump, pump_id)

    if request.method == "POST":
        form = _read_pump_form()
        errors, business_unit = _validate_pump_form(form, exclude_id=pump.id)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/petrol_pumps/form.html",
                form=form,
                retail_units=_active_retail_units(),
                mode="edit",
                pump=pump,
                **_pump_setup_context(pump),
            )

        pump.business_unit = business_unit
        pump.name = form["name"]
        pump.location = form["location"] or None
        pump.is_active = form["is_active"]
        db.session.commit()
        flash(f"Petrol pump '{pump.name}' updated.", "success")
        return redirect(url_for("master_data.petrol_pumps_view", pump_id=pump.id))

    form = {
        "name": pump.name,
        "business_unit_id": pump.business_unit_id,
        "location": pump.location or "",
        "is_active": pump.is_active,
    }
    return render_template(
        "master_data/petrol_pumps/form.html",
        form=form,
        retail_units=_active_retail_units(),
        mode="edit",
        pump=pump,
        **_pump_setup_context(pump),
    )


@master_data_bp.route("/petrol-pumps/<int:pump_id>/toggle-status", methods=["POST"])
@role_required(*MANAGE_ROLES)
def petrol_pumps_toggle_status(pump_id):
    """Activate / deactivate a petrol pump (no hard delete)."""
    pump = db.get_or_404(PetrolPump, pump_id)
    pump.is_active = not pump.is_active
    db.session.commit()
    state = "activated" if pump.is_active else "deactivated"
    flash(f"Petrol pump '{pump.name}' {state}.", "info")
    return redirect(url_for("master_data.petrol_pumps_list"))


# --------------------------------------------------------------------------- #
# Product Categories
# --------------------------------------------------------------------------- #
def _category_name_taken(name, exclude_id=None):
    """True if another product category already uses this name (case-insensitive)."""
    query = ProductCategory.query.filter(
        db.func.lower(ProductCategory.name) == name.lower()
    )
    if exclude_id is not None:
        query = query.filter(ProductCategory.id != exclude_id)
    return db.session.query(query.exists()).scalar()


@master_data_bp.route("/product-categories")
@role_required(*MANAGE_ROLES)
def product_categories_list():
    """List product categories, with optional search by name."""
    q = request.args.get("q", "").strip()
    query = ProductCategory.query
    if q:
        query = query.filter(ProductCategory.name.ilike(f"%{q}%"))
    categories = query.order_by(ProductCategory.name).all()
    return render_template(
        "master_data/product_categories/list.html", categories=categories, q=q
    )


@master_data_bp.route("/product-categories/create", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def product_categories_create():
    """Create a new product category."""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        is_active = bool(request.form.get("is_active"))
        form = {"name": name, "description": description, "is_active": is_active}

        errors = []
        if not name:
            errors.append("Name is required.")
        elif _category_name_taken(name):
            errors.append(f"A product category named '{name}' already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/product_categories/form.html",
                form=form, mode="create",
            )

        category = ProductCategory(
            name=name, description=description or None, is_active=is_active
        )
        db.session.add(category)
        db.session.commit()
        flash(f"Product category '{category.name}' created.", "success")
        return redirect(
            url_for("master_data.product_categories_view", category_id=category.id)
        )

    form = {"name": "", "description": "", "is_active": True}
    return render_template(
        "master_data/product_categories/form.html", form=form, mode="create"
    )


@master_data_bp.route("/product-categories/<int:category_id>")
@role_required(*MANAGE_ROLES)
def product_categories_view(category_id):
    """View a product category and its related products."""
    category = db.get_or_404(ProductCategory, category_id)
    return render_template(
        "master_data/product_categories/detail.html", category=category
    )


@master_data_bp.route(
    "/product-categories/<int:category_id>/edit", methods=["GET", "POST"]
)
@role_required(*MANAGE_ROLES)
def product_categories_edit(category_id):
    """Edit an existing product category."""
    category = db.get_or_404(ProductCategory, category_id)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        is_active = bool(request.form.get("is_active"))
        form = {"name": name, "description": description, "is_active": is_active}

        errors = []
        if not name:
            errors.append("Name is required.")
        elif _category_name_taken(name, exclude_id=category.id):
            errors.append(f"A product category named '{name}' already exists.")

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/product_categories/form.html",
                form=form, mode="edit", category=category,
            )

        category.name = name
        category.description = description or None
        category.is_active = is_active
        db.session.commit()
        flash(f"Product category '{category.name}' updated.", "success")
        return redirect(
            url_for("master_data.product_categories_view", category_id=category.id)
        )

    form = {
        "name": category.name,
        "description": category.description or "",
        "is_active": category.is_active,
    }
    return render_template(
        "master_data/product_categories/form.html",
        form=form, mode="edit", category=category,
    )


@master_data_bp.route(
    "/product-categories/<int:category_id>/toggle-status", methods=["POST"]
)
@role_required(*MANAGE_ROLES)
def product_categories_toggle_status(category_id):
    """Activate / deactivate a product category (no hard delete)."""
    category = db.get_or_404(ProductCategory, category_id)
    category.is_active = not category.is_active
    db.session.commit()
    state = "activated" if category.is_active else "deactivated"
    flash(f"Product category '{category.name}' {state}.", "info")
    return redirect(url_for("master_data.product_categories_list"))


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
def _active_categories():
    """Active product categories for the product dropdowns."""
    return (
        ProductCategory.query.filter_by(is_active=True)
        .order_by(ProductCategory.name)
        .all()
    )


def _product_name_taken_in_category(name, category_id, exclude_id=None):
    """True if a product with this name already exists in the same category."""
    query = Product.query.filter(
        db.func.lower(Product.name) == name.lower(),
        Product.category_id == category_id,
    )
    if exclude_id is not None:
        query = query.filter(Product.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _read_product_form():
    """Read product fields from the submitted form into a plain dict."""
    raw_cat = request.form.get("category_id", "").strip()
    try:
        category_id = int(raw_cat) if raw_cat else None
    except ValueError:
        category_id = None
    return {
        "name": request.form.get("name", "").strip(),
        "category_id": category_id,
        "unit": request.form.get("unit", "").strip(),
        "default_purchase_rate": request.form.get("default_purchase_rate", "").strip(),
        "default_sale_rate": request.form.get("default_sale_rate", "").strip(),
        "linked_stock_account_name": request.form.get(
            "linked_stock_account_name", ""
        ).strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _parse_rate(raw_value, field_label, errors):
    """Parse an optional non-negative rate. Appends to errors on problems."""
    if raw_value == "":
        return None
    try:
        value = float(raw_value)
    except ValueError:
        errors.append(f"{field_label} must be a number.")
        return None
    if value < 0:
        errors.append(f"{field_label} must not be negative.")
        return None
    return value


def _validate_product_form(form, exclude_id=None):
    """Shared create/edit validation. Returns (errors, category, rates)."""
    errors = []

    name = form["name"]
    category_id = form["category_id"]

    if not name:
        errors.append("Name is required.")
    if not form["unit"]:
        errors.append("Unit is required.")
    elif form["unit"] not in PRODUCT_UNITS:
        errors.append("Please select a valid unit.")

    category = None
    if not category_id:
        errors.append("Product category is required.")
    else:
        category = db.session.get(ProductCategory, category_id)
        if category is None:
            errors.append("Please select a valid product category.")

    # Duplicate name only matters when we have both a name and a valid category.
    if name and category is not None and _product_name_taken_in_category(
        name, category.id, exclude_id=exclude_id
    ):
        errors.append(
            f"A product named '{name}' already exists in this category."
        )

    purchase_rate = _parse_rate(
        form["default_purchase_rate"], "Default purchase rate", errors
    )
    sale_rate = _parse_rate(
        form["default_sale_rate"], "Default sale rate", errors
    )

    return errors, category, (purchase_rate, sale_rate)


@master_data_bp.route("/products")
@role_required(*MANAGE_ROLES)
def products_list():
    """List products, with optional search by name/category/unit."""
    q = request.args.get("q", "").strip()
    query = Product.query.join(ProductCategory)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Product.name.ilike(like),
                Product.unit.ilike(like),
                ProductCategory.name.ilike(like),
            )
        )
    products = query.order_by(ProductCategory.name, Product.name).all()
    return render_template("master_data/products/list.html", products=products, q=q)


@master_data_bp.route("/products/create", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def products_create():
    """Create a new product."""
    if request.method == "POST":
        form = _read_product_form()
        errors, category, (purchase_rate, sale_rate) = _validate_product_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/products/form.html",
                form=form, categories=_active_categories(),
                units=PRODUCT_UNITS, mode="create",
            )

        product = Product(
            category=category,
            name=form["name"],
            unit=form["unit"],
            default_purchase_rate=purchase_rate,
            default_sale_rate=sale_rate,
            linked_stock_account_name=form["linked_stock_account_name"] or None,
            is_active=form["is_active"],
        )
        db.session.add(product)
        db.session.commit()
        flash(f"Product '{product.name}' created.", "success")
        return redirect(url_for("master_data.products_view", product_id=product.id))

    form = {
        "name": "", "category_id": None, "unit": "",
        "default_purchase_rate": "", "default_sale_rate": "",
        "linked_stock_account_name": "", "is_active": True,
    }
    return render_template(
        "master_data/products/form.html",
        form=form, categories=_active_categories(),
        units=PRODUCT_UNITS, mode="create",
    )


@master_data_bp.route("/products/<int:product_id>")
@role_required(*MANAGE_ROLES)
def products_view(product_id):
    """View a single product."""
    product = db.get_or_404(Product, product_id)
    return render_template("master_data/products/detail.html", product=product)


@master_data_bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def products_edit(product_id):
    """Edit an existing product."""
    product = db.get_or_404(Product, product_id)

    if request.method == "POST":
        form = _read_product_form()
        errors, category, (purchase_rate, sale_rate) = _validate_product_form(
            form, exclude_id=product.id
        )

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/products/form.html",
                form=form, categories=_active_categories(),
                units=PRODUCT_UNITS, mode="edit", product=product,
            )

        product.category = category
        product.name = form["name"]
        product.unit = form["unit"]
        product.default_purchase_rate = purchase_rate
        product.default_sale_rate = sale_rate
        product.linked_stock_account_name = (
            form["linked_stock_account_name"] or None
        )
        product.is_active = form["is_active"]
        db.session.commit()
        flash(f"Product '{product.name}' updated.", "success")
        return redirect(url_for("master_data.products_view", product_id=product.id))

    form = {
        "name": product.name,
        "category_id": product.category_id,
        "unit": product.unit,
        "default_purchase_rate": (
            "" if product.default_purchase_rate is None
            else str(product.default_purchase_rate)
        ),
        "default_sale_rate": (
            "" if product.default_sale_rate is None
            else str(product.default_sale_rate)
        ),
        "linked_stock_account_name": product.linked_stock_account_name or "",
        "is_active": product.is_active,
    }
    return render_template(
        "master_data/products/form.html",
        form=form, categories=_active_categories(),
        units=PRODUCT_UNITS, mode="edit", product=product,
    )


@master_data_bp.route("/products/<int:product_id>/toggle-status", methods=["POST"])
@role_required(*MANAGE_ROLES)
def products_toggle_status(product_id):
    """Activate / deactivate a product (no hard delete)."""
    product = db.get_or_404(Product, product_id)
    product.is_active = not product.is_active
    db.session.commit()
    state = "activated" if product.is_active else "deactivated"
    flash(f"Product '{product.name}' {state}.", "info")
    return redirect(url_for("master_data.products_list"))


# --------------------------------------------------------------------------- #
# Shared helpers for Customers & Vendors
# --------------------------------------------------------------------------- #
def _active_business_units():
    """All active business units (for customer/vendor dropdowns)."""
    return (
        BusinessUnit.query.filter_by(is_active=True)
        .order_by(BusinessUnit.name)
        .all()
    )


def _parse_amount(raw_value, field_label, errors, default=None):
    """Parse an optional non-negative money amount.

    Returns `default` when the field is left blank; otherwise the parsed value.
    Appends a friendly message to `errors` on invalid or negative input.
    """
    raw_value = (raw_value or "").strip()
    if raw_value == "":
        return default
    try:
        value = float(raw_value)
    except ValueError:
        errors.append(f"{field_label} must be a number.")
        return default
    if value < 0:
        errors.append(f"{field_label} must not be negative.")
        return default
    return value


def _read_business_unit_id():
    """Read business_unit_id from the form as an int (or None)."""
    raw = request.form.get("business_unit_id", "").strip()
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Customers
# --------------------------------------------------------------------------- #
def _customer_name_taken_in_unit(name, business_unit_id, exclude_id=None):
    """True if a customer with this name already exists in the same unit."""
    query = Customer.query.filter(
        db.func.lower(Customer.name) == name.lower(),
        Customer.business_unit_id == business_unit_id,
    )
    if exclude_id is not None:
        query = query.filter(Customer.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _read_customer_form():
    """Read customer fields from the submitted form into a plain dict."""
    return {
        "name": request.form.get("name", "").strip(),
        "business_unit_id": _read_business_unit_id(),
        "petrol_pump_id": _int_or_none_md(request.form.get("petrol_pump_id")),
        "business_type": request.form.get("business_type", "").strip(),
        "cnic_ntn": request.form.get("cnic_ntn", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip(),
        "address": request.form.get("address", "").strip(),
        "contact_person": request.form.get("contact_person", "").strip(),
        "opening_balance": request.form.get("opening_balance", "").strip(),
        "credit_limit": request.form.get("credit_limit", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _customer_pumps():
    return PetrolPump.query.filter_by(is_active=True).order_by(PetrolPump.name).all()


def _retail_unit_id():
    unit = BusinessUnit.query.filter_by(
        type=BusinessUnitType.PETROL_PUMP_RETAIL
    ).first()
    return unit.id if unit else None


def _validate_customer_form(form, exclude_id=None):
    """Shared customer validation. Returns (errors, unit, opening, credit)."""
    errors = []

    name = form["name"]
    business_unit_id = form["business_unit_id"]

    if not name:
        errors.append("Name is required.")

    business_unit = None
    if not business_unit_id:
        errors.append("Business unit is required.")
    else:
        business_unit = db.session.get(BusinessUnit, business_unit_id)
        if business_unit is None:
            errors.append("Please select a valid business unit.")

    if name and business_unit is not None and _customer_name_taken_in_unit(
        name, business_unit.id, exclude_id=exclude_id
    ):
        errors.append(
            f"A customer named '{name}' already exists in this business unit."
        )

    opening_balance = _parse_amount(
        form["opening_balance"], "Opening balance", errors, default=0
    )
    credit_limit = _parse_amount(
        form["credit_limit"], "Credit limit", errors, default=None
    )

    # When the customer is under Petrol Pump Retail, they MUST be tied to a pump
    # (so each pump has its own customers + ledger). Other units ignore the pump.
    petrol_pump = None
    if business_unit is not None:
        if business_unit.type == BusinessUnitType.PETROL_PUMP_RETAIL:
            if not form["petrol_pump_id"]:
                errors.append("Please choose which petrol pump this customer belongs to.")
            else:
                petrol_pump = db.session.get(PetrolPump, form["petrol_pump_id"])
                if petrol_pump is None or not petrol_pump.is_active:
                    errors.append("Please select a valid petrol pump.")

    return errors, business_unit, opening_balance, credit_limit, petrol_pump


@master_data_bp.route("/customers")
@role_required(*MANAGE_ROLES)
def customers_list():
    """List customers, with optional search by name/phone/type/business unit."""
    q = request.args.get("q", "").strip()
    query = Customer.query.join(BusinessUnit)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Customer.name.ilike(like),
                Customer.phone_number.ilike(like),
                Customer.business_type.ilike(like),
                BusinessUnit.name.ilike(like),
            )
        )
    customers = query.order_by(BusinessUnit.name, Customer.name).all()
    return render_template(
        "master_data/customers/list.html", customers=customers, q=q
    )


@master_data_bp.route("/customers/create", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def customers_create():
    """Create a new customer."""
    if request.method == "POST":
        form = _read_customer_form()
        errors, unit, opening, credit, pump = _validate_customer_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/customers/form.html",
                form=form, business_units=_active_business_units(), mode="create",
                pumps=_customer_pumps(), retail_unit_id=_retail_unit_id(),
            )

        customer = Customer(
            business_unit=unit,
            petrol_pump=pump,
            name=form["name"],
            business_type=form["business_type"] or None,
            cnic_ntn=form["cnic_ntn"] or None,
            phone_number=form["phone_number"] or None,
            address=form["address"] or None,
            contact_person=form["contact_person"] or None,
            opening_balance=opening,
            credit_limit=credit,
            is_active=form["is_active"],
        )
        db.session.add(customer)
        db.session.commit()
        flash(f"Customer '{customer.name}' created.", "success")
        return redirect(
            url_for("master_data.customers_view", customer_id=customer.id)
        )

    form = {
        "name": "", "business_unit_id": None, "petrol_pump_id": None,
        "business_type": "",
        "cnic_ntn": "", "phone_number": "", "address": "", "contact_person": "",
        "opening_balance": "0", "credit_limit": "", "is_active": True,
    }
    return render_template(
        "master_data/customers/form.html",
        form=form, business_units=_active_business_units(), mode="create",
        pumps=_customer_pumps(), retail_unit_id=_retail_unit_id(),
    )


@master_data_bp.route("/customers/<int:customer_id>")
@role_required(*MANAGE_ROLES)
def customers_view(customer_id):
    """View a single customer."""
    customer = db.get_or_404(Customer, customer_id)
    return render_template("master_data/customers/detail.html", customer=customer)


@master_data_bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def customers_edit(customer_id):
    """Edit an existing customer."""
    customer = db.get_or_404(Customer, customer_id)

    if request.method == "POST":
        form = _read_customer_form()
        errors, unit, opening, credit, pump = _validate_customer_form(
            form, exclude_id=customer.id
        )

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/customers/form.html",
                form=form, business_units=_active_business_units(),
                mode="edit", customer=customer,
                pumps=_customer_pumps(), retail_unit_id=_retail_unit_id(),
            )

        customer.business_unit = unit
        customer.petrol_pump = pump
        customer.name = form["name"]
        customer.business_type = form["business_type"] or None
        customer.cnic_ntn = form["cnic_ntn"] or None
        customer.phone_number = form["phone_number"] or None
        customer.address = form["address"] or None
        customer.contact_person = form["contact_person"] or None
        customer.opening_balance = opening
        customer.credit_limit = credit
        customer.is_active = form["is_active"]
        db.session.commit()
        flash(f"Customer '{customer.name}' updated.", "success")
        return redirect(
            url_for("master_data.customers_view", customer_id=customer.id)
        )

    form = {
        "name": customer.name,
        "business_unit_id": customer.business_unit_id,
        "petrol_pump_id": customer.petrol_pump_id,
        "business_type": customer.business_type or "",
        "cnic_ntn": customer.cnic_ntn or "",
        "phone_number": customer.phone_number or "",
        "address": customer.address or "",
        "contact_person": customer.contact_person or "",
        "opening_balance": str(customer.opening_balance),
        "credit_limit": (
            "" if customer.credit_limit is None else str(customer.credit_limit)
        ),
        "is_active": customer.is_active,
    }
    return render_template(
        "master_data/customers/form.html",
        form=form, business_units=_active_business_units(),
        mode="edit", customer=customer,
        pumps=_customer_pumps(), retail_unit_id=_retail_unit_id(),
    )


@master_data_bp.route("/customers/<int:customer_id>/toggle-status", methods=["POST"])
@role_required(*MANAGE_ROLES)
def customers_toggle_status(customer_id):
    """Activate / deactivate a customer (no hard delete)."""
    customer = db.get_or_404(Customer, customer_id)
    customer.is_active = not customer.is_active
    db.session.commit()
    state = "activated" if customer.is_active else "deactivated"
    flash(f"Customer '{customer.name}' {state}.", "info")
    return redirect(url_for("master_data.customers_list"))


def _customer_usage(customer_id):
    """Count transactions referencing a customer, by area (for delete guard)."""
    from app.accounting.models import CustomerReceipt
    from app.agency.models import AgencySale
    from app.bulk_sale.models import BulkSale
    from app.petrol_pumps.models import LubricantSale, PumpDailyClosing

    return {
        "bulk sales": BulkSale.query.filter_by(customer_id=customer_id).count(),
        "agency sales": AgencySale.query.filter_by(customer_id=customer_id).count(),
        "lubricant sales": LubricantSale.query.filter_by(customer_id=customer_id).count(),
        "daily-closing credit sales": PumpDailyClosing.query.filter_by(credit_customer_id=customer_id).count(),
        "receipts": CustomerReceipt.query.filter_by(customer_id=customer_id).count(),
    }


@master_data_bp.route("/customers/<int:customer_id>/delete", methods=["POST"])
@role_required(*MANAGE_ROLES)
def customers_delete(customer_id):
    """Permanently remove a customer — only if nothing references it.

    Master data is normally retired via Deactivate (soft delete) so historical
    transactions keep their links. Hard delete is allowed only when the customer
    has no dependent records; otherwise we refuse and point to Deactivate.
    """
    customer = db.get_or_404(Customer, customer_id)
    usage = _customer_usage(customer_id)
    blocking = {area: n for area, n in usage.items() if n}
    if blocking:
        detail = ", ".join(f"{n} {area}" for area, n in blocking.items())
        flash(
            f"Cannot delete '{customer.name}': it is used by {detail}. "
            "Deactivate it instead to keep history intact.",
            "danger",
        )
        return redirect(url_for("master_data.customers_view", customer_id=customer_id))

    name = customer.name
    db.session.delete(customer)
    db.session.commit()
    flash(f"Customer '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.customers_list"))


# --------------------------------------------------------------------------- #
# Vendors
# --------------------------------------------------------------------------- #
def _vendor_name_taken_in_unit(name, business_unit_id, exclude_id=None):
    """True if a vendor with this name already exists in the same unit."""
    query = Vendor.query.filter(
        db.func.lower(Vendor.name) == name.lower(),
        Vendor.business_unit_id == business_unit_id,
    )
    if exclude_id is not None:
        query = query.filter(Vendor.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _read_vendor_form():
    """Read vendor fields from the submitted form into a plain dict."""
    return {
        "name": request.form.get("name", "").strip(),
        "business_unit_id": _read_business_unit_id(),
        "vendor_type": request.form.get("vendor_type", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip(),
        "address": request.form.get("address", "").strip(),
        "contact_person": request.form.get("contact_person", "").strip(),
        "opening_balance": request.form.get("opening_balance", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_vendor_form(form, exclude_id=None):
    """Shared vendor validation. Returns (errors, unit, vendor_type, opening)."""
    errors = []

    name = form["name"]
    business_unit_id = form["business_unit_id"]
    type_value = form["vendor_type"]

    if not name:
        errors.append("Name is required.")

    business_unit = None
    if not business_unit_id:
        errors.append("Business unit is required.")
    else:
        business_unit = db.session.get(BusinessUnit, business_unit_id)
        if business_unit is None:
            errors.append("Please select a valid business unit.")

    vendor_type = None
    if not type_value:
        errors.append("Vendor type is required.")
    else:
        try:
            vendor_type = VendorType[type_value]
        except KeyError:
            errors.append("Please select a valid vendor type.")

    if name and business_unit is not None and _vendor_name_taken_in_unit(
        name, business_unit.id, exclude_id=exclude_id
    ):
        errors.append(
            f"A vendor named '{name}' already exists in this business unit."
        )

    opening_balance = _parse_amount(
        form["opening_balance"], "Opening balance", errors, default=0
    )

    return errors, business_unit, vendor_type, opening_balance


@master_data_bp.route("/vendors")
@role_required(*MANAGE_ROLES)
def vendors_list():
    """List vendors, with optional search by name/phone/type/business unit."""
    q = request.args.get("q", "").strip()
    query = Vendor.query.join(BusinessUnit)
    if q:
        like = f"%{q}%"
        matching_types = [
            t for t in VendorType if q.lower() in t.value.lower()
        ]
        conditions = [
            Vendor.name.ilike(like),
            Vendor.phone_number.ilike(like),
            BusinessUnit.name.ilike(like),
        ]
        if matching_types:
            conditions.append(Vendor.vendor_type.in_(matching_types))
        query = query.filter(db.or_(*conditions))
    vendors = query.order_by(BusinessUnit.name, Vendor.name).all()
    return render_template("master_data/vendors/list.html", vendors=vendors, q=q)


@master_data_bp.route("/vendors/create", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def vendors_create():
    """Create a new vendor."""
    if request.method == "POST":
        form = _read_vendor_form()
        errors, unit, vendor_type, opening = _validate_vendor_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/vendors/form.html",
                form=form, business_units=_active_business_units(),
                vendor_types=list(VendorType), mode="create",
            )

        vendor = Vendor(
            business_unit=unit,
            name=form["name"],
            vendor_type=vendor_type,
            phone_number=form["phone_number"] or None,
            address=form["address"] or None,
            contact_person=form["contact_person"] or None,
            opening_balance=opening,
            is_active=form["is_active"],
        )
        db.session.add(vendor)
        db.session.commit()
        flash(f"Vendor '{vendor.name}' created.", "success")
        return redirect(url_for("master_data.vendors_view", vendor_id=vendor.id))

    form = {
        "name": "", "business_unit_id": None, "vendor_type": "",
        "phone_number": "", "address": "", "contact_person": "",
        "opening_balance": "0", "is_active": True,
    }
    return render_template(
        "master_data/vendors/form.html",
        form=form, business_units=_active_business_units(),
        vendor_types=list(VendorType), mode="create",
    )


def _vendor_pso_cards(vendor):
    """PSO-card receipts owed to this (PSO) vendor, grouped per pump.

    Returns (pump_groups, pending_total, verified_total) where each group is
    {pump, entries, pending, verified}. Empty for non-PSO vendors."""
    from app.petrol_pumps.models import PsoCardPayment
    if vendor.vendor_type != VendorType.PSO:
        return [], Decimal("0"), Decimal("0")
    rows = (
        PsoCardPayment.query
        .filter(PsoCardPayment.vendor_id == vendor.id, PsoCardPayment.is_active.is_(True))
        .order_by(PsoCardPayment.payment_date.desc(), PsoCardPayment.id.desc())
        .all()
    )
    groups = {}
    pending_total = Decimal("0")
    verified_total = Decimal("0")
    for r in rows:
        g = groups.setdefault(r.petrol_pump_id, {
            "pump": r.petrol_pump, "entries": [],
            "pending": Decimal("0"), "verified": Decimal("0"),
        })
        g["entries"].append(r)
        if r.is_verified:
            g["verified"] += r.amount or Decimal("0")
            verified_total += r.amount or Decimal("0")
        else:
            g["pending"] += r.amount or Decimal("0")
            pending_total += r.amount or Decimal("0")
    pump_groups = sorted(groups.values(), key=lambda g: (g["pump"].name if g["pump"] else ""))
    return pump_groups, pending_total, verified_total


@master_data_bp.route("/vendors/<int:vendor_id>")
@role_required(*MANAGE_ROLES)
def vendors_view(vendor_id):
    """View a single vendor."""
    from app.accounting import ledger
    vendor = db.get_or_404(Vendor, vendor_id)
    pso_groups, pso_pending, pso_verified = _vendor_pso_cards(vendor)
    return render_template(
        "master_data/vendors/detail.html", vendor=vendor,
        payable=ledger.vendor_balance(vendor),
        pso_groups=pso_groups, pso_pending=pso_pending, pso_verified=pso_verified,
    )


@master_data_bp.route("/vendors/<int:vendor_id>/pso-card/<int:payment_id>/verify", methods=["POST"])
@role_required(*MANAGE_ROLES)
def vendors_pso_card_verify(vendor_id, payment_id):
    """Verify (or un-verify) a PSO card receipt. Verifying reduces the PSO
    payable; un-verifying puts it back to pending."""
    from datetime import datetime
    from flask_login import current_user
    from app.petrol_pumps.models import PsoCardPayment
    payment = db.get_or_404(PsoCardPayment, payment_id)
    if payment.vendor_id != vendor_id:
        flash("That PSO card entry does not belong to this vendor.", "danger")
        return redirect(url_for("master_data.vendors_view", vendor_id=vendor_id))
    if payment.is_verified:
        payment.is_verified = False
        payment.verified_by_id = None
        payment.verified_at = None
        flash(f"PSO card payment of {payment.amount} set back to pending.", "info")
    else:
        payment.is_verified = True
        payment.verified_by_id = current_user.id
        payment.verified_at = datetime.utcnow()
        flash(f"PSO card payment of {payment.amount} verified — PSO payable reduced.", "success")
    db.session.commit()
    return redirect(url_for("master_data.vendors_view", vendor_id=vendor_id))


@master_data_bp.route("/vendors/<int:vendor_id>/edit", methods=["GET", "POST"])
@role_required(*MANAGE_ROLES)
def vendors_edit(vendor_id):
    """Edit an existing vendor."""
    vendor = db.get_or_404(Vendor, vendor_id)

    if request.method == "POST":
        form = _read_vendor_form()
        errors, unit, vendor_type, opening = _validate_vendor_form(
            form, exclude_id=vendor.id
        )

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/vendors/form.html",
                form=form, business_units=_active_business_units(),
                vendor_types=list(VendorType), mode="edit", vendor=vendor,
            )

        vendor.business_unit = unit
        vendor.name = form["name"]
        vendor.vendor_type = vendor_type
        vendor.phone_number = form["phone_number"] or None
        vendor.address = form["address"] or None
        vendor.contact_person = form["contact_person"] or None
        vendor.opening_balance = opening
        vendor.is_active = form["is_active"]
        db.session.commit()
        flash(f"Vendor '{vendor.name}' updated.", "success")
        return redirect(url_for("master_data.vendors_view", vendor_id=vendor.id))

    form = {
        "name": vendor.name,
        "business_unit_id": vendor.business_unit_id,
        "vendor_type": vendor.vendor_type.name,
        "phone_number": vendor.phone_number or "",
        "address": vendor.address or "",
        "contact_person": vendor.contact_person or "",
        "opening_balance": str(vendor.opening_balance),
        "is_active": vendor.is_active,
    }
    return render_template(
        "master_data/vendors/form.html",
        form=form, business_units=_active_business_units(),
        vendor_types=list(VendorType), mode="edit", vendor=vendor,
    )


@master_data_bp.route("/vendors/<int:vendor_id>/toggle-status", methods=["POST"])
@role_required(*MANAGE_ROLES)
def vendors_toggle_status(vendor_id):
    """Activate / deactivate a vendor (no hard delete)."""
    vendor = db.get_or_404(Vendor, vendor_id)
    vendor.is_active = not vendor.is_active
    db.session.commit()
    state = "activated" if vendor.is_active else "deactivated"
    flash(f"Vendor '{vendor.name}' {state}.", "info")
    return redirect(url_for("master_data.vendors_list"))


def _vendor_usage(vendor_id):
    """Count transactions referencing a vendor, by area (for delete guard)."""
    from app.agency.models import AgencyPurchase
    from app.bulk_sale.models import BulkSale
    from app.carriage.models import CarriageTrip
    from app.head_office.models import HeadOfficeExpense, VendorPayment
    from app.petrol_pumps.models import PumpExpense, PumpPurchase

    return {
        "pump purchases": PumpPurchase.query.filter_by(vendor_id=vendor_id).count(),
        "pump expenses": PumpExpense.query.filter_by(vendor_id=vendor_id).count(),
        "bulk sales": BulkSale.query.filter_by(vendor_id=vendor_id).count(),
        "agency purchases": AgencyPurchase.query.filter_by(vendor_id=vendor_id).count(),
        "carriage trips": CarriageTrip.query.filter_by(rented_vehicle_vendor_id=vendor_id).count(),
        "head-office expenses": HeadOfficeExpense.query.filter_by(vendor_id=vendor_id).count(),
        "vendor payments": VendorPayment.query.filter_by(vendor_id=vendor_id).count(),
    }


@master_data_bp.route("/vendors/<int:vendor_id>/delete", methods=["POST"])
@role_required(*MANAGE_ROLES)
def vendors_delete(vendor_id):
    """Permanently remove a vendor — only if nothing references it.

    Like customers: hard delete only when there are no dependent transactions,
    otherwise refuse and point to Deactivate so history stays intact.
    """
    vendor = db.get_or_404(Vendor, vendor_id)
    usage = _vendor_usage(vendor_id)
    blocking = {area: n for area, n in usage.items() if n}
    if blocking:
        detail = ", ".join(f"{n} {area}" for area, n in blocking.items())
        flash(
            f"Cannot delete '{vendor.name}': it is used by {detail}. "
            "Deactivate it instead to keep history intact.",
            "danger",
        )
        return redirect(url_for("master_data.vendors_view", vendor_id=vendor_id))

    name = vendor.name
    db.session.delete(vendor)
    db.session.commit()
    flash(f"Vendor '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.vendors_list"))


# --------------------------------------------------------------------------- #
# Drivers
# --------------------------------------------------------------------------- #
def _read_driver_form():
    """Read driver fields from the submitted form into a plain dict."""
    return {
        "name": request.form.get("name", "").strip(),
        "cnic": request.form.get("cnic", "").strip(),
        "phone_number": request.form.get("phone_number", "").strip(),
        "address": request.form.get("address", "").strip(),
        "salary": request.form.get("salary", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_driver_form(form):
    """Shared driver validation. Returns (errors, salary)."""
    errors = []
    if not form["name"]:
        errors.append("Name is required.")
    salary = _parse_amount(form["salary"], "Salary", errors, default=None)
    return errors, salary


@master_data_bp.route("/drivers")
@role_required(*VEHICLE_DRIVER_ROLES)
def drivers_list():
    """List drivers, with optional search by name/CNIC/phone."""
    q = request.args.get("q", "").strip()
    query = Driver.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Driver.name.ilike(like),
                Driver.cnic.ilike(like),
                Driver.phone_number.ilike(like),
            )
        )
    drivers = query.order_by(Driver.name).all()
    return render_template("master_data/drivers/list.html", drivers=drivers, q=q)


@master_data_bp.route("/drivers/create", methods=["GET", "POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def drivers_create():
    """Create a new driver."""
    if request.method == "POST":
        form = _read_driver_form()
        errors, salary = _validate_driver_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/drivers/form.html", form=form, mode="create"
            )

        driver = Driver(
            name=form["name"],
            cnic=form["cnic"] or None,
            phone_number=form["phone_number"] or None,
            address=form["address"] or None,
            salary=salary,
            is_active=form["is_active"],
        )
        db.session.add(driver)
        db.session.commit()
        flash(f"Driver '{driver.name}' created.", "success")
        return redirect(url_for("master_data.drivers_view", driver_id=driver.id))

    form = {
        "name": "", "cnic": "", "phone_number": "", "address": "",
        "salary": "", "is_active": True,
    }
    return render_template("master_data/drivers/form.html", form=form, mode="create")


@master_data_bp.route("/drivers/<int:driver_id>")
@role_required(*VEHICLE_DRIVER_ROLES)
def drivers_view(driver_id):
    """View a single driver and the vehicles assigned to them."""
    driver = db.get_or_404(Driver, driver_id)
    return render_template("master_data/drivers/detail.html", driver=driver)


@master_data_bp.route("/drivers/<int:driver_id>/edit", methods=["GET", "POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def drivers_edit(driver_id):
    """Edit an existing driver."""
    driver = db.get_or_404(Driver, driver_id)

    if request.method == "POST":
        form = _read_driver_form()
        errors, salary = _validate_driver_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/drivers/form.html",
                form=form, mode="edit", driver=driver,
            )

        driver.name = form["name"]
        driver.cnic = form["cnic"] or None
        driver.phone_number = form["phone_number"] or None
        driver.address = form["address"] or None
        driver.salary = salary
        driver.is_active = form["is_active"]
        db.session.commit()
        flash(f"Driver '{driver.name}' updated.", "success")
        return redirect(url_for("master_data.drivers_view", driver_id=driver.id))

    form = {
        "name": driver.name,
        "cnic": driver.cnic or "",
        "phone_number": driver.phone_number or "",
        "address": driver.address or "",
        "salary": "" if driver.salary is None else str(driver.salary),
        "is_active": driver.is_active,
    }
    return render_template(
        "master_data/drivers/form.html", form=form, mode="edit", driver=driver
    )


@master_data_bp.route("/drivers/<int:driver_id>/toggle-status", methods=["POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def drivers_toggle_status(driver_id):
    """Activate / deactivate a driver (no hard delete)."""
    driver = db.get_or_404(Driver, driver_id)
    driver.is_active = not driver.is_active
    db.session.commit()
    state = "activated" if driver.is_active else "deactivated"
    flash(f"Driver '{driver.name}' {state}.", "info")
    return redirect(url_for("master_data.drivers_list"))


# --------------------------------------------------------------------------- #
# Vehicles
# --------------------------------------------------------------------------- #
def _active_drivers():
    """Active drivers for the vehicle's driver dropdown."""
    return Driver.query.filter_by(is_active=True).order_by(Driver.name).all()


def _vehicle_number_taken(vehicle_number, exclude_id=None):
    """True if another vehicle already uses this number (case-insensitive)."""
    query = Vehicle.query.filter(
        db.func.lower(Vehicle.vehicle_number) == vehicle_number.lower()
    )
    if exclude_id is not None:
        query = query.filter(Vehicle.id != exclude_id)
    return db.session.query(query.exists()).scalar()


def _read_vehicle_form():
    """Read vehicle fields from the submitted form into a plain dict."""
    raw_driver = request.form.get("driver_id", "").strip()
    try:
        driver_id = int(raw_driver) if raw_driver else None
    except ValueError:
        driver_id = None
    return {
        "vehicle_number": request.form.get("vehicle_number", "").strip(),
        "vehicle_type": request.form.get("vehicle_type", "").strip(),
        "capacity": request.form.get("capacity", "").strip(),
        "ownership_type": request.form.get("ownership_type", "").strip(),
        "owner_name": request.form.get("owner_name", "").strip(),
        "driver_id": driver_id,
        "documents_path": request.form.get("documents_path", "").strip(),
        "insurance_details": request.form.get("insurance_details", "").strip(),
        "fitness_permit_details": request.form.get(
            "fitness_permit_details", ""
        ).strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_vehicle_form(form, exclude_id=None):
    """Shared vehicle validation. Returns (errors, driver, capacity)."""
    errors = []

    number = form["vehicle_number"]
    if not number:
        errors.append("Vehicle number is required.")
    elif _vehicle_number_taken(number, exclude_id=exclude_id):
        errors.append(f"Vehicle number '{number}' already exists.")

    if not form["vehicle_type"]:
        errors.append("Vehicle type is required.")
    elif form["vehicle_type"] not in VEHICLE_TYPES:
        errors.append("Please select a valid vehicle type.")

    if not form["ownership_type"]:
        errors.append("Ownership type is required.")
    elif form["ownership_type"] not in OWNERSHIP_TYPES:
        errors.append("Please select a valid ownership type.")

    # Rented vehicles must record who they are rented from.
    if form["ownership_type"] == "Rented Vehicle" and not form["owner_name"]:
        errors.append("Owner name is required for a rented vehicle.")

    driver = None
    if form["driver_id"]:
        driver = db.session.get(Driver, form["driver_id"])
        if driver is None:
            errors.append("Please select a valid driver.")

    capacity = _parse_amount(form["capacity"], "Capacity", errors, default=None)

    return errors, driver, capacity


@master_data_bp.route("/vehicles")
@role_required(*VEHICLE_DRIVER_ROLES)
def vehicles_list():
    """List vehicles, with optional search by number/type/ownership/owner."""
    q = request.args.get("q", "").strip()
    query = Vehicle.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                Vehicle.vehicle_number.ilike(like),
                Vehicle.vehicle_type.ilike(like),
                Vehicle.ownership_type.ilike(like),
                Vehicle.owner_name.ilike(like),
            )
        )
    vehicles = query.order_by(Vehicle.vehicle_number).all()
    return render_template("master_data/vehicles/list.html", vehicles=vehicles, q=q)


@master_data_bp.route("/vehicles/create", methods=["GET", "POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def vehicles_create():
    """Create a new vehicle."""
    if request.method == "POST":
        form = _read_vehicle_form()
        errors, driver, capacity = _validate_vehicle_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/vehicles/form.html",
                form=form, drivers=_active_drivers(),
                vehicle_types=VEHICLE_TYPES, ownership_types=OWNERSHIP_TYPES,
                mode="create",
            )

        vehicle = Vehicle(
            vehicle_number=form["vehicle_number"],
            vehicle_type=form["vehicle_type"],
            capacity=capacity,
            ownership_type=form["ownership_type"],
            owner_name=form["owner_name"] or None,
            driver=driver,
            documents_path=form["documents_path"] or None,
            insurance_details=form["insurance_details"] or None,
            fitness_permit_details=form["fitness_permit_details"] or None,
            is_active=form["is_active"],
        )
        db.session.add(vehicle)
        db.session.commit()
        flash(f"Vehicle '{vehicle.vehicle_number}' created.", "success")
        return redirect(url_for("master_data.vehicles_view", vehicle_id=vehicle.id))

    form = {
        "vehicle_number": "", "vehicle_type": "", "capacity": "",
        "ownership_type": "", "owner_name": "", "driver_id": None,
        "documents_path": "", "insurance_details": "",
        "fitness_permit_details": "", "is_active": True,
    }
    return render_template(
        "master_data/vehicles/form.html",
        form=form, drivers=_active_drivers(),
        vehicle_types=VEHICLE_TYPES, ownership_types=OWNERSHIP_TYPES,
        mode="create",
    )


@master_data_bp.route("/vehicles/<int:vehicle_id>")
@role_required(*VEHICLE_DRIVER_ROLES)
def vehicles_view(vehicle_id):
    """View a single vehicle."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    return render_template("master_data/vehicles/detail.html", vehicle=vehicle)


@master_data_bp.route("/vehicles/<int:vehicle_id>/edit", methods=["GET", "POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def vehicles_edit(vehicle_id):
    """Edit an existing vehicle."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)

    if request.method == "POST":
        form = _read_vehicle_form()
        errors, driver, capacity = _validate_vehicle_form(
            form, exclude_id=vehicle.id
        )

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/vehicles/form.html",
                form=form, drivers=_active_drivers(),
                vehicle_types=VEHICLE_TYPES, ownership_types=OWNERSHIP_TYPES,
                mode="edit", vehicle=vehicle,
            )

        vehicle.vehicle_number = form["vehicle_number"]
        vehicle.vehicle_type = form["vehicle_type"]
        vehicle.capacity = capacity
        vehicle.ownership_type = form["ownership_type"]
        vehicle.owner_name = form["owner_name"] or None
        vehicle.driver = driver
        vehicle.documents_path = form["documents_path"] or None
        vehicle.insurance_details = form["insurance_details"] or None
        vehicle.fitness_permit_details = form["fitness_permit_details"] or None
        vehicle.is_active = form["is_active"]
        db.session.commit()
        flash(f"Vehicle '{vehicle.vehicle_number}' updated.", "success")
        return redirect(url_for("master_data.vehicles_view", vehicle_id=vehicle.id))

    form = {
        "vehicle_number": vehicle.vehicle_number,
        "vehicle_type": vehicle.vehicle_type,
        "capacity": "" if vehicle.capacity is None else str(vehicle.capacity),
        "ownership_type": vehicle.ownership_type,
        "owner_name": vehicle.owner_name or "",
        "driver_id": vehicle.driver_id,
        "documents_path": vehicle.documents_path or "",
        "insurance_details": vehicle.insurance_details or "",
        "fitness_permit_details": vehicle.fitness_permit_details or "",
        "is_active": vehicle.is_active,
    }
    return render_template(
        "master_data/vehicles/form.html",
        form=form, drivers=_active_drivers(),
        vehicle_types=VEHICLE_TYPES, ownership_types=OWNERSHIP_TYPES,
        mode="edit", vehicle=vehicle,
    )


@master_data_bp.route("/vehicles/<int:vehicle_id>/toggle-status", methods=["POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def vehicles_toggle_status(vehicle_id):
    """Activate / deactivate a vehicle (no hard delete)."""
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    vehicle.is_active = not vehicle.is_active
    db.session.commit()
    state = "activated" if vehicle.is_active else "deactivated"
    flash(f"Vehicle '{vehicle.vehicle_number}' {state}.", "info")
    return redirect(url_for("master_data.vehicles_list"))


# --------------------------------------------------------------------------- #
# Shared helper for scoped (business-unit-aware, NULL = global) name checks
# --------------------------------------------------------------------------- #
def _name_taken_in_scope(model, name, business_unit_id, exclude_id=None):
    """True if `name` is already used by `model` within the same scope.

    The "scope" is the business unit, where business_unit_id of None means the
    global scope. Comparison is case-insensitive.
    """
    query = model.query.filter(db.func.lower(model.name) == name.lower())
    if business_unit_id is None:
        query = query.filter(model.business_unit_id.is_(None))
    else:
        query = query.filter(model.business_unit_id == business_unit_id)
    if exclude_id is not None:
        query = query.filter(model.id != exclude_id)
    return db.session.query(query.exists()).scalar()


# --------------------------------------------------------------------------- #
# Cash / Bank / Wallet Accounts
# --------------------------------------------------------------------------- #
def _active_pumps_md():
    """Active petrol pumps (for the account 'belongs to pump' selector)."""
    return PetrolPump.query.filter_by(is_active=True).order_by(PetrolPump.name).all()


def _read_account_form():
    """Read cash/bank account fields from the submitted form."""
    return {
        "name": request.form.get("name", "").strip(),
        "business_unit_id": _read_business_unit_id(),
        "petrol_pump_id": _int_or_none_md(request.form.get("petrol_pump_id")),
        "account_type": request.form.get("account_type", "").strip(),
        "account_number": request.form.get("account_number", "").strip(),
        "bank_name": request.form.get("bank_name", "").strip(),
        "opening_balance": request.form.get("opening_balance", "").strip(),
        "current_balance": request.form.get("current_balance", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_account_form(form, exclude_id=None):
    """Shared account validation. Returns (errors, unit, pump, opening, current)."""
    errors = []

    name = form["name"]
    if not name:
        errors.append("Name is required.")

    if not form["account_type"]:
        errors.append("Account type is required.")
    elif form["account_type"] not in ACCOUNT_TYPES:
        errors.append("Please select a valid account type.")

    # Business unit is optional (None = global account), but must exist if given.
    business_unit = None
    if form["business_unit_id"] is not None:
        business_unit = db.session.get(BusinessUnit, form["business_unit_id"])
        if business_unit is None:
            errors.append("Please select a valid business unit.")

    # A pump-specific account (optional): the account belongs to this pump, so
    # pump screens only offer it. Auto-files it under the Petrol Pump Retail unit.
    pump = None
    if form["petrol_pump_id"] is not None:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    if name and _name_taken_in_scope(
        CashBankAccount, name, form["business_unit_id"], exclude_id=exclude_id
    ):
        scope = "this business unit" if form["business_unit_id"] else "global accounts"
        errors.append(f"An account named '{name}' already exists in {scope}.")

    opening = _parse_amount(form["opening_balance"], "Opening balance", errors, default=0)
    current = _parse_amount(form["current_balance"], "Current balance", errors, default=0)

    return errors, business_unit, pump, opening, current


@master_data_bp.route("/cash-bank-accounts")
@role_required(*ACCOUNT_ROLES)
def accounts_list():
    """List cash/bank accounts, with optional search."""
    q = request.args.get("q", "").strip()
    query = CashBankAccount.query.outerjoin(BusinessUnit)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                CashBankAccount.name.ilike(like),
                CashBankAccount.account_type.ilike(like),
                CashBankAccount.bank_name.ilike(like),
                BusinessUnit.name.ilike(like),
            )
        )
    accounts = query.order_by(CashBankAccount.name).all()
    return render_template(
        "master_data/cash_bank_accounts/list.html", accounts=accounts, q=q
    )


@master_data_bp.route("/cash-bank-accounts/create", methods=["GET", "POST"])
@role_required(*ACCOUNT_ROLES)
def accounts_create():
    """Create a new cash/bank account."""
    if request.method == "POST":
        form = _read_account_form()
        errors, unit, pump, opening, current = _validate_account_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/cash_bank_accounts/form.html",
                form=form, business_units=_active_business_units(),
                pumps=_active_pumps_md(), account_types=ACCOUNT_TYPES, mode="create",
            )

        # A pump-specific account is filed under the pump's business unit.
        if pump is not None:
            unit = pump.business_unit
        account = CashBankAccount(
            business_unit=unit,
            petrol_pump=pump,
            name=form["name"],
            account_type=form["account_type"],
            account_number=form["account_number"] or None,
            bank_name=form["bank_name"] or None,
            opening_balance=opening,
            current_balance=current,
            is_active=form["is_active"],
        )
        db.session.add(account)
        db.session.commit()
        flash(f"Account '{account.name}' created.", "success")
        return redirect(url_for("master_data.accounts_view", account_id=account.id))

    form = {
        "name": "", "business_unit_id": None, "petrol_pump_id": None, "account_type": "",
        "account_number": "", "bank_name": "",
        "opening_balance": "0", "current_balance": "0", "is_active": True,
    }
    return render_template(
        "master_data/cash_bank_accounts/form.html",
        form=form, business_units=_active_business_units(),
        pumps=_active_pumps_md(), account_types=ACCOUNT_TYPES, mode="create",
    )


@master_data_bp.route("/cash-bank-accounts/<int:account_id>")
@role_required(*ACCOUNT_ROLES)
def accounts_view(account_id):
    """View a single cash/bank account."""
    account = db.get_or_404(CashBankAccount, account_id)
    return render_template(
        "master_data/cash_bank_accounts/detail.html", account=account
    )


@master_data_bp.route(
    "/cash-bank-accounts/<int:account_id>/edit", methods=["GET", "POST"]
)
@role_required(*ACCOUNT_ROLES)
def accounts_edit(account_id):
    """Edit an existing cash/bank account."""
    account = db.get_or_404(CashBankAccount, account_id)

    if request.method == "POST":
        form = _read_account_form()
        errors, unit, pump, opening, current = _validate_account_form(
            form, exclude_id=account.id
        )

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/cash_bank_accounts/form.html",
                form=form, business_units=_active_business_units(),
                pumps=_active_pumps_md(), account_types=ACCOUNT_TYPES,
                mode="edit", account=account,
            )

        if pump is not None:
            unit = pump.business_unit
        account.business_unit = unit
        account.petrol_pump = pump
        account.name = form["name"]
        account.account_type = form["account_type"]
        account.account_number = form["account_number"] or None
        account.bank_name = form["bank_name"] or None
        account.opening_balance = opening
        account.current_balance = current
        account.is_active = form["is_active"]
        db.session.commit()
        flash(f"Account '{account.name}' updated.", "success")
        return redirect(url_for("master_data.accounts_view", account_id=account.id))

    form = {
        "name": account.name,
        "business_unit_id": account.business_unit_id,
        "petrol_pump_id": account.petrol_pump_id,
        "account_type": account.account_type,
        "account_number": account.account_number or "",
        "bank_name": account.bank_name or "",
        "opening_balance": str(account.opening_balance),
        "current_balance": str(account.current_balance),
        "is_active": account.is_active,
    }
    return render_template(
        "master_data/cash_bank_accounts/form.html",
        form=form, business_units=_active_business_units(),
        pumps=_active_pumps_md(), account_types=ACCOUNT_TYPES,
        mode="edit", account=account,
    )


@master_data_bp.route(
    "/cash-bank-accounts/<int:account_id>/toggle-status", methods=["POST"]
)
@role_required(*ACCOUNT_ROLES)
def accounts_toggle_status(account_id):
    """Activate / deactivate a cash/bank account (no hard delete)."""
    account = db.get_or_404(CashBankAccount, account_id)
    account.is_active = not account.is_active
    db.session.commit()
    state = "activated" if account.is_active else "deactivated"
    flash(f"Account '{account.name}' {state}.", "info")
    return redirect(url_for("master_data.accounts_list"))


# --------------------------------------------------------------------------- #
# Expense Categories
# --------------------------------------------------------------------------- #
def _read_expense_category_form():
    """Read expense category fields from the submitted form."""
    return {
        "name": request.form.get("name", "").strip(),
        "business_unit_id": _read_business_unit_id(),
        "description": request.form.get("description", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_expense_category_form(form, exclude_id=None):
    """Shared expense category validation. Returns (errors, unit)."""
    errors = []

    name = form["name"]
    if not name:
        errors.append("Name is required.")

    business_unit = None
    if form["business_unit_id"] is not None:
        business_unit = db.session.get(BusinessUnit, form["business_unit_id"])
        if business_unit is None:
            errors.append("Please select a valid business unit.")

    if name and _name_taken_in_scope(
        ExpenseCategory, name, form["business_unit_id"], exclude_id=exclude_id
    ):
        scope = "this business unit" if form["business_unit_id"] else "global categories"
        errors.append(f"An expense category named '{name}' already exists in {scope}.")

    return errors, business_unit


@master_data_bp.route("/expense-categories")
@role_required(*ACCOUNT_ROLES)
def expense_categories_list():
    """List expense categories, with optional search by name/business unit."""
    q = request.args.get("q", "").strip()
    query = ExpenseCategory.query.outerjoin(BusinessUnit)
    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                ExpenseCategory.name.ilike(like),
                BusinessUnit.name.ilike(like),
            )
        )
    categories = query.order_by(ExpenseCategory.name).all()
    return render_template(
        "master_data/expense_categories/list.html", categories=categories, q=q
    )


@master_data_bp.route("/expense-categories/create", methods=["GET", "POST"])
@role_required(*ACCOUNT_ROLES)
def expense_categories_create():
    """Create a new expense category."""
    if request.method == "POST":
        form = _read_expense_category_form()
        errors, unit = _validate_expense_category_form(form)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/expense_categories/form.html",
                form=form, business_units=_active_business_units(), mode="create",
            )

        category = ExpenseCategory(
            business_unit=unit,
            name=form["name"],
            description=form["description"] or None,
            is_active=form["is_active"],
        )
        db.session.add(category)
        db.session.commit()
        flash(f"Expense category '{category.name}' created.", "success")
        return redirect(
            url_for("master_data.expense_categories_view", category_id=category.id)
        )

    form = {
        "name": "", "business_unit_id": None, "description": "", "is_active": True,
    }
    return render_template(
        "master_data/expense_categories/form.html",
        form=form, business_units=_active_business_units(), mode="create",
    )


@master_data_bp.route("/expense-categories/<int:category_id>")
@role_required(*ACCOUNT_ROLES)
def expense_categories_view(category_id):
    """View a single expense category."""
    category = db.get_or_404(ExpenseCategory, category_id)
    return render_template(
        "master_data/expense_categories/detail.html", category=category
    )


@master_data_bp.route(
    "/expense-categories/<int:category_id>/edit", methods=["GET", "POST"]
)
@role_required(*ACCOUNT_ROLES)
def expense_categories_edit(category_id):
    """Edit an existing expense category."""
    category = db.get_or_404(ExpenseCategory, category_id)

    if request.method == "POST":
        form = _read_expense_category_form()
        errors, unit = _validate_expense_category_form(form, exclude_id=category.id)

        if errors:
            for message in errors:
                flash(message, "danger")
            return render_template(
                "master_data/expense_categories/form.html",
                form=form, business_units=_active_business_units(),
                mode="edit", category=category,
            )

        category.business_unit = unit
        category.name = form["name"]
        category.description = form["description"] or None
        category.is_active = form["is_active"]
        db.session.commit()
        flash(f"Expense category '{category.name}' updated.", "success")
        return redirect(
            url_for("master_data.expense_categories_view", category_id=category.id)
        )

    form = {
        "name": category.name,
        "business_unit_id": category.business_unit_id,
        "description": category.description or "",
        "is_active": category.is_active,
    }
    return render_template(
        "master_data/expense_categories/form.html",
        form=form, business_units=_active_business_units(),
        mode="edit", category=category,
    )


@master_data_bp.route(
    "/expense-categories/<int:category_id>/toggle-status", methods=["POST"]
)
@role_required(*ACCOUNT_ROLES)
def expense_categories_toggle_status(category_id):
    """Activate / deactivate an expense category (no hard delete)."""
    category = db.get_or_404(ExpenseCategory, category_id)
    category.is_active = not category.is_active
    db.session.commit()
    state = "activated" if category.is_active else "deactivated"
    flash(f"Expense category '{category.name}' {state}.", "info")
    return redirect(url_for("master_data.expense_categories_list"))


# --------------------------------------------------------------------------- #
# Hard-delete routes (guarded)
#
# Master data is normally retired via Deactivate so historical transactions keep
# their links. Hard delete is offered alongside every Deactivate button, but it
# is refused when dependent records exist (SQLite FKs are not enforced, so usage
# is counted explicitly) — the message then points the user to Deactivate.
# --------------------------------------------------------------------------- #
def _blocking(usage):
    return {area: n for area, n in usage.items() if n}


def _refuse(name, blocking, redirect_to):
    detail = ", ".join(f"{n} {area}" for area, n in blocking.items())
    flash(
        f"Cannot delete '{name}': it is used by {detail}. "
        "Deactivate it instead to keep history intact.",
        "danger",
    )
    return redirect(redirect_to)


@master_data_bp.route("/petrol-pumps/<int:pump_id>/delete", methods=["POST"])
@role_required(*MANAGE_ROLES)
def petrol_pumps_delete(pump_id):
    """Delete a pump and its setup (machines, nozzles, tanks, staff) — blocked if
    it has any transaction history."""
    from app.petrol_pumps.models import (
        MachineReading, PumpPurchase, PumpExpense, LubricantSale, PumpDailyClosing,
        StockMovement, StockAdjustment, PumpSalaryPayment, PumpAttendance,
        DailyChecklist, MaintenanceComplaint, PumpMachine, PumpNozzle, PumpTank,
        PumpStaff,
    )
    pump = db.get_or_404(PetrolPump, pump_id)
    usage = {
        "machine readings": MachineReading.query.filter_by(petrol_pump_id=pump_id).count(),
        "purchases": PumpPurchase.query.filter_by(petrol_pump_id=pump_id).count(),
        "expenses": PumpExpense.query.filter_by(petrol_pump_id=pump_id).count(),
        "lubricant sales": LubricantSale.query.filter_by(petrol_pump_id=pump_id).count(),
        "daily closings": PumpDailyClosing.query.filter_by(petrol_pump_id=pump_id).count(),
        "stock movements": StockMovement.query.filter_by(petrol_pump_id=pump_id).count(),
        "stock adjustments": StockAdjustment.query.filter_by(petrol_pump_id=pump_id).count(),
        "salary payments": PumpSalaryPayment.query.filter_by(petrol_pump_id=pump_id).count(),
        "attendance records": PumpAttendance.query.filter_by(petrol_pump_id=pump_id).count(),
        "checklists": DailyChecklist.query.filter_by(petrol_pump_id=pump_id).count(),
        "maintenance complaints": MaintenanceComplaint.query.filter_by(petrol_pump_id=pump_id).count(),
    }
    blocking = _blocking(usage)
    if blocking:
        return _refuse(pump.name, blocking, url_for("master_data.petrol_pumps_list"))
    name = pump.name
    PumpNozzle.query.filter_by(petrol_pump_id=pump_id).delete()
    PumpMachine.query.filter_by(petrol_pump_id=pump_id).delete()
    PumpTank.query.filter_by(petrol_pump_id=pump_id).delete()
    PumpStaff.query.filter_by(petrol_pump_id=pump_id).delete()
    db.session.delete(pump)
    db.session.commit()
    flash(f"Petrol pump '{name}' and its setup permanently deleted.", "success")
    return redirect(url_for("master_data.petrol_pumps_list"))


@master_data_bp.route("/products/<int:product_id>/delete", methods=["POST"])
@role_required(*MANAGE_ROLES)
def products_delete(product_id):
    """Delete a product — blocked if any pump setup or transaction references it."""
    from app.petrol_pumps.models import (
        PumpMachine, PumpNozzle, PumpTank, MachineReading, LubricantSale,
        PumpPurchaseItem, StockMovement, StockAdjustment,
    )
    from app.agency.models import AgencyPurchase, AgencySale
    from app.bulk_sale.models import BulkSale
    from app.carriage.models import CarriageTrip
    product = db.get_or_404(Product, product_id)
    usage = {
        "pump machines": PumpMachine.query.filter_by(product_id=product_id).count(),
        "pump nozzles": PumpNozzle.query.filter_by(product_id=product_id).count(),
        "pump tanks": PumpTank.query.filter_by(product_id=product_id).count(),
        "machine readings": MachineReading.query.filter_by(product_id=product_id).count(),
        "lubricant sales": LubricantSale.query.filter_by(product_id=product_id).count(),
        "purchase items": PumpPurchaseItem.query.filter_by(product_id=product_id).count(),
        "stock movements": StockMovement.query.filter_by(product_id=product_id).count(),
        "stock adjustments": StockAdjustment.query.filter_by(product_id=product_id).count(),
        "agency purchases": AgencyPurchase.query.filter_by(product_id=product_id).count(),
        "agency sales": AgencySale.query.filter_by(product_id=product_id).count(),
        "bulk sales": BulkSale.query.filter_by(product_id=product_id).count(),
        "carriage trips": CarriageTrip.query.filter_by(product_id=product_id).count(),
    }
    blocking = _blocking(usage)
    if blocking:
        return _refuse(product.name, blocking, url_for("master_data.products_view", product_id=product_id))
    name = product.name
    db.session.delete(product)
    db.session.commit()
    flash(f"Product '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.products_list"))


@master_data_bp.route("/product-categories/<int:category_id>/delete", methods=["POST"])
@role_required(*MANAGE_ROLES)
def product_categories_delete(category_id):
    """Delete a product category — blocked if any product belongs to it."""
    category = db.get_or_404(ProductCategory, category_id)
    blocking = _blocking({"products": Product.query.filter_by(category_id=category_id).count()})
    if blocking:
        return _refuse(category.name, blocking, url_for("master_data.product_categories_view", category_id=category_id))
    name = category.name
    db.session.delete(category)
    db.session.commit()
    flash(f"Product category '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.product_categories_list"))


@master_data_bp.route("/drivers/<int:driver_id>/delete", methods=["POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def drivers_delete(driver_id):
    """Delete a driver — blocked if any vehicle or carriage trip references it."""
    from app.carriage.models import CarriageTrip
    driver = db.get_or_404(Driver, driver_id)
    usage = {
        "vehicles": Vehicle.query.filter_by(driver_id=driver_id).count(),
        "carriage trips": CarriageTrip.query.filter_by(driver_id=driver_id).count(),
    }
    blocking = _blocking(usage)
    if blocking:
        return _refuse(driver.name, blocking, url_for("master_data.drivers_view", driver_id=driver_id))
    name = driver.name
    db.session.delete(driver)
    db.session.commit()
    flash(f"Driver '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.drivers_list"))


@master_data_bp.route("/vehicles/<int:vehicle_id>/delete", methods=["POST"])
@role_required(*VEHICLE_DRIVER_ROLES)
def vehicles_delete(vehicle_id):
    """Delete a vehicle — blocked if any carriage trip used it.

    (Pump purchases record the delivery vehicle as free-text `vehicle_number`,
    not a FK, so they never block a vehicle delete.)
    """
    from app.carriage.models import CarriageTrip
    vehicle = db.get_or_404(Vehicle, vehicle_id)
    usage = {
        "carriage trips": CarriageTrip.query.filter_by(vehicle_id=vehicle_id).count(),
    }
    blocking = _blocking(usage)
    if blocking:
        return _refuse(vehicle.vehicle_number, blocking, url_for("master_data.vehicles_view", vehicle_id=vehicle_id))
    label = vehicle.vehicle_number
    db.session.delete(vehicle)
    db.session.commit()
    flash(f"Vehicle '{label}' permanently deleted.", "success")
    return redirect(url_for("master_data.vehicles_list"))


@master_data_bp.route("/cash-bank-accounts/<int:account_id>/delete", methods=["POST"])
@role_required(*ACCOUNT_ROLES)
def accounts_delete(account_id):
    """Delete a cash/bank account — blocked if any transaction used it."""
    from app.petrol_pumps.models import PumpExpense, PumpSalaryPayment
    from app.head_office.models import (
        HeadOfficeCashReceipt, HeadOfficeExpense, VendorPayment, CashTransfer,
    )
    from app.carriage.models import CarriageTrip
    from app.accounting.models import CustomerReceipt
    account = db.get_or_404(CashBankAccount, account_id)
    usage = {
        "pump expenses": PumpExpense.query.filter_by(paid_from_account_id=account_id).count(),
        "salary payments": PumpSalaryPayment.query.filter_by(paid_from_account_id=account_id).count(),
        "cash receipts": HeadOfficeCashReceipt.query.filter_by(received_into_account_id=account_id).count(),
        "head-office expenses": HeadOfficeExpense.query.filter_by(paid_from_account_id=account_id).count(),
        "vendor payments": VendorPayment.query.filter_by(paid_from_account_id=account_id).count(),
        "carriage trips": CarriageTrip.query.filter_by(paid_from_account_id=account_id).count(),
        "customer receipts": CustomerReceipt.query.filter_by(received_into_account_id=account_id).count(),
        "cash transfers": CashTransfer.query.filter(
            (CashTransfer.from_account_id == account_id)
            | (CashTransfer.to_account_id == account_id)
        ).count(),
    }
    blocking = _blocking(usage)
    if blocking:
        return _refuse(account.name, blocking, url_for("master_data.accounts_view", account_id=account_id))
    name = account.name
    db.session.delete(account)
    db.session.commit()
    flash(f"Account '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.accounts_list"))


@master_data_bp.route("/expense-categories/<int:category_id>/delete", methods=["POST"])
@role_required(*ACCOUNT_ROLES)
def expense_categories_delete(category_id):
    """Delete an expense category — blocked if any expense uses it."""
    from app.petrol_pumps.models import PumpExpense
    from app.head_office.models import HeadOfficeExpense
    category = db.get_or_404(ExpenseCategory, category_id)
    usage = {
        "pump expenses": PumpExpense.query.filter_by(expense_category_id=category_id).count(),
        "head-office expenses": HeadOfficeExpense.query.filter_by(expense_category_id=category_id).count(),
    }
    blocking = _blocking(usage)
    if blocking:
        return _refuse(category.name, blocking, url_for("master_data.expense_categories_view", category_id=category_id))
    name = category.name
    db.session.delete(category)
    db.session.commit()
    flash(f"Expense category '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.expense_categories_list"))


@master_data_bp.route("/business-units/<int:unit_id>/delete", methods=["POST"])
@role_required(*MANAGE_ROLES)
def business_units_delete(unit_id):
    """Delete a business unit — blocked if anything is filed under it."""
    from app.accounting.models import ChartOfAccount, Voucher, JournalEntry
    unit = db.get_or_404(BusinessUnit, unit_id)
    usage = {
        "petrol pumps": PetrolPump.query.filter_by(business_unit_id=unit_id).count(),
        "customers": Customer.query.filter_by(business_unit_id=unit_id).count(),
        "vendors": Vendor.query.filter_by(business_unit_id=unit_id).count(),
        "accounts": CashBankAccount.query.filter_by(business_unit_id=unit_id).count(),
        "expense categories": ExpenseCategory.query.filter_by(business_unit_id=unit_id).count(),
        "chart accounts": ChartOfAccount.query.filter_by(business_unit_id=unit_id).count(),
        "vouchers": Voucher.query.filter_by(business_unit_id=unit_id).count(),
        "journal entries": JournalEntry.query.filter_by(business_unit_id=unit_id).count(),
    }
    blocking = _blocking(usage)
    if blocking:
        return _refuse(unit.name, blocking, url_for("master_data.business_units_view", unit_id=unit_id))
    name = unit.name
    db.session.delete(unit)
    db.session.commit()
    flash(f"Business unit '{name}' permanently deleted.", "success")
    return redirect(url_for("master_data.business_units_list"))
