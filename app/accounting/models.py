"""Accounting models: chart of accounts, vouchers and double-entry journals.

Design (kept deliberately simple):
  - ChartOfAccount : the account tree (optionally per business unit)
  - Voucher        : a numbered business document (Journal/Sale/Payment/...)
  - JournalEntry   : a balanced set of debit/credit lines, optionally tied to a
                     voucher; always tagged with a business unit
  - JournalEntryLine : one debit OR credit against a chart account

Balancing rules (total debit == total credit, etc.) are enforced in
app/accounting/services.py, not in the models themselves.
"""
from datetime import datetime, timezone

from app.extensions import db


def _utcnow():
    """Timezone-aware UTC now (avoids deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc)


# The accounting classification of a chart account.
ACCOUNTING_ACCOUNT_TYPES = [
    "Asset",
    "Liability",
    "Equity",
    "Income",
    "Cost of Goods Sold",
    "Expense",
]

# The kinds of voucher the system can produce.
VOUCHER_TYPES = [
    "Sale Voucher",
    "Purchase Voucher",
    "Payment Voucher",
    "Receipt Voucher",
    "Journal Voucher",
    "Expense Voucher",
    "Cash Transfer Voucher",
    "Stock Adjustment Voucher",
    "Trip Voucher",
]


class ChartOfAccount(db.Model):
    """A single account in the chart of accounts.

    May belong to a business unit (or be global) and may have a parent account
    to form a tree (e.g. "Pump Expenses" under "Expenses").
    """

    __tablename__ = "chart_of_accounts"

    id = db.Column(db.Integer, primary_key=True)
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=True
    )
    code = db.Column(db.String(30), nullable=False, unique=True, index=True)
    name = db.Column(db.String(150), nullable=False)
    account_type = db.Column(db.String(40), nullable=False)
    parent_id = db.Column(
        db.Integer, db.ForeignKey("chart_of_accounts.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit")
    parent = db.relationship(
        "ChartOfAccount", remote_side=[id], backref="children"
    )

    def __repr__(self):
        return f"<ChartOfAccount id={self.id} code={self.code!r}>"


class Voucher(db.Model):
    """A numbered accounting document tied to a business unit."""

    __tablename__ = "vouchers"

    id = db.Column(db.Integer, primary_key=True)
    voucher_number = db.Column(
        db.String(40), nullable=False, unique=True, index=True
    )
    voucher_type = db.Column(db.String(40), nullable=False)
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=False
    )
    date = db.Column(db.Date, nullable=False, default=lambda: _utcnow().date())
    description = db.Column(db.String(300), nullable=True)
    reference_number = db.Column(db.String(80), nullable=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_posted = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit")
    created_by = db.relationship("User")
    journal_entries = db.relationship(
        "JournalEntry", back_populates="voucher"
    )

    def __repr__(self):
        return f"<Voucher id={self.id} number={self.voucher_number!r}>"


class JournalEntry(db.Model):
    """A balanced group of debit/credit lines for one business unit."""

    __tablename__ = "journal_entries"

    id = db.Column(db.Integer, primary_key=True)
    voucher_id = db.Column(
        db.Integer, db.ForeignKey("vouchers.id"), nullable=True
    )
    business_unit_id = db.Column(
        db.Integer, db.ForeignKey("business_units.id"), nullable=False
    )
    entry_date = db.Column(
        db.Date, nullable=False, default=lambda: _utcnow().date()
    )
    description = db.Column(db.String(300), nullable=True)
    # Auto-posting link: entries generated from a source transaction carry its
    # (source_type, source_id) so they can be found, replaced or removed
    # idempotently. Manual journal vouchers leave these NULL.
    source_type = db.Column(db.String(40), nullable=True, index=True)
    source_id = db.Column(db.Integer, nullable=True, index=True)
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_posted = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    business_unit = db.relationship("BusinessUnit")
    created_by = db.relationship("User")
    voucher = db.relationship("Voucher", back_populates="journal_entries")
    lines = db.relationship(
        "JournalEntryLine",
        back_populates="journal_entry",
        cascade="all, delete-orphan",
    )

    @property
    def total_debit(self):
        return sum((line.debit or 0) for line in self.lines)

    @property
    def total_credit(self):
        return sum((line.credit or 0) for line in self.lines)

    def __repr__(self):
        return f"<JournalEntry id={self.id} bu={self.business_unit_id}>"


class JournalEntryLine(db.Model):
    """One debit or credit posting against a chart account."""

    __tablename__ = "journal_entry_lines"

    id = db.Column(db.Integer, primary_key=True)
    journal_entry_id = db.Column(
        db.Integer, db.ForeignKey("journal_entries.id"), nullable=False
    )
    chart_of_account_id = db.Column(
        db.Integer, db.ForeignKey("chart_of_accounts.id"), nullable=False
    )
    debit = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    credit = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    description = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    journal_entry = db.relationship("JournalEntry", back_populates="lines")
    account = db.relationship("ChartOfAccount")

    def __repr__(self):
        return (
            f"<JournalEntryLine id={self.id} "
            f"dr={self.debit} cr={self.credit}>"
        )


# Payment methods used when receiving money from a customer.
RECEIPT_PAYMENT_METHODS = [
    "Cash", "Bank Transfer", "Bank Card", "Easypaisa", "JazzCash", "Other",
]


class CustomerReceipt(db.Model):
    """Money received from a customer (reduces their receivable balance).

    When `received_into_account` is set and the receipt is active, that
    account's balance is increased; an `is_posted` flag keeps the posting
    idempotent.
    """

    __tablename__ = "customer_receipts"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customers.id"), nullable=False
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
    created_by_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=True
    )
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow
    )

    customer = db.relationship("Customer")
    received_into_account = db.relationship("CashBankAccount")
    created_by = db.relationship("User")

    def __repr__(self):
        return f"<CustomerReceipt id={self.id} amount={self.amount}>"
