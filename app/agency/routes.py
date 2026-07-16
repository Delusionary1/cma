"""Oil Agencies routes: dashboard + agency purchases and sales.

Accessible by the agency module roles (Admin / Owner, Head Office Manager).
Completely separate from the petrol pump business. Products must be agency
products (LDO, Kerosene).
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
from app.core.models import Customer, Product, ProductCategory, Vendor
from app.agency.models import (
    AGENCY_DELIVERY_METHODS,
    AGENCY_PAYMENT_METHODS,
    AGENCY_PAYMENT_STATUSES,
    AgencyPurchase,
    AgencySale,
)

agency_bp = Blueprint("agency", __name__)

AGENCY_ROLES = roles_for_module("agency")

from app.agency import reports as agency_reports_mod  # noqa: E402
from app.reports.exporters import VALID_FORMATS, export_response, fmt  # noqa: E402


def _ag_date_range():
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


@agency_bp.route("/reports")
@role_required(*AGENCY_ROLES)
def reports():
    """Product-wise / customer-wise sale + vendor-wise purchase (BRD §13.4)."""
    date_from, date_to, filters = _ag_date_range()
    data = agency_reports_mod.agency_reports(date_from, date_to)

    export = request.args.get("export", "").strip().lower()
    if export in VALID_FORMATS:
        def sblock(title, grp, key):
            rows = [[r["label"], str(r["deals"]), fmt(r["qty"]), fmt(r["sale"]),
                     fmt(r["net"]), fmt(r["outstanding"])] for r in grp["rows"]]
            t = grp["totals"]
            rows.append(["TOTAL", str(t["deals"]), fmt(t["qty"]), fmt(t["sale"]), fmt(t["net"]), fmt(t["outstanding"])])
            return {"title": title, "headers": [key, "Deals", "Qty", "Sale", "Net", "Receivable"], "rows": rows}
        d = data["delivery"]
        drows = [[r["label"], str(r["deals"]), fmt(r["qty"]), fmt(r["sale"]),
                  fmt(r["carriage"]), fmt(r["net"])] for r in d["rows"]]
        dt = d["totals"]
        drows.append(["TOTAL", str(dt["deals"]), fmt(dt["qty"]), fmt(dt["sale"]), fmt(dt["carriage"]), fmt(dt["net"])])
        v = data["vendors"]
        vrows = [[r["label"], str(r["deals"]), fmt(r["qty"]), fmt(r["purchase"]), fmt(r["payable"])] for r in v["rows"]]
        vrows.append(["TOTAL", str(v["totals"]["deals"]), fmt(v["totals"]["qty"]), fmt(v["totals"]["purchase"]), fmt(v["totals"]["payable"])])
        g = data["godown"]
        grows = [[r["product"], fmt(r["received"]), fmt(r["sold"]), fmt(r["on_hand"])] for r in g["rows"]]
        if grows:
            gt = g["totals"]
            grows.append(["TOTAL", fmt(gt["received"]), fmt(gt["sold"]), fmt(gt["on_hand"])])
        blocks = [
            sblock("Product-wise Sale (LDO / Kerosene)", data["products"], "Product"),
            sblock("Customer-wise Sale", data["customers"], "Customer"),
            {"title": "Delivery-method-wise Sale & Carriage Cost",
             "headers": ["Delivery Method", "Deals", "Qty", "Sale", "Carriage Cost", "Net"], "rows": drows},
            {"title": "Vendor-wise Purchase", "headers": ["Vendor", "Deals", "Qty", "Purchase", "Payable"], "rows": vrows},
            {"title": "Godown Stock (on-hand by product)",
             "headers": ["Product", "Received", "Sold", "On Hand"], "rows": grows},
        ]
        return export_response(export, "agency_reports", "Agency Reports", blocks)

    return render_template("agency/reports.html", data=data, filters=filters)
AGENCY_CATEGORY_NAME = "Agency Products"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _active_vendors():
    return Vendor.query.filter_by(is_active=True).order_by(Vendor.name).all()


def _active_customers():
    return Customer.query.filter_by(is_active=True).order_by(Customer.name).all()


def _agency_products():
    return (
        Product.query.join(ProductCategory)
        .filter(ProductCategory.name == AGENCY_CATEGORY_NAME, Product.is_active.is_(True))
        .order_by(Product.name)
        .all()
    )


def _is_agency_product(product):
    return (
        product is not None
        and product.category is not None
        and product.category.name == AGENCY_CATEGORY_NAME
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


def _common_choices():
    return {
        "products": _agency_products(),
        "delivery_methods": AGENCY_DELIVERY_METHODS,
        "payment_statuses": AGENCY_PAYMENT_STATUSES,
        "payment_methods": AGENCY_PAYMENT_METHODS,
    }


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #
@agency_bp.route("/")
@role_required(*AGENCY_ROLES)
def index():
    purchase_total = db.session.query(db.func.coalesce(db.func.sum(AgencyPurchase.total_amount), 0)).filter(AgencyPurchase.is_active.is_(True)).scalar()
    sale_total = db.session.query(db.func.coalesce(db.func.sum(AgencySale.total_sale_amount), 0)).filter(AgencySale.is_active.is_(True)).scalar()
    profit_total = db.session.query(db.func.coalesce(db.func.sum(AgencySale.net_profit), 0)).filter(AgencySale.is_active.is_(True)).scalar()
    totals = {
        "purchases": AgencyPurchase.query.count(),
        "sales": AgencySale.query.count(),
        "purchase_total": Decimal(str(purchase_total)),
        "sale_total": Decimal(str(sale_total)),
        "profit_total": Decimal(str(profit_total)),
    }
    return render_template("agency/index.html", totals=totals)


# --------------------------------------------------------------------------- #
# Agency Purchases
# --------------------------------------------------------------------------- #
@agency_bp.route("/purchases")
@role_required(*AGENCY_ROLES)
def purchases_list():
    f_date = request.args.get("date", "").strip()
    f_vendor = _int_or_none(request.args.get("vendor_id"))
    f_product = _int_or_none(request.args.get("product_id"))
    f_status = request.args.get("payment_status", "").strip()

    query = AgencyPurchase.query
    if f_date:
        try:
            query = query.filter(AgencyPurchase.purchase_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_vendor:
        query = query.filter(AgencyPurchase.vendor_id == f_vendor)
    if f_product:
        query = query.filter(AgencyPurchase.product_id == f_product)
    if f_status:
        query = query.filter(AgencyPurchase.payment_status == f_status)

    purchases = query.order_by(AgencyPurchase.purchase_date.desc(), AgencyPurchase.id.desc()).all()
    return render_template(
        "agency/purchases/list.html",
        purchases=purchases,
        filters={"date": f_date, "vendor_id": f_vendor, "product_id": f_product, "payment_status": f_status},
        vendors=_active_vendors(), **_common_choices(),
    )


def _read_purchase_form():
    return {
        "vendor_id": _int_or_none(request.form.get("vendor_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "purchase_date": request.form.get("purchase_date", "").strip(),
        "quantity": request.form.get("quantity", "").strip(),
        "purchase_rate": request.form.get("purchase_rate", "").strip(),
        "invoice_number": request.form.get("invoice_number", "").strip(),
        "loading_point": request.form.get("loading_point", "").strip(),
        "delivery_method": request.form.get("delivery_method", "").strip(),
        "vehicle_number": request.form.get("vehicle_number", "").strip(),
        "driver_name": request.form.get("driver_name", "").strip(),
        "payment_status": request.form.get("payment_status", "").strip(),
        "to_godown": bool(request.form.get("to_godown")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _validate_purchase(form):
    errors = []
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
        elif not _is_agency_product(product):
            errors.append("Product must be an agency product (LDO, Kerosene).")

    purchase_date = None
    if not form["purchase_date"]:
        errors.append("Purchase date is required.")
    else:
        try:
            purchase_date = datetime.strptime(form["purchase_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid purchase date.")

    quantity = _parse_nonneg(form["quantity"], "Quantity", errors, default=None)
    if quantity is None and form["quantity"] == "":
        errors.append("Quantity is required.")
    elif quantity is not None and quantity <= 0:
        errors.append("Quantity must be greater than zero.")

    rate = _parse_nonneg(form["purchase_rate"], "Purchase rate", errors)

    if not form["payment_status"]:
        errors.append("Payment status is required.")
    elif form["payment_status"] not in AGENCY_PAYMENT_STATUSES:
        errors.append("Please select a valid payment status.")
    if form["delivery_method"] and form["delivery_method"] not in AGENCY_DELIVERY_METHODS:
        errors.append("Please select a valid delivery method.")

    return errors, vendor, product, purchase_date, quantity, rate


def _apply_purchase(p, form, vendor, product, purchase_date, quantity, rate):
    p.vendor = vendor
    p.product = product
    p.purchase_date = purchase_date
    p.quantity = quantity
    p.purchase_rate = rate
    p.total_amount = quantity * rate
    p.invoice_number = form["invoice_number"] or None
    p.loading_point = form["loading_point"] or None
    p.delivery_method = form["delivery_method"] or None
    p.vehicle_number = form["vehicle_number"] or None
    p.driver_name = form["driver_name"] or None
    p.payment_status = form["payment_status"]
    p.to_godown = form["to_godown"]
    p.notes = form["notes"] or None
    p.is_active = form["is_active"]


@agency_bp.route("/purchases/create", methods=["GET", "POST"])
@role_required(*AGENCY_ROLES)
def purchases_create():
    if request.method == "POST":
        form = _read_purchase_form()
        errors, vendor, product, purchase_date, quantity, rate = _validate_purchase(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("agency/purchases/form.html", form=form, mode="create", vendors=_active_vendors(), **_common_choices())
        p = AgencyPurchase(created_by_id=current_user.id)
        _apply_purchase(p, form, vendor, product, purchase_date, quantity, rate)
        db.session.add(p)
        db.session.flush()
        posting.sync_agency_purchase(p)
        db.session.commit()
        flash(f"Agency purchase saved. Total {p.total_amount}.", "success")
        return redirect(url_for("agency.purchases_view", purchase_id=p.id))

    form = {
        "vendor_id": None, "product_id": None, "purchase_date": date.today().isoformat(),
        "quantity": "", "purchase_rate": "", "invoice_number": "", "loading_point": "",
        "delivery_method": "", "vehicle_number": "", "driver_name": "",
        "payment_status": "Unpaid", "to_godown": False, "notes": "", "is_active": True,
    }
    return render_template("agency/purchases/form.html", form=form, mode="create", vendors=_active_vendors(), **_common_choices())


@agency_bp.route("/purchases/<int:purchase_id>")
@role_required(*AGENCY_ROLES)
def purchases_view(purchase_id):
    purchase = db.get_or_404(AgencyPurchase, purchase_id)
    return render_template("agency/purchases/detail.html", purchase=purchase)


@agency_bp.route("/purchases/<int:purchase_id>/edit", methods=["GET", "POST"])
@role_required(*AGENCY_ROLES)
def purchases_edit(purchase_id):
    purchase = db.get_or_404(AgencyPurchase, purchase_id)
    if request.method == "POST":
        form = _read_purchase_form()
        errors, vendor, product, purchase_date, quantity, rate = _validate_purchase(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("agency/purchases/form.html", form=form, mode="edit", purchase=purchase, vendors=_active_vendors(), **_common_choices())
        _apply_purchase(purchase, form, vendor, product, purchase_date, quantity, rate)
        posting.sync_agency_purchase(purchase)
        db.session.commit()
        flash("Agency purchase updated.", "success")
        return redirect(url_for("agency.purchases_view", purchase_id=purchase.id))

    form = {
        "vendor_id": purchase.vendor_id, "product_id": purchase.product_id,
        "purchase_date": purchase.purchase_date.isoformat(), "quantity": str(purchase.quantity),
        "purchase_rate": str(purchase.purchase_rate), "invoice_number": purchase.invoice_number or "",
        "loading_point": purchase.loading_point or "", "delivery_method": purchase.delivery_method or "",
        "vehicle_number": purchase.vehicle_number or "", "driver_name": purchase.driver_name or "",
        "payment_status": purchase.payment_status, "to_godown": purchase.to_godown,
        "notes": purchase.notes or "", "is_active": purchase.is_active,
    }
    return render_template("agency/purchases/form.html", form=form, mode="edit", purchase=purchase, vendors=_active_vendors(), **_common_choices())


@agency_bp.route("/purchases/<int:purchase_id>/toggle-status", methods=["POST"])
@role_required(*AGENCY_ROLES)
def purchases_toggle_status(purchase_id):
    purchase = db.get_or_404(AgencyPurchase, purchase_id)
    purchase.is_active = not purchase.is_active
    posting.sync_agency_purchase(purchase)
    db.session.commit()
    state = "activated" if purchase.is_active else "deactivated"
    flash(f"Agency purchase {state}.", "info")
    return redirect(url_for("agency.purchases_list"))


@agency_bp.route("/purchases/<int:purchase_id>/delete", methods=["POST"])
@role_required(*AGENCY_ROLES)
def purchases_delete(purchase_id):
    from app.attachments.models import Attachment

    purchase = db.get_or_404(AgencyPurchase, purchase_id)
    purchase.is_active = False
    posting.sync_agency_purchase(purchase)  # reverse GL before removing
    Attachment.query.filter_by(entity_type="agency_purchase", entity_id=purchase.id).delete()
    db.session.delete(purchase)
    db.session.commit()
    flash("Agency purchase permanently deleted.", "success")
    return redirect(url_for("agency.purchases_list"))


# --------------------------------------------------------------------------- #
# Agency Sales
# --------------------------------------------------------------------------- #
@agency_bp.route("/sales")
@role_required(*AGENCY_ROLES)
def sales_list():
    f_date = request.args.get("date", "").strip()
    f_customer = _int_or_none(request.args.get("customer_id"))
    f_product = _int_or_none(request.args.get("product_id"))
    f_status = request.args.get("payment_status", "").strip()

    query = AgencySale.query
    if f_date:
        try:
            query = query.filter(AgencySale.sale_date == datetime.strptime(f_date, "%Y-%m-%d").date())
        except ValueError:
            pass
    if f_customer:
        query = query.filter(AgencySale.customer_id == f_customer)
    if f_product:
        query = query.filter(AgencySale.product_id == f_product)
    if f_status:
        query = query.filter(AgencySale.payment_status == f_status)

    sales = query.order_by(AgencySale.sale_date.desc(), AgencySale.id.desc()).all()
    return render_template(
        "agency/sales/list.html",
        sales=sales,
        filters={"date": f_date, "customer_id": f_customer, "product_id": f_product, "payment_status": f_status},
        customers=_active_customers(), **_common_choices(),
    )


def _read_sale_form():
    return {
        "customer_id": _int_or_none(request.form.get("customer_id")),
        "product_id": _int_or_none(request.form.get("product_id")),
        "sale_date": request.form.get("sale_date", "").strip(),
        "quantity": request.form.get("quantity", "").strip(),
        "purchase_rate": request.form.get("purchase_rate", "").strip(),
        "sale_rate": request.form.get("sale_rate", "").strip(),
        "carriage_cost": request.form.get("carriage_cost", "").strip(),
        "other_expense": request.form.get("other_expense", "").strip(),
        "delivery_method": request.form.get("delivery_method", "").strip(),
        "vehicle_number": request.form.get("vehicle_number", "").strip(),
        "driver_name": request.form.get("driver_name", "").strip(),
        "payment_method": request.form.get("payment_method", "").strip(),
        "payment_status": request.form.get("payment_status", "").strip(),
        "from_godown": bool(request.form.get("from_godown")),
        "notes": request.form.get("notes", "").strip(),
        "is_active": bool(request.form.get("is_active")),
    }


def _agency_godown_stock(product_id, exclude_sale_id=None):
    """Agency godown on-hand for a product = Σ purchases received into godown
    (to_godown) − Σ sales drawn from godown (from_godown)."""
    purchased = (
        db.session.query(db.func.coalesce(db.func.sum(AgencyPurchase.quantity), 0))
        .filter(AgencyPurchase.product_id == product_id,
                AgencyPurchase.is_active.is_(True),
                AgencyPurchase.to_godown.is_(True))
        .scalar()
    )
    sold_q = AgencySale.query.filter(
        AgencySale.product_id == product_id,
        AgencySale.is_active.is_(True),
        AgencySale.from_godown.is_(True),
    )
    if exclude_sale_id is not None:
        sold_q = sold_q.filter(AgencySale.id != exclude_sale_id)
    sold = sum((s.quantity or Decimal("0")) for s in sold_q.all())
    return Decimal(str(purchased)) - sold


def _validate_sale(form, exclude_sale_id=None):
    errors = []
    customer = None
    if form["customer_id"] is None:
        errors.append("Customer is required.")
    else:
        customer = db.session.get(Customer, form["customer_id"])
        if customer is None:
            errors.append("Please select a valid customer.")

    product = None
    if form["product_id"] is None:
        errors.append("Product is required.")
    else:
        product = db.session.get(Product, form["product_id"])
        if product is None:
            errors.append("Please select a valid product.")
        elif not _is_agency_product(product):
            errors.append("Product must be an agency product (LDO, Kerosene).")

    sale_date = None
    if not form["sale_date"]:
        errors.append("Sale date is required.")
    else:
        try:
            sale_date = datetime.strptime(form["sale_date"], "%Y-%m-%d").date()
        except ValueError:
            errors.append("Please enter a valid sale date.")

    quantity = _parse_nonneg(form["quantity"], "Quantity", errors, default=None)
    if quantity is None and form["quantity"] == "":
        errors.append("Quantity is required.")
    elif quantity is not None and quantity <= 0:
        errors.append("Quantity must be greater than zero.")
    elif quantity is not None and product is not None and form.get("from_godown"):
        # Stock limit: a godown sale can't exceed agency godown on-hand stock.
        avail = _agency_godown_stock(product.id, exclude_sale_id=exclude_sale_id)
        if quantity > avail:
            errors.append(
                f"Not enough {product.name} in the agency godown: only "
                f"{avail:,.2f} on hand, but the sale is {quantity:,.2f}. "
                f"Record a godown purchase first (or untick 'sold from godown' for a direct sale)."
            )

    purchase_rate = _parse_nonneg(form["purchase_rate"], "Purchase rate", errors)
    sale_rate = _parse_nonneg(form["sale_rate"], "Sale rate", errors)
    carriage = _parse_nonneg(form["carriage_cost"], "Carriage cost", errors)
    other = _parse_nonneg(form["other_expense"], "Other expense", errors)

    if not form["payment_status"]:
        errors.append("Payment status is required.")
    elif form["payment_status"] not in AGENCY_PAYMENT_STATUSES:
        errors.append("Please select a valid payment status.")
    if form["delivery_method"] and form["delivery_method"] not in AGENCY_DELIVERY_METHODS:
        errors.append("Please select a valid delivery method.")
    if form["payment_method"] and form["payment_method"] not in AGENCY_PAYMENT_METHODS:
        errors.append("Please select a valid payment method.")

    return errors, {"customer": customer, "product": product, "sale_date": sale_date,
                    "quantity": quantity, "purchase_rate": purchase_rate, "sale_rate": sale_rate,
                    "carriage": carriage, "other": other}


def _apply_sale(s, form, ctx):
    s.customer = ctx["customer"]
    s.product = ctx["product"]
    s.sale_date = ctx["sale_date"]
    s.quantity = ctx["quantity"]
    s.purchase_rate = ctx["purchase_rate"]
    s.sale_rate = ctx["sale_rate"]
    s.total_purchase_amount = ctx["quantity"] * ctx["purchase_rate"]
    s.total_sale_amount = ctx["quantity"] * ctx["sale_rate"]
    s.carriage_cost = ctx["carriage"]
    s.other_expense = ctx["other"]
    s.net_profit = s.total_sale_amount - s.total_purchase_amount - ctx["carriage"] - ctx["other"]
    s.delivery_method = form["delivery_method"] or None
    s.vehicle_number = form["vehicle_number"] or None
    s.driver_name = form["driver_name"] or None
    s.payment_method = form["payment_method"] or None
    s.payment_status = form["payment_status"]
    s.from_godown = form["from_godown"]
    s.notes = form["notes"] or None
    s.is_active = form["is_active"]


@agency_bp.route("/sales/create", methods=["GET", "POST"])
@role_required(*AGENCY_ROLES)
def sales_create():
    if request.method == "POST":
        form = _read_sale_form()
        errors, ctx = _validate_sale(form)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("agency/sales/form.html", form=form, mode="create", customers=_active_customers(), **_common_choices())
        s = AgencySale(created_by_id=current_user.id)
        _apply_sale(s, form, ctx)
        db.session.add(s)
        db.session.flush()
        approvals.request_if_needed("agency_sale", s.id, s.total_sale_amount,
                                    s.created_by_id, title=f"Agency sale — {s.total_sale_amount}")
        posting.sync_agency_sale(s)  # held out of GL until approved (BRD §16)
        db.session.commit()
        flash(f"Agency sale saved. Net profit {s.net_profit}.", "success")
        return redirect(url_for("agency.sales_view", sale_id=s.id))

    form = {
        "customer_id": None, "product_id": None, "sale_date": date.today().isoformat(),
        "quantity": "", "purchase_rate": "", "sale_rate": "", "carriage_cost": "0",
        "other_expense": "0", "delivery_method": "Customer Pickup", "vehicle_number": "",
        "driver_name": "", "payment_method": "Cash", "payment_status": "Unpaid",
        "from_godown": False, "notes": "", "is_active": True,
    }
    return render_template("agency/sales/form.html", form=form, mode="create", customers=_active_customers(), **_common_choices())


@agency_bp.route("/sales/<int:sale_id>")
@role_required(*AGENCY_ROLES)
def sales_view(sale_id):
    sale = db.get_or_404(AgencySale, sale_id)
    return render_template("agency/sales/detail.html", sale=sale)


@agency_bp.route("/sales/<int:sale_id>/edit", methods=["GET", "POST"])
@role_required(*AGENCY_ROLES)
def sales_edit(sale_id):
    sale = db.get_or_404(AgencySale, sale_id)
    if request.method == "POST":
        form = _read_sale_form()
        errors, ctx = _validate_sale(form, exclude_sale_id=sale.id)
        if errors:
            for m in errors:
                flash(m, "danger")
            return render_template("agency/sales/form.html", form=form, mode="edit", sale=sale, customers=_active_customers(), **_common_choices())
        _apply_sale(sale, form, ctx)
        approvals.request_if_needed("agency_sale", sale.id, sale.total_sale_amount,
                                    sale.created_by_id, title=f"Agency sale — {sale.total_sale_amount}")
        posting.sync_agency_sale(sale)  # held out of GL until approved (BRD §16)
        db.session.commit()
        flash("Agency sale updated.", "success")
        return redirect(url_for("agency.sales_view", sale_id=sale.id))

    form = {
        "customer_id": sale.customer_id, "product_id": sale.product_id,
        "sale_date": sale.sale_date.isoformat(), "quantity": str(sale.quantity),
        "purchase_rate": str(sale.purchase_rate), "sale_rate": str(sale.sale_rate),
        "carriage_cost": str(sale.carriage_cost), "other_expense": str(sale.other_expense),
        "delivery_method": sale.delivery_method or "Customer Pickup",
        "vehicle_number": sale.vehicle_number or "", "driver_name": sale.driver_name or "",
        "payment_method": sale.payment_method or "Cash", "payment_status": sale.payment_status,
        "from_godown": sale.from_godown, "notes": sale.notes or "", "is_active": sale.is_active,
    }
    return render_template("agency/sales/form.html", form=form, mode="edit", sale=sale, customers=_active_customers(), **_common_choices())


@agency_bp.route("/sales/<int:sale_id>/toggle-status", methods=["POST"])
@role_required(*AGENCY_ROLES)
def sales_toggle_status(sale_id):
    sale = db.get_or_404(AgencySale, sale_id)
    sale.is_active = not sale.is_active
    posting.sync_agency_sale(sale)
    db.session.commit()
    state = "activated" if sale.is_active else "deactivated"
    flash(f"Agency sale {state}.", "info")
    return redirect(url_for("agency.sales_list"))


@agency_bp.route("/sales/<int:sale_id>/delete", methods=["POST"])
@role_required(*AGENCY_ROLES)
def sales_delete(sale_id):
    from app.approvals.models import Approval

    sale = db.get_or_404(AgencySale, sale_id)
    sale.is_active = False
    posting.sync_agency_sale(sale)  # reverse GL before removing
    Approval.query.filter_by(entity_type="agency_sale", entity_id=sale.id).delete()
    db.session.delete(sale)
    db.session.commit()
    flash("Agency sale permanently deleted.", "success")
    return redirect(url_for("agency.sales_list"))
