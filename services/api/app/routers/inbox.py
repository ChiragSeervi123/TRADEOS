import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import audit
from app.common import PageParams, page_out
from app.deps import Ctx, require
from app.errors import AppError
from app.extraction.providers import provider_status
from app.models import Document, Extraction, Order, OrderSource, User
from app.services import extraction as svc
from app.services import jobs
from app.services.storage import signed_url, validate_upload

router = APIRouter(prefix="/order-extractions", tags=["order inbox"])


def source_summary(ctx: Ctx, s: OrderSource, ext: Extraction | None) -> dict:
    res = (ext.result or {}) if ext else {}
    return {
        "id": str(s.id),
        "channel": s.channel,
        "status": s.status,
        "sender": s.sender,
        "preview": (s.raw_text or (s.meta or {}).get("filename") or "")[:140],
        "received_at": s.created_at.isoformat(),
        "order_id": str(s.order_id) if s.order_id else None,
        "provider": ext.provider if ext else None,
        "overall_confidence": str(ext.overall_confidence) if ext and ext.overall_confidence is not None else None,
        "summary": res.get("summary"),
        "error": ext.error if ext else None,
        "possible_duplicate_of": (s.meta or {}).get("possible_duplicate_of"),
        "is_synthetic": s.is_synthetic,
    }


def source_detail(ctx: Ctx, s: OrderSource) -> dict:
    ext = ctx.db.get(Extraction, s.latest_extraction_id) if s.latest_extraction_id else None
    out = source_summary(ctx, s, ext)
    doc = ctx.db.get(Document, s.document_id) if s.document_id else None
    order = ctx.db.get(Order, s.order_id) if s.order_id else None
    history = ctx.db.scalars(select(Extraction).where(Extraction.source_type == "order_source", Extraction.source_id == str(s.id)).order_by(Extraction.created_at)).all()
    creator = ctx.db.get(User, s.created_by_id) if s.created_by_id else None
    out.update(
        {
            "raw_text": s.raw_text,
            "document": {"id": str(doc.id), "filename": doc.filename, "content_type": doc.content_type, "size_bytes": doc.size_bytes, "text_content": doc.text_content, **signed_url(doc.id)} if doc else None,
            "extraction": {
                "id": str(ext.id),
                "provider": ext.provider,
                "model": ext.model,
                "schema_version": ext.schema_version,
                "status": ext.status,
                "error": ext.error,
                "latency_ms": ext.latency_ms,
                "input_tokens": ext.input_tokens,
                "output_tokens": ext.output_tokens,
                "result": ext.result,
                "raw_output": ext.raw_output,
                "warnings": ext.warnings,
                "created_at": ext.created_at.isoformat(),
            }
            if ext
            else None,
            "order": {"id": str(order.id), "order_number": order.order_number, "status": order.status} if order else None,
            "history": [{"id": str(e.id), "provider": e.provider, "status": e.status, "at": e.created_at.isoformat()} for e in history],
            "created_by": creator.full_name if creator else None,
            "meta": s.meta,
        }
    )
    return out


@router.get("/provider-status")
def get_provider_status(ctx: Ctx = Depends(require("orders.read"))):
    return provider_status(ctx.org)


@router.get("")
def list_sources(params: PageParams = Depends(), status: str | None = None, ctx: Ctx = Depends(require("orders.read"))):
    stmt = select(OrderSource).where(OrderSource.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(OrderSource.status.in_(status.split(",")))
    if ctx.role == "sales_rep":
        stmt = stmt.where(OrderSource.created_by_id == ctx.user_id)
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(OrderSource.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    exts = {e.id: e for e in ctx.db.scalars(select(Extraction).where(Extraction.id.in_([s.latest_extraction_id for s in rows if s.latest_extraction_id]))).all()}
    counts = dict(ctx.db.execute(select(OrderSource.status, func.count()).where(OrderSource.organization_id == ctx.org_id).group_by(OrderSource.status)).all())
    out = page_out([source_summary(ctx, s, exts.get(s.latest_extraction_id)) for s in rows], total, params)
    out["status_counts"] = counts
    return out


def _get(ctx: Ctx, source_id, lock=False) -> OrderSource:
    s = ctx.get(OrderSource, source_id, "Inbox item", lock=lock)
    if ctx.role == "sales_rep" and s.created_by_id != ctx.user_id:
        raise AppError("NOT_FOUND", "Inbox item not found.", 404)
    return s


class TextIn(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    sender: str | None = Field(default=None, max_length=200)


@router.post("/text", status_code=201)
def submit_text(body: TextIn, ctx: Ctx = Depends(require("orders.create"))):
    src, job_ids = svc.ingest_text(ctx, body.text, body.sender)
    audit.record(ctx, "inbox.receive", "order_source", src.id, meta={"channel": "text"})
    ctx.db.commit()
    jobs.run_pending_inline(job_ids)
    ctx.db.expire_all()
    return source_detail(ctx, _get(ctx, src.id))


@router.post("/upload", status_code=201)
async def submit_file(file: UploadFile = File(...), sender: str | None = Form(default=None), ctx: Ctx = Depends(require("orders.create"))):
    content = await file.read()
    ct = validate_upload(file.filename or "", file.content_type, content)
    if ct.startswith("text/"):
        src, job_ids = svc.ingest_text(ctx, content.decode("utf-8", errors="replace"), sender)
    else:
        doc = svc.store_document(ctx, file.filename or "upload", ct, content, "order")
        src, job_ids = svc.ingest_document(ctx, doc, "pdf" if ct == "application/pdf" else "image")
        src.sender = sender
    audit.record(ctx, "inbox.receive", "order_source", src.id, meta={"channel": src.channel, "filename": file.filename})
    ctx.db.commit()
    jobs.run_pending_inline(job_ids)
    ctx.db.expire_all()
    return source_detail(ctx, _get(ctx, src.id))


@router.get("/{source_id}")
def get_source(source_id: uuid.UUID, ctx: Ctx = Depends(require("orders.read"))):
    return source_detail(ctx, _get(ctx, source_id))


@router.post("/{source_id}/reextract")
def reextract(source_id: uuid.UUID, ctx: Ctx = Depends(require("orders.create"))):
    s = _get(ctx, source_id, lock=True)
    job_ids = svc.reextract(ctx, s)
    audit.record(ctx, "inbox.reextract", "order_source", s.id)
    ctx.db.commit()
    jobs.run_pending_inline(job_ids)
    ctx.db.expire_all()
    return source_detail(ctx, _get(ctx, source_id))


class ConvertLine(BaseModel):
    index: int | None = Field(default=None, description="Extraction line index; null for a manually added line")
    product_id: uuid.UUID | None = None
    quantity: Decimal = Field(gt=0)
    unit: str | None = None
    include: bool = True
    confirm: bool = Field(default=False, description="Required for lines the extraction marked ambiguous/unmatched")
    source_description: str | None = None


class ConvertIn(BaseModel):
    retailer_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    requested_delivery_date: date | None = None
    notes: str | None = Field(default=None, max_length=2000)
    lines: list[ConvertLine] = Field(min_length=1, max_length=300)


@router.post("/{source_id}/convert", status_code=201)
def convert(source_id: uuid.UUID, body: ConvertIn, ctx: Ctx = Depends(require("orders.create"))):
    from app.routers.orders import _default_warehouse, order_detail

    s = _get(ctx, source_id, lock=True)
    order = svc.convert_to_order(
        ctx,
        s,
        retailer_id=body.retailer_id,
        warehouse_id=body.warehouse_id or _default_warehouse(ctx),
        lines=[l.model_dump() for l in body.lines],
        requested_delivery_date=body.requested_delivery_date,
        notes=body.notes,
    )
    ctx.db.commit()
    return order_detail(ctx, order)


class DiscardIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/{source_id}/discard")
def discard(source_id: uuid.UUID, body: DiscardIn, ctx: Ctx = Depends(require("orders.create"))):
    s = _get(ctx, source_id, lock=True)
    if s.status == "converted":
        raise AppError("INVALID_STATUS", "Converted items cannot be discarded; cancel the order instead.", 409)
    s.status = "discarded"
    s.meta = {**(s.meta or {}), "discard_reason": body.reason}
    audit.record(ctx, "inbox.discard", "order_source", s.id, meta={"reason": body.reason})
    ctx.db.commit()
    return source_detail(ctx, s)
