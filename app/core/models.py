"""Core / shared database models for the Oil & Gas Multi-Business ERP.

These are the foundation tables that every future business module will build
on top of. The most important idea here is the **BusinessUnit**: every future
transaction (pump sale, bulk sale, carriage trip, agency sale, head office
entry) must be tagged with a business unit so the businesses are never mixed.

Only foundation models live here for now:
  - Company       : the owning company / head office details
  - BusinessUnit  : the 5 business units (with a type enum)
  - PetrolPump    : the 3 physical pumps, linked to the Petrol Pump Retail unit
"""
import enum
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    """Return the current time as a timezone-aware UTC datetime.

    Used as the default for created_at / updated_at. Defined as a helper so we
    avoid the deprecated datetime.utcnow() on modern Python versions.
    """
    return datetime.now(timezone.utc)


class BusinessUnitType(enum.Enum):
    """The fixed set of business unit types the ERP supports."""

    PETROL_PUMP_RETAIL = "PETROL_PUMP_RETAIL"
    BULK_SALE = "BULK_SALE"
    CARRIAGE = "CARRIAGE"
    OIL_AGENCY = "OIL_AGENCY"
    HEAD_OFFICE = "HEAD_OFFICE"


# The units a product can be measured/sold in. Kept as a simple list (and the
# Product.unit column is a plain string) so adding a unit later needs no schema
# change. Forms use this for the dropdown; routes use it for validation.
PRODUCT_UNITS = ["liters", "bottles", "cartons", "drums", "pieces"]


class VendorType(enum.Enum):
    """The fixed set of vendor types. The value is the human-readable label."""

    PSO = "PSO"
    AGENCY_SUPPLIER = "Agency Supplier Company"
    RENTED_VEHICLE_PROVIDER = "Rented Vehicle Provider"
    MAINTENANCE_VENDOR = "Maintenance Vendor"
    ELECTRICIAN = "Electrician"
    PLUMBER = "Plumber"
    GENERATOR_MECHANIC = "Generator Mechanic"
    GARDENER = "Gardener"
    CLEANING_VENDOR = "Cleaning Vendor"
    SECURITY_VENDOR = "Security Vendor"
    GENERAL_VENDOR = "General Vendor"
    OTHER = "Other"


# Vehicle classification and ownership options. Kept as plain string lists (the
# columns are strings) for the same reason as PRODUCT_UNITS: easy to extend
# without a schema change. Forms use them for dropdowns; routes for validation.
VEHICLE_TYPES = ["Tanker", "Truck", "Pickup", "Other"]
OWNERSHIP_TYPES = ["Own Vehicle", "Rented Vehicle", "Customer Vehicle"]

# Cash / bank / wallet account classifications. Plain string list (the column
# is a string) for easy extension without a schema change.
ACCOUNT_TYPES = [
    "Cash",
    "Bank",
    "Wallet",
    "PSO Ledger",
    "Customer Receivable Control",
    "Vendor Payable Control",
    "Other",
]


class Company(db.Model):
    """The owning company / head office master record."""

    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.String(300), nullable=True)
    contact_number = db.Column(db.String(50), nullable=True)
    logo_path = db.Column(db.String(300), nullable=True)
    ntn_number = db.Column(db.String(50), nullable=True)
    default_currency = db.Column(db.String(10), nullable=False, default="PKR")
    financial_year_start = db.Column(db.Date, nullable=True)
    financial_year_end = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self):
        return f"<Company id={self.id} name={self.name!r}>"


class BusinessUnit(db.Model):
    """One of the 5 business units the company operates.

    Every future transactional table will carry a business_unit_id pointing
    here, which is how the system keeps each business separate while still
    allowing a combined head office view.
    """

    __tablename__ = "business_units"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    type = db.Column(db.Enum(BusinessUnitType), nullable=False)
    description = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # A Petrol Pump Retail business unit owns many physical pumps.
    petrol_pumps = db.relationship(
        "PetrolPump", back_populates="business_unit", lazy="select"
    )

    def __repr__(self):
        return (
            f"<BusinessUnit id={self.id} name={self.name!r} "
            f"type={self.type.name}>"
        )


class PetrolPump(db.Model):
    """A single physical petrol pump.

    Always belongs to the Petrol Pump Retail business unit. Account name fields
    are kept as simple text for now; they will be linked to real ledger /
    cash-bank accounts in a later accounting task.
    """

    __tablename__ = "petrol_pumps"

    id = db.Column(db.Integer, primary_key=True)
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=False
    )
    name = db.Column(db.String(150), nullable=False)
    location = db.Column(db.String(200), nullable=True)
    pso_account_name = db.Column(db.String(150), nullable=True)
    cash_account_name = db.Column(db.String(150), nullable=True)
    bank_account_name = db.Column(db.String(150), nullable=True)
    manager_name = db.Column(db.String(150), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit", back_populates="petrol_pumps")

    def __repr__(self):
        return f"<PetrolPump id={self.id} name={self.name!r}>"


class ProductCategory(db.Model):
    """A grouping of products (e.g. Fuel Products, Lubricants, Agency Products)."""

    __tablename__ = "product_categories"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    products = db.relationship(
        "Product", back_populates="category", lazy="select"
    )

    def __repr__(self):
        return f"<ProductCategory id={self.id} name={self.name!r}>"


class Product(db.Model):
    """A single product/item that belongs to a product category.

    Rates are optional defaults used to pre-fill future purchase/sale entries;
    they are not transactions themselves.
    """

    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(
        db.Integer, db.ForeignKey("product_categories.id"), nullable=False
    )
    name = db.Column(db.String(150), nullable=False)
    unit = db.Column(db.String(30), nullable=False)
    default_purchase_rate = db.Column(db.Numeric(12, 2), nullable=True)
    default_sale_rate = db.Column(db.Numeric(12, 2), nullable=True)
    linked_stock_account_name = db.Column(db.String(150), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    category = db.relationship("ProductCategory", back_populates="products")

    def __repr__(self):
        return f"<Product id={self.id} name={self.name!r}>"


class Customer(db.Model):
    """A customer that belongs to a single business unit.

    The same customer name may appear under different business units, but not
    twice within the same business unit (enforced in the routes).
    """

    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=False
    )
    # When the customer belongs to the Petrol Pump Retail unit, this tags WHICH
    # pump they are a customer of (so each pump has its own customers + ledger).
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=True
    )
    name = db.Column(db.String(150), nullable=False)
    business_type = db.Column(db.String(100), nullable=True)
    cnic_ntn = db.Column(db.String(50), nullable=True)
    phone_number = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    contact_person = db.Column(db.String(150), nullable=True)
    opening_balance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    credit_limit = db.Column(db.Numeric(14, 2), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit")
    petrol_pump = db.relationship("PetrolPump")

    def __repr__(self):
        return f"<Customer id={self.id} name={self.name!r}>"


class Vendor(db.Model):
    """A vendor/supplier that belongs to a single business unit.

    Like customers, vendor names are unique only within a business unit.
    """

    __tablename__ = "vendors"

    id = db.Column(db.Integer, primary_key=True)
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=False
    )
    name = db.Column(db.String(150), nullable=False)
    vendor_type = db.Column(db.Enum(VendorType), nullable=False)
    phone_number = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    contact_person = db.Column(db.String(150), nullable=True)
    opening_balance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit")

    def __repr__(self):
        return f"<Vendor id={self.id} name={self.name!r}>"


class Driver(db.Model):
    """A driver who may be assigned to one or more vehicles."""

    __tablename__ = "drivers"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    cnic = db.Column(db.String(50), nullable=True)
    phone_number = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    salary = db.Column(db.Numeric(12, 2), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    # A driver can be linked to several vehicles.
    vehicles = db.relationship("Vehicle", back_populates="driver")

    def __repr__(self):
        return f"<Driver id={self.id} name={self.name!r}>"


class Vehicle(db.Model):
    """A vehicle/tanker, optionally assigned to a driver."""

    __tablename__ = "vehicles"

    id = db.Column(db.Integer, primary_key=True)
    vehicle_number = db.Column(
        db.String(50), nullable=False, unique=True, index=True
    )
    vehicle_type = db.Column(db.String(30), nullable=False)
    capacity = db.Column(db.Numeric(12, 2), nullable=True)
    ownership_type = db.Column(db.String(30), nullable=False)
    owner_name = db.Column(db.String(150), nullable=True)
    driver_id = db.Column(
        db.Integer, db.ForeignKey("drivers.id"), nullable=True
    )
    documents_path = db.Column(db.String(300), nullable=True)
    insurance_details = db.Column(db.String(300), nullable=True)
    fitness_permit_details = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    driver = db.relationship("Driver", back_populates="vehicles")

    def __repr__(self):
        return f"<Vehicle id={self.id} number={self.vehicle_number!r}>"


class CashBankAccount(db.Model):
    """A cash, bank, wallet or control account.

    May belong to a business unit, or be global (business_unit_id is NULL) for
    company / head office level accounts. Names are unique within their scope
    (the same name is allowed under different business units).
    """

    __tablename__ = "cash_bank_accounts"

    id = db.Column(db.Integer, primary_key=True)
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=True
    )
    # An account may belong to a SPECIFIC petrol pump (its own bank / wallet),
    # so pump screens (e.g. daily closing) only offer that pump's accounts.
    # NULL = a general / head-office / business-unit account.
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=True
    )
    name = db.Column(db.String(150), nullable=False)
    account_type = db.Column(db.String(50), nullable=False)
    account_number = db.Column(db.String(80), nullable=True)
    bank_name = db.Column(db.String(150), nullable=True)
    opening_balance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    current_balance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit")
    petrol_pump = db.relationship("PetrolPump")

    def __repr__(self):
        return f"<CashBankAccount id={self.id} name={self.name!r}>"


class ExpenseCategory(db.Model):
    """A category for classifying expenses.

    May belong to a business unit or be global (business_unit_id is NULL).
    Names are unique within their scope.
    """

    __tablename__ = "expense_categories"

    id = db.Column(db.Integer, primary_key=True)
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=True
    )
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit")

    def __repr__(self):
        return f"<ExpenseCategory id={self.id} name={self.name!r}>"
