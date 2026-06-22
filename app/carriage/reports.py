"""Carriage profit/loss reports (BRD §13.3): vehicle-wise and driver-wise.

Trips may reference a master vehicle/driver (by id) or carry a free-text
number/name, so labels fall back accordingly. Aggregation is done in Python over
the (small) trip set, which keeps the mixed id/text grouping simple.
"""
from decimal import Decimal

from app.carriage.models import CarriageTrip

_ZERO = Decimal("0")


def _dec(v):
    return Decimal(str(v or 0))


def _vehicle_label(trip):
    if trip.vehicle is not None and trip.vehicle.vehicle_number:
        return trip.vehicle.vehicle_number
    if trip.vehicle_number:
        return trip.vehicle_number
    return "Unassigned"


def _driver_label(trip):
    if trip.driver is not None and trip.driver.name:
        return trip.driver.name
    if trip.driver_name:
        return trip.driver_name
    return "Unassigned"


def _scoped_trips(date_from, date_to):
    query = CarriageTrip.query.filter(CarriageTrip.is_active.is_(True))
    if date_from:
        query = query.filter(CarriageTrip.trip_date >= date_from)
    if date_to:
        query = query.filter(CarriageTrip.trip_date <= date_to)
    return query.all()


def _group(trips, label_fn):
    groups = {}
    for t in trips:
        label = label_fn(t)
        g = groups.setdefault(label, {
            "label": label, "trips": 0, "freight": _ZERO,
            "expenses": _ZERO, "net": _ZERO, "ownership": set(),
        })
        g["trips"] += 1
        g["freight"] += _dec(t.total_freight_amount)
        g["expenses"] += _dec(t.total_expenses)
        g["net"] += _dec(t.net_profit)
        if t.vehicle_ownership:
            g["ownership"].add(t.vehicle_ownership)
    rows = sorted(groups.values(), key=lambda r: r["net"], reverse=True)
    for r in rows:
        r["ownership"] = ", ".join(sorted(r["ownership"]))
    totals = {
        "trips": sum(r["trips"] for r in rows),
        "freight": sum((r["freight"] for r in rows), _ZERO),
        "expenses": sum((r["expenses"] for r in rows), _ZERO),
        "net": sum((r["net"] for r in rows), _ZERO),
    }
    return {"rows": rows, "totals": totals}


def vehicle_pl(date_from=None, date_to=None):
    return _group(_scoped_trips(date_from, date_to), _vehicle_label)


def driver_pl(date_from=None, date_to=None):
    return _group(_scoped_trips(date_from, date_to), _driver_label)


def delivery_pl(date_from=None, date_to=None):
    """Delivery report grouped by trip type (BRD §8.7): PSO→pump, →bulk
    customer, godown→customer, supplier→agency customer, etc. One breakdown
    covers the customer / pump / agency / bulk-sale delivery reports."""
    return _group(_scoped_trips(date_from, date_to), lambda t: t.trip_type or "—")


_EXPENSE_CATEGORIES = [
    ("Rent (rented vehicle)", "rent_amount"),
    ("Fuel", "fuel_expense"),
    ("Toll tax", "toll_tax"),
    ("Loading / unloading", "loading_unloading_expense"),
    ("Driver", "driver_expense"),
    ("Maintenance", "maintenance_expense"),
    ("Other", "other_expense"),
]


def expense_breakdown(date_from=None, date_to=None):
    """Carriage expenses split by category across all trips (BRD §8.7).

    Returns rows of {category, amount, trips} (trips = how many trips carried a
    non-zero amount in that category) plus a grand total, sorted by amount.
    """
    trips = _scoped_trips(date_from, date_to)
    rows = []
    for label, attr in _EXPENSE_CATEGORIES:
        amount = _ZERO
        count = 0
        for t in trips:
            v = _dec(getattr(t, attr))
            if v > 0:
                amount += v
                count += 1
        rows.append({"label": label, "amount": amount, "trips": count})
    rows.sort(key=lambda r: r["amount"], reverse=True)
    total = sum((r["amount"] for r in rows), _ZERO)
    return {"rows": rows, "totals": {"amount": total, "trips": len(trips)}}


def rented_vehicle_payable(date_from=None, date_to=None):
    """Rent owed to rented-vehicle providers, grouped by vendor (BRD §13.3)."""
    trips = [t for t in _scoped_trips(date_from, date_to)
             if t.vehicle_ownership == "Rented Vehicle" and _dec(t.rent_amount) > 0]
    groups = {}
    for t in trips:
        label = t.rented_vehicle_vendor.name if t.rented_vehicle_vendor else "Unnamed provider"
        g = groups.setdefault(label, {"label": label, "trips": 0, "rent": _ZERO})
        g["trips"] += 1
        g["rent"] += _dec(t.rent_amount)
    rows = sorted(groups.values(), key=lambda r: r["rent"], reverse=True)
    totals = {"trips": sum(r["trips"] for r in rows),
              "rent": sum((r["rent"] for r in rows), _ZERO)}
    return {"rows": rows, "totals": totals}
