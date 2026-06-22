"""Bulk Sale models: pump-name-based bulk fuel sales.

Bulk sale is done in the *name* of a petrol pump but is completely separate from
petrol pump retail sale: it must never affect machine readings, retail daily
sale, or pump tank stock (the fuel goes directly from PSO to the customer).

A bulk sale records the purchase (from PSO/vendor) and the sale (to the
customer) for one fuel product, and computes the net profit:

    total_purchase_amount = quantity_liters * purchase_rate
    total_sale_amount     = quantity_liters * sale_rate
    net_profit            = total_sale_amount - total_purchase_amount
                            - carriage_charges - other_expense
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


BULK_DELIVERY_METHODS = ["Own Vehicle", "Rented Vehicle", "Customer Vehicle"]
CARRIAGE_PAID_BY_OPTIONS = ["Company", "Customer"]
BULK_PAYMENT_METHODS = [
    "Cash", "Bank Transfer", "Bank Card", "Easypaisa", "JazzCash",
    "Credit Customer", "Other",
]
BULK_PAYMENT_STATUSES = ["Paid", "Unpaid", "Partial"]
BULK_DELIVERY_STATUSES = ["Pending", "Loaded", "Delivered", "Completed"]


class BulkSale(db.Model):
    """One pump-name-based bulk fuel sale (PSO -> customer)."""

    __tablename__ = "bulk_sales"

    id = db.Column(db.Integer, primary_key=True)
    # "Pump name" — mandatory, but this never touches the pump's retail records.
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=False
    )
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    sale_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    quantity_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    pso_invoice_number = db.Column(db.String(80), nullable=True)
    purchase_rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    sale_rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_purchase_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    total_sale_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    carriage_charges = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    carriage_paid_by = db.Column(db.String(20), nullable=True)
    other_expense = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    net_profit = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    vehicle_number = db.Column(db.String(50), nullable=True)
    driver_name = db.Column(db.String(150), nullable=True)
    delivery_location = db.Column(db.String(200), nullable=True)
    delivery_method = db.Column(db.String(30), nullable=True)
    payment_method = db.Column(db.String(40), nullable=True)
    payment_status = db.Column(db.String(20), nullable=False, default="Unpaid")
    delivery_status = db.Column(db.String(20), nullable=False, default="Pending")
    notes = db.Column(db.String(300), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    customer = db.relationship("Customer")
    vendor = db.relationship("Vendor")
    product = db.relationship("Product")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<BulkSale id={self.id} qty={self.quantity_liters} profit={self.net_profit}>"
