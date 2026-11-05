"""Analytics, audit log, integrations and system status."""

import csv
import io
import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import audit
from app.common import PageParams, page_out
from app.config import get_settings
from app.db import get_db, utcnow
from app.deps import Ctx, get_ctx, require
from app.errors import AppError
from app.extraction.providers import provider_status
from app.models import AuditEvent, IntegrationConnection, IntegrationSyncRun, Organization, Retailer, SalesInvoice, User
from app.security import hash_token, new_opaque_token, rate_limiter
from app.services import analytics, jobs

router = APIRouter(tags=["platform"])


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    d1 = date_to or date.today()
    d0 = date_from or (d1 - timedelta(days=29))
    if d0 > d1:
        raise AppError("INVALID_PERIOD", "date_from must be on or before date_to.", 422)
    if (d1 - d0).days > 366:
        raise AppError("PERIOD_TOO_LONG", "Periods are limited to one year.", 422)
    return d0, d1


@router.get("/analytics/overview")
def overview(date_from: date | None = None, date_to: date | None = None, ctx: Ctx = Depends(require("reports.read"))):
    d0, d1 = _period(date_from, date_to)
    return analytics.owner_dashboard(ctx.db, ctx.org, d0, d1, finance=ctx.can("finance.read"))


@router.get("/analytics/operations")
def operations(ctx: Ctx = Depends(require("orders.read"))):
    if not (ctx.can("reports.read") or ctx.can("fulfilment.read")):
        raise AppError("FORBIDDEN", "Missing permission: reports.read", 403)
    return analytics.operations_dashboard(ctx.db, ctx.org)


@router.get("/analytics/accounting")
def accounting(ctx: Ctx = Depends(require("finance.read"))):
    return analytics.accountant_dashboard(ctx.db, ctx.org)


@router.get("/analytics/trends")
def trends(date_from: date | None = None, date_to: date | None = None, ctx: Ctx = Depends(require("reports.read"))):
    d0, d1 = _period(date_from, date_to)
    return analytics.trends(ctx.db, ctx.org_id, d0, d1, finance=ctx.can("finance.read"))


@router.get("/analytics/definitions")
def definitions(ctx: Ctx = Depends(get_ctx)):
    return {"source": "app/services/analytics.py", "definitions": analytics.__doc__}


@router.get("/analytics/export")
def export_report(report: str = Query(..., pattern="^(sales_by_product|sales_by_retailer|daily)$"), date_from: date | None = None, date_to: date | None = None, ctx: Ctx = Depends(require("reports.export"))):
    d0, d1 = _period(date_from, date_to)
    data = analytics.trends(ctx.db, ctx.org_id, d0, d1, finance=ctx.can("finance.read"))
    rows = {"sales_by_product": data["top_products"], "sales_by_retailer": data["top_retailers"], "daily": data["daily"]}[report]
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    audit.record(ctx, "data.export", "report", report, meta={"from": d0, "to": d1, "rows": len(rows)})
    ctx.db.commit()
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="tradeos_{report}_{d0}_{d1}.csv"'})


# ------------------------------------------------------------------------------ audit


@router.get("/audit")
def audit_log(
    params: PageParams = Depends(),
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    ctx: Ctx = Depends(require("audit.read")),
):
    stmt = select(AuditEvent).where(AuditEvent.organization_id == ctx.org_id)
    if action:
        stmt = stmt.where(AuditEvent.action.ilike(f"{action}%"))
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == entity_id)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    if date_from:
        stmt = stmt.where(AuditEvent.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        stmt = stmt.where(AuditEvent.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.scalars(stmt.order_by(AuditEvent.created_at.desc(), AuditEvent.id).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    users = {u.id: u.full_name for u in ctx.db.scalars(select(User).where(User.id.in_({r.actor_id for r in rows if r.actor_id}))).all()}
    items = [
        {"id": str(r.id), "at": r.created_at.isoformat(), "actor": users.get(r.actor_id, "system" if not r.actor_id else None), "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id, "request_id": r.request_id, "before": r.before, "after": r.after, "meta": r.meta, "ip": r.ip}
        for r in rows
    ]
    return page_out(items, total, params)


# ----------------------------------------------------------------------- integrations

ADAPTERS = {
    "anthropic": {"name": "Claude (Anthropic) document extraction", "kind": "ai"},
    "csv": {"name": "CSV import / export", "kind": "file"},
    "webhook": {"name": "Inbound order webhook (generic REST)", "kind": "api"},
    "tally": {"name": "Tally (voucher XML export)", "kind": "accounting"},
    "busy": {"name": "Busy accounting", "kind": "accounting"},
    "marg": {"name": "Marg ERP", "kind": "accounting"},
    "whatsapp": {"name": "WhatsApp Business Platform", "kind": "messaging"},
    "email": {"name": "Email / document ingestion", "kind": "messaging"},
}


def _conn(ctx, provider) -> IntegrationConnection | None:
    return ctx.db.scalars(select(IntegrationConnection).where(IntegrationConnection.organization_id == ctx.org_id, IntegrationConnection.provider == provider)).first()


def integration_status(ctx) -> list[dict]:
    out = []
    for key, meta in ADAPTERS.items():
        c = _conn(ctx, key)
        base = {"provider": key, **meta, "last_sync_at": c.last_sync_at.isoformat() if c and c.last_sync_at else None, "last_success_at": c.last_success_at.isoformat() if c and c.last_success_at else None, "last_error": c.last_error if c else None}
        if key == "anthropic":
            ps = provider_status(ctx.org)
            base.update(status=ps["status"], detail=ps["message"], model=ps["model"], enabled_for_org=ctx.org.ai_external_enabled)
        elif key == "csv":
            base.update(status="available", detail="Built-in. Templates for products, retailers, suppliers, opening stock and prices. All-or-nothing imports with row-level errors.")
        elif key == "webhook":
            base.update(status=(c.status if c else "not_configured"), detail="POST /api/v1/integrations/webhook/orders with header X-TRADEOS-Webhook-Token. Each message becomes an inbox item for human review.")
        elif key == "tally":
            base.update(status="simulated", detail="File export only: generates Tally-compatible sales voucher XML for manual import. No live connection to Tally is made or claimed.")
        elif key == "whatsapp":
            base.update(status="not_configured", detail="Requires an authorised WhatsApp Business Platform account. Until then, copy/paste or upload messages in the Order Inbox. No unofficial automation is used.")
        else:
            base.update(status="not_configured", detail="Adapter interface defined; no credentials or test environment available. Use CSV import/export meanwhile.")
        out.append(base)
    return out


@router.get("/integrations")
def list_integrations(ctx: Ctx = Depends(require("org.read"))):
    runs = ctx.db.scalars(select(IntegrationSyncRun).where(IntegrationSyncRun.organization_id == ctx.org_id).order_by(IntegrationSyncRun.started_at.desc()).limit(20)).all()
    conns = {c.id: c.provider for c in ctx.db.scalars(select(IntegrationConnection).where(IntegrationConnection.organization_id == ctx.org_id)).all()}
    return {
        "integrations": integration_status(ctx),
        "recent_runs": [{"id": str(r.id), "provider": conns.get(r.connection_id), "direction": r.direction, "status": r.status, "records_processed": r.records_processed, "records_failed": r.records_failed, "errors": r.errors, "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in runs],
    }


@router.post("/integrations/webhook/token", status_code=201)
def rotate_webhook_token(ctx: Ctx = Depends(require("integrations.manage"))):
    """Create or rotate the inbound webhook token. Only the hash is stored; the token is shown once."""
    token, th = new_opaque_token()
    c = _conn(ctx, "webhook") or IntegrationConnection(organization_id=ctx.org_id, provider="webhook", config={})
    c.config = {"token_hash": th, "rotated_at": utcnow().isoformat()}
    c.status = "configured"
    ctx.db.add(c)
    audit.record(ctx, "integration.webhook_token_rotated", "integration", "webhook")
    ctx.db.commit()
    return {"token": token, "header": "X-TRADEOS-Webhook-Token", "endpoint": "/api/v1/integrations/webhook/orders", "note": "Store this token securely; it will not be shown again."}


class WebhookOrderIn(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    sender: str | None = Field(default=None, max_length=200)
    external_id: str | None = Field(default=None, max_length=200)


@router.post("/integrations/webhook/orders", status_code=202)
def webhook_order(body: WebhookOrderIn, request: Request, x_tradeos_webhook_token: str = Header(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), db=Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if not rate_limiter.allow(f"webhook:{ip}", 120):
        raise AppError("RATE_LIMITED", "Too many requests.", 429)
    th = hash_token(x_tradeos_webhook_token)
    conn = next((c for c in db.scalars(select(IntegrationConnection).where(IntegrationConnection.provider == "webhook", IntegrationConnection.status == "configured")).all() if (c.config or {}).get("token_hash") == th), None)
    if not conn:
        raise AppError("UNAUTHENTICATED", "Invalid webhook token.", 401)
    org = db.get(Organization, conn.organization_id)
    from app.common import idempotency_lookup, idempotency_store
    from app.deps import Ctx as _Ctx
    from app.models import Membership
    from app.permissions import permissions_for
    from app.services.extraction import ingest_text

    key = idempotency_key or body.external_id
    prior = idempotency_lookup(db, org.id, "webhook.orders", key, body.model_dump())
    if prior:
        return {"status": "duplicate", "source_id": prior.resource_id}
    owner_m = db.scalars(select(Membership).where(Membership.organization_id == org.id, Membership.role == "org_owner", Membership.is_active.is_(True))).first()
    owner = db.get(User, owner_m.user_id)
    sctx = _Ctx(db=db, user=owner, org=org, role="org_owner", permissions=permissions_for("org_owner"), request_id=getattr(request.state, "request_id", None), ip=ip)
    run = IntegrationSyncRun(organization_id=org.id, connection_id=conn.id, direction="inbound", status="running")
    db.add(run)
    src, job_ids = ingest_text(sctx, body.text, body.sender, channel="api")
    src.meta = {**(src.meta or {}), "received_via": "webhook", "external_id": body.external_id}
    idempotency_store(db, org.id, "webhook.orders", key, body.model_dump(), "order_source", src.id)
    run.status, run.records_processed, run.finished_at = "succeeded", 1, utcnow()
    conn.last_sync_at = conn.last_success_at = utcnow()
    audit.record(sctx, "integration.webhook_order", "order_source", src.id, meta={"external_id": body.external_id})
    db.commit()
    jobs.run_pending_inline(job_ids)
    return {"status": "accepted", "source_id": str(src.id)}


@router.get("/integrations/tally/export")
def tally_export(date_from: date | None = None, date_to: date | None = None, ctx: Ctx = Depends(require("integrations.manage"))):
    """SIMULATED adapter: Tally-style XML of sales vouchers for manual import."""
    from xml.sax.saxutils import escape

    d0, d1 = _period(date_from, date_to)
    invs = ctx.db.scalars(select(SalesInvoice).where(SalesInvoice.organization_id == ctx.org_id, SalesInvoice.invoice_date >= d0, SalesInvoice.invoice_date <= d1, SalesInvoice.status != "void").order_by(SalesInvoice.invoice_date)).all()
    rmap = {r.id: r for r in ctx.db.scalars(select(Retailer).where(Retailer.organization_id == ctx.org_id)).all()}
    vouchers = []
    for i in invs:
        party = escape(rmap[i.retailer_id].name)
        vouchers.append(
            f'  <VOUCHER VCHTYPE="Sales" ACTION="Create"><DATE>{i.invoice_date:%Y%m%d}</DATE><VOUCHERNUMBER>{escape(i.invoice_number)}</VOUCHERNUMBER>'
            f"<PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>"
            f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>{party}</LEDGERNAME><AMOUNT>-{i.total}</AMOUNT></ALLLEDGERENTRIES.LIST>"
            f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>Sales</LEDGERNAME><AMOUNT>{i.gross_amount - i.discount_amount}</AMOUNT></ALLLEDGERENTRIES.LIST>"
            f"<ALLLEDGERENTRIES.LIST><LEDGERNAME>Output Tax</LEDGERNAME><AMOUNT>{i.tax_amount}</AMOUNT></ALLLEDGERENTRIES.LIST></VOUCHER>"
        )
    xml = "<ENVELOPE><!-- Generated by TRADEOS (simulated Tally adapter: file export, not a live connection). Review ledger names before import. -->\n<BODY><DATA>\n" + "\n".join(vouchers) + "\n</DATA></BODY></ENVELOPE>\n"
    c = _conn(ctx, "tally") or IntegrationConnection(organization_id=ctx.org_id, provider="tally", status="simulated", config={})
    ctx.db.add(c)
    ctx.db.flush()
    ctx.db.add(IntegrationSyncRun(organization_id=ctx.org_id, connection_id=c.id, direction="outbound", status="succeeded", records_processed=len(invs), finished_at=utcnow()))
    c.last_sync_at = c.last_success_at = utcnow()
    audit.record(ctx, "data.export", "integration", "tally", meta={"vouchers": len(invs), "from": d0, "to": d1})
    ctx.db.commit()
    return Response(xml, media_type="application/xml", headers={"Content-Disposition": f'attachment; filename="tally_sales_{d0}_{d1}.xml"'})


@router.get("/system/status")
def system_status(ctx: Ctx = Depends(get_ctx)):
    s = get_settings()
    from app.models import Job

    q = dict(ctx.db.execute(select(Job.status, func.count()).group_by(Job.status)).all()) if ctx.can("integrations.manage") else None
    return {
        "environment": s.environment,
        "database": "sqlite" if s.is_sqlite else "postgresql",
        "jobs_mode": s.jobs_mode,
        "job_counts": q,
        "ai_provider": provider_status(ctx.org) if ctx.org else None,
        "email_delivery": "not_configured",
        "storage": "local (private, signed URLs)",
        "malware_scanning": "not_configured",
    }
