"""Petrol pump fuel cost of goods sold (COGS) via weighted-average costing.

Retail fuel sale is recorded by machine readings (liters), but until now no cost
was matched to those sales, so pump profit was only an indicative
Sales - Expenses figure.

This module derives a real fuel COGS using *weighted-average cost* (the standard
perpetual inventory valuation method, alongside FIFO):

    weighted_avg_cost(pump, product) = sum(purchase total_amount)
                                       / sum(purchase quantity_liters)

The average is taken over a pump's active fuel purchases up to an optional
`date_to`, so it reflects every rate that pump has actually paid PSO for that
product. COGS for a set of readings is then::

    fuel_cogs = sum over (pump, product) of  sold_liters * weighted_avg_cost

Everything here is read-only/derived — no schema change, no posting, no effect on
live tank stock. Cost is computed per (pump, product) so each pump is costed at
its own purchase prices, which keeps business-wise profit correct.
"""
from decimal import Decimal

from app.extensions import db
from app.petrol_pumps.models import (
    MachineReading,
    PumpPurchase,
    PumpPurchaseItem,
)

_ZERO = Decimal("0")


def weighted_avg_costs(date_to=None):
    """Return {(petrol_pump_id, product_id): Decimal unit_cost}.

    Weighted-average purchase rate per pump+product over active fuel purchases
    (optionally only those on/before `date_to`). Products with no purchased
    quantity are omitted (no cost basis to report).
    """
    query = (
        db.session.query(
            PumpPurchase.petrol_pump_id,
            PumpPurchaseItem.product_id,
            db.func.coalesce(db.func.sum(PumpPurchaseItem.total_amount), 0),
            db.func.coalesce(db.func.sum(PumpPurchaseItem.quantity_liters), 0),
        )
        .join(PumpPurchase, PumpPurchaseItem.pump_purchase_id == PumpPurchase.id)
        .filter(PumpPurchase.is_active.is_(True))
    )
    if date_to:
        query = query.filter(PumpPurchase.purchase_date <= date_to)
    query = query.group_by(
        PumpPurchase.petrol_pump_id, PumpPurchaseItem.product_id
    )

    costs = {}
    for pump_id, product_id, total, qty in query.all():
        qty = Decimal(str(qty))
        if qty > 0:
            costs[(pump_id, product_id)] = Decimal(str(total)) / qty
    return costs


def fuel_cogs(date_from=None, date_to=None, pump_id=None, costs=None):
    """Total fuel COGS for active machine readings in the given window.

    `costs` may be a pre-computed weighted_avg_costs() dict to avoid recomputing
    when callers already have it. Readings for a (pump, product) with no purchase
    cost basis contribute zero and are ignored (honest: we don't invent a cost).
    """
    if costs is None:
        costs = weighted_avg_costs(date_to)

    query = db.session.query(
        MachineReading.petrol_pump_id,
        MachineReading.product_id,
        db.func.coalesce(db.func.sum(MachineReading.sale_liters), 0),
    ).filter(MachineReading.is_active.is_(True))
    if pump_id:
        query = query.filter(MachineReading.petrol_pump_id == pump_id)
    if date_from:
        query = query.filter(MachineReading.reading_date >= date_from)
    if date_to:
        query = query.filter(MachineReading.reading_date <= date_to)
    query = query.group_by(
        MachineReading.petrol_pump_id, MachineReading.product_id
    )

    total = _ZERO
    for p_id, prod_id, liters in query.all():
        unit_cost = costs.get((p_id, prod_id))
        if unit_cost is None:
            continue
        total += Decimal(str(liters)) * unit_cost
    return total


def reading_unit_cost(reading, costs=None):
    """Weighted-average unit cost for a single reading, or None if no basis."""
    if costs is None:
        costs = weighted_avg_costs(reading.reading_date)
    return costs.get((reading.petrol_pump_id, reading.product_id))


def stock_adjustment_value(date_from=None, date_to=None):
    """Net P&L value of APPROVED stock gain/loss adjustments (signed).

    Per BRD §6.6 a stock gain affects profit only after approval. Each approved
    adjustment is valued at the pump+product weighted-average cost (gain at
    cost, not at sale price — honest inventory valuation). Adjustments with no
    purchase cost basis contribute zero.
    """
    from app.petrol_pumps.models import (
        ADJUSTMENT_STATUS_APPROVED,
        StockAdjustment,
    )

    query = StockAdjustment.query.filter(
        StockAdjustment.is_active.is_(True),
        StockAdjustment.status == ADJUSTMENT_STATUS_APPROVED,
    )
    if date_from:
        query = query.filter(StockAdjustment.adjustment_date >= date_from)
    if date_to:
        query = query.filter(StockAdjustment.adjustment_date <= date_to)
    adjustments = query.all()
    if not adjustments:
        return _ZERO

    costs = weighted_avg_costs(date_to)
    total = _ZERO
    for adj in adjustments:
        unit_cost = costs.get((adj.petrol_pump_id, adj.product_id))
        if unit_cost is None:
            continue
        total += (adj.difference_liters or _ZERO) * unit_cost
    return total
