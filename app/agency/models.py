"""Oil Agencies models: LDO / Kerosene purchases and sales.

The agency business is completely separate from petrol pumps. It has its own
vendors, customers, purchases and sales. Products are agency products (LDO,
Kerosene). Stock/godown handling is deferred (BRD keeps it optional for now):
for now a sale records its own cost (purchase_rate) so per-sale profit can be
computed directly:

    net_profit = total_sale_amount - total_purchase_amount
                 - carriage_cost - other_expense
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


AGENCY_DELIVERY_METHODS = [
    "Customer Pickup",
    "Rented Vehicle Delivery",
    "Own Vehicle Delivery",
]
AGENCY_PAYMENT_STATUSES = ["Paid", "Unpaid", "Partial"]
AGENCY_PAYMENT_METHODS = [
    "Cash", "Bank Transfer", "Bank Card", "Easypaisa", "JazzCash",
    "Credit Customer", "Other",
]


class AgencyPurchase(db.Model):
    """A purchase of an agency product (LDO / Kerosene) from a vendor."""

    __tablename__ = "agency_purchases"

    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    purchase_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    quantity = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    purchase_rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    invoice_number = db.Column(db.String(80), nullable=True)
    loading_point = db.Column(db.String(200), nullable=True)
    delivery_method = db.Column(db.String(40), nullable=True)
    vehicle_number = db.Column(db.String(50), nullable=True)
    driver_name = db.Column(db.String(150), nullable=True)
    payment_status = db.Column(db.String(20), nullable=False, default="Unpaid")
    # Optional godown stock (BRD §9.5): True if this purchase is received into
    # the agency godown (so it adds to on-hand stock) rather than drop-shipped
    # direct to the customer.
    to_godown = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(300), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    vendor = db.relationship("Vendor")
    product = db.relationship("Product")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<AgencyPurchase id={self.id} total={self.total_amount}>"


class AgencySale(db.Model):
    """A sale of an agency product (LDO / Kerosene) to a customer."""

    __tablename__ = "agency_sales"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    sale_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    quantity = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    purchase_rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    sale_rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_purchase_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    total_sale_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    carriage_cost = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    other_expense = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    net_profit = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    delivery_method = db.Column(db.String(40), nullable=True)
    vehicle_number = db.Column(db.String(50), nullable=True)
    driver_name = db.Column(db.String(150), nullable=True)
    payment_method = db.Column(db.String(40), nullable=True)
    payment_status = db.Column(db.String(20), nullable=False, default="Unpaid")
    # Optional godown stock (BRD §9.5): True if this sale is drawn FROM godown
    # on-hand stock rather than a direct purchase→customer drop-ship.
    from_godown = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(300), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    customer = db.relationship("Customer")
    product = db.relationship("Product")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<AgencySale id={self.id} profit={self.net_profit}>"
