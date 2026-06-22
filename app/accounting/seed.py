"""Idempotent seeding for the chart of accounts.

Seeds a standard set of global accounts only when the chart of accounts table
is empty, so user edits are never overwritten or duplicated.
"""
from app.extensions import db
from app.accounting.models import ChartOfAccount

# (code, name, account_type)
CHART_OF_ACCOUNTS = [
    # Assets
    ("1000", "Cash in Hand", "Asset"),
    ("1010", "Bank Accounts", "Asset"),
    ("1020", "Easypaisa/JazzCash", "Asset"),
    ("1100", "Customer Receivables", "Asset"),
    ("1200", "Stock / Inventory", "Asset"),
    ("1300", "PSO Receivable", "Asset"),
    # Liabilities
    ("2000", "Vendor Payables", "Liability"),
    ("2010", "PSO Payable", "Liability"),
    ("2020", "Rented Vehicle Payable", "Liability"),
    ("2030", "Staff Salary Payable", "Liability"),
    # Equity
    ("3000", "Owner Equity", "Equity"),
    # Income
    ("4000", "Pump Fuel Sale", "Income"),
    ("4010", "Pump Lubricant Sale", "Income"),
    ("4020", "Rental Income", "Income"),
    ("4030", "Stock Gain", "Income"),
    ("4100", "Bulk Sale Income", "Income"),
    ("4200", "Carriage Income", "Income"),
    ("4300", "Agency Sale Income", "Income"),
    # Cost of Goods Sold
    ("5000", "Fuel Purchase Cost", "Cost of Goods Sold"),
    ("5010", "Lubricant Purchase Cost", "Cost of Goods Sold"),
    ("5100", "Bulk Purchase Cost", "Cost of Goods Sold"),
    ("5300", "Agency Product Purchase Cost", "Cost of Goods Sold"),
    # Expenses
    ("6000", "Pump Expenses", "Expense"),
    ("6100", "Carriage Expenses", "Expense"),
    ("6200", "Agency Expenses", "Expense"),
    ("6300", "Head Office Expenses", "Expense"),
    ("6310", "Home Expenses", "Expense"),
    ("6320", "Welfare Expenses", "Expense"),
]


def seed_accounting_data():
    """Insert the default chart of accounts if none exist yet."""
    created = {"chart_of_accounts": 0}

    if ChartOfAccount.query.count() == 0:
        for code, name, account_type in CHART_OF_ACCOUNTS:
            db.session.add(
                ChartOfAccount(code=code, name=name, account_type=account_type)
            )
            created["chart_of_accounts"] += 1
        db.session.commit()

    return created
