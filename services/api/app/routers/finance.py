import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Header, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app import audit
from app.common import PageParams, commit_or_conflict, page_out
from app.deps import Ctx, require
from app.errors import AppError, conflict
from app.models import (
    CreditNote,
    Document,
    Extraction,
    Payment,
    PaymentAllocation,
    Product,
    PurchaseInvoice,
    Retailer,
    SalesInvoice,
    SalesInvoiceLine,
    Supplier,
)
from app.money import money
from app.services import finance as svc
from app.services import jobs, receivables
from app.services.reconciliation import rule_grn_and_quantity, rule_price
from app.services.storage import signed_url, validate_upload

router = APIRouter(tags=["finance"])


# ----------------------------------------------------------------- purchase invoices


class PILineIn(BaseModel):
    product_id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(ge=0)
    unit_price: Decimal = Field(ge=0)
    tax_rate: Decimal = Field(default=Decimal(0), ge=0, le=100)
    tax_amount: Decimal = Field(default=Decimal(0), ge=0)
    line_total: Decimal | None = None


class PIIn(BaseModel):
    supplier_id: uuid.UUID | None = None
    invoice_number: str = Field(min_length=1, max_length=80)
    invoice_date: date | None = None
    due_date: date | None = None
    subtotal: Decimal | None = None
    tax_total: Decimal = Decimal(0)
    other_charges: Decimal = Decimal(0)
    total: Decimal | None = None
    lines: list[PILineIn] = Field(default_factory=list, max_length=500)


class PIUpdate(BaseModel):
    supplier_id: uuid.UUID | None = None
    invoice_number: str | None = Field(default=None, max_length=80)
    invoice_date: date | None = None
    due_date: date | None = None
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    other_charges: Decimal | None = None
    total: Decimal | None = None
    lines: list[PILineIn] | None = None


class VerifyIn(BaseModel):
    accept_differences_reason: str | None = Field(default=None, min_length=5, max_length=500)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def pi_out(ctx: Ctx, pi: PurchaseInvoice, detail=False) -> dict:
    sup = ctx.db.get(Supplier, pi.supplier_id) if pi.supplier_id else None
    out = {
        "id": str(pi.id),
        "supplier_id": str(pi.supplier_id) if pi.supplier_id else None,
        "supplier": sup.name if sup else None,
        "invoice_number": pi.invoice_number,
        "invoice_date": pi.invoice_date.isoformat() if pi.invoice_date else None,
        "due_date": pi.due_date.isoformat() if pi.due_date else None,
        "currency": pi.currency,
        "subtotal": str(pi.subtotal),
        "tax_total": str(pi.tax_total),
        "other_charges": str(pi.other_charges),
        "total": str(pi.total),
        "status": pi.status,
        "issue_count": (pi.validation or {}).get("errors", 0),
        "has_document": bool(pi.document_id),
        "verified_at": pi.verified_at.isoformat() if pi.verified_at else None,
        "created_at": pi.created_at.isoformat(),
        "is_synthetic": pi.is_synthetic,
    }
    if detail:
        products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.id.in_([l.product_id for l in pi.lines if l.product_id]))).all()}
        out["lines"] = [
            {
                "line_no": l.line_no,
                "product_id": str(l.product_id) if l.product_id else None,
                "sku": products[l.product_id].sku if l.product_id in products else None,
                "agreed_price": str(products[l.product_id].cost_price) if l.product_id in products and products[l.product_id].cost_price is not None else None,
                "description": l.description,
                "quantity": str(l.quantity),
                "unit_price": str(l.unit_price),
                "tax_rate": str(l.tax_rate),
                "tax_amount": str(l.tax_amount),
                "line_total": str(l.line_total),
                "match_confidence": str(l.match_confidence) if l.match_confidence is not None else None,
            }
            for l in pi.lines
        ]
        out["validation"] = pi.validation
        doc = ctx.db.get(Document, pi.document_id) if pi.document_id else None
        out["document"] = {"id": str(doc.id), "filename": doc.filename, "content_type": doc.content_type, "text_content": doc.text_content, **signed_url(doc.id)} if doc else None
        ext = ctx.db.get(Extraction, pi.extraction_id) if pi.extraction_id else None
        out["extraction"] = {"id": str(ext.id), "provider": ext.provider, "model": ext.model, "status": ext.status, "error": ext.error, "raw_output": ext.raw_output, "latency_ms": ext.latency_ms} if ext else None
        from app.models import ReconciliationCase

        cases = ctx.db.scalars(select(ReconciliationCase).where(ReconciliationCase.organization_id == ctx.org_id)).all()
        out["cases"] = [{"id": str(c.id), "case_number": c.case_number, "category": c.category, "status": c.status, "difference": str(c.difference) if c.difference is not None else None} for c in cases if any(r.get("id") == str(pi.id) for r in (c.records or []))]
    return out


@router.get("/purchase-invoices")
def list_pis(params: PageParams = Depends(), status: str | None = None, supplier_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("finance.read"))):
    stmt = select(PurchaseInvoice).where(PurchaseInvoice.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(PurchaseInvoice.status.in_(status.split(",")))
    if supplier_id:
        stmt = stmt.where(PurchaseInvoice.supplier_id == supplier_id)
    if params.q:
        stmt = stmt.where(PurchaseInvoice.invoice_number.ilike(f"%{params.q}%"))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(PurchaseInvoice.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return page_out([pi_out(ctx, p) for p in rows], total, params)


def _pi_lines(ctx, lines: list[PILineIn]) -> list[dict]:
    out = []
    for l in lines:
        if l.product_id:
            ctx.get(Product, l.product_id, "Product")
        out.append(l.model_dump())
    return out


@router.post("/purchase-invoices", status_code=201)
def create_pi(body: PIIn, ctx: Ctx = Depends(require("finance.write"))):
    if body.supplier_id:
        ctx.get(Supplier, body.supplier_id, "Supplier")
    if ctx.db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.organization_id == ctx.org_id, PurchaseInvoice.supplier_id == body.supplier_id, PurchaseInvoice.invoice_number == body.invoice_number.strip())).first():
        raise conflict("DUPLICATE_INVOICE", "This supplier invoice number already exists.")
    pi = PurchaseInvoice(organization_id=ctx.org_id, supplier_id=body.supplier_id, invoice_number=body.invoice_number.strip(), invoice_date=body.invoice_date, due_date=body.due_date, currency=ctx.org.currency, tax_total=money(body.tax_total), other_charges=money(body.other_charges), status="needs_review", created_by_id=ctx.user_id)
    ctx.db.add(pi)
    ctx.db.flush()
    svc.set_invoice_lines(ctx.db, ctx.org_id, pi, _pi_lines(ctx, body.lines))
    pi.subtotal = money(body.subtotal) if body.subtotal is not None else money(sum((l.line_total for l in pi.lines), Decimal(0)))
    pi.total = money(body.total) if body.total is not None else money(pi.subtotal + pi.tax_total + pi.other_charges)
    pi.validation = svc.validate_invoice(ctx.org, pi)
    audit.record(ctx, "purchase_invoice.create", "purchase_invoice", pi.id, after={"invoice_number": pi.invoice_number, "total": str(pi.total), "source": "manual"})
    commit_or_conflict(ctx.db, "DUPLICATE_INVOICE", "This supplier invoice number already exists.")
    return pi_out(ctx, pi, detail=True)


@router.post("/purchase-invoices/upload", status_code=201)
async def upload_pi(file: UploadFile = File(...), ctx: Ctx = Depends(require("finance.write"))):
    from app.services.extraction import store_document

    content = await file.read()
    ct = validate_upload(file.filename or "", file.content_type, content)
    doc = store_document(ctx, file.filename or "invoice", ct, content, "purchase_invoice")
    pi = PurchaseInvoice(organization_id=ctx.org_id, invoice_number=f"PENDING-{doc.id.hex[:8]}", currency=ctx.org.currency, status="needs_review", document_id=doc.id, created_by_id=ctx.user_id)
    ctx.db.add(pi)
    ctx.db.flush()
    job = jobs.enqueue(ctx.db, "extract_document", {"document_id": str(doc.id), "kind": "purchase_invoice", "target_id": str(pi.id)}, ctx.org_id, idempotency_key=f"extract_doc:{doc.id}")
    audit.record(ctx, "purchase_invoice.upload", "purchase_invoice", pi.id, meta={"filename": doc.filename, "sha256": doc.sha256})
    ctx.db.commit()
    jobs.run_pending_inline([job.id])
    ctx.db.expire_all()
    return pi_out(ctx, ctx.get(PurchaseInvoice, pi.id), detail=True)


@router.get("/purchase-invoices/{pi_id}")
def get_pi(pi_id: uuid.UUID, ctx: Ctx = Depends(require("finance.read"))):
    return pi_out(ctx, ctx.get(PurchaseInvoice, pi_id, "Purchase invoice"), detail=True)


@router.patch("/purchase-invoices/{pi_id}")
def update_pi(pi_id: uuid.UUID, body: PIUpdate, ctx: Ctx = Depends(require("finance.write"))):
    pi = ctx.get(PurchaseInvoice, pi_id, "Purchase invoice", lock=True)
    if pi.status != "needs_review":
        raise conflict("INVALID_STATUS", "Only invoices awaiting review can be corrected.")
    before = pi_out(ctx, pi, detail=True)
    data = body.model_dump(exclude_unset=True)
    if data.get("supplier_id"):
        ctx.get(Supplier, data["supplier_id"], "Supplier")
    lines = data.pop("lines", None)
    for k, v in data.items():
        setattr(pi, k, money(v) if k in ("subtotal", "tax_total", "other_charges", "total") and v is not None else v)
    if lines is not None:
        svc.set_invoice_lines(ctx.db, ctx.org_id, pi, _pi_lines(ctx, body.lines))
    pi.validation = {**svc.validate_invoice(ctx.org, pi), "extraction_notes": (pi.validation or {}).get("extraction_notes", [])}
    after = pi_out(ctx, pi, detail=True)
    b, a = audit.diff({k: before[k] for k in ("supplier_id", "invoice_number", "invoice_date", "subtotal", "tax_total", "total", "lines")}, {k: after[k] for k in ("supplier_id", "invoice_number", "invoice_date", "subtotal", "tax_total", "total", "lines")})
    audit.record(ctx, "purchase_invoice.correct", "purchase_invoice", pi.id, before=b, after=a)
    commit_or_conflict(ctx.db, "DUPLICATE_INVOICE", "This supplier invoice number already exists.")
    return pi_out(ctx, pi, detail=True)


@router.post("/purchase-invoices/{pi_id}/verify")
def verify_pi(pi_id: uuid.UUID, body: VerifyIn | None = None, ctx: Ctx = Depends(require("finance.approve"))):
    pi = ctx.get(PurchaseInvoice, pi_id, "Purchase invoice", lock=True)
    svc.verify_invoice(ctx, pi, body.accept_differences_reason if body else None)
    from datetime import date as _d

    rule_grn_and_quantity(ctx.db, ctx.org, pi, _d.today(), ctx.user_id)
    rule_price(ctx.db, ctx.org, pi, ctx.user_id)
    ctx.db.commit()
    return pi_out(ctx, pi, detail=True)


@router.post("/purchase-invoices/{pi_id}/reject")
def reject_pi(pi_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("finance.approve"))):
    pi = ctx.get(PurchaseInvoice, pi_id, "Purchase invoice", lock=True)
    if pi.status == "verified":
        raise conflict("INVALID_STATUS", "Verified invoices cannot be rejected; open a reconciliation case instead.")
    pi.status = "rejected"
    pi.validation = {**(pi.validation or {}), "rejected_reason": body.reason}
    audit.record(ctx, "purchase_invoice.reject", "purchase_invoice", pi.id, meta={"reason": body.reason})
    ctx.db.commit()
    return pi_out(ctx, pi, detail=True)


# --------------------------------------------------------------------- sales invoices


def si_out(ctx: Ctx, inv: SalesInvoice, outstanding, retailers: dict, detail=False) -> dict:
    r = retailers.get(inv.retailer_id)
    today = date.today()
    out = {
        "id": str(inv.id),
        "invoice_number": inv.invoice_number,
        "retailer_id": str(inv.retailer_id),
        "retailer": r.name if r else None,
        "order_id": str(inv.order_id) if inv.order_id else None,
        "invoice_date": inv.invoice_date.isoformat(),
        "due_date": inv.due_date.isoformat(),
        "gross_amount": str(inv.gross_amount),
        "discount_amount": str(inv.discount_amount),
        "tax_amount": str(inv.tax_amount),
        "total": str(inv.total),
        "outstanding": str(outstanding),
        "status": inv.status,
        "days_overdue": max(0, (today - inv.due_date).days) if outstanding > 0 else 0,
        "is_synthetic": inv.is_synthetic,
    }
    if detail:
        lines = ctx.db.scalars(select(SalesInvoiceLine).where(SalesInvoiceLine.sales_invoice_id == inv.id).order_by(SalesInvoiceLine.line_no)).all()
        products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.id.in_([l.product_id for l in lines]))).all()}
        out["lines"] = [{"sku": products[l.product_id].sku, "name": products[l.product_id].name, "quantity": str(l.quantity), "unit_price": str(l.unit_price), "gross_amount": str(l.gross_amount), "discount_amount": str(l.discount_amount), "tax_amount": str(l.tax_amount), "line_total": str(l.line_total), "is_free_goods": l.is_free_goods} for l in lines]
        allocs = ctx.db.scalars(select(PaymentAllocation).where(PaymentAllocation.sales_invoice_id == inv.id).order_by(PaymentAllocation.created_at)).all()
        pays = {p.id: p for p in ctx.db.scalars(select(Payment).where(Payment.id.in_([a.payment_id for a in allocs]))).all()} if allocs else {}
        out["allocations"] = [{"id": str(a.id), "payment_id": str(a.payment_id), "payment_number": pays[a.payment_id].payment_number, "amount": str(a.amount), "created_at": a.created_at.isoformat(), "reversed": a.reversed_at is not None, "reversal_reason": a.reversal_reason} for a in allocs]
        notes = ctx.db.scalars(select(CreditNote).where(CreditNote.sales_invoice_id == inv.id)).all()
        out["credit_notes"] = [note_out(n) for n in notes]
    return out


@router.get("/sales-invoices")
def list_sis(params: PageParams = Depends(), status: str | None = None, retailer_id: uuid.UUID | None = None, overdue: bool | None = None, ctx: Ctx = Depends(require("finance.read"))):
    stmt = select(SalesInvoice).where(SalesInvoice.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(SalesInvoice.status.in_(status.split(",")))
    if retailer_id:
        stmt = stmt.where(SalesInvoice.retailer_id == retailer_id)
    if overdue:
        stmt = stmt.where(SalesInvoice.due_date < date.today(), SalesInvoice.status.in_(("open", "partially_paid")))
    if params.q:
        rids = select(Retailer.id).where(Retailer.organization_id == ctx.org_id, Retailer.name.ilike(f"%{params.q}%"))
        stmt = stmt.where(or_(SalesInvoice.invoice_number.ilike(f"%{params.q}%"), SalesInvoice.retailer_id.in_(rids)))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(SalesInvoice.invoice_date.desc(), SalesInvoice.invoice_number.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    outs = receivables.outstanding_map(ctx.db, rows)
    retailers = {r.id: r for r in ctx.db.scalars(select(Retailer).where(Retailer.id.in_({i.retailer_id for i in rows}))).all()}
    return page_out([si_out(ctx, i, outs[i.id], retailers) for i in rows], total, params)


@router.get("/sales-invoices/{invoice_id}")
def get_si(invoice_id: uuid.UUID, ctx: Ctx = Depends(require("finance.read"))):
    inv = ctx.get(SalesInvoice, invoice_id, "Sales invoice")
    r = ctx.db.get(Retailer, inv.retailer_id)
    return si_out(ctx, inv, receivables.invoice_outstanding(ctx.db, inv), {r.id: r}, detail=True)


# ----------------------------------------------------------------------- credit notes


class NoteIn(BaseModel):
    direction: Literal["issued", "received"]
    note_type: Literal["credit", "debit"] = "credit"
    note_number: str | None = Field(default=None, max_length=80)
    retailer_id: uuid.UUID | None = None
    supplier_id: uuid.UUID | None = None
    note_date: date | None = None
    amount: Decimal | None = Field(default=None, gt=0)
    reason: Literal["return", "scheme", "price_adjustment", "quantity_adjustment", "other"] = "other"
    description: str | None = Field(default=None, max_length=1000)
    return_id: uuid.UUID | None = None
    sales_invoice_id: uuid.UUID | None = None
    purchase_invoice_id: uuid.UUID | None = None
    scheme_claim_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None


def note_out(n: CreditNote) -> dict:
    return {
        "id": str(n.id),
        "note_type": n.note_type,
        "direction": n.direction,
        "note_number": n.note_number,
        "retailer_id": str(n.retailer_id) if n.retailer_id else None,
        "supplier_id": str(n.supplier_id) if n.supplier_id else None,
        "note_date": n.note_date.isoformat(),
        "amount": str(n.amount),
        "reason": n.reason,
        "description": n.description,
        "status": n.status,
        "return_id": str(n.return_id) if n.return_id else None,
        "sales_invoice_id": str(n.sales_invoice_id) if n.sales_invoice_id else None,
        "purchase_invoice_id": str(n.purchase_invoice_id) if n.purchase_invoice_id else None,
        "scheme_claim_id": str(n.scheme_claim_id) if n.scheme_claim_id else None,
        "is_synthetic": n.is_synthetic,
    }


@router.get("/credit-notes")
def list_notes(params: PageParams = Depends(), direction: str | None = None, status: str | None = None, ctx: Ctx = Depends(require("finance.read"))):
    stmt = select(CreditNote).where(CreditNote.organization_id == ctx.org_id)
    if direction:
        stmt = stmt.where(CreditNote.direction == direction)
    if status:
        stmt = stmt.where(CreditNote.status.in_(status.split(",")))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(CreditNote.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return page_out([note_out(n) for n in rows], total, params)


@router.post("/credit-notes", status_code=201)
def create_note(body: NoteIn, ctx: Ctx = Depends(require("finance.write"))):
    data = body.model_dump()
    data["note_date"] = data["note_date"] or date.today()
    n = svc.create_note(ctx, data)
    commit_or_conflict(ctx.db, "DUPLICATE_NOTE", "A note with this number already exists.")
    return note_out(n)


@router.post("/credit-notes/{note_id}/issue")
def issue_note(note_id: uuid.UUID, ctx: Ctx = Depends(require("finance.approve"))):
    n = ctx.get(CreditNote, note_id, "Credit note", lock=True)
    svc.issue_note(ctx, n)
    ctx.db.commit()
    return note_out(n)


class ApplyIn(BaseModel):
    sales_invoice_id: uuid.UUID


@router.post("/credit-notes/{note_id}/apply")
def apply_note(note_id: uuid.UUID, body: ApplyIn, ctx: Ctx = Depends(require("payments.allocate"))):
    n = ctx.get(CreditNote, note_id, "Credit note", lock=True)
    inv = ctx.get(SalesInvoice, body.sales_invoice_id, "Sales invoice", lock=True)
    svc.apply_note(ctx, n, inv)
    ctx.db.commit()
    return note_out(n)


@router.post("/credit-notes/{note_id}/void")
def void_note(note_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("finance.approve"))):
    n = ctx.get(CreditNote, note_id, "Credit note", lock=True)
    svc.void_note(ctx, n, body.reason)
    ctx.db.commit()
    return note_out(n)


# --------------------------------------------------------------------------- payments


class PaymentIn(BaseModel):
    retailer_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    received_on: date
    method: Literal["cash", "upi", "cheque", "bank_transfer", "other"] = "bank_transfer"
    reference: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=1000)


class AllocIn(BaseModel):
    sales_invoice_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class AllocsIn(BaseModel):
    allocations: list[AllocIn] = Field(min_length=1, max_length=200)


def payment_out(ctx: Ctx, p: Payment, retailers: dict, detail=False) -> dict:
    r = retailers.get(p.retailer_id)
    out = {
        "id": str(p.id),
        "payment_number": p.payment_number,
        "retailer_id": str(p.retailer_id),
        "retailer": r.name if r else None,
        "amount": str(p.amount),
        "unallocated": str(receivables.payment_unallocated(ctx.db, p)),
        "received_on": p.received_on.isoformat(),
        "method": p.method,
        "reference": p.reference,
        "status": p.status,
        "is_synthetic": p.is_synthetic,
    }
    if detail:
        allocs = ctx.db.scalars(select(PaymentAllocation).where(PaymentAllocation.payment_id == p.id).order_by(PaymentAllocation.created_at)).all()
        invs = {i.id: i for i in ctx.db.scalars(select(SalesInvoice).where(SalesInvoice.id.in_([a.sales_invoice_id for a in allocs]))).all()} if allocs else {}
        out["allocations"] = [{"id": str(a.id), "sales_invoice_id": str(a.sales_invoice_id), "invoice_number": invs[a.sales_invoice_id].invoice_number, "amount": str(a.amount), "created_at": a.created_at.isoformat(), "reversed": a.reversed_at is not None, "reversal_reason": a.reversal_reason} for a in allocs]
        out["suggestions"] = receivables.suggest_allocations(ctx.db, ctx.org_id, p) if p.status == "recorded" else []
        out["notes"] = p.notes
    return out


@router.get("/payments")
def list_payments(params: PageParams = Depends(), retailer_id: uuid.UUID | None = None, unallocated: bool | None = None, ctx: Ctx = Depends(require("payments.read"))):
    stmt = select(Payment).where(Payment.organization_id == ctx.org_id)
    if retailer_id:
        stmt = stmt.where(Payment.retailer_id == retailer_id)
    if params.q:
        stmt = stmt.where(or_(Payment.payment_number.ilike(f"%{params.q}%"), Payment.reference.ilike(f"%{params.q}%")))
    rows = ctx.db.scalars(stmt.order_by(Payment.received_on.desc(), Payment.payment_number.desc())).all()
    retailers = {r.id: r for r in ctx.db.scalars(select(Retailer).where(Retailer.id.in_({p.retailer_id for p in rows}))).all()}
    items = [payment_out(ctx, p, retailers) for p in rows]
    if unallocated:
        items = [i for i in items if Decimal(i["unallocated"]) > 0 and i["status"] == "recorded"]
    start = (params.page - 1) * params.page_size
    return page_out(items[start : start + params.page_size], len(items), params)


@router.post("/payments", status_code=201)
def record_payment(body: PaymentIn, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), ctx: Ctx = Depends(require("payments.allocate"))):
    p, created = svc.record_payment(ctx, body.model_dump(), idempotency_key)
    ctx.db.commit()
    r = ctx.db.get(Retailer, p.retailer_id)
    return {**payment_out(ctx, p, {r.id: r}, detail=True), "idempotent_replay": not created}


@router.get("/payments/{payment_id}")
def get_payment(payment_id: uuid.UUID, ctx: Ctx = Depends(require("payments.read"))):
    p = ctx.get(Payment, payment_id, "Payment")
    r = ctx.db.get(Retailer, p.retailer_id)
    return payment_out(ctx, p, {r.id: r}, detail=True)


@router.post("/payments/{payment_id}/allocations", status_code=201)
def allocate(payment_id: uuid.UUID, body: AllocsIn, ctx: Ctx = Depends(require("payments.allocate"))):
    p = ctx.get(Payment, payment_id, "Payment", lock=True)
    svc.allocate_many(ctx, p, [a.model_dump() for a in body.allocations])
    ctx.db.commit()
    r = ctx.db.get(Retailer, p.retailer_id)
    return payment_out(ctx, p, {r.id: r}, detail=True)


@router.post("/payments/{payment_id}/allocations/{allocation_id}/reverse")
def reverse(payment_id: uuid.UUID, allocation_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("payments.allocate"))):
    p = ctx.get(Payment, payment_id, "Payment", lock=True)
    a = ctx.get(PaymentAllocation, allocation_id, "Allocation", lock=True)
    svc.reverse_allocation(ctx, p, a, body.reason)
    ctx.db.commit()
    r = ctx.db.get(Retailer, p.retailer_id)
    return payment_out(ctx, p, {r.id: r}, detail=True)


@router.post("/payments/{payment_id}/void")
def void_payment(payment_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("finance.approve"))):
    p = ctx.get(Payment, payment_id, "Payment", lock=True)
    if ctx.db.scalars(select(PaymentAllocation).where(PaymentAllocation.payment_id == p.id, PaymentAllocation.reversed_at.is_(None))).first():
        raise conflict("HAS_ALLOCATIONS", "Reverse the payment's allocations before voiding it.")
    p.status = "void"
    audit.record(ctx, "payment.void", "payment", p.id, meta={"reason": body.reason})
    ctx.db.commit()
    r = ctx.db.get(Retailer, p.retailer_id)
    return payment_out(ctx, p, {r.id: r}, detail=True)


# ------------------------------------------------------------------------ receivables


@router.get("/receivables/ageing")
def ageing(as_of: date | None = None, retailer_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("finance.read"))):
    data = receivables.ageing(ctx.db, ctx.org, as_of or date.today(), retailer_id)
    retailers = {str(r.id): r for r in ctx.db.scalars(select(Retailer).where(Retailer.organization_id == ctx.org_id)).all()}
    rows = []
    for rid, buckets in data.pop("by_retailer").items():
        r = retailers.get(rid)
        total = sum(Decimal(v) for v in buckets.values())
        rows.append({"retailer_id": rid, "retailer": r.name if r else None, "code": r.code if r else None, "credit_limit": str(r.credit_limit) if r and r.credit_limit is not None else None, "buckets": buckets, "total": str(money(total))})
    rows.sort(key=lambda x: -Decimal(x["total"]))
    data["retailers"] = rows
    return data


@router.get("/receivables/retailers/{retailer_id}")
def retailer_statement(retailer_id: uuid.UUID, ctx: Ctx = Depends(require("finance.read"))):
    r = ctx.get(Retailer, retailer_id, "Retailer")
    invs = ctx.db.scalars(select(SalesInvoice).where(SalesInvoice.organization_id == ctx.org_id, SalesInvoice.retailer_id == r.id).order_by(SalesInvoice.invoice_date)).all()
    outs = receivables.outstanding_map(ctx.db, invs)
    pays = ctx.db.scalars(select(Payment).where(Payment.organization_id == ctx.org_id, Payment.retailer_id == r.id).order_by(Payment.received_on)).all()
    return {
        "retailer": {"id": str(r.id), "code": r.code, "name": r.name},
        "credit": receivables.credit_exposure(ctx.db, ctx.org, r),
        "invoices": [si_out(ctx, i, outs[i.id], {r.id: r}) for i in invs],
        "payments": [payment_out(ctx, p, {r.id: r}) for p in pays],
    }
