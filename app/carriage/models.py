"""Carriage / Transport models: vehicle/tanker trips.

A carriage trip moves fuel/product between points (PSO, pumps, godowns,
customers). Carriage has its own accounting and profit/loss, separate from the
pump/bulk/agency businesses it may serve.

Net profit is calculated as:
    net_profit = total_freight_amount
                 - (rent_amount + fuel_expense + toll_tax
                    + loading_unloading_expense + driver_expense
                    + maintenance_expense + other_expense)
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


TRIP_TYPES = [
    "PSO to Petrol Pump",
    "PSO to Bulk Customer",
    "PSO to Godown",
    "Godown to Customer",
    "Godown to Petrol Pump",
    "Supplier to Agency Customer",
    "Supplier to Agency Godown",
]
CARRIAGE_OWNERSHIP = ["Own Vehicle", "Rented Vehicle", "Customer Vehicle"]
FREIGHT_PAID_BY_OPTIONS = ["Company", "Customer", "Pump", "Agency"]
TRIP_STATUSES = [
    "Planned", "Loaded", "In Transit", "Delivered", "Completed", "Cancelled",
]
BUSINESS_REFERENCES = [
    "Pump Purchase", "Bulk Sale", "Agency Sale", "Stock Transfer", "Other",
]


class CarriageTrip(db.Model):
    """One vehicle/tanker trip with its freight income and expenses."""

    __tablename__ = "carriage_trips"

    id = db.Column(db.Integer, primary_key=True)
    trip_number = db.Column(db.String(40), nullable=False, unique=True, index=True)
    trip_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    trip_type = db.Column(db.String(60), nullable=False)
    business_reference = db.Column(db.String(40), nullable=True)

    vehicle_ownership = db.Column(db.String(30), nullable=False)
    vehicle_id = db.Column(
        db.Integer, db.ForeignKey("vehicles.id"), nullable=True
    )
    vehicle_number = db.Column(db.String(50), nullable=True)
    driver_id = db.Column(
        db.Integer, db.ForeignKey("drivers.id"), nullable=True
    )
    driver_name = db.Column(db.String(150), nullable=True)
    driver_contact = db.Column(db.String(50), nullable=True)
    # For rented vehicles, the provider whose rent is payable.
    rented_vehicle_vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=True
    )
    # Cash/bank account that own-vehicle trip expenses are paid from. Required
    # for the auto-posting engine to resolve the credit side of cash expenses.
    paid_from_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )

    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=True
    )
    quantity_loaded = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    quantity_delivered = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    loading_point = db.Column(db.String(200), nullable=True)
    unloading_point = db.Column(db.String(200), nullable=True)
    party_name = db.Column(db.String(200), nullable=True)

    freight_rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_freight_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    freight_paid_by = db.Column(db.String(20), nullable=True)

    rent_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    fuel_expense = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    toll_tax = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    loading_unloading_expense = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    driver_expense = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    maintenance_expense = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    other_expense = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    net_profit = db.Column(db.Numeric(16, 2), nullable=False, default=0)

    trip_status = db.Column(db.String(20), nullable=False, default="Planned")
    notes = db.Column(db.String(300), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    vehicle = db.relationship("Vehicle")
    driver = db.relationship("Driver")
    rented_vehicle_vendor = db.relationship("Vendor")
    paid_from_account = db.relationship("CashBankAccount")
    product = db.relationship("Product")
    created_by = db.relationship("User")

    @property
    def total_expenses(self):
        return (
            (self.rent_amount or 0) + (self.fuel_expense or 0)
            + (self.toll_tax or 0) + (self.loading_unloading_expense or 0)
            + (self.driver_expense or 0) + (self.maintenance_expense or 0)
            + (self.other_expense or 0)
        )

    def __repr__(self):
        return f"<CarriageTrip id={self.id} number={self.trip_number!r}>"


class CarriageExpense(db.Model):
    """A standalone carriage-business expense (office, repair, fuel-in-bulk,
    salary, misc) — separate from per-trip expenses.

    When `paid_from_account` is set and the expense is active, that account's
    balance is DECREASED; an `is_posted` flag keeps the posting idempotent
    (same pattern as the head-office expense).
    """

    __tablename__ = "carriage_expenses"

    id = db.Column(db.Integer, primary_key=True)
    expense_category_id = db.Column(
        db.Integer, db.ForeignKey("expense_categories.id"), nullable=True
    )
    expense_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    paid_from_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    notes = db.Column(db.String(300), nullable=True)
    is_posted = db.Column(db.Boolean, nullable=False, default=False)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    expense_category = db.relationship("ExpenseCategory")
    paid_from_account = db.relationship("CashBankAccount")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<CarriageExpense id={self.id} amount={self.amount}>"
