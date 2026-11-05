"""Receivables: outstanding balances, credit exposure, ageing and payment allocation.

Definitions (also in docs/ARCHITECTURE.md#metrics):
- invoice outstanding = total - active payment allocations - applied credit notes
- retailer outstanding = SUM(outstanding of non-void sales invoices)
- open order value = net_total of approved/picking/partially_fulfilled orders minus
  totals already invoiced for those orders
- credit exposure = retailer outstanding + open order value (+ the order being checked)
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.errors import AppError, conflict
from app.models import CreditNote, Order, Payment, PaymentAllocation, Retailer, SalesInvoice
from app.money import ZERO, money

OPEN_ORDER_STATUSES = ("approved", "picking", "partially_fulfilled")


def allocated_by_invoice(db, invoice_ids) -> dict:
    if not invoice_ids:
        return {}
    rows = db.execute(
        select(PaymentAllocation.sales_invoice_id, func.sum(PaymentAllocation.amount))
        .where(PaymentAllocation.sales_invoice_id.in_(list(invoice_ids)), PaymentAllocation.reversed_at.is_(None))
        .group_by(PaymentAllocation.sales_invoice_id)
    ).all()
    return {i: money(a) for i, a in rows}


def credited_by_invoice(db, invoice_ids) -> dict:
    if not invoice_ids:
        return {}
    rows = db.execute(
        select(CreditNote.sales_invoice_id, func.sum(CreditNote.amount))
        .where(
            CreditNote.sales_invoice_id.in_(list(invoice_ids)),
            CreditNote.status == "applied",
            CreditNote.note_type == "credit",
            CreditNote.direction == "issued",
        )
        .group_by(CreditNote.sales_invoice_id)
    ).all()
    return {i: money(a) for i, a in rows}


def outstanding_map(db, invoices: list[SalesInvoice]) -> dict:
    ids = [i.id for i in invoices]
    alloc = allocated_by_invoice(db, ids)
    cred = credited_by_invoice(db, ids)
    return {
        i.id: (ZERO if i.status == "void" else money(i.total - alloc.get(i.id, ZERO) - cred.get(i.id, ZERO)))
        for i in invoices
    }


def invoice_outstanding(db, inv: SalesInvoice) -> Decimal:
    return outstanding_map(db, [inv])[inv.id]


def refresh_invoice_status(db, inv: SalesInvoice) -> None:
    if inv.status == "void":
        return
    out = invoice_outstanding(db, inv)
    inv.status = "paid" if out <= 0 else ("partially_paid" if out < inv.total else "open")


def payment_unallocated(db, payment: Payment) -> Decimal:
    used = db.scalar(
        select(func.sum(PaymentAllocation.amount)).where(
            PaymentAllocation.payment_id == payment.id, PaymentAllocation.reversed_at.is_(None)
        )
    )
    return money(payment.amount - money(used or 0)) if payment.status != "void" else ZERO


def retailer_outstanding(db, org_id, retailer_id) -> Decimal:
    invs = db.scalars(
        select(SalesInvoice).where(
            SalesInvoice.organization_id == org_id,
            SalesInvoice.retailer_id == retailer_id,
            SalesInvoice.status.in_(("open", "partially_paid")),
        )
    ).all()
    return money(sum(outstanding_map(db, invs).values(), ZERO))


def open_order_value(db, org_id, retailer_id, exclude_order_id=None) -> Decimal:
    stmt = select(Order).where(
        Order.organization_id == org_id, Order.retailer_id == retailer_id, Order.status.in_(OPEN_ORDER_STATUSES)
    )
    if exclude_order_id:
        stmt = stmt.where(Order.id != exclude_order_id)
    orders = db.scalars(stmt).all()
    if not orders:
        return ZERO
    invoiced = dict(
        db.execute(
            select(SalesInvoice.order_id, func.sum(SalesInvoice.total))
            .where(SalesInvoice.order_id.in_([o.id for o in orders]), SalesInvoice.status != "void")
            .group_by(SalesInvoice.order_id)
        ).all()
    )
    return money(sum((max(ZERO, o.net_total - money(invoiced.get(o.id) or 0)) for o in orders), ZERO))


def credit_exposure(db, org, retailer: Retailer, additional: Decimal = ZERO, exclude_order_id=None) -> dict:
    outstanding = retailer_outstanding(db, org.id, retailer.id)
    open_orders = open_order_value(db, org.id, retailer.id, exclude_order_id)
    exposure = money(outstanding + open_orders + money(additional))
    limit = retailer.credit_limit
    return {
        "credit_limit": str(limit) if limit is not None else None,
        "outstanding": str(outstanding),
        "open_orders": str(open_orders),
        "this_order": str(money(additional)),
        "exposure": str(exposure),
        "available": str(money(limit - exposure)) if limit is not None else None,
        "exceeded": bool(limit is not None and exposure > limit),
    }


def bucket_labels(buckets: list[int]) -> list[str]:
    labels = ["current"]
    prev = 0
    for b in buckets:
        labels.append(f"{prev + 1}-{b}")
        prev = b
    labels.append(f"{prev}+")
    return labels


def bucket_for(days_past_due: int, buckets: list[int]) -> str:
    labels = bucket_labels(buckets)
    if days_past_due <= 0:
        return labels[0]
    for i, b in enumerate(buckets):
        if days_past_due <= b:
            return labels[i + 1]
    return labels[-1]


def ageing(db, org, as_of: date, retailer_id=None) -> dict:
    """Outstanding by days past due date. Buckets configurable per organisation."""
    buckets = list(org.setting("ageing_buckets"))
    stmt = select(SalesInvoice).where(
        SalesInvoice.organization_id == org.id, SalesInvoice.status.in_(("open", "partially_paid"))
    )
    if retailer_id:
        stmt = stmt.where(SalesInvoice.retailer_id == retailer_id)
    invs = db.scalars(stmt).all()
    outs = outstanding_map(db, invs)
    labels = bucket_labels(buckets)
    totals = {lbl: ZERO for lbl in labels}
    by_retailer: dict = defaultdict(lambda: {lbl: ZERO for lbl in labels})
    for inv in invs:
        o = outs[inv.id]
        if o <= 0:
            continue
        lbl = bucket_for((as_of - inv.due_date).days, buckets)
        totals[lbl] += o
        by_retailer[inv.retailer_id][lbl] += o
    return {
        "as_of": as_of.isoformat(),
        "buckets": labels,
        "totals": {k: str(money(v)) for k, v in totals.items()},
        "total_outstanding": str(money(sum(totals.values(), ZERO))),
        "by_retailer": {str(k): {lbl: str(money(v)) for lbl, v in d.items()} for k, d in by_retailer.items()},
    }


def allocate(db, org_id, payment: Payment, invoice: SalesInvoice, amount, actor_id) -> PaymentAllocation:
    """Allocate part of a payment to an invoice. Caller must have locked both rows
    (SELECT ... FOR UPDATE) inside the transaction."""
    amt = money(amount)
    if amt <= 0:
        raise AppError("INVALID_AMOUNT", "Allocation amount must be positive.", 422)
    if payment.status == "void":
        raise conflict("PAYMENT_VOID", "Cannot allocate a void payment.")
    if invoice.status == "void":
        raise conflict("INVOICE_VOID", "Cannot allocate to a void invoice.")
    if payment.retailer_id != invoice.retailer_id:
        raise AppError("RETAILER_MISMATCH", "Payment and invoice belong to different retailers.", 422)
    if payment.currency != invoice.currency:
        raise AppError("CURRENCY_MISMATCH", "Payment and invoice currencies differ.", 422)
    unalloc = payment_unallocated(db, payment)
    if amt > unalloc:
        raise conflict(
            "OVER_ALLOCATION_PAYMENT",
            "Allocation exceeds the payment's unallocated amount.",
            {"requested": str(amt), "unallocated": str(unalloc)},
        )
    out = invoice_outstanding(db, invoice)
    if amt > out:
        raise conflict(
            "OVER_ALLOCATION_INVOICE",
            "Allocation exceeds the invoice's outstanding amount.",
            {"requested": str(amt), "outstanding": str(out)},
        )
    a = PaymentAllocation(
        organization_id=org_id, payment_id=payment.id, sales_invoice_id=invoice.id, amount=amt, created_by_id=actor_id
    )
    db.add(a)
    db.flush()
    refresh_invoice_status(db, invoice)
    return a


def suggest_allocations(db, org_id, payment: Payment) -> list[dict]:
    """Oldest-due-first suggestion. Never applied without an explicit user action."""
    remaining = payment_unallocated(db, payment)
    invs = db.scalars(
        select(SalesInvoice)
        .where(
            SalesInvoice.organization_id == org_id,
            SalesInvoice.retailer_id == payment.retailer_id,
            SalesInvoice.status.in_(("open", "partially_paid")),
        )
        .order_by(SalesInvoice.due_date, SalesInvoice.invoice_number)
    ).all()
    outs = outstanding_map(db, invs)
    out = []
    for inv in invs:
        if remaining <= 0:
            break
        o = outs[inv.id]
        if o <= 0:
            continue
        take = min(o, remaining)
        out.append({"sales_invoice_id": str(inv.id), "invoice_number": inv.invoice_number, "due_date": inv.due_date.isoformat(), "outstanding": str(o), "amount": str(take)})
        remaining -= take
    return out
