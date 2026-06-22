"""Head Office models: cash received from petrol pumps.

This is the first cross-unit cash flow: a petrol pump submits its daily cash to
head office, and head office records receiving it (optionally into a head office
cash/bank account, increasing that account's balance).
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


# How the cash was received at head office.
HEAD_OFFICE_PAYMENT_METHODS = [
    "Cash",
    "Bank Transfer",
    "Bank Card",
    "Easypaisa",
    "JazzCash",
    "Other",
]


class HeadOfficeCashReceipt(db.Model):
    """Cash received by head office from a petrol pump.

    May be linked to a specific daily closing. When `received_into_account` is
    set and the receipt is active, that account's balance is increased; an
    `is_posted` flag keeps the balance posting idempotent.
    """

    __tablename__ = "head_office_cash_receipts"

    id = db.Column(db.Integer, primary_key=True)
    petrol_pump_id = db.Column(
        db.Integer, db.ForeignKey("petrol_pumps.id"), nullable=False
    )
    daily_closing_id = db.Column(
        db.Integer, db.ForeignKey("pump_daily_closings.id"), nullable=True
    )
    receipt_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    payment_method = db.Column(db.String(40), nullable=False, default="Cash")
    received_into_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    reference_number = db.Column(db.String(80), nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    is_posted = db.Column(db.Boolean, nullable=False, default=False)
    received_by_id = db.Column(
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
    daily_closing = db.relationship("PumpDailyClosing")
    received_into_account = db.relationship("CashBankAccount")
    received_by = db.relationship("User", foreign_keys=[received_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<HeadOfficeCashReceipt id={self.id} amount={self.amount}>"


class HeadOfficeExpense(db.Model):
    """A head office expense (home, welfare, office, etc.).

    When `paid_from_account` is set and the expense is active, that account's
    balance is DECREASED; an `is_posted` flag keeps the posting idempotent.
    """

    __tablename__ = "head_office_expenses"

    id = db.Column(db.Integer, primary_key=True)
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
    reference_number = db.Column(db.String(80), nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    attachment_path = db.Column(db.String(300), nullable=True)
    is_posted = db.Column(db.Boolean, nullable=False, default=False)
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

    expense_category = db.relationship("ExpenseCategory")
    paid_from_account = db.relationship("CashBankAccount")
    vendor = db.relationship("Vendor")
    approved_by = db.relationship("User", foreign_keys=[approved_by_id])
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    def __repr__(self):
        return f"<HeadOfficeExpense id={self.id} amount={self.amount}>"


class VendorPayment(db.Model):
    """A payment to a vendor (e.g. PSO) made by head office.

    When `paid_from_account` is set and the payment is active, that account's
    balance is DECREASED; an `is_posted` flag keeps the posting idempotent.
    """

    __tablename__ = "head_office_vendor_payments"

    id = db.Column(db.Integer, primary_key=True)
    vendor_id = db.Column(
        db.Integer, db.ForeignKey("vendors.id"), nullable=False
    )
    payment_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    paid_from_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=True
    )
    payment_method = db.Column(db.String(40), nullable=False, default="Cash")
    reference_number = db.Column(db.String(80), nullable=True)
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

    vendor = db.relationship("Vendor")
    paid_from_account = db.relationship("CashBankAccount")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<VendorPayment id={self.id} amount={self.amount}>"


HO_SALARY_PAYMENT_TYPES = ["Salary", "Advance"]


class HeadOfficeStaff(db.Model):
    """A head-office employee (management/office staff)."""

    __tablename__ = "head_office_staff"

    id = db.Column(db.Integer, primary_key=True)
    employee_name = db.Column(db.String(150), nullable=False)
    designation = db.Column(db.String(100), nullable=True)
    cnic = db.Column(db.String(50), nullable=True)
    phone_number = db.Column(db.String(50), nullable=True)
    monthly_salary = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    joining_date = db.Column(db.Date, nullable=True)
    notes = db.Column(db.String(300), nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self):
        return f"<HeadOfficeStaff id={self.id} name={self.employee_name!r}>"


class HeadOfficeSalaryPayment(db.Model):
    """A salary/advance paid to a head-office employee. It is a head office
    EXPENSE: when active with a paid-from account, that account's balance is
    DECREASED (idempotent via is_posted) and it counts in HO expense totals."""

    __tablename__ = "head_office_salary_payments"

    id = db.Column(db.Integer, primary_key=True)
    staff_id = db.Column(
        db.Integer, db.ForeignKey("head_office_staff.id"), nullable=False
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

    staff = db.relationship("HeadOfficeStaff")
    paid_from_account = db.relationship("CashBankAccount")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<HeadOfficeSalaryPayment id={self.id} amount={self.amount}>"


class CashTransfer(db.Model):
    """A transfer of money between two cash/bank/wallet accounts (BRD §11.2).

    When active, the FROM account balance is decreased and the TO account
    balance increased; an `is_posted` flag keeps the posting idempotent (same
    pattern as expenses/payments). The two accounts must differ.
    """

    __tablename__ = "head_office_cash_transfers"

    id = db.Column(db.Integer, primary_key=True)
    from_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=False
    )
    to_account_id = db.Column(
        db.Integer, db.ForeignKey("cash_bank_accounts.id"), nullable=False
    )
    transfer_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    amount = db.Column(db.Numeric(16, 2), nullable=False, default=0)
    reference_number = db.Column(db.String(80), nullable=True)
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

    from_account = db.relationship("CashBankAccount", foreign_keys=[from_account_id])
    to_account = db.relationship("CashBankAccount", foreign_keys=[to_account_id])
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<CashTransfer id={self.id} amount={self.amount}>"
