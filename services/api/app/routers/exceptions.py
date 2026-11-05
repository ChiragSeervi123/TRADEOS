import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import audit
from app.common import PageParams, page_out
from app.db import utcnow
from app.deps import Ctx, require
from app.errors import AppError, conflict
from app.models import AuditEvent, Comment, ExceptionItem, Membership, User
from app.services.exceptions import EXCEPTION_TYPES

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

# Exception types a role may see in the queue (finance-only items need finance.read).
FINANCE_TYPES = {"credit_limit_exceeded", "price_mismatch", "invoice_extraction_failure", "reconciliation_mismatch"}


def _visible_types(ctx: Ctx) -> list[str]:
    types = list(EXCEPTION_TYPES)
    if not ctx.can("finance.read"):
        types = [t for t in types if t not in FINANCE_TYPES]
    return types


def exc_out(e: ExceptionItem, users: dict) -> dict:
    return {
        "id": str(e.id),
        "exception_type": e.exception_type,
        "type_label": EXCEPTION_TYPES.get(e.exception_type, e.exception_type),
        "severity": e.severity,
        "status": e.status,
        "title": e.title,
        "description": e.description,
        "entity_type": e.entity_type,
        "entity_id": e.entity_id,
        "evidence": e.evidence,
        "suggested_action": e.suggested_action,
        "assigned_to_id": str(e.assigned_to_id) if e.assigned_to_id else None,
        "assigned_to": users.get(e.assigned_to_id),
        "due_date": e.due_date.isoformat() if e.due_date else None,
        "resolution_reason": e.resolution_reason,
        "resolved_by": users.get(e.resolved_by_id),
        "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
        "created_at": e.created_at.isoformat(),
        "updated_at": e.updated_at.isoformat(),
        "age_days": (utcnow() - e.created_at).days,
        "is_synthetic": e.is_synthetic,
    }


def _users(ctx, rows):
    ids = {x for e in rows for x in (e.assigned_to_id, e.resolved_by_id) if x}
    return {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.id.in_(ids))).all()} if ids else {}


@router.get("")
def list_exceptions(
    params: PageParams = Depends(),
    exception_type: str | None = None,
    severity: str | None = None,
    status: str | None = "open,in_progress",
    assigned_to: str | None = None,
    min_age_days: int | None = None,
    entity_id: str | None = None,
    ctx: Ctx = Depends(require("exceptions.read")),
):
    stmt = select(ExceptionItem).where(ExceptionItem.organization_id == ctx.org_id, ExceptionItem.exception_type.in_(_visible_types(ctx)))
    if exception_type:
        stmt = stmt.where(ExceptionItem.exception_type.in_(exception_type.split(",")))
    if severity:
        stmt = stmt.where(ExceptionItem.severity.in_(severity.split(",")))
    if status and status != "all":
        stmt = stmt.where(ExceptionItem.status.in_(status.split(",")))
    if assigned_to == "me":
        stmt = stmt.where(ExceptionItem.assigned_to_id == ctx.user_id)
    elif assigned_to == "unassigned":
        stmt = stmt.where(ExceptionItem.assigned_to_id.is_(None))
    elif assigned_to:
        stmt = stmt.where(ExceptionItem.assigned_to_id == uuid.UUID(assigned_to))
    if min_age_days:
        stmt = stmt.where(ExceptionItem.created_at <= utcnow() - timedelta(days=min_age_days))
    if entity_id:
        stmt = stmt.where(ExceptionItem.entity_id == entity_id)
    if params.q:
        stmt = stmt.where(ExceptionItem.title.ilike(f"%{params.q}%"))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    rows = ctx.db.scalars(stmt.order_by(ExceptionItem.created_at.desc()).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    rows = sorted(rows, key=lambda e: (e.status not in ("open", "in_progress"), sev_rank.get(e.severity, 9), -e.created_at.timestamp()))
    users = _users(ctx, rows)
    out = page_out([exc_out(e, users) for e in rows], total, params)
    counts = ctx.db.execute(
        select(ExceptionItem.exception_type, func.count())
        .where(ExceptionItem.organization_id == ctx.org_id, ExceptionItem.status.in_(("open", "in_progress")), ExceptionItem.exception_type.in_(_visible_types(ctx)))
        .group_by(ExceptionItem.exception_type)
    ).all()
    out["open_by_type"] = {t: n for t, n in counts}
    out["types"] = {t: EXCEPTION_TYPES[t] for t in _visible_types(ctx)}
    return out


def _get(ctx, exception_id, lock=False) -> ExceptionItem:
    e = ctx.get(ExceptionItem, exception_id, "Exception", lock=lock)
    if e.exception_type not in _visible_types(ctx):
        raise AppError("NOT_FOUND", "Exception not found.", 404)
    return e


@router.get("/{exception_id}")
def get_exception(exception_id: uuid.UUID, ctx: Ctx = Depends(require("exceptions.read"))):
    e = _get(ctx, exception_id)
    users = _users(ctx, [e])
    out = exc_out(e, users)
    comments = ctx.db.scalars(select(Comment).where(Comment.organization_id == ctx.org_id, Comment.entity_type == "exception", Comment.entity_id == str(e.id)).order_by(Comment.created_at)).all()
    names = {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.id.in_({c.author_id for c in comments if c.author_id}))).all()} if comments else {}
    out["comments"] = [{"id": str(c.id), "body": c.body, "author": names.get(c.author_id), "at": c.created_at.isoformat()} for c in comments]
    events = ctx.db.scalars(select(AuditEvent).where(AuditEvent.organization_id == ctx.org_id, AuditEvent.entity_type == "exception", AuditEvent.entity_id == str(e.id)).order_by(AuditEvent.created_at)).all()
    out["history"] = [{"action": ev.action, "at": ev.created_at.isoformat(), "before": ev.before, "after": ev.after, "meta": ev.meta} for ev in events]
    return out


class AssignIn(BaseModel):
    user_id: uuid.UUID | None
    due_date: date | None = None


@router.post("/{exception_id}/assign")
def assign(exception_id: uuid.UUID, body: AssignIn, ctx: Ctx = Depends(require("exceptions.manage"))):
    e = _get(ctx, exception_id, lock=True)
    if body.user_id and not ctx.db.scalars(select(Membership).where(Membership.organization_id == ctx.org_id, Membership.user_id == body.user_id, Membership.is_active.is_(True))).first():
        raise AppError("INVALID_ASSIGNEE", "Assignee must be an active member.", 422)
    before = {"assigned_to": str(e.assigned_to_id) if e.assigned_to_id else None, "status": e.status}
    e.assigned_to_id = body.user_id
    e.due_date = body.due_date
    if e.status == "open" and body.user_id:
        e.status = "in_progress"
    audit.record(ctx, "exception.assign", "exception", e.id, before=before, after={"assigned_to": str(body.user_id) if body.user_id else None, "status": e.status, "due_date": body.due_date})
    ctx.db.commit()
    return get_exception(exception_id, ctx)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


@router.post("/{exception_id}/comments", status_code=201)
def comment(exception_id: uuid.UUID, body: CommentIn, ctx: Ctx = Depends(require("exceptions.read"))):
    e = _get(ctx, exception_id)
    ctx.db.add(Comment(organization_id=ctx.org_id, entity_type="exception", entity_id=str(e.id), body=body.body, author_id=ctx.user_id))
    audit.record(ctx, "exception.comment", "exception", e.id, meta={"length": len(body.body)})
    ctx.db.commit()
    return get_exception(exception_id, ctx)


class ResolveIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    dismiss: bool = Field(default=False, description="Dismiss as not an issue instead of resolving")


@router.post("/{exception_id}/resolve")
def resolve(exception_id: uuid.UUID, body: ResolveIn, ctx: Ctx = Depends(require("exceptions.manage"))):
    e = _get(ctx, exception_id, lock=True)
    if e.status in ("resolved", "dismissed"):
        raise conflict("ALREADY_CLOSED", f"Exception is already {e.status}.")
    if e.exception_type in FINANCE_TYPES:
        ctx.require("finance.read")
    prev = e.status
    e.status = "dismissed" if body.dismiss else "resolved"
    e.resolution_reason = body.reason
    e.resolved_by_id = ctx.user_id
    e.resolved_at = utcnow()
    audit.record(ctx, f"exception.{e.status}", "exception", e.id, before={"status": prev}, after={"status": e.status}, meta={"reason": body.reason})
    ctx.db.commit()
    return get_exception(exception_id, ctx)


class ReopenIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/{exception_id}/reopen")
def reopen(exception_id: uuid.UUID, body: ReopenIn, ctx: Ctx = Depends(require("exceptions.manage"))):
    e = _get(ctx, exception_id, lock=True)
    if e.status not in ("resolved", "dismissed"):
        raise conflict("NOT_CLOSED", "Only resolved or dismissed exceptions can be reopened.")
    prev = e.status
    e.status = "open"
    e.resolved_at = None
    e.resolved_by_id = None
    audit.record(ctx, "exception.reopen", "exception", e.id, before={"status": prev, "resolution_reason": e.resolution_reason}, after={"status": "open"}, meta={"reason": body.reason})
    e.resolution_reason = None
    ctx.db.commit()
    return get_exception(exception_id, ctx)


@router.post("/scan-expiry")
def scan_expiry(ctx: Ctx = Depends(require("inventory.read"))):
    from app.services.scheduled import scan_expiry as run

    out = run(ctx.db, ctx.org)
    ctx.db.commit()
    return out
