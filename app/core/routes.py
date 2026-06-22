"""Core routes: the homepage with the cross-business executive overview."""
from flask import Blueprint, render_template

from app.auth.access import can_access_module
from app.reports import services as report_services

core_bp = Blueprint("core", __name__)


@core_bp.route("/")
def home():
    """Render the homepage.

    Users whose role can see the reports module also get the executive
    overview (group KPIs, business breakdown and trend charts) right on the
    home page; everyone else just gets their module launcher.
    """
    overview = None
    if can_access_module("reports"):
        summary = report_services.combined_summary()
        cash = report_services.cash_position()
        balances = report_services.receivables_payables()
        overview = {
            "s": summary,
            "cash_total": cash["total"],
            "receivables": balances["receivables"],
            "payables": balances["payables"],
            "trend": report_services.monthly_trend(),
        }
    return render_template("core/home.html", overview=overview)
