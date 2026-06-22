"""Agency reports (BRD §13.4): product-wise (LDO/Kerosene) and customer-wise
sale, plus vendor-wise purchases with receivable/payable outstanding.

Python aggregation over active records (small volumes).
"""
from decimal import Decimal

from app.agency.models import AgencyPurchase, AgencySale

_ZERO = Decimal("0")


def _dec(v):
    return Decimal(str(v or 0))


def _scoped_sales(date_from, date_to):
    q = AgencySale.query.filter(AgencySale.is_active.is_(True))
    if date_from:
        q = q.filter(AgencySale.sale_date >= date_from)
    if date_to:
        q = q.filter(AgencySale.sale_date <= date_to)
    return q.all()


def _scoped_purchases(date_from, date_to):
    q = AgencyPurchase.query.filter(AgencyPurchase.is_active.is_(True))
    if date_from:
        q = q.filter(AgencyPurchase.purchase_date >= date_from)
    if date_to:
        q = q.filter(AgencyPurchase.purchase_date <= date_to)
    return q.all()


_SALE_METRICS = ("deals", "qty", "sale", "carriage", "net", "outstanding")


def _group_sales(sales, label_fn):
    groups = {}
    for s in sales:
        label = label_fn(s)
        g = groups.setdefault(label, {
            "label": label, "deals": 0, "qty": _ZERO, "sale": _ZERO,
            "carriage": _ZERO, "net": _ZERO, "outstanding": _ZERO,
        })
        g["deals"] += 1
        g["qty"] += _dec(s.quantity)
        g["sale"] += _dec(s.total_sale_amount)
        g["carriage"] += _dec(s.carriage_cost)
        g["net"] += _dec(s.net_profit)
        if s.payment_status != "Paid":
            g["outstanding"] += _dec(s.total_sale_amount)
    rows = sorted(groups.values(), key=lambda r: r["sale"], reverse=True)
    totals = {k: sum((r[k] for r in rows), _ZERO if k != "deals" else 0)
              for k in _SALE_METRICS}
    return {"rows": rows, "totals": totals}


def _group_purchases(purchases, label_fn):
    groups = {}
    for p in purchases:
        label = label_fn(p)
        g = groups.setdefault(label, {
            "label": label, "deals": 0, "qty": _ZERO, "purchase": _ZERO, "payable": _ZERO,
        })
        g["deals"] += 1
        g["qty"] += _dec(p.quantity)
        g["purchase"] += _dec(p.total_amount)
        if p.payment_status != "Paid":
            g["payable"] += _dec(p.total_amount)
    rows = sorted(groups.values(), key=lambda r: r["purchase"], reverse=True)
    totals = {k: sum((r[k] for r in rows), _ZERO if k != "deals" else 0)
              for k in ("deals", "qty", "purchase", "payable")}
    return {"rows": rows, "totals": totals}


def godown_stock(date_from=None, date_to=None):
    """Agency godown on-hand per product (BRD §9.5, optional stock flow).

    On-hand = Σ purchases received into godown (to_godown) − Σ sales drawn from
    godown (from_godown), over the scoped period. Only products that have any
    godown movement appear. Returns rows of {product, received, sold, on_hand}.
    """
    purchases = [p for p in _scoped_purchases(date_from, date_to) if p.to_godown]
    sales = [s for s in _scoped_sales(date_from, date_to) if s.from_godown]
    groups = {}

    def g(product):
        label = product.name if product else "—"
        return groups.setdefault(label, {"product": label, "received": _ZERO,
                                         "sold": _ZERO, "on_hand": _ZERO})

    for p in purchases:
        g(p.product)["received"] += _dec(p.quantity)
    for s in sales:
        g(s.product)["sold"] += _dec(s.quantity)
    for r in groups.values():
        r["on_hand"] = r["received"] - r["sold"]
    rows = sorted(groups.values(), key=lambda r: r["product"])
    totals = {k: sum((r[k] for r in rows), _ZERO) for k in ("received", "sold", "on_hand")}
    return {"rows": rows, "totals": totals}


def agency_reports(date_from=None, date_to=None):
    sales = _scoped_sales(date_from, date_to)
    purchases = _scoped_purchases(date_from, date_to)
    return {
        "products": _group_sales(sales, lambda s: s.product.name if s.product else "—"),
        "customers": _group_sales(sales, lambda s: s.customer.name if s.customer else "—"),
        "delivery": _group_sales(sales, lambda s: s.delivery_method or "—"),
        "vendors": _group_purchases(purchases, lambda p: p.vendor.name if p.vendor else "—"),
        "godown": godown_stock(date_from, date_to),
    }
