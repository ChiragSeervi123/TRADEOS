import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import audit
from app.common import PageParams, idempotency_lookup, idempotency_store, page_out
from app.deps import Ctx, require
from app.errors import AppError
from app.models import (
    Dispatch,
    Order,
    OrderRevision,
    OrderStatusEvent,
    PickList,
    Product,
    Retailer,
    SalesInvoice,
    SchemeApplication,
    User,
    Warehouse,
)
from app.services import orders as svc
from app.services import receivables

router = APIRouter(prefix="/orders", tags=["orders"])


class LineIn(BaseModel):
    product_id: uuid.UUID | None = None
    quantity: Decimal = Field(gt=0)
    unit: str | None = Field(default=None, max_length=20)
    unit_price_override: Decimal | None = Field(default=None, ge=0)
    price_override_reason: str | None = Field(default=None, max_length=300)
    source_description: str | None = Field(default=None, max_length=300)


class OrderIn(BaseModel):
    retailer_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    order_date: date | None = None
    requested_delivery_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    lines: list[LineIn] = Field(min_length=1, max_length=300)


class OrderUpdate(BaseModel):
    retailer_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID | None = None
    requested_delivery_date: date | None = None
    notes: str | None = None
    lines: list[LineIn] | None = None
    version: int | None = None


class ApproveIn(BaseModel):
    stock_override_reason: str | None = Field(default=None, min_length=5, max_length=500)
    credit_override_reason: str | None = Field(default=None, min_length=5, max_length=500)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _line_dicts(lines: list[LineIn]):
    out = []
    for l in lines:
        d = l.model_dump()
        if d["unit_price_override"] is not None and not d.get("price_override_reason"):
            raise AppError("OVERRIDE_REASON_REQUIRED", "A reason is required when overriding a price.", 422)
        out.append(d)
    return out


def order_summary(o: Order, retailers: dict) -> dict:
    r = retailers.get(o.retailer_id)
    return {
        "id": str(o.id),
        "order_number": o.order_number,
        "status": o.status,
        "channel": o.channel,
        "order_date": o.order_date.isoformat(),
        "retailer_id": str(o.retailer_id),
        "retailer": r.name if r else None,
        "retailer_code": r.code if r else None,
        "net_total": str(o.net_total),
        "currency": o.currency,
        "line_count": len([l for l in o.lines if not l.is_free_goods]),
        "issues": (o.validation or {}).get("blocking", 0) + (o.validation or {}).get("overridable", 0),
        "created_at": o.created_at.isoformat(),
        "approved_at": o.approved_at.isoformat() if o.approved_at else None,
        "is_synthetic": o.is_synthetic,
    }


def order_detail(ctx: Ctx, o: Order) -> dict:
    db = ctx.db
    retailer = db.get(Retailer, o.retailer_id)
    wh = db.get(Warehouse, o.warehouse_id)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_([l.product_id for l in o.lines if l.product_id]))).all()}
    events = db.scalars(select(OrderStatusEvent).where(OrderStatusEvent.order_id == o.id).order_by(OrderStatusEvent.created_at, OrderStatusEvent.id)).all()
    users = {u.id: u.full_name for u in db.scalars(select(User).where(User.id.in_({e.actor_id for e in events if e.actor_id} | {o.created_by_id, o.approved_by_id} - {None}))).all()}
    apps = db.scalars(select(SchemeApplication).where(SchemeApplication.order_id == o.id)).all()
    revisions = db.scalars(select(OrderRevision).where(OrderRevision.order_id == o.id).order_by(OrderRevision.revision)).all()
    picks = db.scalars(select(PickList).where(PickList.order_id == o.id).order_by(PickList.created_at)).all()
    dispatches = db.scalars(select(Dispatch).where(Dispatch.order_id == o.id).order_by(Dispatch.created_at)).all()
    out = order_summary(o, {retailer.id: retailer})
    out.update(
        {
            "warehouse_id": str(o.warehouse_id),
            "warehouse": wh.name,
            "requested_delivery_date": o.requested_delivery_date.isoformat() if o.requested_delivery_date else None,
            "notes": o.notes,
            "source_id": str(o.source_id) if o.source_id else None,
            "totals": {
                "gross": str(o.gross_total),
                "scheme_discount": str(o.scheme_discount_total),
                "manual_discount": str(o.discount_total),
                "taxable": str(o.taxable_total),
                "tax": str(o.tax_total),
                "net": str(o.net_total),
            },
            "validation": o.validation,
            "stock_override_reason": o.stock_override_reason,
            "credit_override_reason": o.credit_override_reason,
            "created_by": users.get(o.created_by_id),
            "approved_by": users.get(o.approved_by_id),
            "revision": o.revision,
            "version": o.version,
            "fulfilment_closed_at": o.fulfilment_closed_at.isoformat() if o.fulfilment_closed_at else None,
            "lines": [
                {
                    "id": str(l.id),
                    "line_no": l.line_no,
                    "product_id": str(l.product_id) if l.product_id else None,
                    "sku": products[l.product_id].sku if l.product_id in products else None,
                    "name": products[l.product_id].name if l.product_id in products else None,
                    "variant": products[l.product_id].variant if l.product_id in products else None,
                    "source_description": l.source_description,
                    "quantity": str(l.quantity),
                    "unit": l.unit,
                    "conversion_factor": str(l.conversion_factor),
                    "base_quantity": str(l.base_quantity),
                    "base_unit": products[l.product_id].base_unit if l.product_id in products else None,
                    "list_price": str(l.list_price) if l.list_price is not None else None,
                    "unit_price": str(l.unit_price),
                    "price_override_reason": l.price_override_reason,
                    "gross_amount": str(l.gross_amount),
                    "scheme_discount": str(l.scheme_discount),
                    "taxable_amount": str(l.taxable_amount),
                    "tax_rate": str(l.tax_rate),
                    "tax_amount": str(l.tax_amount),
                    "line_total": str(l.line_total),
                    "is_free_goods": l.is_free_goods,
                    "dispatched_quantity": str(l.dispatched_quantity),
                    "provenance": l.provenance,
                    "extracted_values": l.extracted_values,
                    "match_confidence": str(l.match_confidence) if l.match_confidence is not None else None,
                }
                for l in o.lines
            ],
            "schemes": [{"scheme_id": str(a.scheme_id), "version": a.scheme_version, "benefit_type": a.benefit_type, "benefit_amount": str(a.benefit_amount), "free_quantity": str(a.free_quantity), "explanation": a.explanation} for a in apps],
            "timeline": [{"from": e.from_status, "to": e.to_status, "action": e.action, "reason": e.reason, "actor": users.get(e.actor_id), "at": e.created_at.isoformat()} for e in events],
            "revisions": [{"revision": r.revision, "reason": r.reason, "at": r.created_at.isoformat(), "snapshot": r.snapshot} for r in revisions],
            "pick_lists": [{"id": str(p.id), "pick_number": p.pick_number, "status": p.status} for p in picks],
            "dispatches": [{"id": str(d.id), "dispatch_number": d.dispatch_number, "status": d.status, "dispatched_at": d.dispatched_at.isoformat(), "delivered_at": d.delivered_at.isoformat() if d.delivered_at else None} for d in dispatches],
        }
    )
    if ctx.can("finance.read"):
        invs = db.scalars(select(SalesInvoice).where(SalesInvoice.order_id == o.id)).all()
        outs = receivables.outstanding_map(db, invs)
        out["invoices"] = [{"id": str(i.id), "invoice_number": i.invoice_number, "total": str(i.total), "outstanding": str(outs[i.id]), "status": i.status} for i in invs]
        out["credit"] = receivables.credit_exposure(db, ctx.org, retailer, o.net_total if o.status in ("draft", "validated") else Decimal("0"), exclude_order_id=o.id)
    return out


def _default_warehouse(ctx) -> uuid.UUID:
    w = ctx.db.scalars(select(Warehouse).where(Warehouse.organization_id == ctx.org_id, Warehouse.is_active.is_(True)).order_by(Warehouse.is_default.desc(), Warehouse.code)).first()
    if not w:
        raise AppError("NO_WAREHOUSE", "Create a warehouse before taking orders.", 422)
    return w.id


@router.get("")
def list_orders(
    params: PageParams = Depends(),
    status: str | None = None,
    retailer_id: uuid.UUID | None = None,
    channel: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    has_issues: bool | None = None,
    ctx: Ctx = Depends(require("orders.read")),
):
    stmt = select(Order).where(Order.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(Order.status.in_(status.split(",")))
    if retailer_id:
        stmt = stmt.where(Order.retailer_id == retailer_id)
    if channel:
        stmt = stmt.where(Order.channel == channel)
    if date_from:
        stmt = stmt.where(Order.order_date >= date_from)
    if date_to:
        stmt = stmt.where(Order.order_date <= date_to)
    if params.q:
        rids = select(Retailer.id).where(Retailer.organization_id == ctx.org_id, or_(Retailer.name.ilike(f"%{params.q}%"), Retailer.code.ilike(f"%{params.q}%")))
        stmt = stmt.where(or_(Order.order_number.ilike(f"%{params.q}%"), Order.retailer_id.in_(rids)))
    if ctx.role == "sales_rep":
        stmt = stmt.where(Order.created_by_id == ctx.user_id)  # reps see their own orders
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    sortable = {"created_at": Order.created_at, "order_date": Order.order_date, "net_total": Order.net_total, "order_number": Order.order_number}
    sort = params.sort or "-created_at"
    col = sortable.get(sort.lstrip("-"))
    if col is None:
        raise AppError("INVALID_SORT", f"Cannot sort by '{sort}'.", 400)
    stmt = stmt.order_by(col.desc() if sort.startswith("-") else col.asc(), Order.order_number.desc())
    rows = ctx.db.scalars(stmt.offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    if has_issues is not None:
        rows = [o for o in rows if bool((o.validation or {}).get("blocking") or (o.validation or {}).get("overridable")) == has_issues]
    retailers = {r.id: r for r in ctx.db.scalars(select(Retailer).where(Retailer.id.in_({o.retailer_id for o in rows}))).all()}
    return page_out([order_summary(o, retailers) for o in rows], total, params)


@router.get("/status-counts")
def status_counts(ctx: Ctx = Depends(require("orders.read"))):
    stmt = select(Order.status, func.count()).where(Order.organization_id == ctx.org_id).group_by(Order.status)
    if ctx.role == "sales_rep":
        stmt = stmt.where(Order.created_by_id == ctx.user_id)
    return dict(ctx.db.execute(stmt).all())


def _get_order(ctx: Ctx, order_id, lock=False) -> Order:
    o = ctx.get(Order, order_id, "Order", lock=lock)
    if ctx.role == "sales_rep" and o.created_by_id != ctx.user_id:
        raise AppError("NOT_FOUND", "Order not found.", 404)
    return o


@router.post("", status_code=201)
def create_order(body: OrderIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), ctx: Ctx = Depends(require("orders.create"))):
    payload = body.model_dump(mode="json")
    prior = idempotency_lookup(ctx.db, ctx.org_id, "orders.create", idempotency_key, payload)
    if prior:
        return {**order_detail(ctx, ctx.get(Order, prior.resource_id)), "idempotent_replay": True}
    order = svc.create_order(
        ctx,
        retailer_id=body.retailer_id,
        warehouse_id=body.warehouse_id or _default_warehouse(ctx),
        lines=_line_dicts(body.lines),
        order_date=body.order_date,
        requested_delivery_date=body.requested_delivery_date,
        notes=body.notes,
        idempotency_key=idempotency_key,
    )
    idempotency_store(ctx.db, ctx.org_id, "orders.create", idempotency_key, payload, "order", order.id)
    ctx.db.commit()
    return order_detail(ctx, order)


@router.get("/{order_id}")
def get_order(order_id: uuid.UUID, ctx: Ctx = Depends(require("orders.read"))):
    return order_detail(ctx, _get_order(ctx, order_id))


@router.patch("/{order_id}")
def update_order(order_id: uuid.UUID, body: OrderUpdate, ctx: Ctx = Depends(require("orders.create"))):
    o = _get_order(ctx, order_id, lock=True)
    if body.version is not None and body.version != o.version:
        raise AppError("CONCURRENT_MODIFICATION", "This order was changed by someone else. Reload and retry.", 409)
    svc.update_draft(
        ctx,
        o,
        lines=_line_dicts(body.lines) if body.lines is not None else None,
        retailer_id=body.retailer_id,
        warehouse_id=body.warehouse_id,
        requested_delivery_date=body.requested_delivery_date,
        notes=body.notes,
        fields_set=body.model_fields_set,
    )
    ctx.db.commit()
    return order_detail(ctx, o)


@router.post("/{order_id}/validate")
def validate_order(order_id: uuid.UUID, ctx: Ctx = Depends(require("orders.create"))):
    o = _get_order(ctx, order_id, lock=True)
    if o.status not in svc.EDITABLE:
        raise AppError("INVALID_STATUS", f"Only draft/validated orders can be re-validated (status '{o.status}').", 409)
    svc.validate(ctx, o)
    ctx.db.commit()
    return order_detail(ctx, o)


@router.post("/{order_id}/approve")
def approve_order(order_id: uuid.UUID, body: ApproveIn | None = None, ctx: Ctx = Depends(require("orders.approve"))):
    body = body or ApproveIn()
    o = ctx.get(Order, order_id, "Order", lock=True)
    _, changed = svc.approve(ctx, o, stock_override_reason=body.stock_override_reason, credit_override_reason=body.credit_override_reason)
    ctx.db.commit()
    return {**order_detail(ctx, o), "already_approved": not changed}


@router.post("/{order_id}/reject")
def reject_order(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.approve"))):
    o = ctx.get(Order, order_id, "Order", lock=True)
    svc.reject(ctx, o, body.reason)
    ctx.db.commit()
    return order_detail(ctx, o)


@router.post("/{order_id}/cancel")
def cancel_order(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.cancel"))):
    o = ctx.get(Order, order_id, "Order", lock=True)
    svc.cancel(ctx, o, body.reason)
    ctx.db.commit()
    return order_detail(ctx, o)


@router.post("/{order_id}/amend")
def amend_order(order_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("orders.approve"))):
    o = ctx.get(Order, order_id, "Order", lock=True)
    svc.amend(ctx, o, body.reason)
    ctx.db.commit()
    return order_detail(ctx, o)


@router.get("/{order_id}/audit")
def order_audit(order_id: uuid.UUID, ctx: Ctx = Depends(require("orders.read"))):
    o = _get_order(ctx, order_id)
    from app.models import AuditEvent

    rows = ctx.db.scalars(select(AuditEvent).where(AuditEvent.organization_id == ctx.org_id, AuditEvent.entity_type == "order", AuditEvent.entity_id == str(o.id)).order_by(AuditEvent.created_at)).all()
    return [{"action": e.action, "at": e.created_at.isoformat(), "before": e.before, "after": e.after, "meta": e.meta} for e in rows]


_ = audit
