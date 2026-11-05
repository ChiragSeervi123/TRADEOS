"""Distributor -> manufacturer data sharing (explicit, scoped, revocable, audited).

A manufacturer sees only aggregated metrics for brands in the grant scope, from
distributors that granted access. Retailer-level detail and financial records are
never exposed unless the grant explicitly includes retailer detail (and even then
only sales aggregates per retailer, no receivables or credit data).
"""

import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app import audit
from app.db import utcnow
from app.deps import Ctx, require
from app.errors import AppError, conflict, not_found
from app.models import (
    DataSharingGrant,
    Order,
    Organization,
    Product,
    Retailer,
    SalesInvoice,
    SalesInvoiceLine,
    Scheme,
    SchemeApplication,
    SchemeVersion,
)
from app.money import ZERO, money
from app.services import inventory

router = APIRouter(tags=["data sharing"])
METRICS = ("sales", "stock", "schemes")


class GrantIn(BaseModel):
    manufacturer_slug: str = Field(min_length=1, max_length=80)
    metrics: list[Literal["sales", "stock", "schemes"]] = Field(min_length=1)
    brands: list[str] = Field(min_length=1, max_length=100)
    include_retailer_detail: bool = False


def grant_out(g: DataSharingGrant, owner: Organization, grantee: Organization) -> dict:
    return {
        "id": str(g.id),
        "distributor": owner.name,
        "manufacturer": grantee.name,
        "manufacturer_slug": grantee.slug,
        "scope": g.scope,
        "status": g.status,
        "created_at": g.created_at.isoformat(),
        "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
    }


@router.get("/data-sharing/grants")
def list_grants(ctx: Ctx = Depends(require("data_sharing.manage"))):
    rows = ctx.db.scalars(select(DataSharingGrant).where(DataSharingGrant.organization_id == ctx.org_id).order_by(DataSharingGrant.created_at.desc())).all()
    return [grant_out(g, ctx.org, ctx.db.get(Organization, g.grantee_organization_id)) for g in rows]


@router.post("/data-sharing/grants", status_code=201)
def create_grant(body: GrantIn, ctx: Ctx = Depends(require("data_sharing.manage"))):
    if ctx.org.org_type != "distributor":
        raise AppError("NOT_AVAILABLE", "Only distributors can share data.", 422)
    m = ctx.db.scalars(select(Organization).where(Organization.slug == body.manufacturer_slug, Organization.org_type == "manufacturer")).first()
    if not m:
        raise not_found("Manufacturer organisation")
    brands = set(ctx.db.scalars(select(Product.brand).where(Product.organization_id == ctx.org_id, Product.brand.is_not(None)).distinct()).all())
    unknown = [b for b in body.brands if b not in brands]
    if unknown:
        raise AppError("UNKNOWN_BRANDS", f"Brands not in your catalogue: {', '.join(unknown)}", 422)
    if ctx.db.scalars(select(DataSharingGrant).where(DataSharingGrant.organization_id == ctx.org_id, DataSharingGrant.grantee_organization_id == m.id, DataSharingGrant.status == "active")).first():
        raise conflict("GRANT_EXISTS", "An active grant already exists for this manufacturer; revoke it first.")
    g = DataSharingGrant(organization_id=ctx.org_id, grantee_organization_id=m.id, scope={"metrics": sorted(set(body.metrics)), "brands": sorted(set(body.brands)), "include_retailer_detail": body.include_retailer_detail}, granted_by_id=ctx.user_id)
    ctx.db.add(g)
    ctx.db.flush()
    audit.record(ctx, "data_sharing.grant", "data_sharing_grant", g.id, after={"manufacturer": m.slug, "scope": g.scope})
    ctx.db.commit()
    return grant_out(g, ctx.org, m)


@router.post("/data-sharing/grants/{grant_id}/revoke")
def revoke_grant(grant_id: uuid.UUID, ctx: Ctx = Depends(require("data_sharing.manage"))):
    g = ctx.get(DataSharingGrant, grant_id, "Grant", lock=True)
    if g.status == "revoked":
        return grant_out(g, ctx.org, ctx.db.get(Organization, g.grantee_organization_id))
    g.status = "revoked"
    g.revoked_at = utcnow()
    audit.record(ctx, "data_sharing.revoke", "data_sharing_grant", g.id)
    ctx.db.commit()
    return grant_out(g, ctx.org, ctx.db.get(Organization, g.grantee_organization_id))


# ------------------------------------------------------------------ manufacturer side


def _grant_for_manufacturer(ctx: Ctx, grant_id) -> DataSharingGrant:
    try:
        gid = uuid.UUID(str(grant_id))
    except ValueError:
        raise not_found("Grant") from None
    g = ctx.db.get(DataSharingGrant, gid)
    if not g or g.grantee_organization_id != ctx.org_id or g.status != "active":
        raise not_found("Grant")
    return g


@router.get("/shared/distributors")
def shared_distributors(ctx: Ctx = Depends(require("shared_data.read"))):
    rows = ctx.db.scalars(select(DataSharingGrant).where(DataSharingGrant.grantee_organization_id == ctx.org_id, DataSharingGrant.status == "active")).all()
    return [grant_out(g, ctx.db.get(Organization, g.organization_id), ctx.org) for g in rows]


@router.get("/shared/distributors/{grant_id}/metrics")
def shared_metrics(grant_id: str, days: int = 30, ctx: Ctx = Depends(require("shared_data.read"))):
    g = _grant_for_manufacturer(ctx, grant_id)
    dist = ctx.db.get(Organization, g.organization_id)
    scope = g.scope or {}
    brands = scope.get("brands", [])
    days = max(1, min(days, 365))
    d1 = date.today()
    d0 = d1 - timedelta(days=days - 1)
    products = {p.id: p for p in ctx.db.scalars(select(Product).where(Product.organization_id == dist.id, Product.brand.in_(brands))).all()}
    out: dict = {"distributor": dist.name, "brands": brands, "period": {"from": d0.isoformat(), "to": d1.isoformat()}, "scope": scope}
    latest = ctx.db.scalar(select(func.max(SalesInvoice.invoice_date)).where(SalesInvoice.organization_id == dist.id))
    out["data_freshness"] = {"generated_at": utcnow().isoformat(), "latest_invoice_date": latest.isoformat() if latest else None}
    if "sales" in scope.get("metrics", []) and products:
        rows = ctx.db.execute(
            select(SalesInvoiceLine.product_id, SalesInvoice.retailer_id, func.sum(SalesInvoiceLine.quantity), func.sum(SalesInvoiceLine.gross_amount), func.sum(SalesInvoiceLine.discount_amount))
            .join(SalesInvoice, SalesInvoice.id == SalesInvoiceLine.sales_invoice_id)
            .where(SalesInvoice.organization_id == dist.id, SalesInvoice.status != "void", SalesInvoice.invoice_date >= d0, SalesInvoice.invoice_date <= d1, SalesInvoiceLine.product_id.in_(list(products)))
            .group_by(SalesInvoiceLine.product_id, SalesInvoice.retailer_id)
        ).all()
        by_product: dict = {}
        by_retailer: dict = {}
        for pid, rid, q, gross, disc in rows:
            p = products[pid]
            e = by_product.setdefault(p.sku, {"sku": p.sku, "name": p.name, "brand": p.brand, "quantity": ZERO, "net_value": ZERO})
            e["quantity"] += q or 0
            e["net_value"] += money((gross or 0) - (disc or 0))
            by_retailer[rid] = by_retailer.get(rid, ZERO) + money((gross or 0) - (disc or 0))
        out["sales"] = {
            "by_product": [{**v, "quantity": str(v["quantity"]), "net_value": str(v["net_value"])} for v in sorted(by_product.values(), key=lambda x: -x["net_value"])],
            "active_retailers": len(by_retailer),
            "definition": "Invoiced value net of discounts, excluding tax, for in-scope brands.",
        }
        if scope.get("include_retailer_detail"):
            rmap = {r.id: r for r in ctx.db.scalars(select(Retailer).where(Retailer.id.in_(list(by_retailer)))).all()}
            out["sales"]["by_retailer"] = [{"retailer": rmap[k].name, "territory": rmap[k].territory, "net_value": str(v)} for k, v in sorted(by_retailer.items(), key=lambda kv: -kv[1])]
    if "stock" in scope.get("metrics", []) and products:
        bals = inventory.balances(ctx.db, dist.id, product_ids=list(products))
        agg: dict = {}
        for (_, pid), b in bals.items():
            p = products[pid]
            e = agg.setdefault(p.sku, {"sku": p.sku, "name": p.name, "on_hand": ZERO, "available": ZERO})
            e["on_hand"] += b.on_hand
            e["available"] += b.available
        out["stock"] = [{**v, "on_hand": str(v["on_hand"]), "available": str(v["available"])} for v in sorted(agg.values(), key=lambda x: x["sku"])]
    if "schemes" in scope.get("metrics", []):
        res = []
        for s in ctx.db.scalars(select(Scheme).where(Scheme.organization_id == dist.id, Scheme.active_version_id.is_not(None))).all():
            v = ctx.db.get(SchemeVersion, s.active_version_id)
            if not set((v.scope or {}).get("brands", [])) & set(brands):
                continue
            total = ctx.db.scalar(
                select(func.sum(SchemeApplication.benefit_amount)).join(Order, Order.id == SchemeApplication.order_id).where(SchemeApplication.scheme_id == s.id, Order.order_date >= d0, Order.order_date <= d1)
            )
            res.append({"scheme": s.name, "type": v.scheme_type, "valid": f"{v.start_date} to {v.end_date}", "benefit_given": str(money(total or 0))})
        out["schemes"] = res
    audit.record(ctx, "shared_data.view", "data_sharing_grant", g.id, meta={"distributor": dist.slug, "days": days}, org_id=ctx.org_id)
    ctx.db.commit()
    return out
