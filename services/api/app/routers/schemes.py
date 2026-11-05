import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import audit
from app.common import PageParams, commit_or_conflict, page_out
from app.deps import Ctx, require
from app.errors import AppError, conflict
from app.models import Product, Retailer, Scheme, SchemeClaim, SchemeVersion, Supplier, User
from app.money import money, qty
from app.services import jobs, pricing, scheme_engine
from app.services import schemes as svc
from app.services.storage import validate_upload

router = APIRouter(prefix="/schemes", tags=["schemes"])


class VersionIn(BaseModel):
    scheme_type: Literal["percentage", "fixed_amount", "buy_x_get_y", "quantity_slab", "value_slab", "combination"]
    config: dict[str, Any]
    scope: dict[str, Any] = Field(default_factory=dict)
    eligibility: dict[str, Any] = Field(default_factory=dict)
    start_date: date
    end_date: date
    stacking: Literal["exclusive", "stackable"] = "exclusive"
    priority: int = Field(default=100, ge=0, le=10000)
    max_benefit: Decimal | None = Field(default=None, gt=0)
    source_reference: str | None = Field(default=None, max_length=300)
    change_note: str | None = Field(default=None, max_length=2000)


class SchemeIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    supplier_id: uuid.UUID | None = None
    funded_by: Literal["supplier", "distributor"] = "supplier"
    version: VersionIn


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def version_out(v: SchemeVersion, users: dict | None = None) -> dict:
    users = users or {}
    return {
        "id": str(v.id),
        "version": v.version,
        "status": v.status,
        "scheme_type": v.scheme_type,
        "config": v.config,
        "scope": v.scope,
        "eligibility": v.eligibility,
        "start_date": v.start_date.isoformat(),
        "end_date": v.end_date.isoformat(),
        "stacking": v.stacking,
        "priority": v.priority,
        "max_benefit": str(v.max_benefit) if v.max_benefit is not None else None,
        "source_reference": v.source_reference,
        "source_document_id": str(v.source_document_id) if v.source_document_id else None,
        "extraction_id": str(v.extraction_id) if v.extraction_id else None,
        "change_note": v.change_note,
        "created_by": users.get(v.created_by_id),
        "submitted_by": users.get(v.submitted_by_id),
        "approved_by": users.get(v.approved_by_id),
        "approved_at": v.approved_at.isoformat() if v.approved_at else None,
        "created_at": v.created_at.isoformat(),
    }


def scheme_out(ctx: Ctx, s: Scheme, detail=False) -> dict:
    versions = ctx.db.scalars(select(SchemeVersion).where(SchemeVersion.scheme_id == s.id).order_by(SchemeVersion.version)).all()
    latest = versions[-1] if versions else None
    active = next((v for v in versions if v.id == s.active_version_id), None)
    shown = active or latest
    sup = ctx.db.get(Supplier, s.supplier_id) if s.supplier_id else None
    out = {
        "id": str(s.id),
        "code": s.code,
        "name": s.name,
        "status": s.status,
        "funded_by": s.funded_by,
        "supplier": sup.name if sup else None,
        "supplier_id": str(s.supplier_id) if s.supplier_id else None,
        "active_version": active.version if active else None,
        "latest_version": latest.version if latest else None,
        "latest_status": latest.status if latest else None,
        "scheme_type": shown.scheme_type if shown else None,
        "start_date": shown.start_date.isoformat() if shown else None,
        "end_date": shown.end_date.isoformat() if shown else None,
        "is_synthetic": s.is_synthetic,
        **svc.scheme_stats(ctx.db, s.id),
    }
    if detail:
        uids = {x for v in versions for x in (v.created_by_id, v.submitted_by_id, v.approved_by_id) if x}
        users = {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
        out["versions"] = [version_out(v, users) for v in versions]
        claims = ctx.db.scalars(select(SchemeClaim).where(SchemeClaim.scheme_id == s.id).order_by(SchemeClaim.created_at.desc())).all()
        out["claims"] = [claim_out(c) for c in claims]
    return out


def claim_out(c: SchemeClaim) -> dict:
    return {
        "id": str(c.id),
        "claim_number": c.claim_number,
        "scheme_id": str(c.scheme_id),
        "supplier_id": str(c.supplier_id) if c.supplier_id else None,
        "period_start": c.period_start.isoformat(),
        "period_end": c.period_end.isoformat(),
        "expected_amount": str(c.expected_amount),
        "status": c.status,
        "submitted_at": c.submitted_at.isoformat() if c.submitted_at else None,
        "notes": c.notes,
        "is_synthetic": c.is_synthetic,
    }


@router.get("")
def list_schemes(params: PageParams = Depends(), status: str | None = None, ctx: Ctx = Depends(require("schemes.read"))):
    stmt = select(Scheme).where(Scheme.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(Scheme.status.in_(status.split(",")))
    if params.q:
        stmt = stmt.where(Scheme.name.ilike(f"%{params.q}%") | Scheme.code.ilike(f"%{params.q}%"))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(Scheme.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return page_out([scheme_out(ctx, s) for s in rows], total, params)


@router.post("", status_code=201)
def create_scheme(body: SchemeIn, ctx: Ctx = Depends(require("schemes.manage"))):
    if body.supplier_id:
        ctx.get(Supplier, body.supplier_id, "Supplier")
    data = svc.validate_version_fields(ctx, body.version.model_dump())
    s = Scheme(organization_id=ctx.org_id, code=body.code.strip().upper(), name=body.name, supplier_id=body.supplier_id, funded_by=body.funded_by, status="draft", created_by_id=ctx.user_id)
    ctx.db.add(s)
    ctx.db.flush()
    v = SchemeVersion(organization_id=ctx.org_id, scheme_id=s.id, version=1, status="draft", created_by_id=ctx.user_id, **{k: (money(v) if k == "max_benefit" and v is not None else v) for k, v in data.items()})
    ctx.db.add(v)
    ctx.db.flush()
    audit.record(ctx, "scheme.create", "scheme", s.id, after={"code": s.code, "version": version_out(v)})
    commit_or_conflict(ctx.db, "DUPLICATE_CODE", "A scheme with this code exists.")
    return scheme_out(ctx, s, detail=True)


@router.get("/claims")
def list_claims(status: str | None = None, ctx: Ctx = Depends(require("schemes.read"))):
    stmt = select(SchemeClaim).where(SchemeClaim.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(SchemeClaim.status.in_(status.split(",")))
    return [claim_out(c) for c in ctx.db.scalars(stmt.order_by(SchemeClaim.created_at.desc()).limit(200)).all()]


class ClaimIn(BaseModel):
    scheme_id: uuid.UUID
    period_start: date
    period_end: date


@router.post("/claims", status_code=201)
def create_claim(body: ClaimIn, ctx: Ctx = Depends(require("schemes.manage"))):
    s = ctx.get(Scheme, body.scheme_id, "Scheme")
    c = svc.generate_claim(ctx, s, body.period_start, body.period_end)
    ctx.db.commit()
    return claim_out(c)


class ClaimSubmittedIn(BaseModel):
    notes: str | None = Field(default=None, max_length=1000)


@router.post("/claims/{claim_id}/mark-submitted")
def mark_submitted(claim_id: uuid.UUID, body: ClaimSubmittedIn, ctx: Ctx = Depends(require("schemes.manage"))):
    """Records that the claim was submitted to the supplier outside TRADEOS.
    TRADEOS does not send claims externally."""
    c = ctx.get(SchemeClaim, claim_id, "Claim", lock=True)
    if c.status != "draft":
        raise conflict("INVALID_STATUS", f"Claim is {c.status}.")
    from app.db import utcnow

    c.status = "submitted"
    c.submitted_at = utcnow()
    c.notes = body.notes
    audit.record(ctx, "scheme_claim.mark_submitted", "scheme_claim", c.id, meta={"notes": body.notes})
    ctx.db.commit()
    return claim_out(c)


class PreviewLine(BaseModel):
    product_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    unit: str | None = None


class PreviewIn(BaseModel):
    retailer_id: uuid.UUID
    order_date: date | None = None
    lines: list[PreviewLine] = Field(min_length=1, max_length=200)
    include_version_id: uuid.UUID | None = Field(default=None, description="Also evaluate this draft/pending version")


@router.post("/preview")
def preview(body: PreviewIn, ctx: Ctx = Depends(require("schemes.read"))):
    """Eligibility + calculation preview. Read-only: nothing is persisted."""
    from app.services.orders import active_rules

    retailer = ctx.get(Retailer, body.retailer_id, "Retailer")
    on = body.order_date or date.today()
    products = {p.id: p for p in (ctx.get(Product, l.product_id, "Product") for l in body.lines)}
    prices = pricing.resolve_prices(ctx.db, ctx.org_id, list(products), retailer, on)
    elines, issues = [], []
    for i, l in enumerate(body.lines, start=1):
        p = products[l.product_id]
        f = pricing.conversion_factor(ctx.db, ctx.org_id, p, l.unit or p.base_unit, on)
        if f is None:
            issues.append(f"Line {i}: no conversion from {l.unit} for {p.sku}")
            continue
        if p.id not in prices:
            issues.append(f"Line {i}: no price for {p.sku}")
            continue
        bq = qty(l.quantity * f)
        price = prices[p.id][0]
        elines.append(scheme_engine.Line(key=i, product_id=str(p.id), sku=p.sku, brand=p.brand, category=p.category, base_qty=bq, unit_price=price, gross=money(bq * price)))
    rules = active_rules(ctx.db, ctx.org_id)
    if body.include_version_id:
        v = ctx.get(SchemeVersion, body.include_version_id, "Scheme version")
        s = ctx.db.get(Scheme, v.scheme_id)
        rules = [r for r in rules if r.scheme_id != str(s.id)]
        rules.append(scheme_engine.Rule(scheme_id=str(s.id), version_id=str(v.id), version=v.version, code=s.code, name=s.name + f" (v{v.version} {v.status})", scheme_type=v.scheme_type, config=v.config, scope=v.scope or {}, eligibility=v.eligibility or {}, start_date=v.start_date, end_date=v.end_date, stacking=v.stacking, priority=v.priority, max_benefit=v.max_benefit))
    result = scheme_engine.evaluate(scheme_engine.OrderInput(order_date=on, retailer_id=str(retailer.id), territory=retailer.territory, lines=elines), rules)
    from app.audit import jsonable

    return {
        "order_date": on.isoformat(),
        "issues": issues,
        "lines": [{"line": l.key, "sku": l.sku, "base_quantity": str(l.base_qty), "unit_price": str(l.unit_price), "gross": str(l.gross), "scheme_discount": str(result.line_discount(l.key))} for l in elines],
        "applied": [jsonable(b.explanation) for b in result.applied],
        "skipped": result.skipped,
        "total_discount": str(money(sum((result.line_discount(l.key) for l in elines), Decimal(0)))),
    }


@router.post("/extract", status_code=201)
async def extract_from_document(file: UploadFile | None = File(default=None), text: str | None = Form(default=None), ctx: Ctx = Depends(require("schemes.manage"))):
    """Upload a scheme circular; candidate rules are created as DRAFT schemes for review."""
    from app.services.extraction import store_document

    if file is None and not text:
        raise AppError("NO_INPUT", "Upload a circular or paste its text.", 422)
    if file is not None:
        content = await file.read()
        ct = validate_upload(file.filename or "", file.content_type, content)
        doc = store_document(ctx, file.filename or "circular", ct, content, "scheme_circular")
    else:
        b = text.encode()
        doc = store_document(ctx, "pasted-circular.txt", "text/plain", b, "scheme_circular")
    job = jobs.enqueue(ctx.db, "extract_document", {"document_id": str(doc.id), "kind": "scheme", "actor_id": str(ctx.user_id)}, ctx.org_id, idempotency_key=f"extract_doc:{doc.id}")
    audit.record(ctx, "scheme.extract_requested", "document", doc.id)
    ctx.db.commit()
    jobs.run_pending_inline([job.id])
    ctx.db.expire_all()
    from app.models import Job

    j = ctx.db.get(Job, job.id)
    return {"document_id": str(doc.id), "job_status": j.status, "result": j.result, "error": j.last_error}


@router.get("/{scheme_id}")
def get_scheme(scheme_id: uuid.UUID, ctx: Ctx = Depends(require("schemes.read"))):
    return scheme_out(ctx, ctx.get(Scheme, scheme_id, "Scheme"), detail=True)


@router.post("/{scheme_id}/versions", status_code=201)
def new_version(scheme_id: uuid.UUID, body: VersionIn, ctx: Ctx = Depends(require("schemes.manage"))):
    s = ctx.get(Scheme, scheme_id, "Scheme", lock=True)
    if ctx.db.scalars(select(SchemeVersion).where(SchemeVersion.scheme_id == s.id, SchemeVersion.status.in_(("draft", "pending_approval")))).first():
        raise conflict("OPEN_VERSION_EXISTS", "A draft or pending version already exists; edit or resolve it first.")
    data = svc.validate_version_fields(ctx, body.model_dump())
    n = (ctx.db.scalar(select(func.max(SchemeVersion.version)).where(SchemeVersion.scheme_id == s.id)) or 0) + 1
    v = SchemeVersion(organization_id=ctx.org_id, scheme_id=s.id, version=n, status="draft", created_by_id=ctx.user_id, **{k: (money(x) if k == "max_benefit" and x is not None else x) for k, x in data.items()})
    ctx.db.add(v)
    ctx.db.flush()
    audit.record(ctx, "scheme.new_version", "scheme_version", v.id, after=version_out(v))
    ctx.db.commit()
    return scheme_out(ctx, s, detail=True)


@router.put("/{scheme_id}/versions/{version_id}")
def edit_draft(scheme_id: uuid.UUID, version_id: uuid.UUID, body: VersionIn, ctx: Ctx = Depends(require("schemes.manage"))):
    s = ctx.get(Scheme, scheme_id, "Scheme")
    v = ctx.get(SchemeVersion, version_id, "Scheme version", lock=True)
    if v.scheme_id != s.id:
        raise AppError("NOT_FOUND", "Scheme version not found.", 404)
    if v.status != "draft":
        raise conflict("VERSION_IMMUTABLE", "Only draft versions can be edited; create a new version instead.")
    before = version_out(v)
    data = svc.validate_version_fields(ctx, body.model_dump())
    for k, x in data.items():
        setattr(v, k, money(x) if k == "max_benefit" and x is not None else x)
    b, a = audit.diff(before, version_out(v))
    audit.record(ctx, "scheme.edit_draft", "scheme_version", v.id, before=b, after=a)
    ctx.db.commit()
    return scheme_out(ctx, s, detail=True)


def _sv(ctx, scheme_id, version_id):
    s = ctx.get(Scheme, scheme_id, "Scheme", lock=True)
    v = ctx.get(SchemeVersion, version_id, "Scheme version", lock=True)
    if v.scheme_id != s.id:
        raise AppError("NOT_FOUND", "Scheme version not found.", 404)
    return s, v


@router.post("/{scheme_id}/versions/{version_id}/submit")
def submit_version(scheme_id: uuid.UUID, version_id: uuid.UUID, ctx: Ctx = Depends(require("schemes.manage"))):
    s, v = _sv(ctx, scheme_id, version_id)
    svc.submit(ctx, s, v)
    ctx.db.commit()
    return scheme_out(ctx, s, detail=True)


@router.post("/{scheme_id}/versions/{version_id}/approve")
def approve_version(scheme_id: uuid.UUID, version_id: uuid.UUID, ctx: Ctx = Depends(require("schemes.approve"))):
    s, v = _sv(ctx, scheme_id, version_id)
    svc.approve(ctx, s, v)
    ctx.db.commit()
    return scheme_out(ctx, s, detail=True)


@router.post("/{scheme_id}/versions/{version_id}/reject")
def reject_version(scheme_id: uuid.UUID, version_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("schemes.approve"))):
    s, v = _sv(ctx, scheme_id, version_id)
    svc.reject(ctx, s, v, body.reason)
    ctx.db.commit()
    return scheme_out(ctx, s, detail=True)


@router.post("/{scheme_id}/deactivate")
def deactivate(scheme_id: uuid.UUID, body: ReasonIn, ctx: Ctx = Depends(require("schemes.approve"))):
    s = ctx.get(Scheme, scheme_id, "Scheme", lock=True)
    svc.deactivate(ctx, s, body.reason)
    ctx.db.commit()
    return scheme_out(ctx, s, detail=True)
