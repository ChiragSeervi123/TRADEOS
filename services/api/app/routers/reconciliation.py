import uuid
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import audit
from app.common import PageParams, next_number, page_out
from app.db import utcnow
from app.deps import Ctx, require
from app.errors import AppError, conflict
from app.models import Document, ReconciliationCase, ReconciliationEvent, ReconciliationMatch, User
from app.models.finance import RECON_CATEGORIES
from app.money import money
from app.services import reconciliation as svc
from app.services.storage import signed_url, validate_upload

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


def case_out(ctx: Ctx, c: ReconciliationCase, users: dict | None = None, detail=False) -> dict:
    users = users or {}
    out = {
        "id": str(c.id),
        "case_number": c.case_number,
        "category": c.category,
        "status": c.status,
        "title": c.title,
        "currency": c.currency,
        "expected_value": str(c.expected_value) if c.expected_value is not None else None,
        "observed_value": str(c.observed_value) if c.observed_value is not None else None,
        "difference": str(c.difference) if c.difference is not None else None,
        "resolved_value": str(c.resolved_value) if c.resolved_value is not None else None,
        "matching_method": c.matching_method,
        "rule": c.rule,
        "confidence": str(c.confidence) if c.confidence is not None else None,
        "assigned_to_id": str(c.assigned_to_id) if c.assigned_to_id else None,
        "assigned_to": users.get(c.assigned_to_id),
        "created_at": c.created_at.isoformat(),
        "age_days": (utcnow() - c.created_at).days,
        "is_synthetic": c.is_synthetic,
    }
    if detail:
        events = ctx.db.scalars(select(ReconciliationEvent).where(ReconciliationEvent.case_id == c.id).order_by(ReconciliationEvent.created_at, ReconciliationEvent.id)).all()
        uids = {e.actor_id for e in events if e.actor_id} | {c.resolved_by_id} - {None}
        names = {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.id.in_(uids))).all()} if uids else {}
        docs = ctx.db.scalars(select(Document).where(Document.id.in_([uuid.UUID(d) for d in (c.document_ids or [])]), Document.organization_id == ctx.org_id)).all() if c.document_ids else []
        out.update(
            {
                "description": c.description,
                "explanation": c.explanation,
                "records": c.records,
                "documents": [{"id": str(d.id), "filename": d.filename, "content_type": d.content_type, **signed_url(d.id)} for d in docs],
                "resolution_note": c.resolution_note,
                "resolved_by": names.get(c.resolved_by_id),
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
                "allowed_transitions": sorted(svc.TRANSITIONS.get(c.status, set())),
                "history": [{"event": e.event_type, "from": e.from_status, "to": e.to_status, "note": e.note, "document_id": str(e.document_id) if e.document_id else None, "actor": names.get(e.actor_id), "at": e.created_at.isoformat()} for e in events],
                "notice": "A mismatch is an exception requiring investigation, not proof of fraud, theft or financial loss.",
            }
        )
    return out


@router.get("/cases")
def list_cases(
    params: PageParams = Depends(),
    status: str | None = None,
    category: str | None = None,
    assigned_to: uuid.UUID | None = None,
    ctx: Ctx = Depends(require("finance.read")),
):
    stmt = select(ReconciliationCase).where(ReconciliationCase.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(ReconciliationCase.status.in_(status.split(",")))
    if category:
        stmt = stmt.where(ReconciliationCase.category == category)
    if assigned_to:
        stmt = stmt.where(ReconciliationCase.assigned_to_id == assigned_to)
    if params.q:
        stmt = stmt.where(ReconciliationCase.title.ilike(f"%{params.q}%") | ReconciliationCase.case_number.ilike(f"%{params.q}%"))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(ReconciliationCase.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    users = {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.id.in_({c.assigned_to_id for c in rows if c.assigned_to_id}))).all()}
    out = page_out([case_out(ctx, c, users) for c in rows], total, params)
    summary = ctx.db.execute(
        select(ReconciliationCase.status, func.count(), func.sum(ReconciliationCase.difference)).where(ReconciliationCase.organization_id == ctx.org_id).group_by(ReconciliationCase.status)
    ).all()
    out["summary"] = {s: {"count": n, "difference": str(money(d or 0))} for s, n, d in summary}
    return out


class CaseIn(BaseModel):
    category: str
    title: str = Field(min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    expected_value: Decimal | None = None
    observed_value: Decimal | None = None
    records: list[dict] = Field(default_factory=list, max_length=50)


@router.post("/cases", status_code=201)
def create_case(body: CaseIn, ctx: Ctx = Depends(require("finance.reconcile"))):
    if body.category not in RECON_CATEGORIES:
        raise AppError("INVALID_CATEGORY", f"Category must be one of {', '.join(RECON_CATEGORIES)}.", 422)
    exp = money(body.expected_value) if body.expected_value is not None else None
    obs = money(body.observed_value) if body.observed_value is not None else None
    c = ReconciliationCase(
        organization_id=ctx.org_id,
        case_number=next_number(ctx.db, ReconciliationCase, ctx.org_id, "case_number", "RC"),
        category=body.category,
        title=body.title,
        description=body.description,
        currency=ctx.org.currency,
        expected_value=exp,
        observed_value=obs,
        difference=money(obs - exp) if exp is not None and obs is not None else None,
        matching_method="manual",
        rule="manual",
        explanation={"source": "Created manually by a user"},
        records=[{"type": str(r.get("type"))[:40], "id": str(r.get("id"))[:64], "label": str(r.get("label", ""))[:120]} for r in body.records],
        assigned_to_id=ctx.user_id,
    )
    ctx.db.add(c)
    ctx.db.flush()
    ctx.db.add(ReconciliationEvent(organization_id=ctx.org_id, case_id=c.id, event_type="created", to_status="open", note="Created manually", actor_id=ctx.user_id))
    audit.record(ctx, "reconciliation.create_case", "reconciliation_case", c.id, after={"category": c.category, "difference": str(c.difference) if c.difference is not None else None})
    ctx.db.commit()
    return case_out(ctx, c, detail=True)


@router.post("/run")
def run(ctx: Ctx = Depends(require("finance.reconcile"))):
    summary = svc.run_rules(ctx.db, ctx.org, ctx.user_id)
    audit.record(ctx, "reconciliation.run", "organization", ctx.org_id, meta=summary)
    ctx.db.commit()
    return summary


@router.get("/cases/{case_id}")
def get_case(case_id: uuid.UUID, ctx: Ctx = Depends(require("finance.read"))):
    c = ctx.get(ReconciliationCase, case_id, "Case")
    u = ctx.db.get(User, c.assigned_to_id) if c.assigned_to_id else None
    return case_out(ctx, c, {u.id: u.full_name} if u else {}, detail=True)


class TransitionIn(BaseModel):
    status: Literal["investigating", "awaiting_evidence", "resolved", "accepted_difference", "closed"]
    note: str | None = Field(default=None, max_length=4000)
    resolved_value: Decimal | None = None


@router.post("/cases/{case_id}/transition")
def transition(case_id: uuid.UUID, body: TransitionIn, ctx: Ctx = Depends(require("finance.reconcile"))):
    c = ctx.get(ReconciliationCase, case_id, "Case", lock=True)
    svc.transition(ctx, c, body.status, body.note, body.resolved_value)
    ctx.db.commit()
    return get_case(case_id, ctx)


class AssignIn(BaseModel):
    user_id: uuid.UUID | None


@router.post("/cases/{case_id}/assign")
def assign(case_id: uuid.UUID, body: AssignIn, ctx: Ctx = Depends(require("finance.reconcile"))):
    c = ctx.get(ReconciliationCase, case_id, "Case", lock=True)
    svc.assign(ctx, c, body.user_id)
    ctx.db.commit()
    return get_case(case_id, ctx)


@router.post("/cases/{case_id}/notes", status_code=201)
async def add_note(case_id: uuid.UUID, note: str = Form(..., min_length=1, max_length=4000), file: UploadFile | None = File(default=None), ctx: Ctx = Depends(require("finance.reconcile"))):
    c = ctx.get(ReconciliationCase, case_id, "Case", lock=True)
    if c.status == "closed":
        raise conflict("CASE_CLOSED", "Closed cases cannot be changed.")
    doc_id = None
    if file is not None:
        from app.services.extraction import store_document

        content = await file.read()
        ct = validate_upload(file.filename or "", file.content_type, content)
        doc_id = store_document(ctx, file.filename or "evidence", ct, content, "evidence").id
    svc.add_note(ctx, c, note, doc_id)
    ctx.db.commit()
    return get_case(case_id, ctx)


# ---------------------------------------------------------------------------- matches


def match_out(m: ReconciliationMatch) -> dict:
    return {"id": str(m.id), "left_type": m.left_type, "left_id": m.left_id, "right_type": m.right_type, "right_id": m.right_id, "method": m.method, "score": str(m.score) if m.score is not None else None, "status": m.status, "details": m.details, "created_at": m.created_at.isoformat()}


@router.get("/matches")
def list_matches(status: str | None = None, left_type: str | None = None, ctx: Ctx = Depends(require("finance.read"))):
    stmt = select(ReconciliationMatch).where(ReconciliationMatch.organization_id == ctx.org_id)
    if status:
        stmt = stmt.where(ReconciliationMatch.status.in_(status.split(",")))
    if left_type:
        stmt = stmt.where(ReconciliationMatch.left_type == left_type)
    return [match_out(m) for m in ctx.db.scalars(stmt.order_by(ReconciliationMatch.created_at.desc()).limit(500)).all()]


MATCHABLE = {"purchase_invoice", "goods_receipt", "scheme_claim", "credit_note", "payment", "sales_invoice", "return"}


class MatchIn(BaseModel):
    left_type: str
    left_id: uuid.UUID
    right_type: str
    right_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)


def _resolve(ctx, kind: str, oid):
    from app.models import CreditNote, GoodsReceipt, Payment, PurchaseInvoice, ReturnRecord, SalesInvoice, SchemeClaim

    models = {"purchase_invoice": PurchaseInvoice, "goods_receipt": GoodsReceipt, "scheme_claim": SchemeClaim, "credit_note": CreditNote, "payment": Payment, "sales_invoice": SalesInvoice, "return": ReturnRecord}
    if kind not in models:
        raise AppError("INVALID_TYPE", f"Cannot match records of type '{kind}'.", 422)
    return ctx.get(models[kind], oid, kind.replace("_", " ").title())


@router.post("/matches", status_code=201)
def manual_match(body: MatchIn, ctx: Ctx = Depends(require("finance.reconcile"))):
    _resolve(ctx, body.left_type, body.left_id)
    _resolve(ctx, body.right_type, body.right_id)
    m = svc._match(ctx.db, ctx.org_id, (body.left_type, body.left_id), (body.right_type, body.right_id), "manual", 1.0, "active", {"note": body.note}, ctx.user_id)
    if m.status != "active":
        m.status = "active"
    audit.record(ctx, "reconciliation.match", "reconciliation_match", m.id, after=match_out(m))
    ctx.db.commit()
    return match_out(m)


@router.post("/matches/{match_id}/confirm")
def confirm_match(match_id: uuid.UUID, ctx: Ctx = Depends(require("finance.reconcile"))):
    m = ctx.get(ReconciliationMatch, match_id, "Match", lock=True)
    if m.status != "suggested":
        raise conflict("INVALID_STATUS", f"Match is {m.status}.")
    m.status = "active"
    audit.record(ctx, "reconciliation.match_confirm", "reconciliation_match", m.id, after=match_out(m))
    ctx.db.commit()
    return match_out(m)


class UnmatchIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/matches/{match_id}/unmatch")
def unmatch(match_id: uuid.UUID, body: UnmatchIn, ctx: Ctx = Depends(require("finance.reconcile"))):
    m = ctx.get(ReconciliationMatch, match_id, "Match", lock=True)
    if m.status == "unmatched":
        return match_out(m)
    prev = m.status
    m.status = "unmatched"
    m.unmatched_by_id = ctx.user_id
    m.unmatched_at = utcnow()
    m.details = {**(m.details or {}), "unmatch_reason": body.reason}
    audit.record(ctx, "reconciliation.unmatch", "reconciliation_match", m.id, before={"status": prev}, after={"status": "unmatched"}, meta={"reason": body.reason})
    ctx.db.commit()
    return match_out(m)
