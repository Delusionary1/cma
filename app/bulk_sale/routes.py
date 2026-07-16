"""Bulk Sale routes: dashboard + pump-name-based bulk fuel sales.

Accessible by the bulk_sale module roles (Admin / Owner, Head Office Manager).
This module is completely separate from petrol pump retail: it never touches
machine readings, retail daily sale or pump tank stock.
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
    Customer,
    PetrolPump,
    Product,
    ProductCategory,
    Vendor,
    VendorType,
)
from app.bulk_sale.models import (
    BULK_DELIVERY_METHODS,
    BULK_DELIVERY_STATUSES,
    BULK_PAYMENT_METHODS,
    BULK_PAYMENT_STATUSES,
    BulkSale,
    CARRIAGE_PAID_BY_OPTIONS,
)

bulk_sale_bp = Blueprint("bulk_sale", __name__)

BULK_ROLES = roles_for_module("bulk_sale")

from app.bulk_sale import reports as bulk_reports_mod  # noqa: E402
from app.reports.exporters import VALID_FORMATS, export_response, fmt  # noqa: E402


def _bs_date_range():
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


@bulk_sale_bp.route("/reports")
@role_required(*BULK_ROLES)
def reports():
    """Customer-wise, product-wise and pump-wise bulk sale reports (§13.2)."""
    date_from, date_to, filters = _bs_date_range()
    data = bulk_reports_mod.bulk_reports(date_from, date_to)

    export = request.args.get("export", "").strip().lower()
    if export in VALID_FORMATS:
        def block(title, grp, key):
            rows = [[r["label"], str(r["deals"]), fmt(r["qty"]), fmt(r["sale"]),
                     fmt(r["net"]), fmt(r["outstanding"])] for r in grp["rows"]]
            t = grp["totals"]
            rows.append(["TOTAL", str(t["deals"]), fmt(t["qty"]), fmt(t["sale"]),
                         fmt(t["net"]), fmt(t["outstanding"])])
            return {"title": title,
                    "headers": [key, "Deals", "Liters", "Sale", "Net", "Outstanding"],
                    "rows": rows}
        blocks = [
            block("Customer-wise Bulk Sale", data["customers"], "Customer"),
            block("Product-wise Bulk Sale", data["products"], "Product"),
            block("Pump-wise Bulk Sale", data["pumps"], "Pump"),
        ]
        return export_response(export, "bulk_sale_reports", "Bulk Sale Reports", blocks)

    return render_template("bulk_sale/reports.html", data=data, filters=filters)
FUEL_CATEGORY_NAME = "Fuel Products"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _active_pumps():
    return PetrolPump.query.filter_by(is_active=True).order_by(PetrolPump.name).all()


def _active_customers():
    return Customer.query.filter_by(is_active=True).order_by(Customer.name).all()


def _active_vendors():
    return Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()


def _fuel_products():
    return (
        Product.query.join(ProductCategory)
        .filter(ProductCategory.name == FUEL_CATEGORY_NAME, Product.is_active.is_(True))
        .order_by(Product.name)
        .all()
    )


def _is_fuel_product(product):
    return (
        product is not None
        and product.category is not None
        and product.category.name == FUEL_CATEGORY_NAME
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


def _form_choices():
    return {
        "pumps": _active_pumps(),
        "customers": _active_customers(),
        "vendors": _active_vendors(),
        "products": _fuel_products(),
        "delivery_methods": BULK_DELIVERY_METHODS,
        "carriage_paid_by_options": CARRIAGE_PAID_BY_OPTIONS,
        "payment_methods": BULK_PAYMENT_METHODS,
        "payment_statuses": BULK_PAYMENT_STATUSES,
        "delivery_statuses": BULK_DELIVERY_STATUSES,
    }


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@bulk_sale_bp.route("/")
@role_required(*BULK_ROLES)
def index():
    row = (
        db.session.query(
            db.func.coalesce(db.func.sum(BulkSale.total_sale_amount), 0),
            db.func.coalesce(db.func.sum(BulkSale.total_purchase_amount), 0),
            db.func.coalesce(db.func.sum(BulkSale.net_profit), 0),
            db.func.coalesce(db.func.sum(BulkSale.quantity_liters), 0),
        )
        .filter(BulkSale.is_active.is_(True))
        .one()
    )
    totals = {
        "count": BulkSale.query.count(),
        "sale": Decimal(str(row[0])),
        "purchase": Decimal(str(row[1])),
        "profit": Decimal(str(row[2])),
        "liters": Decimal(str(row[3])),
    }
    return render_template("bulk_sale/index.html", totals=totals)


# --------------------------------------------------------------------------- #
# Bulk Sales CRUD
# --------------------------------------------------------------------------- #
@bulk_sale_bp.route("/sales")
@role_required(*BULK_ROLES)
def sales_list():
    f_date = request.args.get("date", "").strip()
    f_pump = _int_or_none(request.args.get("petrol_pump_id"))
    f_customer = _int_or_none(request.args.get("customer_id"))
    f_product = _int_or_none(request.args.get("product_id"))
    f_payment = request.args.get("payment_status", "").strip()
    f_delivery = request.args.get("delivery_status", "").strip()

    query = BulkSale.query
    if f_date:
        try:
            query = query.filter(BulkSale.sale_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_pump:
        query = query.filter(BulkSale.petrol_pump_id == f_pump)
    if f_customer:
        query = query.filter(BulkSale.customer_id == f_customer)
    if f_product:
        query = query.filter(BulkSale.product_id == f_product)
    if f_payment:
        query = query.filter(BulkSale.payment_status == f_payment)
    if f_delivery:
        query = query.filter(BulkSale.delivery_status == f_delivery)

    sales = query.order_by(BulkSale.sale_date.desc(), BulkSale.id.desc()).all()
    return render_template(
        "bulk_sale/sales/list.html",
        sales=sales,
        filters={"date": f_date, "petrol_pump_id": f_pump, "customer_id": f_customer,
                 "product_id": f_product, "payment_status": f_payment, "delivery_status": f_delivery},
        **_form_choices(),
    )


def _read_form():
    return {
        "petrol_pump_id": _int_or_none(request.form.get("petrol_pump_id")),
        "customer_id": _int_or_none(request.form.get("customer_id")),
        "vendor_id": _int_or_none(request.form.get("vendor_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "sale_date": request.form.get("sale_date", "").strip(),
        "quantity_liters": request.form.get("quantity_liters", "").strip(),
        "pso_invoice_number": request.form.get("pso_invoice_number", "").strip(),
        "purchase_rate": request.form.get("purchase_rate", "").strip(),
        "sale_rate": request.form.get("sale_rate", "").strip(),
        "carriage_charges": request.form.get("carriage_charges", "").strip(),
        "carriage_paid_by": request.form.get("carriage_paid_by", "").strip(),
        "other_expense": request.form.get("other_expense", "").strip(),
        "vehicle_number": request.form.get("vehicle_number", "").strip(),
        "driver_name": request.form.get("driver_name", "").strip(),
        "delivery_location": request.form.get("delivery_location", "").strip(),
        "delivery_method": request.form.get("delivery_method", "").strip(),
        "payment_method": request.form.get("payment_method", "").strip(),
        "payment_status": request.form.get("payment_status", "").strip(),
        "delivery_status": request.form.get("delivery_status", "").strip(),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate(form):
    errors = []

    pump = None
    if form["petrol_pump_id"] is None:
        errors.append("Petrol pump (pump name) is required.")
    else:
        pump = db.session.get(PetrolPump, form["petrol_pump_id"])
        if pump is None:
            errors.append("Please select a valid petrol pump.")

    customer = None
    if form["customer_id"] is None:
        errors.append("Customer is required.")
    else:
        customer = db.session.get(Customer, form["customer_id"])
        if customer is None:
            errors.append("Please select a valid customer.")

    vendor = None
    if form["vendor_id"] is None:
        errors.append("Vendor is required.")
    else:
        vendor = db.session.get(Vendor, form["vendor_id"])
        if vendor is None:
            errors.append("Please select a valid vendor.")

    product = None
    if form["product_id"] is None:
        errors.append("Product is required.")
    else:
        product = db.session.get(Product, form["product_id"])
        if product is None:
            errors.append("Please select a valid product.")
        elif not _is_fuel_product(product):
            errors.append("Bulk sale product must be a fuel product (Petrol, Diesel, High Octane).")

    sale_date = None
    if not form["sale_date"]:
        errors.append("Sale date is required.")
    else:
        try:
            sale_date = datetime.strptime(form["sale_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid sale date.")

    quantity = _parse_nonneg(form["quantity_liters"], "Quantity", errors, default=None)
    if quantity is None and form["quantity_liters"] == "":
        errors.append("Quantity is required.")
    elif quantity is not None and quantity <= 0:
        errors.append("Quantity must be greater than zero.")

    purchase_rate = _parse_nonneg(form["purchase_rate"], "Purchase rate", errors)
    sale_rate = _parse_nonneg(form["sale_rate"], "Sale rate", errors)
    carriage = _parse_nonneg(form["carriage_charges"], "Carriage charges", errors)
    other = _parse_nonneg(form["other_expense"], "Other expense", errors)

    for field, options, label in [
        ("delivery_method", BULK_DELIVERY_METHODS, "delivery method"),
        ("carriage_paid_by", CARRIAGE_PAID_BY_OPTIONS, "carriage paid by"),
        ("payment_method", BULK_PAYMENT_METHODS, "payment method"),
    ]:
        if form[field] and form[field] not in options:
            errors.append(f"Please select a valid {label}.")

    if not form["payment_status"]:
        errors.append("Payment status is required.")
    elif form["payment_status"] not in BULK_PAYMENT_STATUSES:
        errors.append("Please select a valid payment status.")

    if not form["delivery_status"]:
        errors.append("Delivery status is required.")
    elif form["delivery_status"] not in BULK_DELIVERY_STATUSES:
        errors.append("Please select a valid delivery status.")

    return errors, {
        "pump": pump, "customer": customer, "vendor": vendor, "product": product,
        "sale_date": sale_date, "quantity": quantity, "purchase_rate": purchase_rate,
        "sale_rate": sale_rate, "carriage": carriage, "other": other,
    }


def _apply(sale, form, ctx):
    sale.petrol_pump = ctx["pump"]
    sale.customer = ctx["customer"]
    sale.vendor = ctx["vendor"]
    sale.product = ctx["product"]
    sale.sale_date = ctx["sale_date"]
    sale.quantity_liters = ctx["quantity"]
    sale.pso_invoice_number = form["pso_invoice_number"] or None
    sale.purchase_rate = ctx["purchase_rate"]
    sale.sale_rate = ctx["sale_rate"]
    sale.total_purchase_amount = ctx["quantity"] * ctx["purchase_rate"]
    sale.total_sale_amount = ctx["quantity"] * ctx["sale_rate"]
    sale.carriage_charges = ctx["carriage"]
    sale.carriage_paid_by = form["carriage_paid_by"] or None
    sale.other_expense = ctx["other"]
    sale.net_profit = (
        sale.total_sale_amount - sale.total_purchase_amount
        - ctx["carriage"] - ctx["other"]
    )
    sale.vehicle_number = form["vehicle_number"] or None
    sale.driver_name = form["driver_name"] or None
    sale.delivery_location = form["delivery_location"] or None
    sale.delivery_method = form["delivery_method"] or None
    sale.payment_method = form["payment_method"] or None
    sale.payment_status = form["payment_status"]
    sale.delivery_status = form["delivery_status"]
    sale.notes = form["notes"] or None
    sale.is_active = form["is_active"]


@bulk_sale_bp.route("/sales/create", methods=["GET", "POST"])
@role_required(*BULK_ROLES)
def sales_create():
    if request.method == "POST":
        form = _read_form()
        errors, ctx = _validate(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("bulk_sale/sales/form.html", form=form, mode="create", **_form_choices())
        if ctx["vendor"] is not None and ctx["vendor"].vendor_type != VendorType.PSO:
            flash("Note: the selected vendor is not PSO.", "warning")

        sale = BulkSale(created_by_id=current_user.id)
        _apply(sale, form, ctx)
        db.session.add(sale)
        db.session.flush()
        approvals.request_if_needed("bulk_sale", sale.id, sale.total_sale_amount,
                                    sale.created_by_id,
                                    title=f"{ctx['customer'].name if ctx['customer'] else ''} — {sale.total_sale_amount}")
        posting.sync_bulk_sale(sale)  # held out of GL until approved (BRD §16)
        db.session.commit()
        flash(f"Bulk sale saved. Net profit {sale.net_profit}.", "success")
        return redirect(url_for("bulk_sale.sales_view", sale_id=sale.id))

    form = {
        "petrol_pump_id": None, "customer_id": None, "vendor_id": None, "product_id": None,
        "sale_date": date.today().isoformat(), "quantity_liters": "",
        "pso_invoice_number": "", "purchase_rate": "", "sale_rate": "",
        "carriage_charges": "0", "carriage_paid_by": "Company", "other_expense": "0",
        "vehicle_number": "", "driver_name": "", "delivery_location": "",
        "delivery_method": "Own Vehicle", "payment_method": "Cash",
        "payment_status": "Unpaid", "delivery_status": "Pending",
        "notes": "", "is_active": True,
    }
    return render_template("bulk_sale/sales/form.html", form=form, mode="create", **_form_choices())


@bulk_sale_bp.route("/sales/<int:sale_id>")
@role_required(*BULK_ROLES)
def sales_view(sale_id):
    sale = db.get_or_404(BulkSale, sale_id)
    return render_template("bulk_sale/sales/detail.html", sale=sale)


@bulk_sale_bp.route("/sales/<int:sale_id>/edit", methods=["GET", "POST"])
@role_required(*BULK_ROLES)
def sales_edit(sale_id):
    sale = db.get_or_404(BulkSale, sale_id)
    if request.method == "POST":
        form = _read_form()
        errors, ctx = _validate(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("bulk_sale/sales/form.html", form=form, mode="edit", sale=sale, **_form_choices())
        if ctx["vendor"] is not None and ctx["vendor"].vendor_type != VendorType.PSO:
            flash("Note: the selected vendor is not PSO.", "warning")

        _apply(sale, form, ctx)
        approvals.request_if_needed("bulk_sale", sale.id, sale.total_sale_amount,
                                    sale.created_by_id,
                                    title=f"{ctx['customer'].name if ctx['customer'] else ''} — {sale.total_sale_amount}")
        posting.sync_bulk_sale(sale)  # held out of GL until approved (BRD §16)
        db.session.commit()
        flash(f"Bulk sale updated. Net profit {sale.net_profit}.", "success")
        return redirect(url_for("bulk_sale.sales_view", sale_id=sale.id))

    form = {
        "petrol_pump_id": sale.petrol_pump_id, "customer_id": sale.customer_id,
        "vendor_id": sale.vendor_id, "product_id": sale.product_id,
        "sale_date": sale.sale_date.isoformat(), "quantity_liters": str(sale.quantity_liters),
        "pso_invoice_number": sale.pso_invoice_number or "",
        "purchase_rate": str(sale.purchase_rate), "sale_rate": str(sale.sale_rate),
        "carriage_charges": str(sale.carriage_charges),
        "carriage_paid_by": sale.carriage_paid_by or "Company",
        "other_expense": str(sale.other_expense),
        "vehicle_number": sale.vehicle_number or "", "driver_name": sale.driver_name or "",
        "delivery_location": sale.delivery_location or "",
        "delivery_method": sale.delivery_method or "Own Vehicle",
        "payment_method": sale.payment_method or "Cash",
        "payment_status": sale.payment_status, "delivery_status": sale.delivery_status,
        "notes": sale.notes or "", "is_active": sale.is_active,
    }
    return render_template("bulk_sale/sales/form.html", form=form, mode="edit", sale=sale, **_form_choices())


@bulk_sale_bp.route("/sales/<int:sale_id>/toggle-status", methods=["POST"])
@role_required(*BULK_ROLES)
def sales_toggle_status(sale_id):
    sale = db.get_or_404(BulkSale, sale_id)
    sale.is_active = not sale.is_active
    posting.sync_bulk_sale(sale)
    db.session.commit()
    state = "activated" if sale.is_active else "deactivated"
    flash(f"Bulk sale {state}.", "info")
    return redirect(url_for("bulk_sale.sales_list"))


@bulk_sale_bp.route("/sales/<int:sale_id>/delete", methods=["POST"])
@role_required(*BULK_ROLES)
def sales_delete(sale_id):
    from app.approvals.models import Approval
    from app.attachments.models import Attachment

    sale = db.get_or_404(BulkSale, sale_id)
    sale.is_active = False
    posting.sync_bulk_sale(sale)  # reverse GL before removing
    Approval.query.filter_by(entity_type="bulk_sale", entity_id=sale.id).delete()
    Attachment.query.filter_by(entity_type="bulk_sale", entity_id=sale.id).delete()
    db.session.delete(sale)
    db.session.commit()
    flash("Bulk sale permanently deleted.", "success")
    return redirect(url_for("bulk_sale.sales_list"))
