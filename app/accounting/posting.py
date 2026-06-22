"""Automatic double-entry posting engine.

Source transactions (head-office expenses, vendor payments, customer receipts,
…) are turned into balanced journal vouchers automatically. Each generated
JournalEntry carries the source's (source_type, source_id) so posting is
idempotent: re-posting first clears the previous entry+voucher for that source,
then rebuilds it only if the transaction should currently post.

A transaction "should post" when it is active AND has a cash/bank account
selected — that guarantees both sides of the entry are unambiguous
(Dr/Cr cash). Deactivating a source (toggle) clears its journal automatically.

Account resolution is by chart-of-accounts CODE (see app/accounting/seed.py),
so this module never hard-codes account ids. Cash/bank operational accounts map
to the matching chart account by their type.
"""
from decimal import Decimal

from app.extensions import db
from app.core.models import BusinessUnit, BusinessUnitType, VendorType
from app.accounting.models import ChartOfAccount, JournalEntry, Voucher
from app.accounting.services import (
    create_journal_entry,
    generate_voucher_number,
    validate_journal_entry_lines,
)

_ZERO = Decimal("0")

# Source-type tags for generated journal entries.
SOURCE_HO_EXPENSE = "ho_expense"
SOURCE_VENDOR_PAYMENT = "vendor_payment"
SOURCE_CUSTOMER_RECEIPT = "customer_receipt"
SOURCE_BULK_SALE = "bulk_sale"
SOURCE_AGENCY_SALE = "agency_sale"
SOURCE_AGENCY_PURCHASE = "agency_purchase"
SOURCE_PUMP_PURCHASE = "pump_purchase"
SOURCE_SALARY_PAYMENT = "salary_payment"
SOURCE_HO_SALARY = "ho_salary"
SOURCE_DAILY_CLOSING = "pump_daily_closing"
SOURCE_DAILY_CLOSING_COGS = "pump_daily_closing_cogs"
SOURCE_CASH_TRANSFER = "cash_transfer"
SOURCE_CARRIAGE_TRIP = "carriage_trip"
SOURCE_CARRIAGE_EXPENSE = "carriage_expense"

# Chart-of-account codes used by the postings below.
CODE_CASH = "1000"
CODE_BANK = "1010"
CODE_WALLET = "1020"
CODE_CUSTOMER_RECEIVABLE = "1100"
CODE_STOCK = "1200"
CODE_PSO_RECEIVABLE = "1300"
CODE_VENDOR_PAYABLE = "2000"
CODE_PSO_PAYABLE = "2010"
CODE_FUEL_SALE = "4000"
CODE_LUBRICANT_SALE = "4010"
CODE_BULK_INCOME = "4100"
CODE_CARRIAGE_INCOME = "4200"
CODE_AGENCY_INCOME = "4300"
CODE_FUEL_COST = "5000"
CODE_BULK_COST = "5100"
CODE_AGENCY_COST = "5300"
CODE_PUMP_EXPENSE = "6000"
CODE_CARRIAGE_EXPENSE = "6100"
CODE_HEAD_OFFICE_EXPENSE = "6300"

# Freight charged to an external party (not company-borne) becomes carriage
# income; company-borne freight is an internal cost, so no income is posted.
CARRIAGE_INCOME_PAYERS = {"Customer", "Pump", "Agency"}


# --------------------------------------------------------------------------- #
# Resolution helpers
# --------------------------------------------------------------------------- #
def account_by_code(code):
    """Return the ChartOfAccount with this code, or None."""
    return ChartOfAccount.query.filter_by(code=code).first()


def business_unit_by_type(bu_type):
    """Return the (first active) business unit of a given type, or None."""
    return (
        BusinessUnit.query.filter_by(type=bu_type, is_active=True)
        .order_by(BusinessUnit.id)
        .first()
    )


def bu_id(bu_type):
    bu = business_unit_by_type(bu_type)
    return bu.id if bu else None


def head_office_bu_id():
    return bu_id(BusinessUnitType.HEAD_OFFICE)


def cash_account_code(cash_bank_account):
    """Map an operational cash/bank account to its chart-of-accounts code."""
    if cash_bank_account is None:
        return CODE_CASH
    kind = (cash_bank_account.account_type or "").lower()
    if "bank" in kind:
        return CODE_BANK
    if "wallet" in kind:
        return CODE_WALLET
    return CODE_CASH


# --------------------------------------------------------------------------- #
# Core idempotent posting
# --------------------------------------------------------------------------- #
def clear_source(source_type, source_id):
    """Delete the generated journal entry (+voucher) for a source, if any."""
    if source_id is None:
        return
    entries = JournalEntry.query.filter_by(
        source_type=source_type, source_id=source_id
    ).all()
    for entry in entries:
        voucher = entry.voucher
        db.session.delete(entry)  # cascades to lines
        if voucher is not None:
            db.session.delete(voucher)
    if entries:
        db.session.flush()  # so voucher-number counts are correct on rebuild


def _post(source_type, source_id, *, voucher_type, business_unit_id,
          entry_date, description, lines, created_by_id):
    """Create one balanced voucher + journal entry from coded lines.

    `lines` items: {"code": str, "debit": Decimal, "credit": Decimal,
    "description": str}. Returns the JournalEntry, or None if it cannot be
    built (missing business unit / account / unbalanced — never raises so a
    posting problem can't break the underlying transaction save).
    """
    if business_unit_id is None:
        return None

    resolved = []
    for ln in lines:
        account = account_by_code(ln["code"])
        if account is None:
            return None
        resolved.append({
            "account_id": account.id,
            "debit": ln.get("debit") or _ZERO,
            "credit": ln.get("credit") or _ZERO,
            "description": ln.get("description"),
        })

    if validate_journal_entry_lines(resolved):
        return None  # never post an unbalanced entry

    voucher = Voucher(
        voucher_number=generate_voucher_number(voucher_type),
        voucher_type=voucher_type,
        business_unit_id=business_unit_id,
        date=entry_date,
        description=description,
        created_by_id=created_by_id,
        is_posted=True,
    )
    db.session.add(voucher)

    entry = create_journal_entry(
        business_unit_id=business_unit_id,
        entry_date=entry_date,
        lines=resolved,
        description=description,
        created_by_id=created_by_id,
        voucher=voucher,
    )
    entry.source_type = source_type
    entry.source_id = source_id
    return entry


def _sync(source_type, source_id, should_post, **post_kwargs):
    """Clear then (re)build the source's journal entry idempotently."""
    clear_source(source_type, source_id)
    if should_post:
        _post(source_type, source_id, **post_kwargs)


# Source types whose GL posting is hard-gated by the approval workflow (BRD §16).
GATED_SOURCES = {
    SOURCE_HO_EXPENSE, SOURCE_VENDOR_PAYMENT, SOURCE_BULK_SALE, SOURCE_AGENCY_SALE,
}


def _approval_clears(source_type, source_id):
    """True when this source is allowed to post to the GL under the approval
    gate: non-gated sources always clear; gated sources clear unless an approval
    record exists and is not yet Approved (Pending/Rejected → held out of GL).
    Grandfathered: no approval record → clears (posts as before)."""
    if source_type not in GATED_SOURCES:
        return True
    from app.approvals.service import is_blocked
    return not is_blocked(source_type, source_id)


def resync_for_approval(entity_type, entity_id):
    """Re-run a gated source's GL posting after its approval status changes, so
    approving makes it post and rejecting/reopening clears it. Idempotent."""
    if entity_type == SOURCE_HO_EXPENSE:
        from app.head_office.models import HeadOfficeExpense
        obj = db.session.get(HeadOfficeExpense, entity_id)
        if obj:
            sync_head_office_expense(obj)
    elif entity_type == SOURCE_VENDOR_PAYMENT:
        from app.head_office.models import VendorPayment
        obj = db.session.get(VendorPayment, entity_id)
        if obj:
            sync_vendor_payment(obj)
    elif entity_type == SOURCE_BULK_SALE:
        from app.bulk_sale.models import BulkSale
        obj = db.session.get(BulkSale, entity_id)
        if obj:
            sync_bulk_sale(obj)
    elif entity_type == SOURCE_AGENCY_SALE:
        from app.agency.models import AgencySale
        obj = db.session.get(AgencySale, entity_id)
        if obj:
            sync_agency_sale(obj)


# --------------------------------------------------------------------------- #
# Transaction-specific posters
# --------------------------------------------------------------------------- #
def sync_head_office_expense(expense):
    """Dr Head Office Expenses / Cr Cash-Bank (when active + account set)."""
    amount = expense.amount or _ZERO
    should = bool(expense.is_active and expense.paid_from_account_id and amount > 0
                  and _approval_clears(SOURCE_HO_EXPENSE, expense.id))
    lines = []
    if should:
        lines = [
            {"code": CODE_HEAD_OFFICE_EXPENSE, "debit": amount, "credit": _ZERO,
             "description": "Head office expense"},
            {"code": cash_account_code(expense.paid_from_account),
             "debit": _ZERO, "credit": amount, "description": "Paid"},
        ]
    _sync(
        SOURCE_HO_EXPENSE, expense.id, should,
        voucher_type="Expense Voucher", business_unit_id=head_office_bu_id(),
        entry_date=expense.expense_date,
        description=f"HO expense #{expense.id}",
        lines=lines, created_by_id=expense.created_by_id,
    )


def sync_ho_salary_payment(payment):
    """Head-office staff salary/advance — a head office expense:
    Dr Head Office Expenses / Cr Cash-Bank (when active + account set)."""
    amount = payment.amount or _ZERO
    should = bool(payment.is_active and payment.paid_from_account_id and amount > 0)
    lines = []
    if should:
        lines = [
            {"code": CODE_HEAD_OFFICE_EXPENSE, "debit": amount, "credit": _ZERO,
             "description": f"{payment.payment_type} — head office staff"},
            {"code": cash_account_code(payment.paid_from_account),
             "debit": _ZERO, "credit": amount, "description": "Paid"},
        ]
    _sync(
        SOURCE_HO_SALARY, payment.id, should,
        voucher_type="Payment Voucher", business_unit_id=head_office_bu_id(),
        entry_date=payment.payment_date,
        description=f"HO salary payment #{payment.id}",
        lines=lines, created_by_id=payment.created_by_id,
    )


def sync_vendor_payment(payment):
    """Dr Vendor/PSO Payable / Cr Cash-Bank (when active + account set)."""
    amount = payment.amount or _ZERO
    should = bool(payment.is_active and payment.paid_from_account_id and amount > 0
                  and _approval_clears(SOURCE_VENDOR_PAYMENT, payment.id))
    lines = []
    if should:
        is_pso = (
            payment.vendor is not None
            and payment.vendor.vendor_type == VendorType.PSO
        )
        payable_code = CODE_PSO_PAYABLE if is_pso else CODE_VENDOR_PAYABLE
        lines = [
            {"code": payable_code, "debit": amount, "credit": _ZERO,
             "description": "Payable settled"},
            {"code": cash_account_code(payment.paid_from_account),
             "debit": _ZERO, "credit": amount, "description": "Paid"},
        ]
    _sync(
        SOURCE_VENDOR_PAYMENT, payment.id, should,
        voucher_type="Payment Voucher", business_unit_id=head_office_bu_id(),
        entry_date=payment.payment_date,
        description=f"Vendor payment #{payment.id}",
        lines=lines, created_by_id=payment.created_by_id,
    )


def sync_customer_receipt(receipt):
    """Dr Cash-Bank / Cr Customer Receivables (when active + account set)."""
    amount = receipt.amount or _ZERO
    should = bool(receipt.is_active and receipt.received_into_account_id and amount > 0)
    lines = []
    if should:
        lines = [
            {"code": cash_account_code(receipt.received_into_account),
             "debit": amount, "credit": _ZERO, "description": "Received"},
            {"code": CODE_CUSTOMER_RECEIVABLE, "debit": _ZERO, "credit": amount,
             "description": "Receivable reduced"},
        ]
    _sync(
        SOURCE_CUSTOMER_RECEIPT, receipt.id, should,
        voucher_type="Receipt Voucher", business_unit_id=head_office_bu_id(),
        entry_date=receipt.receipt_date,
        description=f"Customer receipt #{receipt.id}",
        lines=lines, created_by_id=receipt.created_by_id,
    )


def sync_bulk_sale(sale):
    """Bulk sale: Dr Receivable/Cr Bulk Income (sale) + Dr Bulk Cost/Cr PSO
    Payable (purchase). One balanced entry; the cost legs are added only when a
    purchase amount exists."""
    sale_amt = sale.total_sale_amount or _ZERO
    purchase_amt = sale.total_purchase_amount or _ZERO
    should = bool(sale.is_active and sale_amt > 0
                  and _approval_clears(SOURCE_BULK_SALE, sale.id))
    lines = []
    if should:
        lines += [
            {"code": CODE_CUSTOMER_RECEIVABLE, "debit": sale_amt, "credit": _ZERO,
             "description": "Bulk sale receivable"},
            {"code": CODE_BULK_INCOME, "debit": _ZERO, "credit": sale_amt,
             "description": "Bulk sale income"},
        ]
        if purchase_amt > 0:
            lines += [
                {"code": CODE_BULK_COST, "debit": purchase_amt, "credit": _ZERO,
                 "description": "Bulk purchase cost"},
                {"code": CODE_PSO_PAYABLE, "debit": _ZERO, "credit": purchase_amt,
                 "description": "PSO payable"},
            ]
    _sync(
        SOURCE_BULK_SALE, sale.id, should,
        voucher_type="Sale Voucher", business_unit_id=bu_id(BusinessUnitType.BULK_SALE),
        entry_date=sale.sale_date, description=f"Bulk sale #{sale.id}",
        lines=lines, created_by_id=getattr(sale, "created_by_id", None),
    )


def sync_agency_sale(sale):
    """Agency sale: Dr Customer Receivable / Cr Agency Sale Income."""
    amount = sale.total_sale_amount or _ZERO
    should = bool(sale.is_active and amount > 0
                  and _approval_clears(SOURCE_AGENCY_SALE, sale.id))
    lines = []
    if should:
        lines = [
            {"code": CODE_CUSTOMER_RECEIVABLE, "debit": amount, "credit": _ZERO,
             "description": "Agency sale receivable"},
            {"code": CODE_AGENCY_INCOME, "debit": _ZERO, "credit": amount,
             "description": "Agency sale income"},
        ]
    _sync(
        SOURCE_AGENCY_SALE, sale.id, should,
        voucher_type="Sale Voucher", business_unit_id=bu_id(BusinessUnitType.OIL_AGENCY),
        entry_date=sale.sale_date, description=f"Agency sale #{sale.id}",
        lines=lines, created_by_id=getattr(sale, "created_by_id", None),
    )


def sync_agency_purchase(purchase):
    """Agency purchase: Dr Agency Product Purchase Cost / Cr Vendor Payable."""
    amount = purchase.total_amount or _ZERO
    should = bool(purchase.is_active and amount > 0)
    lines = []
    if should:
        lines = [
            {"code": CODE_AGENCY_COST, "debit": amount, "credit": _ZERO,
             "description": "Agency purchase cost"},
            {"code": CODE_VENDOR_PAYABLE, "debit": _ZERO, "credit": amount,
             "description": "Vendor payable"},
        ]
    _sync(
        SOURCE_AGENCY_PURCHASE, purchase.id, should,
        voucher_type="Purchase Voucher", business_unit_id=bu_id(BusinessUnitType.OIL_AGENCY),
        entry_date=purchase.purchase_date, description=f"Agency purchase #{purchase.id}",
        lines=lines, created_by_id=getattr(purchase, "created_by_id", None),
    )


def sync_pump_purchase(purchase):
    """Pump fuel purchase: Dr Stock / Cr PSO Payable (received fuel = stock)."""
    amount = purchase.total_amount or _ZERO
    should = bool(purchase.is_active and amount > 0)
    lines = []
    if should:
        lines = [
            {"code": CODE_STOCK, "debit": amount, "credit": _ZERO,
             "description": "Fuel stock received"},
            {"code": CODE_PSO_PAYABLE, "debit": _ZERO, "credit": amount,
             "description": "PSO payable"},
        ]
    _sync(
        SOURCE_PUMP_PURCHASE, purchase.id, should,
        voucher_type="Purchase Voucher", business_unit_id=bu_id(BusinessUnitType.PETROL_PUMP_RETAIL),
        entry_date=purchase.purchase_date, description=f"Pump purchase #{purchase.id}",
        lines=lines, created_by_id=getattr(purchase, "created_by_id", None),
    )


def sync_salary_payment(payment):
    """Staff salary/advance: Dr Pump Expenses / Cr Cash-Bank (when account set)."""
    amount = payment.amount or _ZERO
    should = bool(payment.is_active and payment.paid_from_account_id and amount > 0)
    lines = []
    if should:
        lines = [
            {"code": CODE_PUMP_EXPENSE, "debit": amount, "credit": _ZERO,
             "description": f"{payment.payment_type} payment"},
            {"code": cash_account_code(payment.paid_from_account),
             "debit": _ZERO, "credit": amount, "description": "Paid"},
        ]
    _sync(
        SOURCE_SALARY_PAYMENT, payment.id, should,
        voucher_type="Payment Voucher", business_unit_id=bu_id(BusinessUnitType.PETROL_PUMP_RETAIL),
        entry_date=payment.payment_date, description=f"Salary payment #{payment.id}",
        lines=lines, created_by_id=getattr(payment, "created_by_id", None),
    )


def sync_daily_closing(closing):
    """Pump retail revenue for the day, from the daily closing.

    Dr the payment-method accounts (cash / bank / PSO card receivable / wallet /
    credit customer) and Cr Pump Fuel Sale + Pump Lubricant Sale. This is the
    single recognition point for pump retail revenue — individual machine
    readings and lubricant sales are NOT posted, so nothing double-counts.

    Posts only when the manually entered payment splits balance the derived sale
    totals (otherwise the closing is flagged in the UI and skipped here).
    """
    fuel = closing.fuel_sale_amount or _ZERO
    lube = closing.lubricant_sale_amount or _ZERO
    cash = closing.cash_received or _ZERO
    bank = (closing.bank_card_received or _ZERO) + (closing.bank_transfer_amount or _ZERO)
    pso = closing.pso_card_amount or _ZERO
    wallet = (closing.easypaisa_amount or _ZERO) + (closing.jazzcash_amount or _ZERO)
    credit = closing.credit_sale_amount or _ZERO

    total_sale = fuel + lube
    total_pay = cash + bank + pso + wallet + credit
    should = bool(closing.is_active and total_sale > 0 and total_pay == total_sale)

    lines = []
    if should:
        for code, amt, desc in [
            (CODE_CASH, cash, "Cash received"),
            (CODE_BANK, bank, "Card / bank transfer"),
            (CODE_PSO_RECEIVABLE, pso, "PSO card"),
            (CODE_WALLET, wallet, "Easypaisa / JazzCash"),
            (CODE_CUSTOMER_RECEIVABLE, credit, "Credit sale"),
        ]:
            if amt > 0:
                lines.append({"code": code, "debit": amt, "credit": _ZERO, "description": desc})
        if fuel > 0:
            lines.append({"code": CODE_FUEL_SALE, "debit": _ZERO, "credit": fuel, "description": "Fuel sale"})
        if lube > 0:
            lines.append({"code": CODE_LUBRICANT_SALE, "debit": _ZERO, "credit": lube, "description": "Lubricant sale"})

    _sync(
        SOURCE_DAILY_CLOSING, closing.id, should,
        voucher_type="Sale Voucher", business_unit_id=bu_id(BusinessUnitType.PETROL_PUMP_RETAIL),
        entry_date=closing.closing_date, description=f"Pump daily closing #{closing.id}",
        lines=lines, created_by_id=getattr(closing, "created_by_id", None),
    )
    # Pair the revenue with the matching cost of fuel sold.
    sync_daily_closing_cogs(closing)


def sync_daily_closing_cogs(closing):
    """Cost of fuel sold for the closing's day: Dr Fuel Purchase Cost / Cr Stock.

    The cost is the weighted-average fuel COGS for this pump on the closing date
    (same figure the reports derive). This relieves the Stock asset that pump
    purchases built up, so the ledger shows true fuel gross profit. Lubricant
    COGS is not tracked (lubricants aren't bought through pump purchases).
    """
    from app.petrol_pumps import costing

    cogs = _ZERO
    if closing.is_active:
        cogs = costing.fuel_cogs(
            closing.closing_date, closing.closing_date,
            pump_id=closing.petrol_pump_id,
        ) or _ZERO
    should = bool(closing.is_active and cogs > 0)

    lines = []
    if should:
        lines = [
            {"code": CODE_FUEL_COST, "debit": cogs, "credit": _ZERO,
             "description": "Cost of fuel sold"},
            {"code": CODE_STOCK, "debit": _ZERO, "credit": cogs,
             "description": "Stock relieved"},
        ]
    _sync(
        SOURCE_DAILY_CLOSING_COGS, closing.id, should,
        voucher_type="Journal Voucher", business_unit_id=bu_id(BusinessUnitType.PETROL_PUMP_RETAIL),
        entry_date=closing.closing_date, description=f"Fuel COGS for closing #{closing.id}",
        lines=lines, created_by_id=getattr(closing, "created_by_id", None),
    )


def sync_cash_transfer(transfer):
    """Internal cash transfer: Dr destination chart account / Cr source.

    Only posts a journal when the two accounts map to DIFFERENT chart accounts
    (e.g. Cash -> Bank). A Cash -> Cash move (both code 1000) has no GL effect,
    so we skip it — the per-account operational balances still move.
    """
    amount = transfer.amount or _ZERO
    from_code = cash_account_code(transfer.from_account)
    to_code = cash_account_code(transfer.to_account)
    should = bool(
        transfer.is_active and amount > 0
        and transfer.from_account_id and transfer.to_account_id
        and transfer.from_account_id != transfer.to_account_id
        and from_code != to_code
    )
    lines = []
    if should:
        lines = [
            {"code": to_code, "debit": amount, "credit": _ZERO, "description": "Transfer in"},
            {"code": from_code, "debit": _ZERO, "credit": amount, "description": "Transfer out"},
        ]
    _sync(
        SOURCE_CASH_TRANSFER, transfer.id, should,
        voucher_type="Cash Transfer Voucher", business_unit_id=head_office_bu_id(),
        entry_date=transfer.transfer_date, description=f"Cash transfer #{transfer.id}",
        lines=lines, created_by_id=getattr(transfer, "created_by_id", None),
    )


def sync_carriage_trip(trip):
    """Carriage trip: one composite, balanced Journal Voucher made of up to
    three internally balanced leg-groups (any subset may apply):

    * Freight income — only when freight is charged to an external party
      (freight_paid_by in Customer/Pump/Agency). Company-borne freight is an
      internal cost, so no income is recognised.
          Dr Customer Receivable (1100) / Cr Carriage Income (4200)
    * Own-vehicle cash expenses (fuel, toll, loading/unloading, driver,
      maintenance, other) — only when a paid-from cash/bank account is set.
          Dr Carriage Expenses (6100) / Cr <paid-from cash account>
    * Rented-vehicle rent — recorded as a payable to the rented vehicle vendor.
          Dr Carriage Expenses (6100) / Cr Vendor Payable (2000)

    Each group balances on its own, so any combination still balances. Posts on
    the carriage business unit. Idempotent via source_type/source_id.
    """
    freight = trip.total_freight_amount or _ZERO
    rent = trip.rent_amount or _ZERO
    cash_expense = (
        (trip.fuel_expense or _ZERO) + (trip.toll_tax or _ZERO)
        + (trip.loading_unloading_expense or _ZERO) + (trip.driver_expense or _ZERO)
        + (trip.maintenance_expense or _ZERO) + (trip.other_expense or _ZERO)
    )

    earns_income = (trip.freight_paid_by in CARRIAGE_INCOME_PAYERS) and freight > 0
    posts_cash_expense = bool(trip.paid_from_account_id) and cash_expense > 0
    posts_rent = rent > 0

    should = bool(
        trip.is_active and (earns_income or posts_cash_expense or posts_rent)
    )

    lines = []
    if should:
        if earns_income:
            lines += [
                {"code": CODE_CUSTOMER_RECEIVABLE, "debit": freight, "credit": _ZERO,
                 "description": "Carriage freight receivable"},
                {"code": CODE_CARRIAGE_INCOME, "debit": _ZERO, "credit": freight,
                 "description": "Carriage income"},
            ]
        if posts_cash_expense:
            lines += [
                {"code": CODE_CARRIAGE_EXPENSE, "debit": cash_expense, "credit": _ZERO,
                 "description": "Trip expenses"},
                {"code": cash_account_code(trip.paid_from_account),
                 "debit": _ZERO, "credit": cash_expense, "description": "Paid"},
            ]
        if posts_rent:
            lines += [
                {"code": CODE_CARRIAGE_EXPENSE, "debit": rent, "credit": _ZERO,
                 "description": "Rented vehicle rent"},
                {"code": CODE_VENDOR_PAYABLE, "debit": _ZERO, "credit": rent,
                 "description": "Rented vehicle payable"},
            ]
    _sync(
        SOURCE_CARRIAGE_TRIP, trip.id, should,
        voucher_type="Journal Voucher", business_unit_id=bu_id(BusinessUnitType.CARRIAGE),
        entry_date=trip.trip_date, description=f"Carriage trip {trip.trip_number}",
        lines=lines, created_by_id=getattr(trip, "created_by_id", None),
    )


def sync_carriage_expense(expense):
    """Carriage business expense: Dr Carriage Expenses / Cr Cash-Bank
    (when active + a paid-from account is set)."""
    amount = expense.amount or _ZERO
    should = bool(expense.is_active and expense.paid_from_account_id and amount > 0)
    lines = []
    if should:
        lines = [
            {"code": CODE_CARRIAGE_EXPENSE, "debit": amount, "credit": _ZERO,
             "description": "Carriage expense"},
            {"code": cash_account_code(expense.paid_from_account),
             "debit": _ZERO, "credit": amount, "description": "Paid"},
        ]
    _sync(
        SOURCE_CARRIAGE_EXPENSE, expense.id, should,
        voucher_type="Expense Voucher", business_unit_id=bu_id(BusinessUnitType.CARRIAGE),
        entry_date=expense.expense_date, description=f"Carriage expense #{expense.id}",
        lines=lines, created_by_id=getattr(expense, "created_by_id", None),
    )


# --------------------------------------------------------------------------- #
# Reporting: trial balance
# --------------------------------------------------------------------------- #
def trial_balance(date_from=None, date_to=None):
    """Return per-account totals from posted journal lines + grand totals.

    Result: {"rows": [{account, debit, credit, balance}], "total_debit",
    "total_credit", "balanced": bool}. `balance` is debit − credit (positive =
    debit balance). Date filters apply to the journal entry date.
    """
    from app.accounting.models import JournalEntryLine

    query = (
        db.session.query(
            ChartOfAccount,
            db.func.coalesce(db.func.sum(JournalEntryLine.debit), 0),
            db.func.coalesce(db.func.sum(JournalEntryLine.credit), 0),
        )
        .join(JournalEntryLine, JournalEntryLine.chart_of_account_id == ChartOfAccount.id)
        .join(JournalEntry, JournalEntryLine.journal_entry_id == JournalEntry.id)
        .filter(JournalEntry.is_posted.is_(True))
    )
    if date_from:
        query = query.filter(JournalEntry.entry_date >= date_from)
    if date_to:
        query = query.filter(JournalEntry.entry_date <= date_to)
    query = query.group_by(ChartOfAccount.id).order_by(ChartOfAccount.code)

    rows = []
    total_debit = _ZERO
    total_credit = _ZERO
    for account, debit, credit in query.all():
        debit = Decimal(str(debit))
        credit = Decimal(str(credit))
        if debit == 0 and credit == 0:
            continue
        total_debit += debit
        total_credit += credit
        rows.append({
            "account": account, "debit": debit, "credit": credit,
            "balance": debit - credit,
        })

    return {
        "rows": rows,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "balanced": total_debit == total_credit,
    }
