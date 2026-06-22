"""Account ledger reports (BRD §13.5): cash/bank book + general ledger.

* cash_account_ledger  — every posted movement against one operational
  cash/bank/wallet account, with a running balance (the "cash book / bank book").
* general_ledger       — journal-line detail for one chart-of-accounts account,
  with a running balance (drill-down behind the trial balance).

Both compute an opening balance from movements before the period so a date range
shows a true statement (opening → movements → closing).
"""
from decimal import Decimal

from app.extensions import db

_ZERO = Decimal("0")


def _dec(v):
    return Decimal(str(v or 0))


# --------------------------------------------------------------------------- #
# Cash / Bank Book — operational account statement
# --------------------------------------------------------------------------- #
def _cash_movements(account_id):
    """All posted movements touching an account as dicts (unsorted, all dates).

    Each row: {date, kind, ref, money_in, money_out}. Money in increases the
    cash balance, money out decreases it.
    """
    from app.accounting.models import CustomerReceipt
    from app.head_office.models import (
        CashTransfer, HeadOfficeCashReceipt, HeadOfficeExpense, VendorPayment,
    )
    from app.petrol_pumps.models import PumpSalaryPayment

    rows = []

    for r in (HeadOfficeCashReceipt.query
              .filter_by(received_into_account_id=account_id, is_active=True, is_posted=True)):
        rows.append({"date": r.receipt_date, "kind": "Cash received from pump",
                     "ref": r.reference_number or "", "in": _dec(r.amount), "out": _ZERO})

    for r in (CustomerReceipt.query
              .filter_by(received_into_account_id=account_id, is_active=True, is_posted=True)):
        name = r.customer.name if r.customer else ""
        rows.append({"date": r.receipt_date, "kind": "Customer receipt",
                     "ref": name, "in": _dec(r.amount), "out": _ZERO})

    for e in (HeadOfficeExpense.query
              .filter_by(paid_from_account_id=account_id, is_active=True, is_posted=True)):
        cat = e.expense_category.name if e.expense_category else "expense"
        rows.append({"date": e.expense_date, "kind": "Head office expense",
                     "ref": cat, "in": _ZERO, "out": _dec(e.amount)})

    for p in (VendorPayment.query
              .filter_by(paid_from_account_id=account_id, is_active=True, is_posted=True)):
        name = p.vendor.name if p.vendor else ""
        rows.append({"date": p.payment_date, "kind": "Vendor payment",
                     "ref": name, "in": _ZERO, "out": _dec(p.amount)})

    for s in (PumpSalaryPayment.query
              .filter_by(paid_from_account_id=account_id, is_active=True, is_posted=True)):
        name = s.staff.employee_name if s.staff else ""
        rows.append({"date": s.payment_date, "kind": f"{s.payment_type} payment",
                     "ref": name, "in": _ZERO, "out": _dec(s.amount)})

    for t in (CashTransfer.query
              .filter(CashTransfer.is_active.is_(True), CashTransfer.is_posted.is_(True),
                      db.or_(CashTransfer.from_account_id == account_id,
                             CashTransfer.to_account_id == account_id))):
        if t.to_account_id == account_id:
            rows.append({"date": t.transfer_date, "kind": "Transfer in",
                         "ref": (t.from_account.name if t.from_account else ""),
                         "in": _dec(t.amount), "out": _ZERO})
        if t.from_account_id == account_id:
            rows.append({"date": t.transfer_date, "kind": "Transfer out",
                         "ref": (t.to_account.name if t.to_account else ""),
                         "in": _ZERO, "out": _dec(t.amount)})

    return rows


def cash_account_ledger(account, date_from=None, date_to=None):
    """Statement for one CashBankAccount: opening, in-period rows, closing."""
    movements = sorted(_cash_movements(account.id), key=lambda r: (r["date"],))

    opening = _ZERO
    for r in movements:
        if date_from and r["date"] < date_from:
            opening += r["in"] - r["out"]

    rows = []
    running = opening
    total_in = _ZERO
    total_out = _ZERO
    for r in movements:
        if date_from and r["date"] < date_from:
            continue
        if date_to and r["date"] > date_to:
            continue
        running += r["in"] - r["out"]
        total_in += r["in"]
        total_out += r["out"]
        rows.append({**r, "balance": running})

    return {
        "account": account, "opening": opening, "rows": rows,
        "total_in": total_in, "total_out": total_out, "closing": running,
    }


# --------------------------------------------------------------------------- #
# General Ledger — chart-account journal detail
# --------------------------------------------------------------------------- #
def general_ledger(account, date_from=None, date_to=None):
    """Posted journal lines for one ChartOfAccount, with a running balance."""
    from app.accounting.models import JournalEntry, JournalEntryLine

    base = (
        db.session.query(JournalEntry, JournalEntryLine)
        .join(JournalEntryLine, JournalEntryLine.journal_entry_id == JournalEntry.id)
        .filter(JournalEntryLine.chart_of_account_id == account.id,
                JournalEntry.is_posted.is_(True))
    )

    # Opening = net (debit - credit) of all entries strictly before the period.
    opening = _ZERO
    if date_from:
        for entry, line in base.filter(JournalEntry.entry_date < date_from).all():
            opening += _dec(line.debit) - _dec(line.credit)

    q = base
    if date_from:
        q = q.filter(JournalEntry.entry_date >= date_from)
    if date_to:
        q = q.filter(JournalEntry.entry_date <= date_to)
    q = q.order_by(JournalEntry.entry_date, JournalEntry.id)

    rows = []
    running = opening
    total_debit = _ZERO
    total_credit = _ZERO
    for entry, line in q.all():
        debit = _dec(line.debit)
        credit = _dec(line.credit)
        running += debit - credit
        total_debit += debit
        total_credit += credit
        rows.append({
            "date": entry.entry_date,
            "voucher": entry.voucher.voucher_number if entry.voucher else "",
            "description": line.description or entry.description or "",
            "debit": debit, "credit": credit, "balance": running,
        })

    return {
        "account": account, "opening": opening, "rows": rows,
        "total_debit": total_debit, "total_credit": total_credit, "closing": running,
    }
