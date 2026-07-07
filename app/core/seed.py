"""Idempotent database seeding for the core foundation data.

Running this more than once will NOT create duplicate records: each insert is
guarded by a lookup on a natural key (company name, business unit type, pump
name). This is the single source of truth used by both `flask seed` and the
root-level `seed.py` script.
"""
from app.extensions import db
from app.core.models import (
    BusinessUnit,
    BusinessUnitType,
    CashBankAccount,
    Company,
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

COMPANY_NAME = "Oil & Gas Business Management Company"

# (display name, type) for each business unit. Type is the unique key.
BUSINESS_UNITS = [
    ("Petrol Pump Retail", BusinessUnitType.PETROL_PUMP_RETAIL),
    ("Bulk Sale", BusinessUnitType.BULK_SALE),
    ("Carriage", BusinessUnitType.CARRIAGE),
    ("Oil Agencies", BusinessUnitType.OIL_AGENCY),
    ("Head Office", BusinessUnitType.HEAD_OFFICE),
]

PETROL_PUMP_NAMES = ["Punjab Petroleum", "Ahmed Filling Station", "Alfateh Petroleum"]

# Default vendors: (vendor name, vendor type, business unit type).
DEFAULT_VENDORS = [
    ("PSO", VendorType.PSO, BusinessUnitType.PETROL_PUMP_RETAIL),
    ("PSO", VendorType.PSO, BusinessUnitType.BULK_SALE),
    ("Default Rented Vehicle Provider", VendorType.RENTED_VEHICLE_PROVIDER,
     BusinessUnitType.CARRIAGE),
    ("Default Agency Supplier", VendorType.AGENCY_SUPPLIER,
     BusinessUnitType.OIL_AGENCY),
]

# Default customers: (customer name, business unit type).
DEFAULT_CUSTOMERS = [
    ("Walk-in Customer", BusinessUnitType.PETROL_PUMP_RETAIL),
    ("Default Bulk Customer", BusinessUnitType.BULK_SALE),
    ("Default Agency Customer", BusinessUnitType.OIL_AGENCY),
    ("Default Carriage Customer", BusinessUnitType.CARRIAGE),
]

# Default drivers (by name).
DEFAULT_DRIVERS = ["Default Driver 1", "Default Driver 2"]

# Default vehicles: (vehicle number, vehicle type, ownership type, owner name).
DEFAULT_VEHICLES = [
    ("OWN-001", "Tanker", "Own Vehicle", None),
    ("RENT-001", "Tanker", "Rented Vehicle", "Default Rented Vehicle Provider"),
    ("CUST-001", "Tanker", "Customer Vehicle", None),
]

# Default cash/bank/wallet accounts: (name, business unit type, account type).
DEFAULT_ACCOUNTS = [
    ("Head Office Cash", BusinessUnitType.HEAD_OFFICE, "Cash"),
    ("Head Office Bank", BusinessUnitType.HEAD_OFFICE, "Bank"),
    ("Pump 1 Cash", BusinessUnitType.PETROL_PUMP_RETAIL, "Cash"),
    ("Pump 2 Cash", BusinessUnitType.PETROL_PUMP_RETAIL, "Cash"),
    ("Pump 3 Cash", BusinessUnitType.PETROL_PUMP_RETAIL, "Cash"),
    ("Pump 1 Bank", BusinessUnitType.PETROL_PUMP_RETAIL, "Bank"),
    ("Pump 2 Bank", BusinessUnitType.PETROL_PUMP_RETAIL, "Bank"),
    ("Pump 3 Bank", BusinessUnitType.PETROL_PUMP_RETAIL, "Bank"),
    ("Easypaisa", BusinessUnitType.HEAD_OFFICE, "Wallet"),
    ("JazzCash", BusinessUnitType.HEAD_OFFICE, "Wallet"),
    ("PSO Payable", BusinessUnitType.PETROL_PUMP_RETAIL, "PSO Ledger"),
    ("Agency Cash Account", BusinessUnitType.OIL_AGENCY, "Cash"),
    ("Carriage Cash Account", BusinessUnitType.CARRIAGE, "Cash"),
]

# Default expense categories per business unit type.
DEFAULT_EXPENSE_CATEGORIES = {
    BusinessUnitType.PETROL_PUMP_RETAIL: [
        "Staff salaries", "Electricity bill", "Generator fuel", "Machine repair",
        "Maintenance expense", "Tank cleaning", "Shop/office expense",
        "Security expense", "Cleaning/sweeper expense", "Washroom maintenance",
        "Stationery", "Tea/food expense", "Bank charges", "Miscellaneous expense",
    ],
    BusinessUnitType.HEAD_OFFICE: [
        "Home expense", "Welfare expense", "Office expense",
        "Staff/management expense", "Bank charges", "Legal/document expense",
        "Vehicle/general expense", "Miscellaneous expense",
    ],
    BusinessUnitType.CARRIAGE: [
        "Fuel expense", "Toll tax", "Loading/unloading expense",
        "Driver expense", "Maintenance expense", "Other expense",
    ],
    BusinessUnitType.OIL_AGENCY: [
        "Loading/unloading expense", "Rented vehicle expense",
        "Own vehicle trip expense", "Office expense", "Bank charges",
        "Documentation expense", "Miscellaneous expense",
    ],
}

# Product categories and the products inside each one.
# Each entry: category name -> list of (product name, unit).
PRODUCT_CATALOG = {
    "Fuel Products": [
        ("Petrol", "liters"),
        ("Diesel", "liters"),
        ("High Octane", "liters"),
    ],
    "Lubricants": [
        ("Engine Oil", "bottles"),
        ("PSO Lubricants", "bottles"),
    ],
    "Agency Products": [
        ("LDO", "liters"),
        ("Kerosene", "liters"),
    ],
}


def seed_data():
    """Insert the core foundation records if they don't already exist.

    Returns a small summary dict so callers can print what happened.
    """
    created = {
        "company": 0,
        "business_units": 0,
        "petrol_pumps": 0,
        "product_categories": 0,
        "products": 0,
        "vendors": 0,
        "customers": 0,
        "drivers": 0,
        "vehicles": 0,
        "cash_bank_accounts": 0,
        "expense_categories": 0,
    }

    # --- Company ---
    company = Company.query.filter_by(name=COMPANY_NAME).first()
    if company is None:
        company = Company(
            name=COMPANY_NAME,
            address="Head Office",
            contact_number="",
            default_currency="PKR",
        )
        db.session.add(company)
        created["company"] += 1

    # --- Business Units (one per type) ---
    for name, unit_type in BUSINESS_UNITS:
        existing = BusinessUnit.query.filter_by(type=unit_type).first()
        if existing is None:
            db.session.add(BusinessUnit(name=name, type=unit_type))
            created["business_units"] += 1

    # Commit so the Petrol Pump Retail unit gets an id before we link pumps.
    db.session.commit()

    # --- Petrol Pumps (all under the Petrol Pump Retail unit) ---
    # Only seed the default pumps on a fresh database. Guarding on the table
    # being empty (instead of on each name) means that once a user renames a
    # pump in Master Data, re-running the seed will NOT recreate the defaults.
    retail_unit = BusinessUnit.query.filter_by(
        type=BusinessUnitType.PETROL_PUMP_RETAIL
    ).first()

    if retail_unit is not None and PetrolPump.query.count() == 0:
        for pump_name in PETROL_PUMP_NAMES:
            db.session.add(
                PetrolPump(name=pump_name, business_unit=retail_unit)
            )
            created["petrol_pumps"] += 1

    db.session.commit()

    # --- Product Categories + Products ---
    # Same idea: seed the whole catalogue only when there are no categories yet,
    # so user edits/renames are never overwritten or duplicated.
    if ProductCategory.query.count() == 0:
        for category_name in PRODUCT_CATALOG:
            db.session.add(ProductCategory(name=category_name))
            created["product_categories"] += 1
        db.session.commit()

        for category_name, products in PRODUCT_CATALOG.items():
            category = ProductCategory.query.filter_by(name=category_name).first()
            for product_name, unit in products:
                db.session.add(
                    Product(category=category, name=product_name, unit=unit)
                )
                created["products"] += 1
        db.session.commit()

    # --- Default Vendors ---
    # Empty-table guard: seed defaults only on a fresh vendors table so user
    # edits/renames are never overwritten or duplicated.
    if Vendor.query.count() == 0:
        for name, vendor_type, unit_type in DEFAULT_VENDORS:
            unit = BusinessUnit.query.filter_by(type=unit_type).first()
            if unit is not None:
                db.session.add(
                    Vendor(name=name, vendor_type=vendor_type, business_unit=unit)
                )
                created["vendors"] += 1
        db.session.commit()

    # --- Default Customers ---
    if Customer.query.count() == 0:
        for name, unit_type in DEFAULT_CUSTOMERS:
            unit = BusinessUnit.query.filter_by(type=unit_type).first()
            if unit is not None:
                db.session.add(Customer(name=name, business_unit=unit))
                created["customers"] += 1
        db.session.commit()

    # --- Default Drivers ---
    if Driver.query.count() == 0:
        for driver_name in DEFAULT_DRIVERS:
            db.session.add(Driver(name=driver_name))
            created["drivers"] += 1
        db.session.commit()

    # --- Default Vehicles ---
    if Vehicle.query.count() == 0:
        for number, vehicle_type, ownership_type, owner_name in DEFAULT_VEHICLES:
            db.session.add(
                Vehicle(
                    vehicle_number=number,
                    vehicle_type=vehicle_type,
                    ownership_type=ownership_type,
                    owner_name=owner_name,
                )
            )
            created["vehicles"] += 1
        db.session.commit()

    # --- Default Cash/Bank/Wallet Accounts ---
    if CashBankAccount.query.count() == 0:
        for name, unit_type, account_type in DEFAULT_ACCOUNTS:
            unit = BusinessUnit.query.filter_by(type=unit_type).first()
            if unit is not None:
                db.session.add(
                    CashBankAccount(
                        name=name,
                        account_type=account_type,
                        business_unit=unit,
                    )
                )
                created["cash_bank_accounts"] += 1
        db.session.commit()

    # --- Default Expense Categories ---
    if ExpenseCategory.query.count() == 0:
        for unit_type, names in DEFAULT_EXPENSE_CATEGORIES.items():
            unit = BusinessUnit.query.filter_by(type=unit_type).first()
            if unit is None:
                continue
            for name in names:
                db.session.add(
                    ExpenseCategory(name=name, business_unit=unit)
                )
                created["expense_categories"] += 1
        db.session.commit()

    return created
