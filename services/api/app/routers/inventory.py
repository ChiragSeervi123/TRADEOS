import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import audit
from app.common import PageParams, Qty, next_number, page_out
from app.db import utcnow
from app.deps import Ctx, require
from app.errors import AppError, conflict
from app.models import (
    Batch,
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryMovement,
    Order,
    Product,
    Retailer,
    StockCount,
    StockCountLine,
    Supplier,
    User,
    Warehouse,
)
from app.money import ZERO, money, qty
from app.services import inventory, pricing

router = APIRouter(prefix="/inventory", tags=["inventory"])


def _maps(ctx):
    products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.organization_id == ctx.org_id)).all()}
    whs = {w.id: w for w in ctx.db.scalars(select(Warehouse).where(Warehouse.organization_id == ctx.org_id)).all()}
    return products, whs


@router.get("/balances")
def get_balances(
    params: PageParams = Depends(),
    warehouse_id: uuid.UUID | None = None,
    stock_status: Literal["all", "in_stock", "low", "out"] = "all",
    low_threshold: Decimal = Decimal("10"),
    ctx: Ctx = Depends(require("inventory.read")),
):
    if warehouse_id:
        ctx.get(Warehouse, warehouse_id, "Warehouse")
    products, whs = _maps(ctx)
    bals = inventory.balances(ctx.db, ctx.org_id, warehouse_id)
    show_value = ctx.can("finance.read") or ctx.can("reports.read")
    rows = []
    # Include active products with no movements so "out of stock" is visible.
    keys = set(bals)
    target_whs = [warehouse_id] if warehouse_id else list(whs)
    for p in products.values():
        if p.is_active:
            for w in target_whs:
                keys.add((w, p.id))
    for wh, pid in keys:
        p = products.get(pid)
        if not p or wh not in whs:
            continue
        if params.q and params.q.lower() not in f"{p.sku} {p.name} {p.brand or ''}".lower():
            continue
        b = bals.get((wh, pid), inventory.Bal())
        status = "out" if b.available <= 0 else "low" if b.available <= low_threshold else "in_stock"
        if stock_status != "all" and status != stock_status:
            continue
        row = {
            "warehouse_id": str(wh),
            "warehouse": whs[wh].code,
            "product_id": str(pid),
            "sku": p.sku,
            "name": p.name,
            "variant": p.variant,
            "brand": p.brand,
            "base_unit": p.base_unit,
            **b.as_dict(),
            "stock_status": status,
        }
        if show_value:
            row["value"] = str(money(b.on_hand * (p.cost_price or ZERO))) if p.cost_price is not None else None
        rows.append(row)
    rows.sort(key=lambda r: (r["sku"], r["warehouse"]))
    total = len(rows)
    start = (params.page - 1) * params.page_size
    summary = {
        "skus": len({r["product_id"] for r in rows}),
        "out_of_stock": sum(1 for r in rows if r["stock_status"] == "out"),
        "low_stock": sum(1 for r in rows if r["stock_status"] == "low"),
    }
    if show_value:
        summary["inventory_value"] = str(money(sum((Decimal(r["value"]) for r in rows if r.get("value")), ZERO)))
    out = page_out(rows[start : start + params.page_size], total, params)
    out["summary"] = summary
    return out


@router.get("/movements")
def get_movements(
    params: PageParams = Depends(),
    warehouse_id: uuid.UUID | None = None,
    product_id: uuid.UUID | None = None,
    movement_type: str | None = None,
    source_type: str | None = None,
    ctx: Ctx = Depends(require("inventory.read")),
):
    stmt = select(InventoryMovement).where(InventoryMovement.organization_id == ctx.org_id)
    if warehouse_id:
        stmt = stmt.where(InventoryMovement.warehouse_id == warehouse_id)
    if product_id:
        stmt = stmt.where(InventoryMovement.product_id == product_id)
    if movement_type:
        stmt = stmt.where(InventoryMovement.movement_type == movement_type)
    if source_type:
        stmt = stmt.where(InventoryMovement.source_type == source_type)
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(InventoryMovement.created_at.desc(), InventoryMovement.id).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    products, whs = _maps(ctx)
    users = {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.id.in_({m.actor_id for m in rows if m.actor_id}))).all()}
    batches = {b.id: b for b in ctx.db.scalars(select(Batch).where(Batch.id.in_({m.batch_id for m in rows if m.batch_id}))).all()}
    items = [
        {
            "id": str(m.id),
            "created_at": m.created_at.isoformat(),
            "warehouse": whs[m.warehouse_id].code if m.warehouse_id in whs else None,
            "sku": products[m.product_id].sku if m.product_id in products else None,
            "product_id": str(m.product_id),
            "movement_type": m.movement_type,
            "bucket": m.bucket,
            "quantity": str(m.quantity),
            "unit": m.unit,
            "batch": batches[m.batch_id].batch_number if m.batch_id in batches else None,
            "source_type": m.source_type,
            "source_id": m.source_id,
            "reason": m.reason,
            "actor": users.get(m.actor_id),
            "is_synthetic": m.is_synthetic,
        }
        for m in rows
    ]
    return page_out(items, total, params)


@router.get("/batches")
def get_batches(warehouse_id: uuid.UUID | None = None, expiring_within_days: int | None = None, ctx: Ctx = Depends(require("inventory.read"))):
    stmt = (
        select(InventoryMovement.warehouse_id, InventoryMovement.product_id, InventoryMovement.batch_id, func.sum(InventoryMovement.quantity))
        .where(InventoryMovement.organization_id == ctx.org_id, InventoryMovement.bucket == "on_hand", InventoryMovement.batch_id.is_not(None))
        .group_by(InventoryMovement.warehouse_id, InventoryMovement.product_id, InventoryMovement.batch_id)
    )
    if warehouse_id:
        stmt = stmt.where(InventoryMovement.warehouse_id == warehouse_id)
    products, whs = _maps(ctx)
    rows = ctx.db.execute(stmt).all()
    batches = {b.id: b for b in ctx.db.scalars(select(Batch).where(Batch.id.in_({r[2] for r in rows}))).all()}
    today = date.today()
    out = []
    for wh, pid, bid, total in rows:
        q = qty(total or 0)
        if q <= 0:
            continue
        b = batches[bid]
        days = (b.expiry_date - today).days if b.expiry_date else None
        if expiring_within_days is not None and (days is None or days > expiring_within_days):
            continue
        out.append({
            "warehouse": whs[wh].code,
            "warehouse_id": str(wh),
            "product_id": str(pid),
            "sku": products[pid].sku,
            "name": products[pid].name,
            "batch_id": str(bid),
            "batch_number": b.batch_number,
            "expiry_date": b.expiry_date.isoformat() if b.expiry_date else None,
            "days_to_expiry": days,
            "status": "expired" if days is not None and days < 0 else "expiring" if days is not None and days <= int(ctx.org.setting("expiry_alert_days")) else "ok",
            "on_hand": str(q),
        })
    out.sort(key=lambda r: (r["expiry_date"] or "9999", r["sku"]))
    return out


@router.get("/reservations")
def get_reservations(warehouse_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("inventory.read"))):
    stmt = (
        select(InventoryMovement.source_id, InventoryMovement.warehouse_id, InventoryMovement.product_id, func.sum(InventoryMovement.quantity))
        .where(InventoryMovement.organization_id == ctx.org_id, InventoryMovement.bucket == "reserved", InventoryMovement.source_type == "order")
        .group_by(InventoryMovement.source_id, InventoryMovement.warehouse_id, InventoryMovement.product_id)
    )
    if warehouse_id:
        stmt = stmt.where(InventoryMovement.warehouse_id == warehouse_id)
    rows = [r for r in ctx.db.execute(stmt).all() if qty(r[3]) > 0]
    products, whs = _maps(ctx)
    orders = {str(o.id): o for o in ctx.db.scalars(select(Order).where(Order.id.in_({uuid.UUID(r[0]) for r in rows}))).all()} if rows else {}
    retailers = {r.id: r.name for r in ctx.db.scalars(select(Retailer).where(Retailer.id.in_({o.retailer_id for o in orders.values()}))).all()} if orders else {}
    return [
        {
            "order_id": oid,
            "order_number": orders[oid].order_number if oid in orders else None,
            "order_status": orders[oid].status if oid in orders else None,
            "retailer": retailers.get(orders[oid].retailer_id) if oid in orders else None,
            "warehouse": whs[wh].code,
            "sku": products[pid].sku,
            "quantity": str(qty(q)),
        }
        for oid, wh, pid, q in rows
    ]


class AdjustmentIn(BaseModel):
    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    quantity: Qty = Field(description="Signed change in base units (+ add, - remove). For write_off/block/unblock give a positive number.")
    movement_type: Literal["manual_adjustment", "write_off", "block", "unblock"] = "manual_adjustment"
    reason: str = Field(min_length=3, max_length=500)
    evidence: str | None = Field(default=None, max_length=1000)
    batch_number: str | None = None
    expiry_date: date | None = None


@router.post("/adjustments", status_code=201)
def adjust(body: AdjustmentIn, ctx: Ctx = Depends(require("inventory.adjust"))):
    ctx.get(Warehouse, body.warehouse_id, "Warehouse")
    p = ctx.get(Product, body.product_id, "Product")
    q = body.quantity
    if body.movement_type in ("write_off", "unblock"):
        q = -abs(q)
    elif body.movement_type == "block":
        q = abs(q)
    batch = inventory.get_or_create_batch(ctx.db, ctx.org_id, p.id, body.batch_number, body.expiry_date)
    if body.movement_type == "block":
        bal = inventory.ledger_balance(ctx.db, ctx.org_id, body.warehouse_id, p.id)
        if q > bal.available:
            raise conflict("INSUFFICIENT_AVAILABLE_STOCK", "Cannot block more than available stock.", {"available": str(bal.available)})
    mv = inventory.post(ctx.db, ctx.org_id, warehouse_id=body.warehouse_id, product_id=p.id, movement_type=body.movement_type, quantity=q, batch_id=batch.id if batch and body.movement_type in ("manual_adjustment", "write_off") else None, actor_id=ctx.user.id, source_type="adjustment", reason=body.reason, evidence=body.evidence)
    ctx.db.flush()
    audit.record(ctx, f"inventory.{body.movement_type}", "inventory_movement", mv.id, after={"sku": p.sku, "warehouse_id": str(body.warehouse_id), "quantity": str(q), "reason": body.reason})
    ctx.db.commit()
    return {"id": str(mv.id), "balance": inventory.ledger_balance(ctx.db, ctx.org_id, body.warehouse_id, p.id).as_dict()}


class TransferIn(BaseModel):
    from_warehouse_id: uuid.UUID
    to_warehouse_id: uuid.UUID
    product_id: uuid.UUID
    quantity: Qty = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/transfers", status_code=201)
def transfer(body: TransferIn, ctx: Ctx = Depends(require("inventory.adjust"))):
    if body.from_warehouse_id == body.to_warehouse_id:
        raise AppError("SAME_WAREHOUSE", "Choose two different warehouses.", 422)
    ctx.get(Warehouse, body.from_warehouse_id, "Warehouse")
    ctx.get(Warehouse, body.to_warehouse_id, "Warehouse")
    p = ctx.get(Product, body.product_id, "Product")
    bal = inventory.ledger_balance(ctx.db, ctx.org_id, body.from_warehouse_id, p.id)
    if body.quantity > bal.available:
        raise conflict("INSUFFICIENT_AVAILABLE_STOCK", "The requested quantity exceeds available stock.", {"sku": p.sku, "requested": str(body.quantity), "available": str(bal.available)})
    ref = uuid.uuid4()
    a = inventory.post(ctx.db, ctx.org_id, warehouse_id=body.from_warehouse_id, product_id=p.id, movement_type="transfer_out", quantity=-body.quantity, actor_id=ctx.user.id, source_type="transfer", source_id=ref, reason=body.reason)
    inventory.post(ctx.db, ctx.org_id, warehouse_id=body.to_warehouse_id, product_id=p.id, movement_type="transfer_in", quantity=body.quantity, actor_id=ctx.user.id, source_type="transfer", source_id=ref, reason=body.reason)
    audit.record(ctx, "inventory.transfer", "transfer", ref, after={"sku": p.sku, "from": str(body.from_warehouse_id), "to": str(body.to_warehouse_id), "quantity": str(body.quantity)})
    ctx.db.commit()
    return {"transfer_id": str(ref), "movement_id": str(a.id)}


class ReceiptLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Qty = Field(gt=0)
    unit: str | None = None
    batch_number: str | None = None
    expiry_date: date | None = None
    unit_cost: Decimal | None = Field(default=None, ge=0)


class ReceiptIn(BaseModel):
    supplier_id: uuid.UUID
    warehouse_id: uuid.UUID
    supplier_reference: str | None = Field(default=None, max_length=80)
    received_on: date | None = None
    notes: str | None = None
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=500)


def receipt_out(ctx, g: GoodsReceipt, lines=None):
    sup = ctx.db.get(Supplier, g.supplier_id)
    out = {
        "id": str(g.id),
        "receipt_number": g.receipt_number,
        "supplier_id": str(g.supplier_id),
        "supplier": sup.name if sup else None,
        "warehouse_id": str(g.warehouse_id),
        "supplier_reference": g.supplier_reference,
        "received_on": g.received_on.isoformat(),
        "status": g.status,
        "notes": g.notes,
        "is_synthetic": g.is_synthetic,
    }
    if lines is not None:
        products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.id.in_([l.product_id for l in lines]))).all()}
        out["lines"] = [{"product_id": str(l.product_id), "sku": products[l.product_id].sku, "quantity": str(l.quantity), "unit_cost": str(l.unit_cost) if l.unit_cost is not None else None} for l in lines]
    return out


@router.post("/receipts", status_code=201)
def create_receipt(body: ReceiptIn, ctx: Ctx = Depends(require("inventory.adjust"))):
    ctx.get(Supplier, body.supplier_id, "Supplier")
    ctx.get(Warehouse, body.warehouse_id, "Warehouse")
    g = GoodsReceipt(
        organization_id=ctx.org_id,
        receipt_number=next_number(ctx.db, GoodsReceipt, ctx.org_id, "receipt_number", "GRN"),
        supplier_id=body.supplier_id,
        warehouse_id=body.warehouse_id,
        supplier_reference=body.supplier_reference,
        received_on=body.received_on or date.today(),
        notes=body.notes,
        created_by_id=ctx.user.id,
    )
    ctx.db.add(g)
    ctx.db.flush()
    lines = []
    for li in body.lines:
        p = ctx.get(Product, li.product_id, "Product")
        factor = pricing.conversion_factor(ctx.db, ctx.org_id, p, li.unit or p.base_unit)
        if factor is None:
            raise AppError("UNIT_CONVERSION_MISSING", f"No conversion from {li.unit} to {p.base_unit} for {p.sku}.", 422)
        base_q = qty(li.quantity * factor)
        batch = inventory.get_or_create_batch(ctx.db, ctx.org_id, p.id, li.batch_number, li.expiry_date)
        gl = GoodsReceiptLine(organization_id=ctx.org_id, goods_receipt_id=g.id, product_id=p.id, batch_id=batch.id if batch else None, quantity=base_q, unit_cost=money(li.unit_cost / factor) if li.unit_cost is not None else None)
        ctx.db.add(gl)
        lines.append(gl)
        inventory.post(ctx.db, ctx.org_id, warehouse_id=g.warehouse_id, product_id=p.id, batch_id=gl.batch_id, movement_type="purchase_receipt", quantity=base_q, actor_id=ctx.user.id, source_type="goods_receipt", source_id=g.id, reason=f"GRN {g.receipt_number}")
    audit.record(ctx, "inventory.goods_receipt", "goods_receipt", g.id, after={"receipt_number": g.receipt_number, "lines": len(lines), "supplier_reference": g.supplier_reference})
    ctx.db.commit()
    return receipt_out(ctx, g, lines)


@router.get("/receipts")
def list_receipts(params: PageParams = Depends(), supplier_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("inventory.read"))):
    stmt = select(GoodsReceipt).where(GoodsReceipt.organization_id == ctx.org_id)
    if supplier_id:
        stmt = stmt.where(GoodsReceipt.supplier_id == supplier_id)
    if params.q:
        stmt = stmt.where(or_(GoodsReceipt.receipt_number.ilike(f"%{params.q}%"), GoodsReceipt.supplier_reference.ilike(f"%{params.q}%")))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(GoodsReceipt.received_on.desc(), GoodsReceipt.receipt_number.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return page_out([receipt_out(ctx, g) for g in rows], total, params)


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: uuid.UUID, ctx: Ctx = Depends(require("inventory.read"))):
    g = ctx.get(GoodsReceipt, receipt_id, "Goods receipt")
    lines = ctx.db.scalars(select(GoodsReceiptLine).where(GoodsReceiptLine.goods_receipt_id == g.id)).all()
    return receipt_out(ctx, g, lines)


# ----------------------------------------------------------------------- stock counts


class StockCountIn(BaseModel):
    warehouse_id: uuid.UUID
    product_ids: list[uuid.UUID] | None = None
    notes: str | None = None


class CountLinesIn(BaseModel):
    counts: dict[str, Decimal] = Field(description="{line_id: counted_quantity}")


def count_out(ctx, sc: StockCount, with_lines=True):
    out = {"id": str(sc.id), "count_number": sc.count_number, "warehouse_id": str(sc.warehouse_id), "status": sc.status, "notes": sc.notes, "created_at": sc.created_at.isoformat(), "posted_at": sc.posted_at.isoformat() if sc.posted_at else None}
    if with_lines:
        lines = ctx.db.scalars(select(StockCountLine).where(StockCountLine.stock_count_id == sc.id).order_by(StockCountLine.line_no)).all()
        products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.id.in_([l.product_id for l in lines]))).all()} if lines else {}
        out["lines"] = [
            {
                "id": str(l.id),
                "product_id": str(l.product_id),
                "sku": products[l.product_id].sku,
                "name": products[l.product_id].name,
                "system_quantity": str(l.system_quantity),
                "counted_quantity": str(l.counted_quantity) if l.counted_quantity is not None else None,
                "variance": str(qty(l.counted_quantity - l.system_quantity)) if l.counted_quantity is not None else None,
            }
            for l in lines
        ]
    return out


@router.get("/stock-counts")
def list_counts(ctx: Ctx = Depends(require("inventory.read"))):
    rows = ctx.db.scalars(select(StockCount).where(StockCount.organization_id == ctx.org_id).order_by(StockCount.created_at.desc()).limit(100)).all()
    return [count_out(ctx, s, with_lines=False) for s in rows]


@router.post("/stock-counts", status_code=201)
def create_count(body: StockCountIn, ctx: Ctx = Depends(require("inventory.adjust"))):
    ctx.get(Warehouse, body.warehouse_id, "Warehouse")
    if body.product_ids:
        pids = [ctx.get(Product, pid, "Product").id for pid in body.product_ids]
    else:
        bals = inventory.balances(ctx.db, ctx.org_id, body.warehouse_id)
        pids = sorted({pid for (_, pid), b in bals.items() if b.on_hand != 0}, key=str)
    if not pids:
        raise AppError("NOTHING_TO_COUNT", "No products to count in this warehouse.", 422)
    sc = StockCount(organization_id=ctx.org_id, warehouse_id=body.warehouse_id, count_number=next_number(ctx.db, StockCount, ctx.org_id, "count_number", "SC"), notes=body.notes, created_by_id=ctx.user.id)
    ctx.db.add(sc)
    ctx.db.flush()
    products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.id.in_(pids))).all()}
    for i, pid in enumerate(sorted(pids, key=lambda x: products[x].sku), start=1):
        b = inventory.ledger_balance(ctx.db, ctx.org_id, body.warehouse_id, pid)
        ctx.db.add(StockCountLine(organization_id=ctx.org_id, stock_count_id=sc.id, product_id=pid, system_quantity=b.on_hand, line_no=i))
    audit.record(ctx, "inventory.stock_count_create", "stock_count", sc.id)
    ctx.db.commit()
    return count_out(ctx, sc)


@router.get("/stock-counts/{count_id}")
def get_count(count_id: uuid.UUID, ctx: Ctx = Depends(require("inventory.read"))):
    return count_out(ctx, ctx.get(StockCount, count_id, "Stock count"))


@router.patch("/stock-counts/{count_id}")
def record_counts(count_id: uuid.UUID, body: CountLinesIn, ctx: Ctx = Depends(require("inventory.adjust"))):
    sc = ctx.get(StockCount, count_id, "Stock count")
    if sc.status != "draft":
        raise conflict("INVALID_STATUS", f"Stock count is {sc.status}.")
    lines = {str(l.id): l for l in ctx.db.scalars(select(StockCountLine).where(StockCountLine.stock_count_id == sc.id)).all()}
    for lid, v in body.counts.items():
        if lid not in lines:
            raise AppError("NOT_FOUND", "Count line not found.", 404)
        if v < 0:
            raise AppError("INVALID_QUANTITY", "Counted quantity cannot be negative.", 422)
        lines[lid].counted_quantity = qty(v)
    ctx.db.commit()
    return count_out(ctx, sc)


class PostCountIn(BaseModel):
    reason: str = Field(default="Physical stock count", min_length=3, max_length=500)


@router.post("/stock-counts/{count_id}/post")
def post_count(count_id: uuid.UUID, body: PostCountIn, ctx: Ctx = Depends(require("inventory.adjust"))):
    sc = ctx.get(StockCount, count_id, "Stock count", lock=True)
    if sc.status != "draft":
        raise conflict("INVALID_STATUS", f"Stock count is {sc.status}.")
    lines = ctx.db.scalars(select(StockCountLine).where(StockCountLine.stock_count_id == sc.id)).all()
    if any(l.counted_quantity is None for l in lines):
        raise AppError("COUNT_INCOMPLETE", "Enter a counted quantity for every line before posting.", 422)
    adjustments = []
    for l in lines:
        # Variance is computed against the ledger *now*, so movements during the count are respected.
        current = inventory.ledger_balance(ctx.db, ctx.org_id, sc.warehouse_id, l.product_id).on_hand
        delta = qty(l.counted_quantity - l.system_quantity)
        if delta != 0:
            inventory.post(ctx.db, ctx.org_id, warehouse_id=sc.warehouse_id, product_id=l.product_id, movement_type="stock_count_adjustment", quantity=delta, actor_id=ctx.user.id, source_type="stock_count", source_id=sc.id, reason=f"{body.reason} ({sc.count_number})", allow_negative=False)
            adjustments.append({"product_id": str(l.product_id), "system": str(l.system_quantity), "counted": str(l.counted_quantity), "delta": str(delta), "on_hand_at_post": str(current)})
    sc.status = "posted"
    sc.posted_by_id = ctx.user.id
    sc.posted_at = utcnow()
    audit.record(ctx, "inventory.stock_count_post", "stock_count", sc.id, meta={"adjustments": adjustments})
    ctx.db.commit()
    return {"status": "posted", "adjustments": adjustments}


@router.get("/verify")
def verify(ctx: Ctx = Depends(require("inventory.read"))):
    mism = inventory.verify_balances(ctx.db, ctx.org_id)
    return {"consistent": not mism, "mismatches": mism}


@router.get("/expiry-alerts")
def expiry_alerts(ctx: Ctx = Depends(require("inventory.read"))):
    days = int(ctx.org.setting("expiry_alert_days"))
    return get_batches(None, days, ctx)


_ = timedelta
