"""Reporting services: consolidate figures across all business units.

Read-only aggregation over the existing transactional tables. Date filtering is
optional (all-time when no dates are given).

Honesty notes:
  * Petrol pump fuel COGS is now matched to sales via weighted-average costing
    (see app/petrol_pumps/costing.py), so pump "net" = revenue - fuel COGS -
    expenses. Lubricant cost is not captured yet (lubricants aren't purchased
    through pump purchases), so lubricant sale still flows in at full value.
  * Receivables/payables are exact (from the customer/vendor ledgers).
"""
from datetime import date, timedelta
from decimal import Decimal

from app.extensions import db
from app.core.models import CashBankAccount
from app.petrol_pumps import costing
from app.petrol_pumps.models import (
    LubricantSale,
    MachineReading,
    PumpExpense,
    PumpPurchase,
)
from app.bulk_sale.models import BulkSale
from app.carriage.models import CarriageTrip
from app.agency.models import AgencyPurchase, AgencySale
from app.head_office.models import (
    HeadOfficeCashReceipt,
    HeadOfficeExpense,
    VendorPayment,
)

_ZERO = Decimal("0")


def _sum(amount_col, date_col, date_from, date_to, extra_filter=None):
    """Sum a numeric column over active rows within an optional date range."""
    query = db.session.query(db.func.coalesce(db.func.sum(amount_col), 0))
    if extra_filter is not None:
        query = query.filter(extra_filter)
    if date_from:
        query = query.filter(date_col >= date_from)
    if date_to:
        query = query.filter(date_col <= date_to)
    return Decimal(str(query.scalar()))


def pump_retail_summary(date_from=None, date_to=None):
    active = MachineReading.is_active.is_(True)
    fuel = _sum(MachineReading.sale_amount, MachineReading.reading_date, date_from, date_to, active)
    fuel_liters = _sum(MachineReading.sale_liters, MachineReading.reading_date, date_from, date_to, active)
    lubricant = _sum(LubricantSale.total_amount, LubricantSale.sale_date, date_from, date_to, LubricantSale.is_active.is_(True))
    expenses = _sum(PumpExpense.amount, PumpExpense.expense_date, date_from, date_to, PumpExpense.is_active.is_(True))
    purchases = _sum(PumpPurchase.total_amount, PumpPurchase.purchase_date, date_from, date_to, PumpPurchase.is_active.is_(True))
    cogs = costing.fuel_cogs(date_from, date_to)
    # Approved stock gain/loss adjustments, valued at weighted-average cost
    # (BRD §6.6: stock gain affects profit only after approval).
    stock_gain_loss = costing.stock_adjustment_value(date_from, date_to)
    revenue = fuel + lubricant
    return {
        "fuel_sale": fuel, "fuel_liters": fuel_liters, "lubricant_sale": lubricant,
        "revenue": revenue, "expenses": expenses, "purchases": purchases,
        "cogs": cogs, "fuel_gross_profit": fuel - cogs,
        "stock_gain_loss": stock_gain_loss,
        "net": revenue - cogs - expenses + stock_gain_loss,
    }


def bulk_summary(date_from=None, date_to=None):
    active = BulkSale.is_active.is_(True)
    sale = _sum(BulkSale.total_sale_amount, BulkSale.sale_date, date_from, date_to, active)
    purchase = _sum(BulkSale.total_purchase_amount, BulkSale.sale_date, date_from, date_to, active)
    profit = _sum(BulkSale.net_profit, BulkSale.sale_date, date_from, date_to, active)
    return {"sale": sale, "purchase": purchase, "net": profit}


def carriage_summary(date_from=None, date_to=None):
    active = CarriageTrip.is_active.is_(True)
    freight = _sum(CarriageTrip.total_freight_amount, CarriageTrip.trip_date, date_from, date_to, active)
    profit = _sum(CarriageTrip.net_profit, CarriageTrip.trip_date, date_from, date_to, active)
    return {"freight": freight, "expenses": freight - profit, "net": profit}


def agency_summary(date_from=None, date_to=None):
    sale = _sum(AgencySale.total_sale_amount, AgencySale.sale_date, date_from, date_to, AgencySale.is_active.is_(True))
    profit = _sum(AgencySale.net_profit, AgencySale.sale_date, date_from, date_to, AgencySale.is_active.is_(True))
    purchases = _sum(AgencyPurchase.total_amount, AgencyPurchase.purchase_date, date_from, date_to, AgencyPurchase.is_active.is_(True))
    return {"sale": sale, "purchases": purchases, "net": profit}


def head_office_summary(date_from=None, date_to=None):
    received = _sum(HeadOfficeCashReceipt.amount, HeadOfficeCashReceipt.receipt_date, date_from, date_to, HeadOfficeCashReceipt.is_active.is_(True))
    expenses = _sum(HeadOfficeExpense.amount, HeadOfficeExpense.expense_date, date_from, date_to, HeadOfficeExpense.is_active.is_(True))
    payments = _sum(VendorPayment.amount, VendorPayment.payment_date, date_from, date_to, VendorPayment.is_active.is_(True))
    return {"cash_received": received, "expenses": expenses, "vendor_payments": payments}


def combined_summary(date_from=None, date_to=None):
    """Combined company profit/loss and the per-business parts.

    Pump net now reflects fuel COGS (weighted-average), so the only remaining
    approximation is lubricant cost (not yet captured).
    """
    pump = pump_retail_summary(date_from, date_to)
    bulk = bulk_summary(date_from, date_to)
    carriage = carriage_summary(date_from, date_to)
    agency = agency_summary(date_from, date_to)
    ho = head_office_summary(date_from, date_to)

    # Head office receipts are internal transfers, not income, so excluded.
    combined_profit = (
        pump["net"] + bulk["net"] + carriage["net"] + agency["net"]
        - ho["expenses"] - ho["vendor_payments"]
    )
    return {
        "pump": pump, "bulk": bulk, "carriage": carriage, "agency": agency,
        "head_office": ho, "combined_profit": combined_profit,
    }


def monthly_trend(months=6):
    """Month-by-month revenue per business and combined net, chart-ready.

    Returns plain floats (not Decimal) so the dict can go straight through
    Jinja's |tojson into a chart on the page.
    """
    today = date.today()
    year, month = today.year, today.month
    starts = []
    for _ in range(months):
        starts.append(date(year, month, 1))
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    starts.reverse()

    out = {"labels": [], "pump": [], "bulk": [], "carriage": [],
           "agency": [], "net": []}
    for start in starts:
        if start.month == 12:
            end = date(start.year, 12, 31)
        else:
            end = date(start.year, start.month + 1, 1) - timedelta(days=1)
        s = combined_summary(start, end)
        out["labels"].append(start.strftime("%b %Y"))
        out["pump"].append(float(s["pump"]["revenue"]))
        out["bulk"].append(float(s["bulk"]["sale"]))
        out["carriage"].append(float(s["carriage"]["freight"]))
        out["agency"].append(float(s["agency"]["sale"]))
        out["net"].append(float(s["combined_profit"]))
    return out


def cash_position():
    """All active cash/bank accounts with balances, plus the grand total."""
    accounts = (
        CashBankAccount.query.filter_by(is_active=True)
        .order_by(CashBankAccount.name)
        .all()
    )
    total = sum((a.current_balance or _ZERO) for a in accounts)
    return {"accounts": accounts, "total": Decimal(str(total))}


def receivables_payables():
    """Exact total receivables and payables from the customer/vendor ledgers."""
    from app.accounting import ledger
    return {
        "receivables": ledger.total_receivables(),
        "payables": ledger.total_payables(),
    }
