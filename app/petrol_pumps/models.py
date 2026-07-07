"""Petrol pump setup models: machines/dispensers, nozzles and tanks.

These configure a petrol pump's physical equipment. They do not record any
sales or stock movements yet — that comes in a later task.

Relationships to PetrolPump and Product (which live in app/core/models.py) are
declared here with backrefs, so the core models do not need to change.
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


class PumpMachine(db.Model):
    """A machine / dispenser at a petrol pump."""

    __tablename__ = "pump_machines"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    # A machine dispenses ONE fuel product; both of its nozzles are that product
    # (a Petrol machine has 2 Petrol nozzles, a Diesel machine 2 Diesel nozzles).
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=True
    )
    # The single tank this machine is wired to — its retail sales decrement THIS
    # tank. The machine's fuel equals the tank's fuel. Nullable for older
    # machines (they fall back to the pump's resolved tank for the product).
    tank_id = db.Column(
        db.Integer, db.ForeignKey("pump_tanks.id"), nullable=True
    )
    machine_name = db.Column(db.String(150), nullable=False)
    machine_number = db.Column(db.String(50), nullable=True)
    description = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump", backref="machines")
    product = db.relationship("Product")
    tank = db.relationship("PumpTank")

    def __repr__(self):
        return f"<PumpMachine id={self.id} name={self.machine_name!r}>"


class PumpNozzle(db.Model):
    """A nozzle on a machine, dispensing one fuel product."""

    __tablename__ = "pump_nozzles"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    machine_id = db.Column(
        db.Integer, db.ForeignKey("pump_machines.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    nozzle_number = db.Column(db.String(50), nullable=False)
    opening_reading = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    current_reading = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump", backref="nozzles")
    machine = db.relationship("PumpMachine", backref="nozzles")
    product = db.relationship("Product")

    def __repr__(self):
        return f"<PumpNozzle id={self.id} number={self.nozzle_number!r}>"


class PumpTank(db.Model):
    """An underground tank at a petrol pump, storing one fuel product."""

    __tablename__ = "pump_tanks"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    tank_name = db.Column(db.String(150), nullable=False)
    capacity_liters = db.Column(db.Numeric(14, 2), nullable=True)
    opening_stock_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    current_stock_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    dip_reading = db.Column(db.Numeric(14, 2), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump", backref="tanks")
    product = db.relationship("Product")

    def __repr__(self):
        return f"<PumpTank id={self.id} name={self.tank_name!r}>"


# Default shift label when none is given.
DEFAULT_SHIFT = "Full Day"

# 12-hour shifts used by the machine Reading Console (two entries per day).
SHIFT_DAY = "Day (12h)"
SHIFT_NIGHT = "Night (12h)"
CONSOLE_SHIFTS = [SHIFT_DAY, SHIFT_NIGHT]


class MachineReading(db.Model):
    """A petrol pump retail fuel sale derived from a nozzle's meter readings.

    Retail fuel sale is always calculated from the meter, never entered
    directly:
        sale_liters = closing_reading - opening_reading
        sale_amount = sale_liters * rate

    This is petrol pump RETAIL sale only. It is completely separate from bulk
    sale and never touches bulk/carriage/agency records.
    """

    __tablename__ = "machine_readings"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    machine_id = db.Column(
        db.Integer, db.ForeignKey("pump_machines.id"), nullable=False
    )
    nozzle_id = db.Column(
        db.Integer, db.ForeignKey("pump_nozzles.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    reading_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    shift = db.Column(db.String(40), nullable=True)
    opening_reading = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    closing_reading = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    sale_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    sale_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    # Tank this retail sale draws from (auto-resolved: the pump's single active
    # tank for the product). stock_posted makes the tank decrement idempotent,
    # exactly like PumpPurchase.stock_posted does for stock increases.
    tank_id = db.Column(
        db.Integer, db.ForeignKey("pump_tanks.id"), nullable=True
    )
    stock_posted = db.Column(db.Boolean, nullable=False, default=False)
    entered_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    approved_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    notes = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    machine = db.relationship("PumpMachine")
    nozzle = db.relationship("PumpNozzle")
    product = db.relationship("Product")
    tank = db.relationship("PumpTank")
    entered_by = db.relationship("User", foreign_keys=[entered_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    def __repr__(self):
        return (
            f"<MachineReading id={self.id} date={self.reading_date} "
            f"liters={self.sale_liters}>"
        )


# Stock movement types. Quantity is signed on the row (+ increases stock,
# - decreases), so these are labels for reporting/audit, not the sign source.
MOVEMENT_PURCHASE_RECEIVED = "Purchase Received"
MOVEMENT_RETAIL_SALE = "Retail Sale"
MOVEMENT_STOCK_GAIN = "Stock Gain"
MOVEMENT_STOCK_LOSS = "Stock Loss"
STOCK_MOVEMENT_TYPES = [
    MOVEMENT_PURCHASE_RECEIVED,
    MOVEMENT_RETAIL_SALE,
    MOVEMENT_STOCK_GAIN,
    MOVEMENT_STOCK_LOSS,
]


class StockMovement(db.Model):
    """Append-managed audit ledger of every fuel tank stock change.

    Each row is one signed movement against a tank. The running balance lives on
    PumpTank.current_stock_liters (updated in the same posting transaction); this
    table is the history/audit trail behind that balance and powers the stock
    movement report. Rows are tied to their source document via
    (source_type, source_id) so an edit/reversal can clear and rebuild them.
    """

    __tablename__ = "stock_movements"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    tank_id = db.Column(
        db.Integer, db.ForeignKey("pump_tanks.id"), nullable=True
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    movement_type = db.Column(db.String(40), nullable=False)
    quantity_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    movement_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    source_type = db.Column(db.String(40), nullable=True)
    source_id = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    petrol_pump = db.relationship("PetrolPump")
    tank = db.relationship("PumpTank")
    product = db.relationship("Product")
    created_by = db.relationship("User")

    def __repr__(self):
        return (
            f"<StockMovement id={self.id} type={self.movement_type!r} "
            f"qty={self.quantity_liters}>"
        )


# Stock adjustment approval lifecycle (BRD §12.3 + §16).
ADJUSTMENT_STATUS_PENDING = "Pending"
ADJUSTMENT_STATUS_APPROVED = "Approved"
ADJUSTMENT_STATUS_REJECTED = "Rejected"
ADJUSTMENT_STATUSES = [
    ADJUSTMENT_STATUS_PENDING,
    ADJUSTMENT_STATUS_APPROVED,
    ADJUSTMENT_STATUS_REJECTED,
]


class StockAdjustment(db.Model):
    """A physical-vs-system fuel count for one tank (gain/loss adjustment).

    BRD §12.3: the manager dips the tank, enters the physical stock, and the
    system records the difference against its book stock. Per §6.6/§16 the
    entry starts Pending and only an APPROVED adjustment posts to the tank
    balance and the StockMovement ledger (idempotent `stock_posted` pattern).

        difference_liters = physical_stock - system_stock
        > 0  -> Stock Gain   |   < 0  -> Stock Loss
    """

    __tablename__ = "stock_adjustments"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    tank_id = db.Column(
        db.Integer, db.ForeignKey("pump_tanks.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    adjustment_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    system_stock_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    physical_stock_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    difference_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    status = db.Column(
        db.String(20), nullable=False, default=ADJUSTMENT_STATUS_PENDING
    )
    stock_posted = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(300), nullable=True)
    rejection_reason = db.Column(db.String(300), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    approved_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    approved_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    tank = db.relationship("PumpTank")
    product = db.relationship("Product")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])

    @property
    def is_gain(self):
        return (self.difference_liters or 0) > 0

    def __repr__(self):
        return (
            f"<StockAdjustment id={self.id} status={self.status!r} "
            f"diff={self.difference_liters}>"
        )


# Delivery status options for a pump fuel purchase.
PURCHASE_DELIVERY_STATUSES = ["Pending", "Received", "Cancelled"]


class PumpPurchase(db.Model):
    """A petrol pump retail fuel purchase (normally from PSO).

    Stock is posted to tanks only when the purchase is active, tank_received is
    True and delivery_status is 'Received'. The `stock_posted` flag makes that
    posting idempotent so stock is never increased twice.
    """

    __tablename__ = "pump_purchases"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=False
    )
    purchase_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    invoice_number = db.Column(db.String(80), nullable=True)
    vehicle_number = db.Column(db.String(50), nullable=True)
    driver_name = db.Column(db.String(150), nullable=True)
    delivery_status = db.Column(db.String(20), nullable=False, default="Received")
    tank_received = db.Column(db.Boolean, nullable=False, default=True)
    stock_posted = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(300), nullable=True)
    total_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    vendor = db.relationship("Vendor")
    created_by = db.relationship("User")
    items = db.relationship(
        "PumpPurchaseItem",
        back_populates="purchase",
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<PumpPurchase id={self.id} total={self.total_amount}>"


class PumpPurchaseItem(db.Model):
    """One product line on a pump fuel purchase."""

    __tablename__ = "pump_purchase_items"

    id = db.Column(db.Integer, primary_key=True)
    pump_purchase_id = db.Column(
        db.Integer, db.ForeignKey("pump_purchases.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    tank_id = db.Column(
        db.Integer, db.ForeignKey("pump_tanks.id"), nullable=True
    )
    quantity_liters = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    purchase = db.relationship("PumpPurchase", back_populates="items")
    product = db.relationship("Product")
    tank = db.relationship("PumpTank")

    def __repr__(self):
        return f"<PumpPurchaseItem id={self.id} qty={self.quantity_liters}>"


class PumpExpense(db.Model):
    """An expense incurred at a petrol pump."""

    __tablename__ = "pump_expenses"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    expense_category_id = db.Column(
        db.Integer, db.ForeignKey("expense_categories.id"), nullable=False
    )
    expense_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    paid_from_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=True
    )
    notes = db.Column(db.String(300), nullable=True)
    attachment_path = db.Column(db.String(300), nullable=True)
    approved_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    expense_category = db.relationship("ExpenseCategory")
    paid_from_account = db.relationship("CashBankAccount")
    vendor = db.relationship("Vendor")
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<PumpExpense id={self.id} amount={self.amount}>"


# Payment methods for a lubricant sale.
LUBRICANT_PAYMENT_METHODS = [
    "Cash",
    "Bank Card",
    "PSO Card",
    "Easypaisa",
    "JazzCash",
    "Bank Transfer",
    "Credit Customer",
]
CREDIT_CUSTOMER_METHOD = "Credit Customer"


class LubricantSale(db.Model):
    """A retail lubricant sale at a petrol pump (non-fuel, non-meter)."""

    __tablename__ = "lubricant_sales"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id"), nullable=False
    )
    sale_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    quantity = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    rate = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    total_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    payment_method = db.Column(db.String(40), nullable=False)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=True
    )
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
    product = db.relationship("Product")
    customer = db.relationship("Customer")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<LubricantSale id={self.id} total={self.total_amount}>"


class PumpDailyClosing(db.Model):
    """A petrol pump's daily closing summary.

    The sale/expense figures are derived from machine readings, lubricant sales
    and pump expenses for the pump and date; the payment-method splits and the
    submitted cash are entered manually.
    """

    __tablename__ = "pump_daily_closings"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    closing_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    fuel_sale_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    fuel_sale_liters = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    lubricant_sale_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    total_sale_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    cash_received = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    bank_card_received = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    pso_card_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    easypaisa_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    jazzcash_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    bank_transfer_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    credit_sale_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    expenses_paid = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    expected_cash_to_submit = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    cash_submitted_to_head_office = db.Column(
        db.Numeric(16, 2), nullable=False, default=0
    )
    difference_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    # The pump's bank/wallet account that all non-cash receipts (card, transfer,
    # PSO card, Easypaisa, JazzCash) are deposited into. posted_* track what was
    # actually credited so edits/deactivation reverse it idempotently.
    bank_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    bank_posted_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    bank_posted_amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    # Per-method destination accounts: each non-cash method routes to its own
    # chosen account (bank card / PSO card / bank transfer -> a bank account;
    # Easypaisa / JazzCash -> a wallet account). The amount lands there so it
    # leaves the pump's cash and shows under head office's bank/wallet balances.
    bank_card_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    pso_card_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    easypaisa_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    jazzcash_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    bank_transfer_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    # JSON snapshot of what was actually credited [{account_id, amount}, ...], so
    # edits / deactivation reverse the exact prior postings idempotently.
    deposit_posted = db.Column(db.Text, nullable=True)
    # Which customer the credit_sale_amount is owed by (required when there is a
    # credit sale) — so the credit lands in that customer's ledger to clear later.
    credit_customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=True
    )
    remarks = db.Column(db.String(300), nullable=True)
    manager_approved = db.Column(db.Boolean, nullable=False, default=False)
    approved_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    bank_account = db.relationship("CashBankAccount", foreign_keys=[bank_account_id])
    bank_card_account = db.relationship("CashBankAccount", foreign_keys=[bank_card_account_id])
    pso_card_account = db.relationship("CashBankAccount", foreign_keys=[pso_card_account_id])
    easypaisa_account = db.relationship("CashBankAccount", foreign_keys=[easypaisa_account_id])
    jazzcash_account = db.relationship("CashBankAccount", foreign_keys=[jazzcash_account_id])
    bank_transfer_account = db.relationship("CashBankAccount", foreign_keys=[bank_transfer_account_id])
    credit_customer = db.relationship("Customer", foreign_keys=[credit_customer_id])
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return (
            f"<PumpDailyClosing id={self.id} date={self.closing_date} "
            f"total={self.total_sale_amount}>"
        )


# Pump staff designations (BRD §6.9) and shift options.
STAFF_DESIGNATIONS = [
    "Manager",
    "Cashier",
    "Filler",
    "Sweeper",
    "Gunman",
    "Other",
]
STAFF_SHIFTS = ["Morning", "Evening", "Night", "Full Day"]

# Monthly salary breakdown (BRD §6.9). The stored `monthly_salary` is the sum of
# these components; each is a non-negative amount. (column name, display label)
SALARY_COMPONENTS = [
    ("basic_salary", "Basic Salary"),
    ("food_allowance", "Food Allowance"),
    ("special_allowance", "Special Allowance"),
    ("medical_allowance", "Medical Allowance"),
    ("mobile_allowance", "Mobile Allowance"),
    ("other_allowance", "Other Allowance"),
]


class PumpStaff(db.Model):
    """An employee working at a petrol pump (BRD §6.9 Pump Labour / HR).

    The duty station is the petrol pump the employee is assigned to. Records are
    retired via is_active (soft delete); a hard delete is offered only when no
    HR records (attendance/salary) reference the employee — added in later
    slices. Monthly salary is the agreed pay; attendance and salary payments are
    separate records.
    """

    __tablename__ = "pump_staff"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    employee_name = db.Column(db.String(150), nullable=False)
    designation = db.Column(db.String(40), nullable=False)
    shift = db.Column(db.String(40), nullable=True)
    cnic = db.Column(db.String(30), nullable=True)
    phone_number = db.Column(db.String(30), nullable=True)
    emergency_contact = db.Column(db.String(30), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    # Salary breakdown; monthly_salary is kept as the sum of the six components.
    basic_salary = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    food_allowance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    special_allowance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    medical_allowance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    mobile_allowance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    other_allowance = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    monthly_salary = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    joining_date = db.Column(db.Date, nullable=True)
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
    created_by = db.relationship("User")

    def __repr__(self):
        return (
            f"<PumpStaff id={self.id} name={self.employee_name!r} "
            f"designation={self.designation!r}>"
        )


# Daily attendance status options and salary payment types (BRD §6.9).
ATTENDANCE_STATUSES = ["Present", "Absent", "Leave", "Half Day"]

# Salary payment types. The six salary components can each be paid separately,
# plus an Advance, plus a Fine. A Fine is a DEDUCTION withheld from the
# employee's salary (it reduces what the pump still owes them and moves no cash);
# every other type is money actually paid out of the pump's cash.
SALARY_FINE_TYPE = "Fine"
# Labels of the "money paid to the employee" types (the six salary components).
SALARY_COMPONENT_LABELS = [label for _, label in SALARY_COMPONENTS]
SALARY_PAYMENT_TYPES = SALARY_COMPONENT_LABELS + ["Advance", SALARY_FINE_TYPE]
# Types counted as "salary paid" in the report: the six components plus the
# legacy single "Salary" type used before the salary breakdown existed.
SALARY_PAID_TYPES = SALARY_COMPONENT_LABELS + ["Salary"]


class PumpAttendance(db.Model):
    """One employee's attendance for one day.

    Unique per (staff, date): marking the same day again updates the record
    rather than creating a duplicate (the mark-attendance grid upserts).
    """

    __tablename__ = "pump_attendance"
    __table_args__ = (
        db.UniqueConstraint(
            "staff_id", "attendance_date", name="uq_attendance_staff_date"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("pump_staff.id"), nullable=False
    )
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    attendance_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    status = db.Column(db.String(20), nullable=False, default="Present")
    notes = db.Column(db.String(300), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    staff = db.relationship("PumpStaff")
    petrol_pump = db.relationship("PetrolPump")
    created_by = db.relationship("User")

    def __repr__(self):
        return (
            f"<PumpAttendance staff={self.staff_id} "
            f"date={self.attendance_date} status={self.status!r}>"
        )


class PumpSalaryPayment(db.Model):
    """A salary or advance payment to a pump employee.

    When `paid_from_account` is set and the payment is active, that account's
    balance is DECREASED; an `is_posted` flag keeps the posting idempotent
    (same pattern as head-office vendor payments / expenses).
    """

    __tablename__ = "pump_salary_payments"

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("pump_staff.id"), nullable=False
    )
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    payment_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    payment_type = db.Column(db.String(20), nullable=False, default="Salary")
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    for_month = db.Column(db.String(40), nullable=True)
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

    staff = db.relationship("PumpStaff")
    petrol_pump = db.relationship("PetrolPump")
    paid_from_account = db.relationship("CashBankAccount")
    created_by = db.relationship("User")

    def __repr__(self):
        return (
            f"<PumpSalaryPayment id={self.id} type={self.payment_type!r} "
            f"amount={self.amount}>"
        )


# Daily checklist standard items (BRD §15.1) and per-item status options.
CHECKLIST_ITEMS = [
    "Pump area cleanliness",
    "Shop area cleanliness",
    "Washroom cleanliness",
    "Machine cleanliness",
    "Lights working",
    "Machines working",
    "Nozzle condition",
    "Tank dip check",
    "Lubricant stock check",
    "Staff attendance",
    "Cashier closing",
]
CHECKLIST_STATUSES = ["OK", "Issue", "N/A"]


class DailyChecklist(db.Model):
    """A petrol pump's daily inspection checklist (BRD §15.1).

    One per (pump, date). The standard items are stored as child rows so the
    list of checks can evolve and issue-reporting is a simple query.
    """

    __tablename__ = "daily_checklists"
    __table_args__ = (
        db.UniqueConstraint(
            "petrol_pump_id", "checklist_date", name="uq_checklist_pump_date"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    checklist_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    remarks = db.Column(db.String(300), nullable=True)
    checked_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    checked_by = db.relationship("User")
    items = db.relationship(
        "DailyChecklistItem",
        back_populates="checklist",
        cascade="all, delete-orphan",
    )

    @property
    def issue_count(self):
        return sum(1 for i in self.items if i.status == "Issue")

    def __repr__(self):
        return f"<DailyChecklist id={self.id} date={self.checklist_date}>"


class DailyChecklistItem(db.Model):
    """One checked item on a daily checklist."""

    __tablename__ = "daily_checklist_items"

    id = db.Column(db.Integer, primary_key=True)
    daily_checklist_id = db.Column(
        db.Integer, db.ForeignKey("daily_checklists.id"), nullable=False
    )
    item_name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(10), nullable=False, default="OK")
    note = db.Column(db.String(200), nullable=True)

    checklist = db.relationship("DailyChecklist", back_populates="items")

    def __repr__(self):
        return f"<DailyChecklistItem {self.item_name!r}={self.status!r}>"


# Maintenance complaint lifecycle (BRD §15.2).
MAINTENANCE_STATUSES = ["Pending", "In Progress", "Completed"]


class MaintenanceComplaint(db.Model):
    """A maintenance complaint / job for a petrol pump (BRD §15.2)."""

    __tablename__ = "maintenance_complaints"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    complaint_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    complaint_type = db.Column(db.String(80), nullable=False)
    description = db.Column(db.String(500), nullable=True)
    assigned_vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=True
    )
    estimated_cost = db.Column(db.Numeric(14, 2), nullable=True)
    actual_cost = db.Column(db.Numeric(14, 2), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="Pending")
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
    assigned_vendor = db.relationship("Vendor")
    created_by = db.relationship("User")

    def __repr__(self):
        return (
            f"<MaintenanceComplaint id={self.id} type={self.complaint_type!r} "
            f"status={self.status!r}>"
        )


class PumpCarriageCashFeed(db.Model):
    """Cash handed from a petrol pump to the Carriage business.

    A single entry recording how much cash the pump fed to carriage. When an
    `into_account` (a carriage cash/bank account) is set and the feed is active,
    that account's balance is increased; `is_posted` keeps that idempotent. The
    amount also reduces the pump's current cash (services.pump_current_cash) —
    exactly like cash submitted to head office, but the destination is carriage.
    """

    __tablename__ = "pump_carriage_cash_feeds"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    feed_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    into_account_id = db.Column(
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

    petrol_pump = db.relationship("PetrolPump")
    into_account = db.relationship("CashBankAccount")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<PumpCarriageCashFeed id={self.id} amount={self.amount}>"


class PsoCardPayment(db.Model):
    """A pump's PSO-card receipts for a day, owed across to the PSO vendor.

    PSO fleet-card payments taken at a pump are NOT bank money — they are
    effectively a payment toward the PSO vendor (our fuel supplier). One row is
    created per daily closing that has a PSO card amount, linking the pump, the
    closing and the PSO vendor. It sits PENDING until the accountant verifies it
    with PSO; once `is_verified`, it reduces the PSO vendor's payable (see
    accounting.ledger). Deactivating/zeroing the source closing sets is_active
    False so it stops counting.
    """

    __tablename__ = "pso_card_payments"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=False
    )
    daily_closing_id = db.Column(
        db.Integer, db.ForeignKey("pump_daily_closings.id"), nullable=True
    )
    payment_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    verified_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    verified_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    petrol_pump = db.relationship("PetrolPump")
    vendor = db.relationship("Vendor")
    daily_closing = db.relationship("PumpDailyClosing")
    verified_by = db.relationship("User")

    def __repr__(self):
        return (
            f"<PsoCardPayment id={self.id} pump={self.petrol_pump_id} "
            f"amount={self.amount} verified={self.is_verified}>"
        )
