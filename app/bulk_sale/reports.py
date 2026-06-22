"""Bulk sale reports (BRD §13.2): customer-wise, product-wise, pump-wise.

Aggregates active bulk sales in Python (small volumes). Outstanding receivable
is the sale value of deals not marked Paid. PSO payable (bulk) is the total
purchase cost — the amount owed to PSO for the bulk fuel.
"""
from decimal import Decimal

from app.bulk_sale.models import BulkSale

_ZERO = Decimal("0")


def _dec(v):
    return Decimal(str(v or 0))


def _scoped(date_from, date_to):
    q = BulkSale.query.filter(BulkSale.is_active.is_(True))
    if date_from:
        q = q.filter(BulkSale.sale_date >= date_from)
    if date_to:
        q = q.filter(BulkSale.sale_date <= date_to)
    return q.all()


def _group(sales, label_fn, with_qty=False):
    groups = {}
    for s in sales:
        label = label_fn(s)
        g = groups.setdefault(label, {
            "label": label, "deals": 0, "qty": _ZERO, "sale": _ZERO,
            "purchase": _ZERO, "net": _ZERO, "outstanding": _ZERO,
        })
        g["deals"] += 1
        g["qty"] += _dec(s.quantity_liters)
        g["sale"] += _dec(s.total_sale_amount)
        g["purchase"] += _dec(s.total_purchase_amount)
        g["net"] += _dec(s.net_profit)
        if s.payment_status != "Paid":
            g["outstanding"] += _dec(s.total_sale_amount)
    rows = sorted(groups.values(), key=lambda r: r["sale"], reverse=True)
    totals = {
        "deals": sum(r["deals"] for r in rows),
        "qty": sum((r["qty"] for r in rows), _ZERO),
        "sale": sum((r["sale"] for r in rows), _ZERO),
        "purchase": sum((r["purchase"] for r in rows), _ZERO),
        "net": sum((r["net"] for r in rows), _ZERO),
        "outstanding": sum((r["outstanding"] for r in rows), _ZERO),
    }
    return {"rows": rows, "totals": totals}


def _cust(s):
    return s.customer.name if s.customer else "—"


def _prod(s):
    return s.product.name if s.product else "—"


def _pump(s):
    return s.petrol_pump.name if s.petrol_pump else "—"


def bulk_reports(date_from=None, date_to=None):
    sales = _scoped(date_from, date_to)
    return {
        "customers": _group(sales, _cust),
        "products": _group(sales, _prod),
        "pumps": _group(sales, _pump),
    }
