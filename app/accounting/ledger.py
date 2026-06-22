"""Customer & vendor subsidiary ledgers (derived from source records).

Balances are computed live from the underlying transactions so they are always
consistent (no separate posting layer to drift out of sync):

    customer receivable = opening + bulk sales + agency sales
                          + credit lubricant sales - customer receipts
    vendor payable      = opening + pump purchases + bulk purchase cost
                          + agency purchases - vendor payments

A positive customer balance means the customer owes us; a positive vendor
balance means we owe the vendor.
"""
from decimal import Decimal

from app.extensions import db
from app.core.models import Customer, Vendor
from app.petrol_pumps.models import (
    LubricantSale,
    PsoCardPayment,
    PumpDailyClosing,
    PumpPurchase,
)
from app.bulk_sale.models import BulkSale
from app.agency.models import AgencyPurchase, AgencySale
from app.head_office.models import VendorPayment
from app.accounting.models import CustomerReceipt

_ZERO = Decimal("0")


def _sum(amount_col, *filters):
    query = db.session.query(db.func.coalesce(db.func.sum(amount_col), 0))
    for f in filters:
        query = query.filter(f)
    return Decimal(str(query.scalar()))


# --------------------------------------------------------------------------- #
# Customers (receivables)
# --------------------------------------------------------------------------- #
def customer_pump_credit_total(customer_id):
    """Credit sales booked against this customer in pump daily closings."""
    return _sum(
        PumpDailyClosing.credit_sale_amount,
        PumpDailyClosing.is_active.is_(True),
        PumpDailyClosing.credit_customer_id == customer_id,
    )


def customer_sales_total(customer_id):
    bulk = _sum(BulkSale.total_sale_amount, BulkSale.is_active.is_(True), BulkSale.customer_id == customer_id)
    agency = _sum(AgencySale.total_sale_amount, AgencySale.is_active.is_(True), AgencySale.customer_id == customer_id)
    lubricant = _sum(
        LubricantSale.total_amount, LubricantSale.is_active.is_(True),
        LubricantSale.customer_id == customer_id,
        LubricantSale.payment_method == "Credit Customer",
    )
    pump_credit = customer_pump_credit_total(customer_id)
    return bulk + agency + lubricant + pump_credit


def customer_receipts_total(customer_id):
    return _sum(CustomerReceipt.amount, CustomerReceipt.is_active.is_(True), CustomerReceipt.customer_id == customer_id)


def customer_balance(customer):
    opening = Decimal(str(customer.opening_balance or 0))
    return opening + customer_sales_total(customer.id) - customer_receipts_total(customer.id)


def customer_ledger(customer):
    """Return (entries_with_running_balance, closing_balance) for a customer."""
    entries = []
    opening = Decimal(str(customer.opening_balance or 0))
    if opening != 0:
        entries.append({"date": None, "description": "Opening balance",
                        "debit": opening, "credit": _ZERO})

    for s in BulkSale.query.filter_by(customer_id=customer.id, is_active=True).all():
        entries.append({"date": s.sale_date, "description": f"Bulk sale #{s.id} ({s.product.name})",
                        "debit": Decimal(str(s.total_sale_amount)), "credit": _ZERO})
    for s in AgencySale.query.filter_by(customer_id=customer.id, is_active=True).all():
        entries.append({"date": s.sale_date, "description": f"Agency sale #{s.id} ({s.product.name})",
                        "debit": Decimal(str(s.total_sale_amount)), "credit": _ZERO})
    for s in LubricantSale.query.filter_by(customer_id=customer.id, is_active=True, payment_method="Credit Customer").all():
        entries.append({"date": s.sale_date, "description": f"Lubricant sale #{s.id} ({s.product.name})",
                        "debit": Decimal(str(s.total_amount)), "credit": _ZERO})
    for cl in PumpDailyClosing.query.filter_by(credit_customer_id=customer.id, is_active=True).all():
        if (cl.credit_sale_amount or 0) > 0:
            entries.append({"date": cl.closing_date,
                            "description": f"Credit fuel sale — daily closing #{cl.id}",
                            "debit": Decimal(str(cl.credit_sale_amount)), "credit": _ZERO})
    for r in CustomerReceipt.query.filter_by(customer_id=customer.id, is_active=True).all():
        entries.append({"date": r.receipt_date, "description": f"Receipt #{r.id} ({r.payment_method})",
                        "debit": _ZERO, "credit": Decimal(str(r.amount))})

    entries.sort(key=lambda e: (e["date"] is not None, e["date"] or ""))
    running = _ZERO
    for e in entries:
        running += e["debit"] - e["credit"]
        e["balance"] = running
    return entries, running


def customer_statement(customer):
    """Business-wise statement for a customer (BRD §5.5 / §11.3).

    Breaks the customer's charges down by business unit (bulk / agency / pump
    lubricant credit) with deal counts, then nets global receipts against the
    total. Receipts are not tagged to a business unit, so they reduce the
    overall balance rather than any single segment. Returns a dict with
    `segments`, `opening`, `charges`, `receipts`, and `closing`.
    """
    cid = customer.id
    opening = Decimal(str(customer.opening_balance or 0))
    segments = [
        {"business": "Bulk Sale",
         "deals": BulkSale.query.filter_by(customer_id=cid, is_active=True).count(),
         "charges": _sum(BulkSale.total_sale_amount, BulkSale.is_active.is_(True), BulkSale.customer_id == cid)},
        {"business": "Oil Agency",
         "deals": AgencySale.query.filter_by(customer_id=cid, is_active=True).count(),
         "charges": _sum(AgencySale.total_sale_amount, AgencySale.is_active.is_(True), AgencySale.customer_id == cid)},
        {"business": "Petrol Pump (lubricant credit)",
         "deals": LubricantSale.query.filter_by(customer_id=cid, is_active=True, payment_method="Credit Customer").count(),
         "charges": _sum(LubricantSale.total_amount, LubricantSale.is_active.is_(True),
                         LubricantSale.customer_id == cid, LubricantSale.payment_method == "Credit Customer")},
        {"business": "Petrol Pump (credit fuel sale)",
         "deals": PumpDailyClosing.query.filter(
             PumpDailyClosing.credit_customer_id == cid,
             PumpDailyClosing.is_active.is_(True),
             PumpDailyClosing.credit_sale_amount > 0).count(),
         "charges": customer_pump_credit_total(cid)},
    ]
    charges = sum((s["charges"] for s in segments), _ZERO)
    receipts = customer_receipts_total(cid)
    return {
        "segments": segments, "opening": opening, "charges": charges,
        "receipts": receipts, "closing": opening + charges - receipts,
    }


def total_receivables():
    return sum((customer_balance(c) for c in Customer.query.all()), _ZERO)


# --------------------------------------------------------------------------- #
# Vendors (payables)
# --------------------------------------------------------------------------- #
def vendor_purchases_total(vendor_id):
    pump = _sum(PumpPurchase.total_amount, PumpPurchase.is_active.is_(True), PumpPurchase.vendor_id == vendor_id)
    bulk = _sum(BulkSale.total_purchase_amount, BulkSale.is_active.is_(True), BulkSale.vendor_id == vendor_id)
    agency = _sum(AgencyPurchase.total_amount, AgencyPurchase.is_active.is_(True), AgencyPurchase.vendor_id == vendor_id)
    return pump + bulk + agency


def vendor_payments_total(vendor_id):
    return _sum(VendorPayment.amount, VendorPayment.is_active.is_(True), VendorPayment.vendor_id == vendor_id)


def vendor_pso_card_verified_total(vendor_id):
    """PSO-card receipts from pumps that have been VERIFIED with PSO — these
    count as payments to the PSO vendor (reduce the payable). Pending (unverified)
    card amounts are NOT included until the accountant verifies them."""
    return _sum(
        PsoCardPayment.amount,
        PsoCardPayment.is_active.is_(True),
        PsoCardPayment.is_verified.is_(True),
        PsoCardPayment.vendor_id == vendor_id,
    )


def vendor_balance(vendor):
    opening = Decimal(str(vendor.opening_balance or 0))
    return (
        opening + vendor_purchases_total(vendor.id)
        - vendor_payments_total(vendor.id)
        - vendor_pso_card_verified_total(vendor.id)
    )


def vendor_ledger(vendor):
    """Return (entries_with_running_balance, closing_balance) for a vendor."""
    entries = []
    opening = Decimal(str(vendor.opening_balance or 0))
    if opening != 0:
        entries.append({"date": None, "description": "Opening balance",
                        "debit": _ZERO, "credit": opening})

    for p in PumpPurchase.query.filter_by(vendor_id=vendor.id, is_active=True).all():
        entries.append({"date": p.purchase_date, "description": f"Pump purchase #{p.id}",
                        "debit": _ZERO, "credit": Decimal(str(p.total_amount))})
    for s in BulkSale.query.filter_by(vendor_id=vendor.id, is_active=True).all():
        entries.append({"date": s.sale_date, "description": f"Bulk purchase (sale #{s.id})",
                        "debit": _ZERO, "credit": Decimal(str(s.total_purchase_amount))})
    for p in AgencyPurchase.query.filter_by(vendor_id=vendor.id, is_active=True).all():
        entries.append({"date": p.purchase_date, "description": f"Agency purchase #{p.id} ({p.product.name})",
                        "debit": _ZERO, "credit": Decimal(str(p.total_amount))})
    for pay in VendorPayment.query.filter_by(vendor_id=vendor.id, is_active=True).all():
        entries.append({"date": pay.payment_date, "description": f"Payment #{pay.id} ({pay.payment_method})",
                        "debit": Decimal(str(pay.amount)), "credit": _ZERO})
    for card in PsoCardPayment.query.filter_by(vendor_id=vendor.id, is_active=True, is_verified=True).all():
        pump = card.petrol_pump.name if card.petrol_pump else "pump"
        entries.append({"date": card.payment_date,
                        "description": f"PSO card (verified) — {pump}",
                        "debit": Decimal(str(card.amount)), "credit": _ZERO})

    entries.sort(key=lambda e: (e["date"] is not None, e["date"] or ""))
    running = _ZERO
    for e in entries:
        running += e["credit"] - e["debit"]
        e["balance"] = running
    return entries, running


def vendor_statement(vendor):
    """Business-wise statement for a vendor (BRD §5.6 / §11.4).

    Breaks the vendor's purchases down by business unit (pump fuel purchases /
    bulk purchase cost / agency purchases) with deal counts, then nets global
    payments against the total. Returns a dict with `segments`, `opening`,
    `purchases`, `payments`, and `closing`.
    """
    vid = vendor.id
    opening = Decimal(str(vendor.opening_balance or 0))
    segments = [
        {"business": "Petrol Pump (purchases)",
         "deals": PumpPurchase.query.filter_by(vendor_id=vid, is_active=True).count(),
         "purchases": _sum(PumpPurchase.total_amount, PumpPurchase.is_active.is_(True), PumpPurchase.vendor_id == vid)},
        {"business": "Bulk Sale (purchase cost)",
         "deals": BulkSale.query.filter_by(vendor_id=vid, is_active=True).count(),
         "purchases": _sum(BulkSale.total_purchase_amount, BulkSale.is_active.is_(True), BulkSale.vendor_id == vid)},
        {"business": "Oil Agency (purchases)",
         "deals": AgencyPurchase.query.filter_by(vendor_id=vid, is_active=True).count(),
         "purchases": _sum(AgencyPurchase.total_amount, AgencyPurchase.is_active.is_(True), AgencyPurchase.vendor_id == vid)},
    ]
    purchases = sum((s["purchases"] for s in segments), _ZERO)
    payments = vendor_payments_total(vid) + vendor_pso_card_verified_total(vid)
    return {
        "segments": segments, "opening": opening, "purchases": purchases,
        "payments": payments, "closing": opening + purchases - payments,
    }


def total_payables():
    return sum((vendor_balance(v) for v in Vendor.query.all()), _ZERO)
