"""Fuel tank stock posting + audit ledger helpers.

Two things move tank fuel stock:
  * Pump purchases  -> increase tank stock  (Purchase Received, +qty)
  * Retail sales    -> decrease tank stock  (Retail Sale, -qty)

The running balance is PumpTank.current_stock_liters; every change is also
written to the StockMovement audit ledger in the same transaction so the two
never drift. Source documents own their movement rows via (source_type,
source_id), so an edit/reversal clears and rebuilds them — mirroring the
idempotent `stock_posted` pattern already used for purchases.

Sales are facts (they come from meter readings), so a sale is allowed to drive a
tank's balance negative; that simply signals a missing purchase entry / stock
discrepancy for the (future) gain-loss adjustment flow to resolve. Stock
increases reversal is guarded against negatives by the purchase routes.
"""
from decimal import Decimal

from app.extensions import db
from app.petrol_pumps.models import (
    ADJUSTMENT_STATUS_APPROVED,
    MOVEMENT_PURCHASE_RECEIVED,
    MOVEMENT_RETAIL_SALE,
    MOVEMENT_STOCK_GAIN,
    MOVEMENT_STOCK_LOSS,
    PumpTank,
    StockMovement,
)

_ZERO = Decimal("0")

SOURCE_PUMP_PURCHASE = "pump_purchase"
SOURCE_MACHINE_READING = "machine_reading"
SOURCE_STOCK_ADJUSTMENT = "stock_adjustment"


# --------------------------------------------------------------------------- #
# Ledger primitives
# --------------------------------------------------------------------------- #
def _movements_for(source_type, source_id):
    if source_id is None:
        return []
    return StockMovement.query.filter_by(
        source_type=source_type, source_id=source_id
    ).all()


def clear_movements(source_type, source_id):
    """Delete all ledger rows belonging to a source document (session-aware)."""
    for movement in _movements_for(source_type, source_id):
        db.session.delete(movement)


def add_movement(
    *, petrol_pump_id, tank_id, product_id, movement_type, quantity_liters,
    movement_date, source_type, source_id, created_by_id=None, notes=None,
):
    """Append one signed ledger row (does not touch the tank balance)."""
    db.session.add(StockMovement(
        petrol_pump_id=petrol_pump_id, tank_id=tank_id, product_id=product_id,
        movement_type=movement_type, quantity_liters=quantity_liters,
        movement_date=movement_date, source_type=source_type,
        source_id=source_id, created_by_id=created_by_id, notes=notes,
    ))


def _adjust_tank(tank_id, delta):
    """Add `delta` (signed) to a tank's running balance."""
    tank = db.session.get(PumpTank, tank_id)
    if tank is not None:
        tank.current_stock_liters = (
            tank.current_stock_liters or _ZERO
        ) + delta


# --------------------------------------------------------------------------- #
# Retail sale (machine reading) — decrement
# --------------------------------------------------------------------------- #
def resolve_tank(petrol_pump_id, product_id):
    """The pump's active tank for a product that a retail sale should draw from.

    A pump may have MORE THAN ONE active tank for the same fuel (e.g. two Petrol
    tanks). In that case the sale is drawn from the tank with the most stock
    (ties broken by lowest id), so entries keep working instead of erroring.
    Returns None only when the pump has NO active tank for the product.
    """
    tanks = (
        PumpTank.query.filter_by(
            petrol_pump_id=petrol_pump_id,
            product_id=product_id,
            is_active=True,
        )
        .all()
    )
    if not tanks:
        return None
    if len(tanks) == 1:
        return tanks[0]
    # Multiple tanks of the same fuel: draw from the fullest.
    return max(tanks, key=lambda t: ((t.current_stock_liters or _ZERO), -t.id))


def tank_for_nozzle(nozzle):
    """The tank a nozzle's retail sale draws from.

    A machine is wired to a specific tank, so prefer the nozzle's machine tank
    (`machine.tank_id`). Fall back to the pump's resolved tank for the product
    (older machines with no tank link).
    """
    if nozzle is None:
        return None
    machine = nozzle.machine
    tank_id = getattr(machine, "tank_id", None) if machine is not None else None
    if tank_id is not None:
        tank = db.session.get(PumpTank, tank_id)
        if tank is not None and tank.is_active:
            return tank
    return resolve_tank(nozzle.petrol_pump_id, nozzle.product_id)


def _reading_should_post(reading, tank):
    return (
        reading.is_active
        and tank is not None
        and (reading.sale_liters or _ZERO) > 0
    )


def sync_reading_stock(reading):
    """Idempotently reflect a retail sale's stock decrement + ledger row.

    Resolves the tank fresh each time, so it self-heals if tank setup changes.
    Uses the reading's machine→tank wiring when present, else the pump's
    resolved tank for the product. Returns True if stock is currently posted.
    """
    tank = tank_for_nozzle(reading.nozzle) if reading.nozzle is not None \
        else resolve_tank(reading.petrol_pump_id, reading.product_id)
    desired = _reading_should_post(reading, tank)

    if reading.stock_posted:
        # Reverse using what was ACTUALLY posted (from the ledger rows), not the
        # reading's current sale_liters — those may already hold edited values.
        for movement in _movements_for(SOURCE_MACHINE_READING, reading.id):
            if movement.tank_id is not None:
                _adjust_tank(movement.tank_id, -(movement.quantity_liters or _ZERO))
        clear_movements(SOURCE_MACHINE_READING, reading.id)
        reading.stock_posted = False
        reading.tank_id = None

    if desired:
        _adjust_tank(tank.id, -(reading.sale_liters or _ZERO))
        add_movement(
            petrol_pump_id=reading.petrol_pump_id, tank_id=tank.id,
            product_id=reading.product_id,
            movement_type=MOVEMENT_RETAIL_SALE,
            quantity_liters=-(reading.sale_liters or _ZERO),
            movement_date=reading.reading_date,
            source_type=SOURCE_MACHINE_READING, source_id=reading.id,
            created_by_id=reading.entered_by_id,
            notes=f"Reading #{reading.id}",
        )
        reading.tank_id = tank.id
        reading.stock_posted = True

    return reading.stock_posted


# --------------------------------------------------------------------------- #
# Stock adjustment (gain/loss) — posts only when APPROVED
# --------------------------------------------------------------------------- #
def _adjustment_should_post(adj):
    return (
        adj.is_active
        and adj.status == ADJUSTMENT_STATUS_APPROVED
        and (adj.difference_liters or _ZERO) != 0
    )


def can_unpost_adjustment(adj):
    """Check that reversing this adjustment won't drive its tank negative.

    Reversing a GAIN removes litres from the tank; if retail sales have since
    consumed them, the reversal would go below zero — block it. Reversing a
    loss only adds litres back, which is always safe.
    """
    if not adj.stock_posted:
        return True, None
    total = sum(
        (m.quantity_liters or _ZERO)
        for m in _movements_for(SOURCE_STOCK_ADJUSTMENT, adj.id)
        if m.tank_id is not None
    )
    if total <= 0:
        return True, None
    tank = db.session.get(PumpTank, adj.tank_id)
    if tank is not None and (tank.current_stock_liters or _ZERO) - total < 0:
        return False, tank
    return True, None


def sync_adjustment_stock(adj):
    """Idempotently reflect an adjustment's gain/loss on the tank + ledger.

    Same contract as sync_reading_stock: any previously posted quantity is
    reversed from the ledger rows (the true posted amounts), then the current
    desired state is re-applied. Returns True if posted afterwards.
    """
    if adj.stock_posted:
        for movement in _movements_for(SOURCE_STOCK_ADJUSTMENT, adj.id):
            if movement.tank_id is not None:
                _adjust_tank(movement.tank_id, -(movement.quantity_liters or _ZERO))
        clear_movements(SOURCE_STOCK_ADJUSTMENT, adj.id)
        adj.stock_posted = False

    if _adjustment_should_post(adj):
        diff = adj.difference_liters or _ZERO
        _adjust_tank(adj.tank_id, diff)
        add_movement(
            petrol_pump_id=adj.petrol_pump_id, tank_id=adj.tank_id,
            product_id=adj.product_id,
            movement_type=MOVEMENT_STOCK_GAIN if diff > 0 else MOVEMENT_STOCK_LOSS,
            quantity_liters=diff,
            movement_date=adj.adjustment_date,
            source_type=SOURCE_STOCK_ADJUSTMENT, source_id=adj.id,
            created_by_id=adj.approved_by_id or adj.created_by_id,
            notes=f"Adjustment #{adj.id}: physical {adj.physical_stock_liters} "
                  f"vs system {adj.system_stock_liters}",
        )
        adj.stock_posted = True

    return adj.stock_posted


# --------------------------------------------------------------------------- #
# Purchase — ledger rows for the increase already applied by the routes
# --------------------------------------------------------------------------- #
def record_purchase_movements(purchase):
    """Write Purchase Received ledger rows for a purchase's tank items.

    The tank balance increase itself is applied by the purchase routes'
    `_apply_stock`; this only records the matching audit rows. Always clears
    first so it is safe to call on re-post.
    """
    clear_movements(SOURCE_PUMP_PURCHASE, purchase.id)
    for item in purchase.items:
        if item.tank_id is None:
            continue
        add_movement(
            petrol_pump_id=purchase.petrol_pump_id, tank_id=item.tank_id,
            product_id=item.product_id,
            movement_type=MOVEMENT_PURCHASE_RECEIVED,
            quantity_liters=(item.quantity_liters or _ZERO),
            movement_date=purchase.purchase_date,
            source_type=SOURCE_PUMP_PURCHASE, source_id=purchase.id,
            created_by_id=purchase.created_by_id,
            notes=purchase.invoice_number or f"Purchase #{purchase.id}",
        )


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def tank_stock_rows(petrol_pump_id=None):
    """Active tanks with their current balance, for the tank-stock report."""
    query = PumpTank.query.filter_by(is_active=True)
    if petrol_pump_id:
        query = query.filter(PumpTank.petrol_pump_id == petrol_pump_id)
    return query.join(PumpTank.petrol_pump).order_by(
        PumpTank.petrol_pump_id, PumpTank.tank_name
    ).all()
