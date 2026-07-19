"""Seed the real opening-balance data for CMA & Co. as of 30 June.

Run this ONCE after `python seed.py` (which creates the generic company /
business-unit / default pump structure). This script layers the actual
business data on top: real bank accounts, PSO's opening payable, each pump's
real tank/machine/nozzle setup, purchase rates, and opening fuel/lubricant
stock (given a cost basis without physically re-receiving it or double-
posting to the GL/payable).

Safe to run more than once: every step is guarded on a natural key (account
name, tank name, purchase invoice number, product name) so nothing is
duplicated, and the PSO vendor's payable is *recomputed* from the opening
purchases that exist rather than incrementally adjusted, so re-running always
converges to the same correct state.

Usage:  python opening_data_seed.py
(Also runs automatically as part of `deploy.py`'s one-shot cPanel setup.)
"""
from datetime import date
from decimal import Decimal

from app.extensions import db
from app.core.models import (
    BusinessUnit, BusinessUnitType, CashBankAccount, PetrolPump, Product,
    ProductCategory, Vendor,
)
from app.petrol_pumps.models import PumpMachine, PumpNozzle, PumpPurchase, PumpPurchaseItem, PumpTank

OPENING_DATE = date(2026, 6, 30)
PSO_BASE_PAYABLE = Decimal("29909000")

# Distinguishing keyword per pump, used as a last-resort fuzzy match if the
# exact name isn't found (e.g. the pump was renamed, or has extra whitespace).
_PUMP_KEYWORDS = {
    "Punjab Petroleum": "Punjab",
    "Ahmed Filling Station": "Ahmed",
    "Alfateh Petroleum": "Alfateh",
}


def _find_pump(pump_name):
    """Robustly resolve a pump by name: exact match, then case/whitespace-
    insensitive, then a fuzzy keyword match. Always logs what it found (or
    didn't) so a deploy log makes it obvious WHY a pump's data was skipped,
    instead of silently doing nothing."""
    pump = PetrolPump.query.filter_by(name=pump_name).first()
    if pump is not None:
        return pump

    pump = PetrolPump.query.filter(
        db.func.lower(db.func.trim(PetrolPump.name)) == pump_name.lower()
    ).first()
    if pump is not None:
        print(f"NOTE: pump '{pump_name}' matched case/whitespace-insensitively "
              f"to existing pump '{pump.name}' (id={pump.id}).")
        return pump

    keyword = _PUMP_KEYWORDS.get(pump_name)
    if keyword:
        pump = PetrolPump.query.filter(PetrolPump.name.ilike(f"%{keyword}%")).first()
        if pump is not None:
            print(f"NOTE: pump '{pump_name}' fuzzy-matched to existing pump "
                  f"'{pump.name}' (id={pump.id}) via keyword '{keyword}'.")
            return pump

    all_pumps = [p.name for p in PetrolPump.query.all()]
    print(f"WARNING: could not find pump '{pump_name}' by any match — "
          f"SKIPPING its data. Pumps that actually exist in this database: {all_pumps}")
    return None


# --------------------------------------------------------------------------- #
# 1. Bank / cash accounts
# --------------------------------------------------------------------------- #
# Per-pump real bank details (name, account_number, balance). Alfateh's real
# bank hasn't been given yet, so it just gets a proper name + petrol_pump link
# with a zero balance, same as its Cash account.
PUMP_BANK_DETAILS = {
    "Punjab Petroleum": ("Punjab Petroleum Service", "0010003055900074", Decimal("1162880")),
    "Ahmed Filling Station": ("Ahmed Filling Station", "0010003055900118", Decimal("1442538")),
    "Alfateh Petroleum": ("Alfateh Petroleum Bank", None, Decimal("0")),
}

# Positional fallback for the very old pre-fix default seed, which created
# unlinked "Pump 1/2/3 Cash"/"Pump 1/2/3 Bank" rows with no petrol_pump_id at
# all, in this pump order.
OLD_STYLE_POSITION = {
    "Punjab Petroleum": 1, "Ahmed Filling Station": 2, "Alfateh Petroleum": 3,
}


def seed_bank_accounts():
    head_office = BusinessUnit.query.filter_by(type=BusinessUnitType.HEAD_OFFICE).first()

    def set_account(*, candidates, name, account_type, account_number, balance, petrol_pump=None):
        """Find the existing row to repurpose by trying each candidate name in
        order (covers both the current default-seed naming AND the very old
        pre-fix "Pump N Cash/Bank" naming some deployments may still have from
        before pump names were wired up), else fall back to matching by
        (petrol_pump, account_type) which is name-independent, else create."""
        acc = None
        for candidate in candidates:
            acc = CashBankAccount.query.filter_by(name=candidate).first()
            if acc is not None:
                break
        if acc is None and petrol_pump is not None:
            acc = CashBankAccount.query.filter_by(
                petrol_pump_id=petrol_pump.id, account_type=account_type
            ).first()
        if acc is None:
            acc = CashBankAccount(business_unit=head_office, account_type=account_type)
            db.session.add(acc)
        acc.name = name
        acc.account_type = account_type
        acc.account_number = account_number
        acc.opening_balance = balance
        acc.current_balance = balance
        if petrol_pump is not None:
            acc.petrol_pump = petrol_pump
            acc.business_unit = petrol_pump.business_unit
        return acc

    # Head Office's own two bank accounts + cash in hand.
    set_account(
        candidates=["Head Office Bank", "Muhammad Asghar"], name="Muhammad Asghar",
        account_type="Bank", account_number="001003055900101", balance=Decimal("300000"),
    )
    set_account(
        candidates=["Ch Muhammad Asghar & CO"], name="Ch Muhammad Asghar & CO",
        account_type="Bank", account_number="0010003055900080", balance=Decimal("6128162"),
    )
    set_account(
        candidates=["Head Office Cash"], name="Head Office Cash",
        account_type="Cash", account_number=None, balance=Decimal("67000"),
    )

    # Every pump's own Cash + Bank account.
    for pump_name, (bank_name, bank_number, bank_balance) in PUMP_BANK_DETAILS.items():
        pump = _find_pump(pump_name)
        if pump is None:
            continue
        pos = OLD_STYLE_POSITION[pump_name]
        set_account(
            candidates=[f"{pump_name} Cash", f"Pump {pos} Cash"], name=f"{pump_name} Cash",
            account_type="Cash", account_number=None, balance=Decimal("0"), petrol_pump=pump,
        )
        set_account(
            candidates=[f"{pump_name} Bank", f"Pump {pos} Bank", bank_name],
            name=bank_name, account_type="Bank", account_number=bank_number,
            balance=bank_balance, petrol_pump=pump,
        )

    db.session.commit()
    print("Bank accounts seeded.")


# --------------------------------------------------------------------------- #
# 2. Tank / machine / nozzle setup per pump
# --------------------------------------------------------------------------- #
# tank_name -> (product_name, capacity, current_stock)
PUMP_TANK_SPECS = {
    "Punjab Petroleum": {
        "P1": ("Petrol", Decimal("23474"), Decimal("10075")),
        "P2": ("Petrol", Decimal("23474"), Decimal("6905")),
        "D1": ("Diesel", Decimal("23374"), Decimal("6787")),
        "D2": ("Diesel", Decimal("23374"), Decimal("6085")),
    },
    "Ahmed Filling Station": {
        "P1": ("Petrol", Decimal("16643"), Decimal("9830")),
        "P2": ("Petrol", Decimal("16643"), Decimal("8370")),
        "D": ("Diesel", Decimal("23275"), Decimal("7108")),
        "O": ("High Octane", Decimal("16643"), Decimal("454")),
    },
    "Alfateh Petroleum": {
        "P1": ("Petrol", Decimal("15682"), Decimal("252")),
        "P2": ("Petrol", Decimal("46248"), Decimal("2986")),
        "D": ("Diesel", Decimal("46248"), Decimal("4880")),
    },
}

# tank_name -> [(machine_suffix, [nozzle numbers]), ...]. Tanks not listed
# here (e.g. Alfateh's P1) have no machine yet.
PUMP_MACHINE_SPECS = {
    "Punjab Petroleum": {
        "D1": [(1, ["1", "2"]), (2, ["3", "4"])],
        "D2": [(1, ["5", "6"]), (2, ["15", "16"])],
        "P1": [(1, ["7", "8"]), (2, ["9", "10"])],
        "P2": [(1, ["11", "12"]), (2, ["13", "14"])],
    },
    "Ahmed Filling Station": {
        "P1": [(1, ["7", "8"]), (2, ["9", "10"])],
        "P2": [(1, ["3", "4"]), (2, ["5", "6"])],
        "D": [(1, ["1", "2"]), (2, ["13", "14"]), (3, ["15", "16"])],
        "O": [(1, ["11", "12"])],
    },
    "Alfateh Petroleum": {
        "P2": [(1, ["1", "2"])],
        "D": [(1, ["3", "4"])],
    },
}


def seed_pump_setups():
    """Set up each pump's real tanks/machines/nozzles idempotently and WITHOUT
    destructive deletes.

    Why no delete-and-rebuild: production runs on MySQL, which ENFORCES foreign
    keys (SQLite does not). Once an opening-stock PumpPurchaseItem references a
    tank, deleting that tank raises an IntegrityError on MySQL — which silently
    broke the whole pump section on the server while working fine locally. So
    instead we UPSERT the real rows by name (keeping their ids stable, so
    purchase items stay valid) and merely DEACTIVATE any leftover default rows
    ("Petrol Tank", "Machine 1", ...) — deactivation is always FK-safe.
    """
    for pump_name, tank_specs in PUMP_TANK_SPECS.items():
        pump = _find_pump(pump_name)
        if pump is None:
            continue

        desired_tank_names = set(tank_specs.keys())
        machine_specs = PUMP_MACHINE_SPECS.get(pump_name, {})
        # Which machine names + nozzle numbers this pump SHOULD have.
        desired_machine_names = set()
        desired_nozzle_numbers = set()
        for tank_name, machines in machine_specs.items():
            for suffix, nozzle_numbers in machines:
                desired_machine_names.add(f"{tank_name} Machine {suffix}")
                desired_nozzle_numbers.update(nozzle_numbers)

        # --- Upsert tanks by (pump, tank_name) ---
        tanks = {}
        for tank_name, (product_name, capacity, current_stock) in tank_specs.items():
            product = Product.query.filter_by(name=product_name).first()
            tank = PumpTank.query.filter_by(
                petrol_pump_id=pump.id, tank_name=tank_name
            ).first()
            newly_created = tank is None
            if newly_created:
                tank = PumpTank(petrol_pump_id=pump.id, tank_name=tank_name)
                db.session.add(tank)
            if product is not None:
                tank.product_id = product.id
            tank.capacity_liters = capacity
            tank.opening_stock_liters = current_stock
            # Only (re)set live stock when the tank is new or still empty, so a
            # re-deploy never wipes out real trading once it has started.
            if newly_created or not tank.current_stock_liters:
                tank.current_stock_liters = current_stock
            tank.is_active = True
            db.session.flush()
            tanks[tank_name] = tank

        # --- Upsert machines by (pump, machine_name) + their nozzles ---
        for tank_name, machines in machine_specs.items():
            tank = tanks[tank_name]
            for suffix, nozzle_numbers in machines:
                machine_name = f"{tank_name} Machine {suffix}"
                machine = PumpMachine.query.filter_by(
                    petrol_pump_id=pump.id, machine_name=machine_name
                ).first()
                if machine is None:
                    machine = PumpMachine(
                        petrol_pump_id=pump.id, machine_name=machine_name
                    )
                    db.session.add(machine)
                machine.product_id = tank.product_id
                machine.tank_id = tank.id
                machine.is_active = True
                db.session.flush()
                for num in nozzle_numbers:
                    nozzle = PumpNozzle.query.filter_by(
                        petrol_pump_id=pump.id, nozzle_number=num
                    ).first()
                    if nozzle is None:
                        nozzle = PumpNozzle(
                            petrol_pump_id=pump.id, nozzle_number=num,
                            opening_reading=Decimal("0"), current_reading=Decimal("0"),
                        )
                        db.session.add(nozzle)
                    nozzle.machine_id = machine.id
                    nozzle.product_id = tank.product_id
                    nozzle.is_active = True
                    db.session.flush()

        # --- Deactivate any leftover default rows (never delete — FK-safe) ---
        deactivated = 0
        for nozzle in PumpNozzle.query.filter_by(petrol_pump_id=pump.id).all():
            if nozzle.nozzle_number not in desired_nozzle_numbers:
                nozzle.is_active = False
                deactivated += 1
        for machine in PumpMachine.query.filter_by(petrol_pump_id=pump.id).all():
            if machine.machine_name not in desired_machine_names:
                machine.is_active = False
                deactivated += 1
        for tank in PumpTank.query.filter_by(petrol_pump_id=pump.id).all():
            if tank.tank_name not in desired_tank_names:
                tank.is_active = False
                deactivated += 1

        db.session.commit()
        print(f"{pump_name}: tanks/machines/nozzles upserted "
              f"({len(tanks)} tanks; {deactivated} leftover default rows deactivated).")


# --------------------------------------------------------------------------- #
# 3. Product purchase rates
# --------------------------------------------------------------------------- #
FUEL_PURCHASE_RATES = {
    "Petrol": Decimal("293.66"),
    "Diesel": Decimal("305.63"),
    "High Octane": Decimal("331.81"),
}

LUBRICANT_PURCHASE_RATES = {
    "Diesel Lube 4L": Decimal("3732.8"),
    "Deo 8000 4L": Decimal("5856.8"),
    "Deo 8000 10L": Decimal("14642"),
    "Deo 8000 Drum": Decimal("1450"),
    "Deo 6000 4L": Decimal("5112"),
    "Deo 3000 4L": Decimal("4425"),
    "Deo Max 4L": Decimal("6450"),
    "Carrient F/S 4L": Decimal("7450"),
    "Carrient Plus 4L": Decimal("5372"),
    "Carrient Plus 1L": Decimal("1373"),
    "Blaze 1L": Decimal("1123"),
    "Blaze 700ml": Decimal("812"),
    "M.oil Drum": Decimal("761.2"),
}


def seed_purchase_rates():
    for name, rate in {**FUEL_PURCHASE_RATES, **LUBRICANT_PURCHASE_RATES}.items():
        product = Product.query.filter_by(name=name).first()
        if product is not None:
            product.default_purchase_rate = rate
    db.session.commit()
    print("Purchase rates seeded.")


# --------------------------------------------------------------------------- #
# 4. Opening fuel stock cost basis (not physically received, no GL posted)
# --------------------------------------------------------------------------- #
# pump -> tank_name -> rate actually paid for THAT pump's fuel (Ahmed's differs
# from Punjab/Alfateh's — see the opening purchase note, not the global default).
PUMP_FUEL_RATES = {
    "Punjab Petroleum": {"P1": "293.66", "P2": "293.66", "D1": "305.63", "D2": "305.63"},
    "Ahmed Filling Station": {"P1": "293.80", "P2": "293.80", "D": "305.77", "O": "331.81"},
    "Alfateh Petroleum": {"P1": "293.66", "P2": "293.66", "D": "305.63"},
}

# (name, quantity, rate, amount) per pump, matching the owner's lubricant sheets.
PUMP_LUBRICANT_ROWS = {
    "Punjab Petroleum": [
        ("Diesel Lube 4L", 58, Decimal("3732.8"), Decimal("216502.4")),
        ("Deo 8000 4L", 85, Decimal("5856.8"), Decimal("497828")),
        ("Deo 8000 10L", 48, Decimal("14642"), Decimal("702816")),
        ("Deo 6000 4L", 42, Decimal("5112"), Decimal("214704")),
        ("Carrient F/S 4L", 34, Decimal("7450"), Decimal("253300")),
        ("Deo Max 4L", 7, Decimal("6450"), Decimal("45150")),
        ("Carrient Plus 4L", 34, Decimal("5372"), Decimal("182648")),
        ("Carrient Plus 1L", 96, Decimal("1373"), Decimal("131808")),
        ("Blaze 1L", 44, Decimal("1123"), Decimal("49412")),
        ("Blaze 700ml", 182, Decimal("812"), Decimal("147784")),
        ("M.oil Drum", 740, Decimal("761.2"), Decimal("563288")),
        ("Deo 8000 Drum", 210, Decimal("1450"), Decimal("304500")),
    ],
    "Ahmed Filling Station": [
        ("Diesel Lube 4L", 29, Decimal("3732.8"), Decimal("108251")),
        ("Deo 8000 4L", 44, Decimal("5856.8"), Decimal("257700")),
        ("Deo Max 4L", 2, Decimal("6450"), Decimal("12902")),
        ("Carrient F/S 4L", 18, Decimal("7450"), Decimal("134140")),
        ("Deo 3000 4L", 6, Decimal("4425"), Decimal("26548")),
        ("Carrient Plus 4L", 16, Decimal("5372"), Decimal("85952")),
        ("Carrient Plus 1L", 12, Decimal("1373"), Decimal("16476")),
        ("Blaze 1L", 75, Decimal("1123"), Decimal("84170")),
        ("Blaze 700ml", 46, Decimal("812"), Decimal("37342")),
        ("M.oil Drum", 156, Decimal("761.2"), Decimal("118745")),
    ],
}


def _get_or_create_purchase(pump, invoice_number):
    existing = PumpPurchase.query.filter_by(
        petrol_pump_id=pump.id, invoice_number=invoice_number
    ).first()
    if existing is not None:
        return existing, False
    pso_vendor = Vendor.query.filter_by(name="PSO", business_unit_id=pump.business_unit_id).first()
    purchase = PumpPurchase(
        petrol_pump_id=pump.id, vendor_id=pso_vendor.id, purchase_date=OPENING_DATE,
        invoice_number=invoice_number, delivery_status="Received",
        tank_received=False, stock_posted=False, total_amount=Decimal("0"),
        is_active=True,
        notes="Opening stock as of 30 June — not a physical delivery; "
              "establishes quantity/cost basis only (no GL posting).",
    )
    db.session.add(purchase)
    db.session.flush()
    return purchase, True


def seed_opening_fuel_stock():
    # Rebuild the purchase's line items every run so they always reference the
    # CURRENT active tanks — this self-heals any stale/broken tank links left by
    # earlier (delete-and-rebuild) deploys. PumpPurchaseItem is a leaf table
    # (nothing references it), so clearing it is FK-safe on MySQL.
    for pump_name, rates in PUMP_FUEL_RATES.items():
        pump = _find_pump(pump_name)
        if pump is None:
            continue
        purchase, is_new = _get_or_create_purchase(pump, "OPENING-STOCK")
        PumpPurchaseItem.query.filter_by(pump_purchase_id=purchase.id).delete()
        db.session.flush()
        total = Decimal("0")
        item_count = 0
        for tank in PumpTank.query.filter_by(petrol_pump_id=pump.id, is_active=True).all():
            # Use the tank's OPENING stock (fixed) not its live current stock, so
            # re-deploying after trading never rewrites the opening record.
            qty = tank.opening_stock_liters or tank.current_stock_liters or Decimal("0")
            if qty <= 0:
                continue
            rate = Decimal(rates.get(tank.tank_name, "0"))
            amount = (qty * rate).quantize(Decimal("0.01"))
            db.session.add(PumpPurchaseItem(
                pump_purchase_id=purchase.id, product_id=tank.product_id,
                tank_id=tank.id, quantity_liters=qty, rate=rate, total_amount=amount,
            ))
            total += amount
            item_count += 1
        purchase.total_amount = total
        db.session.commit()
        print(f"{pump_name}: opening fuel stock purchase set "
              f"({item_count} tank lines, total {total}).")


def seed_opening_lubricant_stock():
    category = ProductCategory.query.filter_by(name="Lubricants").first()
    for pump_name, rows in PUMP_LUBRICANT_ROWS.items():
        pump = _find_pump(pump_name)
        if pump is None:
            continue
        purchase, is_new = _get_or_create_purchase(pump, "OPENING-LUBRICANTS")
        PumpPurchaseItem.query.filter_by(pump_purchase_id=purchase.id).delete()
        db.session.flush()
        total = Decimal("0")
        for name, qty, rate, amount in rows:
            product = Product.query.filter_by(name=name, category_id=category.id).first()
            if product is None:
                product = Product(
                    category_id=category.id, name=name, unit="bottles",
                    default_purchase_rate=rate, is_active=True,
                )
                db.session.add(product)
                db.session.flush()
            db.session.add(PumpPurchaseItem(
                pump_purchase_id=purchase.id, product_id=product.id, tank_id=None,
                quantity_liters=Decimal(str(qty)), rate=rate, total_amount=amount,
            ))
            total += amount
        purchase.total_amount = total
        db.session.commit()
        print(f"{pump_name}: opening lubricant stock purchase set "
              f"({len(rows)} lines, total {total}).")


# --------------------------------------------------------------------------- #
# 5. PSO's opening payable — recomputed from whatever opening purchases exist,
#    so it's always correct however many times this script runs.
# --------------------------------------------------------------------------- #
def seed_pso_payable():
    vendor = Vendor.query.filter_by(
        name="PSO", business_unit_id=BusinessUnit.query.filter_by(
            type=BusinessUnitType.PETROL_PUMP_RETAIL
        ).first().id,
    ).first()
    if vendor is None:
        print("PSO vendor not found — skipping payable seed.")
        return

    opening_total = (
        db.session.query(db.func.coalesce(db.func.sum(PumpPurchase.total_amount), 0))
        .filter(PumpPurchase.vendor_id == vendor.id,
                PumpPurchase.invoice_number.in_(["OPENING-STOCK", "OPENING-LUBRICANTS"]))
        .scalar()
    )
    vendor.opening_balance = PSO_BASE_PAYABLE - Decimal(str(opening_total))
    db.session.commit()
    print(f"PSO vendor opening_balance set to {vendor.opening_balance} "
          f"(base {PSO_BASE_PAYABLE} minus {opening_total} of opening stock already covered by it).")

    from app.accounting.ledger import vendor_balance
    print("PSO vendor's overall computed payable (should be", PSO_BASE_PAYABLE, "):", vendor_balance(vendor))


def run_all():
    """Run every step. Assumes an app context is already active (this is what
    `deploy.py` calls). For standalone use, see `main()` below."""
    existing_pumps = [(p.id, p.name) for p in PetrolPump.query.all()]
    print(f"Pumps currently in this database: {existing_pumps}")
    seed_bank_accounts()
    seed_pump_setups()
    seed_purchase_rates()
    seed_opening_fuel_stock()
    seed_opening_lubricant_stock()
    seed_pso_payable()
    print("\nOpening data seed complete.")


def main():
    """Standalone entry point: creates its own app + context, then runs everything."""
    from app import create_app
    app = create_app()
    with app.app_context():
        run_all()


if __name__ == "__main__":
    main()
