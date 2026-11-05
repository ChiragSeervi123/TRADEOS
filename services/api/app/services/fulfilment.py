"""Pick -> dispatch -> deliver, plus customer/supplier returns.

Dispatch is idempotent: a pick list can be dispatched at most once (DB unique
constraint on dispatches.pick_list_id + status check under row lock), so a
retried dispatch never double-decrements stock.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app import audit
from app.common import next_number
from app.db import utcnow
from app.errors import AppError, conflict
from app.models import (
    Dispatch,
    Order,
    OrderLine,
    PickList,
    PickListLine,
    Product,
    Retailer,
    ReturnLine,
    ReturnRecord,
    SalesInvoice,
    SalesInvoiceLine,
)
from app.money import ZERO, money, pct_of, qty
from app.services import inventory
from app.services.orders import add_event


def create_pick_list(ctx, order: Order) -> PickList | None:
    """Pick list for all undispatched quantities, batches suggested FEFO."""
    remaining = [(l, qty(l.base_quantity - l.dispatched_quantity)) for l in order.lines if l.product_id and l.base_quantity > l.dispatched_quantity]
    if not remaining:
        return None
    pl = PickList(
        organization_id=order.organization_id,
        order_id=order.id,
        warehouse_id=order.warehouse_id,
        pick_number=next_number(ctx.db, PickList, order.organization_id, "pick_number", "PK"),
        status="open",
    )
    ctx.db.add(pl)
    ctx.db.flush()
    n = 1
    # Track batch quantity already suggested within this pick list (same product on several lines).
    used: dict = defaultdict(lambda: ZERO)
    for line, need in remaining:
        batches = inventory.batch_on_hand(ctx.db, order.organization_id, order.warehouse_id, line.product_id)
        for b, avail in batches:
            if need <= 0:
                break
            key = (line.product_id, b.id if b else None)
            free = avail - used[key]
            if free <= 0:
                continue
            take = min(free, need)
            ctx.db.add(PickListLine(organization_id=order.organization_id, pick_list_id=pl.id, order_line_id=line.id, line_no=n, product_id=line.product_id, batch_id=b.id if b else None, requested_quantity=take))
            used[key] += take
            need -= take
            n += 1
        if need > 0:  # not enough batch stock located (e.g. override): pick unbatched
            ctx.db.add(PickListLine(organization_id=order.organization_id, pick_list_id=pl.id, order_line_id=line.id, line_no=n, product_id=line.product_id, batch_id=None, requested_quantity=need))
            n += 1
    ctx.db.flush()
    return pl


def start_picking(ctx, pl: PickList, order: Order) -> PickList:
    if pl.status != "open":
        raise conflict("INVALID_STATUS", f"Pick list is {pl.status}.")
    pl.status = "picking"
    pl.assigned_to_id = ctx.user.id
    if order.status == "approved":
        add_event(ctx.db, order, "picking", "start_picking", ctx.user.id)
        order.status = "picking"
    audit.record(ctx, "fulfilment.start_picking", "pick_list", pl.id)
    return pl


def record_picks(ctx, pl: PickList, picks: dict) -> PickList:
    if pl.status not in ("open", "picking", "picked"):
        raise conflict("INVALID_STATUS", f"Pick list is {pl.status}.")
    lines = {str(l.id): l for l in ctx.db.scalars(select(PickListLine).where(PickListLine.pick_list_id == pl.id)).all()}
    for lid, q in picks.items():
        line = lines.get(str(lid))
        if not line:
            raise AppError("NOT_FOUND", "Pick line not found.", 404)
        q = qty(q)
        if q < 0 or q > line.requested_quantity:
            raise AppError("INVALID_QUANTITY", "Picked quantity must be between 0 and the requested quantity.", 422, {"line_no": line.line_no})
        line.picked_quantity = q
    if all(l.picked_quantity is not None for l in lines.values()):
        pl.status = "picked"
    elif pl.status == "open":
        pl.status = "picking"
    audit.record(ctx, "fulfilment.record_picks", "pick_list", pl.id, meta={"lines": {k: str(v) for k, v in picks.items()}})
    return pl


def _invoice_dispatch(ctx, order: Order, dispatch: Dispatch, dispatched: dict) -> SalesInvoice | None:
    """Sales invoice for quantities in this dispatch. Amounts are proportional to the
    order line; the final dispatch of a line takes the exact remainder so invoice
    totals always sum to the order line totals (no rounding drift)."""
    retailer = ctx.db.get(Retailer, order.retailer_id)
    inv_date = date.today()
    inv = SalesInvoice(
        organization_id=order.organization_id,
        invoice_number=next_number(ctx.db, SalesInvoice, order.organization_id, "invoice_number", "INV"),
        retailer_id=order.retailer_id,
        order_id=order.id,
        dispatch_id=dispatch.id,
        invoice_date=inv_date,
        due_date=inv_date + timedelta(days=retailer.payment_terms_days or 0),
        currency=order.currency,
        gross_amount=ZERO,
        total=ZERO,
        is_synthetic=order.is_synthetic,
    )
    ctx.db.add(inv)
    ctx.db.flush()
    n = 1
    for line in order.lines:
        q = dispatched.get(line.id, ZERO)
        if q <= 0:
            continue
        if line.is_free_goods:
            gross = disc = tax = ZERO
        else:
            prev = ctx.db.execute(
                select(func.sum(SalesInvoiceLine.quantity), func.sum(SalesInvoiceLine.gross_amount), func.sum(SalesInvoiceLine.discount_amount), func.sum(SalesInvoiceLine.tax_amount))
                .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.sales_invoice_id)
                .where(SalesInvoiceLine.order_line_id == line.id, SalesInvoice.status != "void", SalesInvoice.id != inv.id)
            ).one()
            prev_q, prev_g, prev_d, prev_t = (qty(prev[0] or 0), money(prev[1] or 0), money(prev[2] or 0), money(prev[3] or 0))
            if qty(prev_q + q) >= line.base_quantity:
                gross = money(line.gross_amount - prev_g)
                disc = money(line.scheme_discount + line.manual_discount - prev_d)
                tax = money(line.tax_amount - prev_t)
            else:
                frac = q / line.base_quantity
                gross = money(q * line.unit_price)
                disc = money((line.scheme_discount + line.manual_discount) * frac)
                tax = pct_of(gross - disc, line.tax_rate)
        ctx.db.add(
            SalesInvoiceLine(
                organization_id=order.organization_id,
                sales_invoice_id=inv.id,
                line_no=n,
                product_id=line.product_id,
                order_line_id=line.id,
                quantity=q,
                unit_price=line.unit_price,
                gross_amount=gross,
                discount_amount=disc,
                tax_amount=tax,
                line_total=money(gross - disc + tax),
                is_free_goods=line.is_free_goods,
            )
        )
        inv.gross_amount += gross
        inv.discount_amount += disc
        inv.tax_amount += tax
        n += 1
    inv.gross_amount = money(inv.gross_amount)
    inv.discount_amount = money(inv.discount_amount)
    inv.tax_amount = money(inv.tax_amount)
    inv.total = money(inv.gross_amount - inv.discount_amount + inv.tax_amount)
    return inv


def dispatch(ctx, pl: PickList, order: Order, *, vehicle: str | None = None) -> tuple[Dispatch, bool]:
    """Returns (dispatch, created). Caller holds row locks on pick list + order."""
    existing = ctx.db.scalars(select(Dispatch).where(Dispatch.pick_list_id == pl.id)).first()
    if existing:
        return existing, False
    if pl.status not in ("open", "picking", "picked"):
        raise conflict("INVALID_STATUS", f"Pick list is {pl.status}.")
    if order.status not in ("approved", "picking", "partially_fulfilled"):
        raise conflict("INVALID_STATUS", f"Order is {order.status}.")
    lines = ctx.db.scalars(select(PickListLine).where(PickListLine.pick_list_id == pl.id).order_by(PickListLine.line_no)).all()
    picked_total = sum((l.picked_quantity if l.picked_quantity is not None else l.requested_quantity for l in lines), ZERO)
    if picked_total <= 0:
        raise AppError("NOTHING_PICKED", "Nothing was picked; record picked quantities first or cancel.", 422)
    d = Dispatch(
        organization_id=order.organization_id,
        order_id=order.id,
        pick_list_id=pl.id,
        dispatch_number=next_number(ctx.db, Dispatch, order.organization_id, "dispatch_number", "DSP"),
        vehicle=vehicle,
        actor_id=ctx.user.id,
    )
    ctx.db.add(d)
    ctx.db.flush()
    by_order_line: dict = defaultdict(lambda: ZERO)
    by_product: dict = defaultdict(lambda: ZERO)
    for l in lines:
        q = l.picked_quantity if l.picked_quantity is not None else l.requested_quantity
        l.picked_quantity = q
        if q <= 0:
            continue
        inventory.post(ctx.db, ctx.org_id, warehouse_id=pl.warehouse_id, product_id=l.product_id, batch_id=l.batch_id, movement_type="dispatch", quantity=-q, actor_id=ctx.user.id, source_type="dispatch", source_id=d.id, reason=f"Dispatch {d.dispatch_number} for {order.order_number}", allow_negative=bool(order.stock_override_reason), is_synthetic=order.is_synthetic)
        by_order_line[l.order_line_id] += q
        by_product[l.product_id] += q
    held = inventory.reserved_for_source(ctx.db, ctx.org_id, "order", order.id)
    for pid, q in sorted(by_product.items(), key=lambda kv: str(kv[0])):
        release = min(q, held.get((pl.warehouse_id, pid), ZERO))
        if release > 0:
            inventory.post(ctx.db, ctx.org_id, warehouse_id=pl.warehouse_id, product_id=pid, movement_type="reservation_release", quantity=-release, actor_id=ctx.user.id, source_type="order", source_id=order.id, reason=f"Consumed by dispatch {d.dispatch_number}")
    for ol in order.lines:
        if ol.id in by_order_line:
            ol.dispatched_quantity = qty(ol.dispatched_quantity + by_order_line[ol.id])
    pl.status = "dispatched"
    inv = _invoice_dispatch(ctx, order, d, dict(by_order_line))
    fully = all(l.dispatched_quantity >= l.base_quantity for l in order.lines if l.product_id)
    new_status = "dispatched" if fully else "partially_fulfilled"
    add_event(ctx.db, order, new_status, "dispatch", ctx.user.id, f"{d.dispatch_number}")
    order.status = new_status
    order.dispatched_at = order.dispatched_at or utcnow()
    audit.record(ctx, "fulfilment.dispatch", "dispatch", d.id, after={"order": order.order_number, "dispatch": d.dispatch_number, "invoice": inv.invoice_number if inv else None, "quantities": {str(k): str(v) for k, v in by_product.items()}, "order_status": new_status})
    return d, True


def deliver(ctx, d: Dispatch, order: Order, note: str | None = None, failed: bool = False) -> Dispatch:
    if d.status != "dispatched":
        raise conflict("INVALID_STATUS", f"Dispatch is already {d.status}.")
    d.status = "failed" if failed else "delivered"
    d.delivered_at = None if failed else utcnow()
    d.delivery_note = note
    if not failed:
        open_dispatches = ctx.db.scalar(select(func.count()).select_from(Dispatch).where(Dispatch.order_id == order.id, Dispatch.status == "dispatched", Dispatch.id != d.id))
        if order.status == "dispatched" and not open_dispatches:
            add_event(ctx.db, order, "delivered", "deliver", ctx.user.id, note)
            order.status = "delivered"
            order.delivered_at = utcnow()
    audit.record(ctx, "fulfilment.delivery_failed" if failed else "fulfilment.deliver", "dispatch", d.id, meta={"note": note})
    return d


def close_short(ctx, order: Order, reason: str) -> Order:
    if order.status != "partially_fulfilled":
        raise conflict("INVALID_STATUS", "Only partially fulfilled orders can be closed short.")
    if order.fulfilment_closed_at:
        return order
    inventory.release_all(ctx.db, ctx.org_id, source_type="order", source_id=order.id, actor_id=ctx.user.id, reason=f"Closed short: {reason}")
    for pl in ctx.db.scalars(select(PickList).where(PickList.order_id == order.id, PickList.status.in_(("open", "picking", "picked")))).all():
        pl.status = "cancelled"
    order.fulfilment_closed_at = utcnow()
    add_event(ctx.db, order, "partially_fulfilled", "close_short", ctx.user.id, reason)
    audit.record(ctx, "order.close_short", "order", order.id, meta={"reason": reason})
    return order


# --------------------------------------------------------------------------- returns


def create_return(ctx, *, return_type: str, warehouse_id, lines: list[dict], retailer_id=None, supplier_id=None, order_id=None, sales_invoice_id=None, reason=None, is_synthetic=False) -> ReturnRecord:
    from app.models import Supplier, Warehouse

    ctx.get(Warehouse, warehouse_id, "Warehouse")
    if return_type == "customer":
        if not retailer_id:
            raise AppError("RETAILER_REQUIRED", "Customer returns need a retailer.", 422)
        ctx.get(Retailer, retailer_id, "Retailer")
    elif return_type == "supplier":
        if not supplier_id:
            raise AppError("SUPPLIER_REQUIRED", "Supplier returns need a supplier.", 422)
        ctx.get(Supplier, supplier_id, "Supplier")
    else:
        raise AppError("INVALID_TYPE", "return_type must be customer or supplier.", 422)
    inv = ctx.maybe(SalesInvoice, sales_invoice_id)
    if inv and str(inv.retailer_id) != str(retailer_id):
        raise AppError("RETAILER_MISMATCH", "Invoice belongs to another retailer.", 422)
    if order_id:
        o = ctx.get(Order, order_id, "Order")
        if return_type == "customer" and str(o.retailer_id) != str(retailer_id):
            raise AppError("RETAILER_MISMATCH", "Order belongs to another retailer.", 422)
    if not lines:
        raise AppError("NO_LINES", "A return needs at least one line.", 422)
    r = ReturnRecord(
        organization_id=ctx.org_id,
        return_number=next_number(ctx.db, ReturnRecord, ctx.org_id, "return_number", "CR" if return_type == "customer" else "SR"),
        return_type=return_type,
        retailer_id=retailer_id,
        supplier_id=supplier_id,
        order_id=order_id,
        sales_invoice_id=sales_invoice_id,
        warehouse_id=warehouse_id,
        reason=reason,
        return_date=date.today(),
        created_by_id=ctx.user.id,
        is_synthetic=is_synthetic,
    )
    ctx.db.add(r)
    ctx.db.flush()
    total = ZERO
    for i, li in enumerate(lines, start=1):
        p = ctx.get(Product, li["product_id"], "Product")
        q = qty(li["quantity"])
        if q <= 0:
            raise AppError("INVALID_QUANTITY", "Return quantity must be positive.", 422)
        price = li.get("unit_price")
        if price is None:
            price = _reference_price(ctx, p, inv, return_type)
        price = money(price)
        amt = money(q * price)
        total += amt
        batch = inventory.get_or_create_batch(ctx.db, ctx.org_id, p.id, li.get("batch_number"), li.get("expiry_date")) if li.get("batch_number") else None
        ctx.db.add(ReturnLine(organization_id=ctx.org_id, return_id=r.id, line_no=i, product_id=p.id, batch_id=batch.id if batch else None, quantity=q, unit_price=price, condition=li.get("condition") or "sellable", amount=amt))
    r.total_value = money(total)
    audit.record(ctx, "return.create", "return", r.id, after={"number": r.return_number, "type": return_type, "total": str(r.total_value)})
    return r


def _reference_price(ctx, product: Product, inv: SalesInvoice | None, return_type: str) -> Decimal:
    if return_type == "supplier":
        return product.cost_price or ZERO
    if inv:
        line = ctx.db.scalars(select(SalesInvoiceLine).where(SalesInvoiceLine.sales_invoice_id == inv.id, SalesInvoiceLine.product_id == product.id, SalesInvoiceLine.is_free_goods.is_(False))).first()
        if line and line.quantity:
            return money((line.gross_amount - line.discount_amount) / line.quantity)
    return product.cost_price or ZERO


def receive_return(ctx, r: ReturnRecord) -> ReturnRecord:
    """Post stock movements. Customer returns add stock (damaged/expired goods are
    blocked so they are never available to sell); supplier returns remove stock."""
    if r.status != "pending":
        raise conflict("INVALID_STATUS", f"Return is already {r.status}.")
    for l in ctx.db.scalars(select(ReturnLine).where(ReturnLine.return_id == r.id).order_by(ReturnLine.line_no)).all():
        if r.return_type == "customer":
            inventory.post(ctx.db, ctx.org_id, warehouse_id=r.warehouse_id, product_id=l.product_id, batch_id=l.batch_id, movement_type="customer_return", quantity=l.quantity, actor_id=ctx.user.id, source_type="return", source_id=r.id, reason=r.reason, is_synthetic=r.is_synthetic)
            if l.condition != "sellable":
                inventory.post(ctx.db, ctx.org_id, warehouse_id=r.warehouse_id, product_id=l.product_id, movement_type="block", quantity=l.quantity, actor_id=ctx.user.id, source_type="return", source_id=r.id, reason=f"Returned {l.condition}")
        else:
            inventory.post(ctx.db, ctx.org_id, warehouse_id=r.warehouse_id, product_id=l.product_id, batch_id=l.batch_id, movement_type="supplier_return", quantity=-l.quantity, actor_id=ctx.user.id, source_type="return", source_id=r.id, reason=r.reason, is_synthetic=r.is_synthetic)
    r.status = "received"
    r.received_at = utcnow()
    audit.record(ctx, "return.receive", "return", r.id, after={"status": "received"})
    return r


def remaining_lines(order: Order) -> list[OrderLine]:
    return [l for l in order.lines if l.product_id and l.base_quantity > l.dispatched_quantity]
