"""Accounting service helpers: journal totals, validation and creation.

These functions hold the double-entry rules so the routes stay thin. A "line"
here is a plain dict with at least:
    {"account_id": int, "debit": Decimal, "credit": Decimal, "description": str}
"""
from decimal import Decimal

from app.extensions import db
from app.accounting.models import JournalEntry, JournalEntryLine, Voucher

# Two-decimal money quantum used for rounding/comparison.
_CENTS = Decimal("0.01")

# Prefix used in generated voucher numbers, by voucher type.
VOUCHER_NUMBER_PREFIX = {
    "Sale Voucher": "SV",
    "Purchase Voucher": "PV",
    "Payment Voucher": "PMV",
    "Receipt Voucher": "RV",
    "Journal Voucher": "JV",
    "Expense Voucher": "EV",
    "Cash Transfer Voucher": "CTV",
    "Stock Adjustment Voucher": "SAV",
    "Trip Voucher": "TV",
}


def calculate_journal_totals(lines):
    """Return (total_debit, total_credit) as Decimals rounded to 2 places."""
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    for line in lines:
        total_debit += Decimal(str(line.get("debit") or 0))
        total_credit += Decimal(str(line.get("credit") or 0))
    return total_debit.quantize(_CENTS), total_credit.quantize(_CENTS)


def validate_journal_entry_lines(lines):
    """Validate the double-entry rules. Returns a list of error messages.

    Rules enforced:
      * at least 2 lines
      * every line has a debit or a credit (not zero/zero)
      * no negative debit or credit
      * a single line cannot have both debit and credit > 0
      * total debit must equal total credit
      * totals must be greater than zero
    """
    errors = []

    if len(lines) < 2:
        errors.append("A journal entry must have at least 2 lines.")

    for index, line in enumerate(lines, start=1):
        debit = Decimal(str(line.get("debit") or 0))
        credit = Decimal(str(line.get("credit") or 0))

        if debit < 0 or credit < 0:
            errors.append(f"Line {index}: debit and credit cannot be negative.")
        if debit > 0 and credit > 0:
            errors.append(
                f"Line {index}: a line cannot have both debit and credit."
            )
        if debit == 0 and credit == 0:
            errors.append(f"Line {index}: enter a debit or a credit amount.")

    total_debit, total_credit = calculate_journal_totals(lines)

    if total_debit <= 0 or total_credit <= 0:
        errors.append("Total debit and total credit must be greater than zero.")
    if total_debit != total_credit:
        errors.append(
            f"Journal entry is not balanced: total debit ({total_debit}) "
            f"must equal total credit ({total_credit})."
        )

    return errors


def generate_voucher_number(voucher_type):
    """Generate a unique voucher number like 'JV-0001' for the given type."""
    prefix = VOUCHER_NUMBER_PREFIX.get(voucher_type, "VCH")
    count = Voucher.query.filter_by(voucher_type=voucher_type).count()
    number = count + 1
    # Guard against gaps/collisions so the number is always unique.
    while (
        Voucher.query.filter_by(
            voucher_number=f"{prefix}-{number:04d}"
        ).first()
        is not None
    ):
        number += 1
    return f"{prefix}-{number:04d}"


def create_journal_entry(
    *,
    business_unit_id,
    entry_date,
    lines,
    description=None,
    created_by_id=None,
    voucher=None,
    is_posted=True,
):
    """Build and stage a JournalEntry with its lines.

    Assumes the lines have already passed validate_journal_entry_lines(). Adds
    the entry to the session but does NOT commit (the caller controls the
    transaction).
    """
    entry = JournalEntry(
        business_unit_id=business_unit_id,
        entry_date=entry_date,
        description=description,
        created_by_id=created_by_id,
        is_posted=is_posted,
        voucher=voucher,
    )
    for line in lines:
        entry.lines.append(
            JournalEntryLine(
                chart_of_account_id=line["account_id"],
                debit=Decimal(str(line.get("debit") or 0)),
                credit=Decimal(str(line.get("credit") or 0)),
                description=(line.get("description") or None),
            )
        )
    db.session.add(entry)
    return entry
