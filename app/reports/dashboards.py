"""Per-business-unit dashboard data (BRD §14.2).

Each function returns a plain dict of KPIs + small breakdowns for one business
unit's dashboard, reusing app/reports/services.py where possible and adding the
unit-specific aggregations. Date filtering is optional (all-time when omitted).
"""
from decimal import Decimal

from app.extensions import db
from app.core.models import CashBankAccount, Product
from app.reports import services

_ZERO = Decimal("0")


def _dec(v):
    return Decimal(str(v or 0))


# --------------------------------------------------------------------------- #
# Petrol Pump Retail
# --------------------------------------------------------------------------- #
def pump_dashboard(date_from=None, date_to=None):
    from app.petrol_pumps.models import MachineReading, PumpTank

    s = services.pump_retail_summary(date_from, date_to)

    # Product-wise fuel sale (from machine readings).
    q = (
        db.session.query(
            Product.name,
            db.func.coalesce(db.func.sum(MachineReading.sale_liters), 0),
            db.func.coalesce(db.func.sum(MachineReading.sale_amount), 0),
        )
        .join(Product, MachineReading.product_id == Product.id)
        .filter(MachineReading.is_active.is_(True))
    )
    if date_from:
        q = q.filter(MachineReading.reading_date >= date_from)
    if date_to:
        q = q.filter(MachineReading.reading_date <= date_to)
    products = [
        {"name": n, "liters": _dec(l), "amount": _dec(a)}
        for n, l, a in q.group_by(Product.name).order_by(Product.name).all()
    ]

    # Current tank stock (liters on hand).
    tank_liters = _dec(
        db.session.query(db.func.coalesce(db.func.sum(PumpTank.current_stock_liters), 0))
        .filter(PumpTank.is_active.is_(True)).scalar()
    )

    return {"summary": s, "products": products, "tank_liters": tank_liters}


# --------------------------------------------------------------------------- #
# Bulk Sale
# --------------------------------------------------------------------------- #
def bulk_dashboard(date_from=None, date_to=None):
    from app.bulk_sale.models import BulkSale
    from app.core.models import PetrolPump

    s = services.bulk_summary(date_from, date_to)
    active = BulkSale.is_active.is_(True)

    def _scope(q):
        if date_from:
            q = q.filter(BulkSale.sale_date >= date_from)
        if date_to:
            q = q.filter(BulkSale.sale_date <= date_to)
        return q

    # Pump-wise bulk sale.
    q = _scope(
        db.session.query(
            PetrolPump.name,
            db.func.coalesce(db.func.sum(BulkSale.total_sale_amount), 0),
        ).join(PetrolPump, BulkSale.petrol_pump_id == PetrolPump.id).filter(active)
    )
    pumps = [{"name": n, "amount": _dec(a)}
             for n, a in q.group_by(PetrolPump.name).order_by(PetrolPump.name).all()]

    # Delivery status counts.
    q = _scope(
        db.session.query(BulkSale.delivery_status, db.func.count(BulkSale.id)).filter(active)
    )
    statuses = [{"name": st or "—", "count": c}
                for st, c in q.group_by(BulkSale.delivery_status).all()]

    # Outstanding receivable = unpaid/partial sales.
    outstanding = _dec(
        _scope(db.session.query(db.func.coalesce(db.func.sum(BulkSale.total_sale_amount), 0))
               .filter(active, BulkSale.payment_status != "Paid")).scalar()
    )

    return {"summary": s, "pumps": pumps, "statuses": statuses, "outstanding": outstanding}


# --------------------------------------------------------------------------- #
# Carriage
# --------------------------------------------------------------------------- #
def carriage_dashboard(date_from=None, date_to=None):
    from app.carriage.models import CarriageTrip

    s = services.carriage_summary(date_from, date_to)
    active = CarriageTrip.is_active.is_(True)

    def _scope(q):
        if date_from:
            q = q.filter(CarriageTrip.trip_date >= date_from)
        if date_to:
            q = q.filter(CarriageTrip.trip_date <= date_to)
        return q

    total_trips = _scope(db.session.query(db.func.count(CarriageTrip.id)).filter(active)).scalar()

    q = _scope(
        db.session.query(CarriageTrip.vehicle_ownership, db.func.count(CarriageTrip.id),
                         db.func.coalesce(db.func.sum(CarriageTrip.net_profit), 0)).filter(active)
    )
    ownership = [{"name": o or "—", "count": c, "profit": _dec(p)}
                 for o, c, p in q.group_by(CarriageTrip.vehicle_ownership).all()]

    q = _scope(
        db.session.query(CarriageTrip.trip_status, db.func.count(CarriageTrip.id)).filter(active)
    )
    statuses = [{"name": st or "—", "count": c}
                for st, c in q.group_by(CarriageTrip.trip_status).all()]

    return {"summary": s, "total_trips": total_trips or 0,
            "ownership": ownership, "statuses": statuses}


# --------------------------------------------------------------------------- #
# Oil Agency
# --------------------------------------------------------------------------- #
def agency_dashboard(date_from=None, date_to=None):
    from app.agency.models import AgencySale, AgencyPurchase

    s = services.agency_summary(date_from, date_to)
    active = AgencySale.is_active.is_(True)

    def _scope(q, model):
        if date_from:
            q = q.filter(model.sale_date >= date_from) if model is AgencySale else q.filter(model.purchase_date >= date_from)
        if date_to:
            q = q.filter(model.sale_date <= date_to) if model is AgencySale else q.filter(model.purchase_date <= date_to)
        return q

    # Product-wise sale (LDO / Kerosene).
    q = _scope(
        db.session.query(
            Product.name,
            db.func.coalesce(db.func.sum(AgencySale.quantity), 0),
            db.func.coalesce(db.func.sum(AgencySale.total_sale_amount), 0),
        ).join(Product, AgencySale.product_id == Product.id).filter(active),
        AgencySale,
    )
    products = [{"name": n, "qty": _dec(qty), "amount": _dec(a)}
                for n, qty, a in q.group_by(Product.name).order_by(Product.name).all()]

    outstanding = _dec(
        _scope(db.session.query(db.func.coalesce(db.func.sum(AgencySale.total_sale_amount), 0))
               .filter(active, AgencySale.payment_status != "Paid"), AgencySale).scalar()
    )
    payable = _dec(
        _scope(db.session.query(db.func.coalesce(db.func.sum(AgencyPurchase.total_amount), 0))
               .filter(AgencyPurchase.is_active.is_(True),
                       AgencyPurchase.payment_status != "Paid"), AgencyPurchase).scalar()
    )

    return {"summary": s, "products": products,
            "outstanding": outstanding, "payable": payable}


# --------------------------------------------------------------------------- #
# Head Office
# --------------------------------------------------------------------------- #
def head_office_dashboard(date_from=None, date_to=None):
    s = services.combined_summary(date_from, date_to)
    ho = services.head_office_summary(date_from, date_to)
    balances = services.receivables_payables()

    accounts = CashBankAccount.query.filter_by(is_active=True).all()
    cash_total = _ZERO
    bank_total = _ZERO
    wallet_total = _ZERO
    for a in accounts:
        bal = a.current_balance or _ZERO
        kind = (a.account_type or "").lower()
        if "bank" in kind:
            bank_total += bal
        elif "wallet" in kind:
            wallet_total += bal
        else:
            cash_total += bal

    return {
        "combined": s, "head_office": ho, "balances": balances,
        "cash_total": cash_total, "bank_total": bank_total,
        "wallet_total": wallet_total,
    }
