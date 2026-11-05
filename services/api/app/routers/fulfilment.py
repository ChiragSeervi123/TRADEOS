import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.common import PageParams, page_out
from app.deps import Ctx, require
from app.errors import AppError
from app.models import Batch, Dispatch, Order, PickList, PickListLine, Product, Retailer, ReturnLine, ReturnRecord, Supplier, Warehouse
from app.services import fulfilment as svc

router = APIRouter(tags=["fulfilment"])


def pick_out(ctx: Ctx, pl: PickList, detail=False) -> dict:
    order = ctx.db.get(Order, pl.order_id)
    retailer = ctx.db.get(Retailer, order.retailer_id)
    wh = ctx.db.get(Warehouse, pl.warehouse_id)
    out = {
        "id": str(pl.id),
        "pick_number": pl.pick_number,
        "status": pl.status,
        "order_id": str(order.id),
        "order_number": order.order_number,
        "order_status": order.status,
        "retailer": retailer.name,
        "delivery_address": retailer.delivery_address,
        "route": retailer.route,
        "warehouse": wh.name,
        "requested_delivery_date": order.requested_delivery_date.isoformat() if order.requested_delivery_date else None,
        "created_at": pl.created_at.isoformat(),
    }
    if detail:
        lines = ctx.db.scalars(select(PickListLine).where(PickListLine.pick_list_id == pl.id).order_by(PickListLine.line_no)).all()
        products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.id.in_([l.product_id for l in lines]))).all()}
        batches = {b.id: b for b in ctx.db.scalars(select(Batch).where(Batch.id.in_([l.batch_id for l in lines if l.batch_id]))).all()}
        out["lines"] = [
            {
                "id": str(l.id),
                "line_no": l.line_no,
                "sku": products[l.product_id].sku,
                "name": products[l.product_id].name,
                "variant": products[l.product_id].variant,
                "base_unit": products[l.product_id].base_unit,
                "batch_number": batches[l.batch_id].batch_number if l.batch_id in batches else None,
                "expiry_date": batches[l.batch_id].expiry_date.isoformat() if l.batch_id in batches and batches[l.batch_id].expiry_date else None,
                "requested_quantity": str(l.requested_quantity),
                "picked_quantity": str(l.picked_quantity) if l.picked_quantity is not None else None,
            }
            for l in lines
        ]
        d = ctx.db.scalars(select(Dispatch).where(Dispatch.pick_list_id == pl.id)).first()
        out["dispatch"] = {"id": str(d.id), "dispatch_number": d.dispatch_number, "status": d.status} if d else None
    return out


@router.get("/fulfilment/pick-lists")
def list_pick_lists(params: PageParams = Depends(), status: str | None = None, ctx: Ctx = Depends(require("fulfilment.read"))):
    stmt = select(PickList).where(PickList.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(PickList.status.in_(status.split(",")))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(PickList.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return page_out([pick_out(ctx, p) for p in rows], total, params)


@router.get("/fulfilment/pick-lists/{pick_list_id}")
def get_pick_list(pick_list_id: uuid.UUID, ctx: Ctx = Depends(require("fulfilment.read"))):
    return pick_out(ctx, ctx.get(PickList, pick_list_id, "Pick list"), detail=True)


@router.post("/fulfilment/pick-lists/{pick_list_id}/start")
def start(pick_list_id: uuid.UUID, ctx: Ctx = Depends(require("fulfilment.execute"))):
    pl = ctx.get(PickList, pick_list_id, "Pick list", lock=True)
    order = ctx.get(Order, pl.order_id, "Order", lock=True)
    svc.start_picking(ctx, pl, order)
    ctx.db.commit()
    return pick_out(ctx, pl, detail=True)


class PicksIn(BaseModel):
    picks: dict[str, Decimal] = Field(description="{pick_line_id: picked_quantity}")


@router.post("/fulfilment/pick-lists/{pick_list_id}/picks")
def record_picks(pick_list_id: uuid.UUID, body: PicksIn, ctx: Ctx = Depends(require("fulfilment.execute"))):
    pl = ctx.get(PickList, pick_list_id, "Pick list", lock=True)
    svc.record_picks(ctx, pl, body.picks)
    ctx.db.commit()
    return pick_out(ctx, pl, detail=True)


class DispatchIn(BaseModel):
    vehicle: str | None = Field(default=None, max_length=60)


@router.post("/fulfilment/pick-lists/{pick_list_id}/dispatch")
def dispatch(pick_list_id: uuid.UUID, body: DispatchIn | None = None, ctx: Ctx = Depends(require("fulfilment.execute"))):
    pl = ctx.get(PickList, pick_list_id, "Pick list", lock=True)
    order = ctx.get(Order, pl.order_id, "Order", lock=True)
    d, created = svc.dispatch(ctx, pl, order, vehicle=(body.vehicle if body else None))
    ctx.db.commit()
    return {"dispatch_id": str(d.id), "dispatch_number": d.dispatch_number, "order_status": order.status, "already_dispatched": not created}


@router.get("/fulfilment/dispatches")
def list_dispatches(params: PageParams = Depends(), status: str | None = None, ctx: Ctx = Depends(require("fulfilment.read"))):
    stmt = select(Dispatch).where(Dispatch.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(Dispatch.status == status)
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(Dispatch.dispatched_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    orders = {o.id: o for o in ctx.db.scalars(select(Order).where(Order.id.in_({d.order_id for d in rows}))).all()}
    retailers = {r.id: r for r in ctx.db.scalars(select(Retailer).where(Retailer.id.in_({o.retailer_id for o in orders.values()}))).all()}
    items = [
        {
            "id": str(d.id),
            "dispatch_number": d.dispatch_number,
            "status": d.status,
            "order_id": str(d.order_id),
            "order_number": orders[d.order_id].order_number,
            "retailer": retailers[orders[d.order_id].retailer_id].name,
            "vehicle": d.vehicle,
            "dispatched_at": d.dispatched_at.isoformat(),
            "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None,
            "delivery_note": d.delivery_note,
        }
        for d in rows
    ]
    return page_out(items, total, params)


class DeliveryIn(BaseModel):
    outcome: Literal["delivered", "failed"] = "delivered"
    note: str | None = Field(default=None, max_length=500)


@router.post("/fulfilment/dispatches/{dispatch_id}/delivery")
def delivery(dispatch_id: uuid.UUID, body: DeliveryIn, ctx: Ctx = Depends(require("fulfilment.execute"))):
    d = ctx.get(Dispatch, dispatch_id, "Dispatch", lock=True)
    order = ctx.get(Order, d.order_id, "Order", lock=True)
    svc.deliver(ctx, d, order, body.note, failed=body.outcome == "failed")
    ctx.db.commit()
    return {"status": d.status, "order_status": order.status}


@router.post("/orders/{order_id}/backorder-pick-list", status_code=201)
def backorder(order_id: uuid.UUID, ctx: Ctx = Depends(require("fulfilment.execute"))):
    order = ctx.get(Order, order_id, "Order", lock=True)
    if order.status != "partially_fulfilled" or order.fulfilment_closed_at:
        raise AppError("INVALID_STATUS", "Only open partially fulfilled orders can get a new pick list.", 409)
    if ctx.db.scalars(select(PickList).where(PickList.order_id == order.id, PickList.status.in_(("open", "picking", "picked")))).first():
        raise AppError("PICK_LIST_OPEN", "An open pick list already exists for this order.", 409)
    pl = svc.create_pick_list(ctx, order)
    if not pl:
        raise AppError("NOTHING_REMAINING", "No remaining quantity to pick.", 409)
    ctx.db.commit()
    return pick_out(ctx, pl, detail=True)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/orders/{order_id}/close-short")
def close_short(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.cancel"))):
    order = ctx.get(Order, order_id, "Order", lock=True)
    svc.close_short(ctx, order, body.reason)
    ctx.db.commit()
    return {"status": order.status, "fulfilment_closed_at": order.fulfilment_closed_at.isoformat()}


# ---------------------------------------------------------------------------- returns


class ReturnLineIn(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal | None = Field(default=None, ge=0)
    condition: Literal["sellable", "damaged", "expired"] = "sellable"
    batch_number: str | None = None
    expiry_date: date | None = None


class ReturnIn(BaseModel):
    return_type: Literal["customer", "supplier"]
    warehouse_id: uuid.UUID
    retailer_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    order_id: uuid.UUID | None = None
    sales_invoice_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=500)
    lines: list[ReturnLineIn] = Field(min_length=1, max_length=200)


def return_out(ctx: Ctx, r: ReturnRecord, detail=False):
    party = ctx.db.get(Retailer, r.retailer_id) if r.retailer_id else ctx.db.get(Supplier, r.supplier_id)
    out = {
        "id": str(r.id),
        "return_number": r.return_number,
        "return_type": r.return_type,
        "status": r.status,
        "party": party.name if party else None,
        "retailer_id": str(r.retailer_id) if r.retailer_id else None,
        "supplier_id": str(r.supplier_id) if r.supplier_id else None,
        "order_id": str(r.order_id) if r.order_id else None,
        "sales_invoice_id": str(r.sales_invoice_id) if r.sales_invoice_id else None,
        "reason": r.reason,
        "return_date": r.return_date.isoformat(),
        "total_value": str(r.total_value),
        "is_synthetic": r.is_synthetic,
    }
    if detail:
        lines = ctx.db.scalars(select(ReturnLine).where(ReturnLine.return_id == r.id).order_by(ReturnLine.line_no)).all()
        products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.id.in_([l.product_id for l in lines]))).all()}
        out["lines"] = [{"sku": products[l.product_id].sku, "name": products[l.product_id].name, "quantity": str(l.quantity), "unit_price": str(l.unit_price), "amount": str(l.amount), "condition": l.condition} for l in lines]
        from app.models import CreditNote

        notes = ctx.db.scalars(select(CreditNote).where(CreditNote.return_id == r.id)).all()
        out["credit_notes"] = [{"id": str(n.id), "note_number": n.note_number, "amount": str(n.amount), "status": n.status} for n in notes]
    return out


@router.get("/returns")
def list_returns(params: PageParams = Depends(), status: str | None = None, return_type: str | None = None, ctx: Ctx = Depends(require("orders.read"))):
    stmt = select(ReturnRecord).where(ReturnRecord.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(ReturnRecord.status.in_(status.split(",")))
    if return_type:
        stmt = stmt.where(ReturnRecord.return_type == return_type)
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(ReturnRecord.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return page_out([return_out(ctx, r) for r in rows], total, params)


@router.post("/returns", status_code=201)
def create_return(body: ReturnIn, ctx: Ctx = Depends(require("orders.create"))):
    if body.return_type == "supplier":
        ctx.require("inventory.adjust")
    r = svc.create_return(
        ctx,
        return_type=body.return_type,
        warehouse_id=body.warehouse_id,
        lines=[l.model_dump() for l in body.lines],
        retailer_id=body.retailer_id,
        supplier_id=body.supplier_id,
        order_id=body.order_id,
        sales_invoice_id=body.sales_invoice_id,
        reason=body.reason,
    )
    ctx.db.commit()
    return return_out(ctx, r, detail=True)


@router.get("/returns/{return_id}")
def get_return(return_id: uuid.UUID, ctx: Ctx = Depends(require("orders.read"))):
    return return_out(ctx, ctx.get(ReturnRecord, return_id, "Return"), detail=True)


@router.post("/returns/{return_id}/receive")
def receive_return(return_id: uuid.UUID, ctx: Ctx = Depends(require("inventory.adjust"))):
    r = ctx.get(ReturnRecord, return_id, "Return", lock=True)
    svc.receive_return(ctx, r)
    ctx.db.commit()
    return return_out(ctx, r, detail=True)
