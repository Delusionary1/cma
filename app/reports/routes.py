"""Reports & Dashboards: consolidated business-wise and combined views.

Read-only. Accessible by the reports module roles (Admin / Owner, Head Office
Manager, Petrol Pump Manager, Accountant, Transport Manager).
"""
from datetime import datetime

from flask import Blueprint, render_template, request

from app.extensions import db
from app.auth.access import roles_for_module
from app.auth.decorators import role_required
from app.reports import services
from app.reports.exporters import VALID_FORMATS, export_response, fmt

reports_bp = Blueprint("reports", __name__)

REPORT_ROLES = roles_for_module("reports")


def _export_format():
    """Return 'xlsx'/'pdf' if a valid ?export= is requested, else None."""
    requested = request.args.get("export", "").strip().lower()
    return requested if requested in VALID_FORMATS else None


def _range_label(filters):
    """Human-readable date-range caption for export documents."""
    if filters.get("from") and filters.get("to"):
        return f"{filters['from']} to {filters['to']}"
    if filters.get("from"):
        return f"from {filters['from']}"
    if filters.get("to"):
        return f"up to {filters['to']}"
    return "all dates"


def _date_range():
    """Parse optional ?from=&to= date filters (YYYY-MM-DD)."""
    def parse(name):
        raw = request.args.get(name, "").strip()
        if not raw:
            return None, raw
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date(), raw
        except ValueError:
            return None, raw

    date_from, raw_from = parse("from")
    date_to, raw_to = parse("to")
    return date_from, date_to, {"from": raw_from, "to": raw_to}


@reports_bp.route("/")
@role_required(*REPORT_ROLES)
def index():
    """Main consolidated dashboard."""
    date_from, date_to, filters = _date_range()
    summary = services.combined_summary(date_from, date_to)
    cash = services.cash_position()
    balances = services.receivables_payables()
    return render_template(
        "reports/index.html",
        s=summary, cash=cash, balances=balances, filters=filters,
    )


@reports_bp.route("/profit-loss")
@role_required(*REPORT_ROLES)
def profit_loss():
    """Business-wise profit/loss table (with Excel/PDF export)."""
    date_from, date_to, filters = _date_range()
    summary = services.combined_summary(date_from, date_to)

    export = _export_format()
    if export:
        s = summary
        blocks = [
            {"title": "Petrol Pump Retail", "headers": ["Item", "Amount"], "rows": [
                ["Fuel sale", fmt(s["pump"]["fuel_sale"])],
                ["Fuel liters", fmt(s["pump"]["fuel_liters"])],
                ["Lubricant sale", fmt(s["pump"]["lubricant_sale"])],
                ["Total revenue", fmt(s["pump"]["revenue"])],
                ["Fuel COGS (weighted-avg)", fmt(s["pump"]["cogs"])],
                ["Fuel gross profit", fmt(s["pump"]["fuel_gross_profit"])],
                ["Stock gain/loss (approved)", fmt(s["pump"]["stock_gain_loss"])],
                ["Expenses", fmt(s["pump"]["expenses"])],
                ["Net", fmt(s["pump"]["net"])],
            ]},
            {"title": "Bulk Sale", "headers": ["Item", "Amount"], "rows": [
                ["Sale", fmt(s["bulk"]["sale"])],
                ["Purchase cost", fmt(s["bulk"]["purchase"])],
                ["Net profit", fmt(s["bulk"]["net"])],
            ]},
            {"title": "Carriage", "headers": ["Item", "Amount"], "rows": [
                ["Freight income", fmt(s["carriage"]["freight"])],
                ["Trip expenses", fmt(s["carriage"]["expenses"])],
                ["Net profit", fmt(s["carriage"]["net"])],
            ]},
            {"title": "Oil Agencies", "headers": ["Item", "Amount"], "rows": [
                ["Sale", fmt(s["agency"]["sale"])],
                ["Purchases (vendor)", fmt(s["agency"]["purchases"])],
                ["Net profit (sales)", fmt(s["agency"]["net"])],
            ]},
            {"title": "Head Office (overhead)", "headers": ["Item", "Amount"], "rows": [
                ["Cash received from pumps", fmt(s["head_office"]["cash_received"])],
                ["Head office expenses", fmt(s["head_office"]["expenses"])],
                ["Vendor payments", fmt(s["head_office"]["vendor_payments"])],
            ]},
            {"title": "Combined", "headers": ["Item", "Amount"], "rows": [
                ["Combined Profit / Loss", fmt(s["combined_profit"])],
            ]},
        ]
        title = f"Business-wise Profit / Loss ({_range_label(filters)})"
        return export_response(export, "profit_loss", title, blocks)

    return render_template("reports/profit_loss.html", s=summary, filters=filters)


_DASHBOARDS = {
    "pump": ("Petrol Pump Retail", "pump_dashboard"),
    "bulk": ("Bulk Sale", "bulk_dashboard"),
    "carriage": ("Carriage / Transport", "carriage_dashboard"),
    "agency": ("Oil Agency", "agency_dashboard"),
    "head-office": ("Head Office", "head_office_dashboard"),
}


@reports_bp.route("/dashboard/<unit>")
@role_required(*REPORT_ROLES)
def business_dashboard(unit):
    """Per-business-unit dashboard (BRD §14.2)."""
    from flask import abort
    from app.reports import dashboards

    if unit not in _DASHBOARDS:
        abort(404)
    title, func_name = _DASHBOARDS[unit]
    date_from, date_to, filters = _date_range()
    data = getattr(dashboards, func_name)(date_from, date_to)
    return render_template(
        f"reports/dashboards/{unit.replace('-', '_')}.html",
        d=data, filters=filters, title=title, unit=unit,
        dashboards_nav=_DASHBOARDS,
    )


@reports_bp.route("/trial-balance")
@role_required(*REPORT_ROLES)
def trial_balance():
    """Trial balance from auto-posted journal entries (debits must equal credits)."""
    from app.accounting import posting

    date_from, date_to, filters = _date_range()
    tb = posting.trial_balance(date_from, date_to)

    export = _export_format()
    if export:
        rows = [[
            r["account"].code, r["account"].name, r["account"].account_type,
            fmt(r["debit"]), fmt(r["credit"]),
        ] for r in tb["rows"]]
        rows.append(["", "TOTAL", "", fmt(tb["total_debit"]), fmt(tb["total_credit"])])
        blocks = [{
            "headers": ["Code", "Account", "Type", "Debit", "Credit"],
            "rows": rows,
        }]
        title = f"Trial Balance ({_range_label(filters)})"
        return export_response(export, "trial_balance", title, blocks)

    return render_template("reports/trial_balance.html", tb=tb, filters=filters)


@reports_bp.route("/cash-book")
@role_required(*REPORT_ROLES)
def cash_book():
    """Cash / Bank book — per-account statement with running balance (§13.5)."""
    from app.core.models import CashBankAccount
    from app.reports import ledgers

    accounts = CashBankAccount.query.filter_by(is_active=True).order_by(CashBankAccount.name).all()
    account_id = request.args.get("account_id", type=int)
    account = db.session.get(CashBankAccount, account_id) if account_id else None
    date_from, date_to, filters = _date_range()

    data = ledgers.cash_account_ledger(account, date_from, date_to) if account else None

    export = _export_format()
    if export and data:
        rows = [[
            r["date"].isoformat(), r["kind"], r["ref"],
            fmt(r["in"]) if r["in"] else "", fmt(r["out"]) if r["out"] else "",
            fmt(r["balance"]),
        ] for r in data["rows"]]
        blocks = [{
            "title": f"{account.name} — opening {fmt(data['opening'])}",
            "headers": ["Date", "Type", "Reference", "In", "Out", "Balance"],
            "rows": rows + [["", "Closing balance", "", fmt(data["total_in"]),
                             fmt(data["total_out"]), fmt(data["closing"])]],
        }]
        return export_response(export, "cash_book", f"Cash Book — {account.name}", blocks)

    return render_template(
        "reports/cash_book.html", accounts=accounts, account=account,
        data=data, filters=filters,
    )


@reports_bp.route("/general-ledger")
@role_required(*REPORT_ROLES)
def general_ledger():
    """General ledger — per chart-account journal detail (drill-down)."""
    from app.accounting.models import ChartOfAccount
    from app.reports import ledgers

    accounts = ChartOfAccount.query.filter_by(is_active=True).order_by(ChartOfAccount.code).all()
    # Accept either ?code= (from the trial balance) or ?account_id=.
    code = request.args.get("code", "").strip()
    account_id = request.args.get("account_id", type=int)
    if code and not account_id:
        acc = ChartOfAccount.query.filter_by(code=code).first()
        account_id = acc.id if acc else None
    account = db.session.get(ChartOfAccount, account_id) if account_id else None
    date_from, date_to, filters = _date_range()

    data = ledgers.general_ledger(account, date_from, date_to) if account else None

    export = _export_format()
    if export and data:
        rows = [[
            r["date"].isoformat(), r["voucher"], r["description"],
            fmt(r["debit"]) if r["debit"] else "", fmt(r["credit"]) if r["credit"] else "",
            fmt(r["balance"]),
        ] for r in data["rows"]]
        blocks = [{
            "title": f"{account.code} {account.name} — opening {fmt(data['opening'])}",
            "headers": ["Date", "Voucher", "Description", "Debit", "Credit", "Balance"],
            "rows": rows + [["", "", "Totals", fmt(data["total_debit"]),
                             fmt(data["total_credit"]), fmt(data["closing"])]],
        }]
        return export_response(export, "general_ledger", f"General Ledger — {account.code}", blocks)

    return render_template(
        "reports/general_ledger.html", accounts=accounts, account=account,
        data=data, filters=filters,
    )


@reports_bp.route("/cash-position")
@role_required(*REPORT_ROLES)
def cash_position():
    """Cash & bank balances across all accounts (with Excel/PDF export)."""
    cash = services.cash_position()

    export = _export_format()
    if export:
        rows = [
            [
                a.name,
                a.account_type,
                a.business_unit.name if a.business_unit else "Global",
                fmt(a.current_balance),
            ]
            for a in cash["accounts"]
        ]
        rows.append(["Total", "", "", fmt(cash["total"])])
        blocks = [{
            "headers": ["Account", "Type", "Business Unit", "Current Balance"],
            "rows": rows,
        }]
        return export_response(export, "cash_position", "Cash & Bank Position", blocks)

    return render_template("reports/cash_position.html", cash=cash)
