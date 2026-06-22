"""Idempotent seeding for basic petrol pump setup (machines, nozzles, tanks).

Each existing petrol pump gets 2 machines (each with a Petrol and a Diesel
nozzle) and 3 tanks (Petrol, Diesel, High Octane). Guarded on empty tables so
user edits are never overwritten or duplicated.
"""
from app.extensions import db
from app.core.models import PetrolPump, Product, ProductCategory
from app.petrol_pumps.models import PumpMachine, PumpNozzle, PumpTank

FUEL_CATEGORY_NAME = "Fuel Products"

# Tank product -> default capacity in liters.
TANK_CAPACITY = {
    "Petrol": 10000,
    "Diesel": 10000,
    "High Octane": 5000,
}


def _fuel_product(name):
    """Look up a fuel product by name within the Fuel Products category."""
    return (
        Product.query.join(ProductCategory)
        .filter(ProductCategory.name == FUEL_CATEGORY_NAME, Product.name == name)
        .first()
    )


def seed_pump_setup_data():
    """Seed machines, nozzles and tanks for every petrol pump."""
    created = {"pump_machines": 0, "pump_nozzles": 0, "pump_tanks": 0}

    pumps = PetrolPump.query.order_by(PetrolPump.id).all()
    petrol = _fuel_product("Petrol")
    diesel = _fuel_product("Diesel")
    high_octane = _fuel_product("High Octane")

    # --- Machines + their nozzles ---
    if PumpMachine.query.count() == 0:
        for pump in pumps:
            for machine_index in (1, 2):
                machine = PumpMachine(
                    petrol_pump=pump, machine_name=f"Machine {machine_index}"
                )
                db.session.add(machine)
                created["pump_machines"] += 1

                # Nozzle 1 -> Petrol, Nozzle 2 -> Diesel
                for nozzle_number, product in (
                    ("Nozzle 1", petrol),
                    ("Nozzle 2", diesel),
                ):
                    if product is not None:
                        db.session.add(
                            PumpNozzle(
                                petrol_pump=pump,
                                machine=machine,
                                product=product,
                                nozzle_number=nozzle_number,
                            )
                        )
                        created["pump_nozzles"] += 1
        db.session.commit()

    # --- Tanks ---
    if PumpTank.query.count() == 0:
        for pump in pumps:
            for product in (petrol, diesel, high_octane):
                if product is None:
                    continue
                db.session.add(
                    PumpTank(
                        petrol_pump=pump,
                        product=product,
                        tank_name=f"{product.name} Tank",
                        capacity_liters=TANK_CAPACITY.get(product.name),
                    )
                )
                created["pump_tanks"] += 1
        db.session.commit()

    return created
