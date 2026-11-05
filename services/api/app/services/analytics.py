"""Dashboard metrics, all computed from database records.

Metric definitions (period = [date_from, date_to], inclusive, by business date):

orders_received        COUNT(orders) with order_date in period (any status)
orders_approved        COUNT(orders) with approved_at in period
orders_dispatched      COUNT(DISTINCT dispatches.order_id) with dispatched_at in period
gross_sales            SUM(sales_invoices.gross_amount) with invoice_date in period, status != void
                       (quantity x unit price, before discounts, excluding tax)
net_sales              gross_sales - SUM(invoice discount_amount) - SUM(credit notes issued to
                       retailers, note_date in period, status issued/applied)
                       + SUM(debit notes issued, same filter). Excludes tax. Not profit.
avg_processing_hours   AVG(approved_at - created_at) for orders approved in period
order_correction_rate  orders created from the inbox in period with >= 1 line whose provenance
                       is 'corrected' / all orders created from the inbox in period
inventory_value        SUM(on_hand x product.cost_price) now (products without cost excluded and counted)
receivables            outstanding (see receivables service) now, with ageing buckets
open_cases             COUNT(reconciliation cases in open|investigating|awaiting_evidence)
potential_discrepancy  SUM(ABS(difference)) of open cases - UNVERIFIED, not a loss figure
resolved_discrepancy   SUM(resolved_value) of cases resolved/closed with resolved_at in period
scheme_benefits        SUM(scheme_applications.benefit_amount) on orders approved in period
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.db import utcnow
from app.models import (
    CreditNote,
    Dispatch,
    ExceptionItem,
    InventoryMovement,
    Order,
    OrderLine,
    OrderSource,
    Payment,
    PickList,
    Product,
    PurchaseInvoice,
    ReconciliationCase,
    ReconciliationMatch,
    Retailer,
    ReturnRecord,
    SalesInvoice,
    SalesInvoiceLine,
    SchemeApplication,
    SchemeClaim,
)
from app.money import ZERO, money
from app.services import inventory, receivables

OPEN_CASE = ("open", "investigating", "awaiting_evidence")


def _dt(d: date, end=False) -> datetime:
    from datetime import UTC

    return datetime.combine(d, time.max if end else time.min, tzinfo=UTC)


def _sum(db, col, *conds) -> Decimal:
    return money(db.scalar(select(func.sum(col)).where(*conds)) or 0)


def sales_totals(db, org_id, d0: date, d1: date) -> dict:
    inv = (SalesInvoice.organization_id == org_id, SalesInvoice.invoice_date >= d0, SalesInvoice.invoice_date <= d1, SalesInvoice.status != "void")
    gross = _sum(db, SalesInvoice.gross_amount, *inv)
    disc = _sum(db, SalesInvoice.discount_amount, *inv)
    tax = _sum(db, SalesInvoice.tax_amount, *inv)
    note = (CreditNote.organization_id == org_id, CreditNote.direction == "issued", CreditNote.status.in_(("issued", "applied")), CreditNote.note_date >= d0, CreditNote.note_date <= d1)
    credits = _sum(db, CreditNote.amount, *note, CreditNote.note_type == "credit")
    debits = _sum(db, CreditNote.amount, *note, CreditNote.note_type == "debit")
    return {
        "gross_sales": gross,
        "discounts": disc,
        "credit_notes": credits,
        "debit_notes": debits,
        "net_sales": money(gross - disc - credits + debits),
        "tax_invoiced": tax,
        "invoice_count": db.scalar(select(func.count()).select_from(SalesInvoice).where(*inv)) or 0,
    }


def order_metrics(db, org_id, d0: date, d1: date) -> dict:
    received = db.scalar(select(func.count()).select_from(Order).where(Order.organization_id == org_id, Order.order_date >= d0, Order.order_date <= d1)) or 0
    approved_rows = db.execute(select(Order.created_at, Order.approved_at).where(Order.organization_id == org_id, Order.approved_at >= _dt(d0), Order.approved_at <= _dt(d1, True))).all()
    dispatched = db.scalar(select(func.count(func.distinct(Dispatch.order_id))).where(Dispatch.organization_id == org_id, Dispatch.dispatched_at >= _dt(d0), Dispatch.dispatched_at <= _dt(d1, True))) or 0
    hours = sorted(max(0.0, (b - a).total_seconds() / 3600) for a, b in approved_rows if a and b)
    inbox_orders = db.scalars(select(Order.id).where(Order.organization_id == org_id, Order.source_id.is_not(None), Order.order_date >= d0, Order.order_date <= d1)).all()
    corrected = 0
    if inbox_orders:
        corrected = db.scalar(select(func.count(func.distinct(OrderLine.order_id))).where(OrderLine.order_id.in_(inbox_orders), OrderLine.provenance == "corrected")) or 0
    by_status = dict(db.execute(select(Order.status, func.count()).where(Order.organization_id == org_id, Order.order_date >= d0, Order.order_date <= d1).group_by(Order.status)).all())
    return {
        "orders_received": received,
        "orders_approved": len(approved_rows),
        "orders_dispatched": dispatched,
        "avg_processing_hours": round(sum(hours) / len(hours), 2) if hours else None,
        "median_processing_hours": round(hours[len(hours) // 2], 2) if hours else None,
        "inbox_orders": len(inbox_orders),
        "order_correction_rate": round(corrected / len(inbox_orders), 4) if inbox_orders else None,
        "by_status": by_status,
    }


def inventory_metrics(db, org) -> dict:
    products = {p.id: p for p in db.scalars(select(Product).where(Product.organization_id == org.id)).all()}
    bals = inventory.balances(db, org.id)
    value = ZERO
    missing_cost = 0
    per_product: dict = defaultdict(lambda: Decimal(0))
    for (wh, pid), b in bals.items():
        p = products.get(pid)
        if not p:
            continue
        per_product[pid] += b.available
        if p.cost_price is None:
            if b.on_hand:
                missing_cost += 1
            continue
        value += money(b.on_hand * p.cost_price)
    active = [p for p in products.values() if p.is_active]
    out_of_stock = sum(1 for p in active if per_product.get(p.id, 0) <= 0)
    low = sum(1 for p in active if 0 < per_product.get(p.id, 0) <= 10)
    days = int(org.setting("expiry_alert_days"))
    from app.models import Batch

    rows = db.execute(
        select(InventoryMovement.batch_id, func.sum(InventoryMovement.quantity)).where(InventoryMovement.organization_id == org.id, InventoryMovement.bucket == "on_hand", InventoryMovement.batch_id.is_not(None)).group_by(InventoryMovement.batch_id)
    ).all()
    expiring = expired = 0
    today = date.today()
    for bid, q in rows:
        if (q or 0) <= 0:
            continue
        b = db.get(Batch, bid)
        if b and b.expiry_date:
            dd = (b.expiry_date - today).days
            if dd < 0:
                expired += 1
            elif dd <= days:
                expiring += 1
    return {"inventory_value": money(value), "products_without_cost": missing_cost, "out_of_stock_skus": out_of_stock, "low_stock_skus": low, "expiring_batches": expiring, "expired_batches": expired, "active_skus": len(active)}


def recon_metrics(db, org_id, d0: date, d1: date) -> dict:
    open_rows = db.execute(select(ReconciliationCase.category, ReconciliationCase.difference).where(ReconciliationCase.organization_id == org_id, ReconciliationCase.status.in_(OPEN_CASE))).all()
    resolved = _sum(db, ReconciliationCase.resolved_value, ReconciliationCase.organization_id == org_id, ReconciliationCase.status.in_(("resolved", "closed")), ReconciliationCase.resolved_at >= _dt(d0), ReconciliationCase.resolved_at <= _dt(d1, True))
    by_cat: dict = defaultdict(lambda: {"count": 0, "difference": Decimal(0)})
    for cat, diff in open_rows:
        by_cat[cat]["count"] += 1
        by_cat[cat]["difference"] += abs(diff or 0)
    outcomes = dict(db.execute(select(ReconciliationCase.status, func.count()).where(ReconciliationCase.organization_id == org_id).group_by(ReconciliationCase.status)).all())
    return {
        "open_cases": len(open_rows),
        "potential_discrepancy_value": money(sum((abs(d or 0) for _, d in open_rows), Decimal(0))),
        "potential_discrepancy_note": "Unverified: sum of absolute differences on open cases. Not a confirmed loss.",
        "resolved_discrepancy_value": resolved,
        "open_by_category": {k: {"count": v["count"], "difference": str(money(v["difference"]))} for k, v in by_cat.items()},
        "cases_by_status": outcomes,
    }


def scheme_metrics(db, org_id, d0: date, d1: date) -> dict:
    total = _sum(db, SchemeApplication.benefit_amount, SchemeApplication.organization_id == org_id, SchemeApplication.order_id.in_(select(Order.id).where(Order.organization_id == org_id, Order.approved_at >= _dt(d0), Order.approved_at <= _dt(d1, True))))
    claims = db.execute(select(SchemeClaim.status, func.count(), func.sum(SchemeClaim.expected_amount)).where(SchemeClaim.organization_id == org_id).group_by(SchemeClaim.status)).all()
    return {"scheme_benefits": total, "claims": {s: {"count": n, "expected": str(money(a or 0))} for s, n, a in claims}}


def owner_dashboard(db, org, d0: date, d1: date, finance: bool) -> dict:
    out = {"period": {"from": d0.isoformat(), "to": d1.isoformat()}, "orders": order_metrics(db, org.id, d0, d1), "inventory": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in inventory_metrics(db, org).items()}}
    if finance:
        out["sales"] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in sales_totals(db, org.id, d0, d1).items()}
        ag = receivables.ageing(db, org, date.today())
        ag.pop("by_retailer")
        out["receivables"] = ag
        out["reconciliation"] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in recon_metrics(db, org.id, d0, d1).items()}
        out["schemes"] = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in scheme_metrics(db, org.id, d0, d1).items()}
    else:
        out["inventory"].pop("inventory_value", None)
    out["exceptions"] = exception_summary(db, org.id, finance)
    out["is_demo"] = org.is_demo
    out["generated_at"] = utcnow().isoformat()
    return out


def exception_summary(db, org_id, finance: bool) -> dict:
    from app.routers.exceptions import FINANCE_TYPES

    rows = db.execute(select(ExceptionItem.exception_type, ExceptionItem.severity, ExceptionItem.created_at).where(ExceptionItem.organization_id == org_id, ExceptionItem.status.in_(("open", "in_progress")))).all()
    if not finance:
        rows = [r for r in rows if r[0] not in FINANCE_TYPES]
    now = utcnow()
    ageing = {"0-1d": 0, "2-7d": 0, "8-30d": 0, "30d+": 0}
    for _, _, created in rows:
        d = (now - created).days
        ageing["0-1d" if d <= 1 else "2-7d" if d <= 7 else "8-30d" if d <= 30 else "30d+"] += 1
    by_sev: dict = defaultdict(int)
    by_type: dict = defaultdict(int)
    for t, s, _ in rows:
        by_sev[s] += 1
        by_type[t] += 1
    return {"open": len(rows), "by_severity": dict(by_sev), "by_type": dict(by_type), "ageing": ageing}


def operations_dashboard(db, org) -> dict:
    oid = org.id
    pending = db.scalars(select(Order).where(Order.organization_id == oid, Order.status.in_(("draft", "validated")))).all()
    blocked_stock = sum(1 for o in pending if any(i["code"] == "INSUFFICIENT_AVAILABLE_STOCK" for i in (o.validation or {}).get("issues", [])))
    blocked_credit = sum(1 for o in pending if any(i["code"] == "CREDIT_LIMIT_EXCEEDED" for i in (o.validation or {}).get("issues", [])))
    count = lambda m, *c: db.scalar(select(func.count()).select_from(m).where(m.organization_id == oid, *c)) or 0  # noqa: E731
    return {
        "pending_orders": len(pending),
        "orders_awaiting_approval": sum(1 for o in pending if o.status == "validated"),
        "orders_needing_fixes": sum(1 for o in pending if o.status == "draft"),
        "inbox_awaiting_review": count(OrderSource, OrderSource.status.in_(("needs_review", "failed", "received", "extracting"))),
        "blocked_by_stock": blocked_stock,
        "blocked_by_credit": blocked_credit,
        "pick_queue": count(PickList, PickList.status.in_(("open", "picking"))),
        "ready_to_dispatch": count(PickList, PickList.status == "picked"),
        "in_transit": count(Dispatch, Dispatch.status == "dispatched"),
        "partially_fulfilled_open": count(Order, Order.status == "partially_fulfilled", Order.fulfilment_closed_at.is_(None)),
        "returns_awaiting_receipt": count(ReturnRecord, ReturnRecord.status == "pending"),
        "returns_awaiting_credit": count(ReturnRecord, ReturnRecord.status == "received", ReturnRecord.return_type == "customer"),
        "exceptions": exception_summary(db, oid, finance=False),
    }


def accountant_dashboard(db, org) -> dict:
    oid = org.id
    count = lambda m, *c: db.scalar(select(func.count()).select_from(m).where(m.organization_id == oid, *c)) or 0  # noqa: E731
    pays = db.scalars(select(Payment).where(Payment.organization_id == oid, Payment.status == "recorded")).all()
    unalloc = [(p, receivables.payment_unallocated(db, p)) for p in pays]
    unalloc = [(p, u) for p, u in unalloc if u > 0]
    verified_unmatched = 0
    for pi in db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.organization_id == oid, PurchaseInvoice.status == "verified")).all():
        if not db.scalars(select(ReconciliationMatch).where(ReconciliationMatch.left_type == "purchase_invoice", ReconciliationMatch.left_id == str(pi.id), ReconciliationMatch.status == "active")).first():
            verified_unmatched += 1
    ag = receivables.ageing(db, org, date.today())
    ag.pop("by_retailer")
    return {
        "invoices_awaiting_verification": count(PurchaseInvoice, PurchaseInvoice.status == "needs_review"),
        "unmatched_purchase_invoices": verified_unmatched,
        "suggested_matches": count(ReconciliationMatch, ReconciliationMatch.status == "suggested"),
        "open_reconciliation_cases": count(ReconciliationCase, ReconciliationCase.status.in_(OPEN_CASE)),
        "unallocated_payments": len(unalloc),
        "unallocated_amount": str(money(sum((u for _, u in unalloc), Decimal(0)))),
        "credit_notes_pending": count(CreditNote, CreditNote.status == "draft"),
        "receivables": ag,
    }


def trends(db, org_id, d0: date, d1: date, finance: bool) -> dict:
    days = (d1 - d0).days + 1
    series = {(d0 + timedelta(days=i)).isoformat(): {"orders": 0, "net_sales": Decimal(0)} for i in range(days)}
    for od, n in db.execute(select(Order.order_date, func.count()).where(Order.organization_id == org_id, Order.order_date >= d0, Order.order_date <= d1).group_by(Order.order_date)).all():
        series[od.isoformat()]["orders"] = n
    if finance:
        for idate, g, dsc in db.execute(select(SalesInvoice.invoice_date, func.sum(SalesInvoice.gross_amount), func.sum(SalesInvoice.discount_amount)).where(SalesInvoice.organization_id == org_id, SalesInvoice.invoice_date >= d0, SalesInvoice.invoice_date <= d1, SalesInvoice.status != "void").group_by(SalesInvoice.invoice_date)).all():
            series[idate.isoformat()]["net_sales"] = money((g or 0) - (dsc or 0))
    out = {"daily": [{"date": k, "orders": v["orders"], **({"net_sales": str(v["net_sales"])} if finance else {})} for k, v in series.items()]}
    # top products / retailers by invoiced value (net of discounts, excl. tax)
    prod = db.execute(
        select(SalesInvoiceLine.product_id, func.sum(SalesInvoiceLine.quantity), func.sum(SalesInvoiceLine.gross_amount), func.sum(SalesInvoiceLine.discount_amount))
        .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.sales_invoice_id)
        .where(SalesInvoice.organization_id == org_id, SalesInvoice.invoice_date >= d0, SalesInvoice.invoice_date <= d1, SalesInvoice.status != "void")
        .group_by(SalesInvoiceLine.product_id)
    ).all()
    pmap = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([r[0] for r in prod]))).all()} if prod else {}
    top_p = sorted(prod, key=lambda r: -((r[2] or 0) - (r[3] or 0)))[:10]
    out["top_products"] = [{"sku": pmap[r[0]].sku, "name": pmap[r[0]].name, "quantity": str(r[1]), **({"net_value": str(money((r[2] or 0) - (r[3] or 0)))} if finance else {})} for r in top_p]
    ret = db.execute(
        select(SalesInvoice.retailer_id, func.count(), func.sum(SalesInvoice.gross_amount), func.sum(SalesInvoice.discount_amount))
        .where(SalesInvoice.organization_id == org_id, SalesInvoice.invoice_date >= d0, SalesInvoice.invoice_date <= d1, SalesInvoice.status != "void")
        .group_by(SalesInvoice.retailer_id)
    ).all()
    rmap = {r.id: r for r in db.scalars(select(Retailer).where(Retailer.id.in_([r[0] for r in ret]))).all()} if ret else {}
    top_r = sorted(ret, key=lambda r: -((r[2] or 0) - (r[3] or 0)))[:10]
    out["top_retailers"] = [{"code": rmap[r[0]].code, "name": rmap[r[0]].name, "invoices": r[1], **({"net_value": str(money((r[2] or 0) - (r[3] or 0)))} if finance else {})} for r in top_r]
    mv = db.execute(
        select(InventoryMovement.movement_type, func.sum(InventoryMovement.quantity), func.count()).where(InventoryMovement.organization_id == org_id, InventoryMovement.created_at >= _dt(d0), InventoryMovement.created_at <= _dt(d1, True)).group_by(InventoryMovement.movement_type)
    ).all()
    out["inventory_movements"] = [{"movement_type": t, "quantity": str(q), "count": n} for t, q, n in mv]
    return out
