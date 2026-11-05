import re
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import func, select

from app import audit
from app.config import get_settings
from app.db import utcnow
from app.deps import Ctx, get_ctx, require
from app.errors import AppError, forbidden, not_found
from app.models import (
    InventoryMovement,
    Invitation,
    Membership,
    Organization,
    PriceList,
    PriceListItem,
    Product,
    Retailer,
    Supplier,
    UnitConversion,
    UnitOfMeasure,
    User,
    Warehouse,
)
from app.models.identity import DEFAULT_ORG_SETTINGS
from app.permissions import DISTRIBUTOR_ROLES, MANUFACTURER_ROLES
from app.routers.auth import issue_tokens
from app.security import new_opaque_token

router = APIRouter(tags=["organizations"])


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    org_type: str = Field(default="distributor", pattern="^(distributor|manufacturer)$")
    timezone: str = "Asia/Kolkata"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    gstin: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=500)


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    timezone: str | None = None
    gstin: str | None = Field(default=None, max_length=20)
    address: str | None = Field(default=None, max_length=500)
    settings: dict[str, Any] | None = None
    ai_external_enabled: bool | None = None

    @field_validator("settings")
    @classmethod
    def known_keys(cls, v):
        if v is None:
            return v
        unknown = set(v) - set(DEFAULT_ORG_SETTINGS)
        if unknown:
            raise ValueError(f"Unknown settings: {sorted(unknown)}")
        if "negative_stock_policy" in v and v["negative_stock_policy"] not in ("block", "allow_with_override"):
            raise ValueError("negative_stock_policy must be 'block' or 'allow_with_override'")
        if "ageing_buckets" in v:
            b = v["ageing_buckets"]
            if not isinstance(b, list) or not b or any(not isinstance(x, int) or x <= 0 for x in b) or b != sorted(set(b)):
                raise ValueError("ageing_buckets must be a strictly increasing list of positive integers")
        return v


def org_out(o: Organization) -> dict:
    return {
        "id": str(o.id),
        "name": o.name,
        "slug": o.slug,
        "org_type": o.org_type,
        "timezone": o.timezone,
        "currency": o.currency,
        "gstin": o.gstin,
        "address": o.address,
        "is_demo": o.is_demo,
        "ai_external_enabled": o.ai_external_enabled,
        "settings": {**DEFAULT_ORG_SETTINGS, **(o.settings or {})},
    }


def _slug(db, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    slug, n = base, 1
    while db.scalars(select(Organization).where(Organization.slug == slug)).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def bootstrap_org(db, org: Organization) -> None:
    """Default units and price list for a new distributor."""
    if org.org_type != "distributor":
        return
    for code, name, is_base in (("PCS", "Piece", True), ("CASE", "Case", False), ("DOZEN", "Dozen", False), ("BOX", "Box", False)):
        db.add(UnitOfMeasure(organization_id=org.id, code=code, name=name, is_base=is_base))
    db.add(UnitConversion(organization_id=org.id, product_id=None, unit_code="DOZEN", factor=Decimal("12")))
    db.add(PriceList(organization_id=org.id, name="Standard", currency=org.currency, is_default=True))


def create_organization(db, user: User, body: OrgCreate, ctx_like=None) -> Organization:
    org = Organization(
        name=body.name.strip(),
        slug=_slug(db, body.name),
        org_type=body.org_type,
        timezone=body.timezone,
        currency=body.currency.upper(),
        gstin=body.gstin,
        address=body.address,
        settings={},
    )
    db.add(org)
    db.flush()
    db.add(Membership(user_id=user.id, organization_id=org.id, role="org_owner"))
    bootstrap_org(db, org)
    return org


@router.post("/organizations", status_code=201)
def create_org(body: OrgCreate, response: Response, ctx: Ctx = Depends(get_ctx)):
    org = create_organization(ctx.db, ctx.user, body)
    audit.record(ctx, "organization.create", "organization", org.id, after=org_out(org), org_id=org.id)
    tokens = issue_tokens(ctx.db, response, ctx.user, org.id)
    ctx.db.commit()
    return {"organization": org_out(org), "tokens": tokens}


@router.get("/organizations/current")
def get_current(ctx: Ctx = Depends(require("org.read"))):
    return org_out(ctx.org)


@router.patch("/organizations/current")
def update_current(body: OrgUpdate, ctx: Ctx = Depends(require("org.manage"))):
    org = ctx.org
    before = org_out(org)
    data = body.model_dump(exclude_unset=True)
    if "settings" in data:
        org.settings = {**(org.settings or {}), **data.pop("settings")}
    for k, v in data.items():
        setattr(org, k, v)
    after = org_out(org)
    b, a = audit.diff(before, after)
    audit.record(ctx, "organization.update", "organization", org.id, before=b, after=a)
    ctx.db.commit()
    return after


@router.get("/organizations/current/onboarding")
def onboarding(ctx: Ctx = Depends(require("org.read"))):
    db, oid = ctx.db, ctx.org_id

    def count(model, *conds):
        return db.scalar(select(func.count()).select_from(model).where(model.organization_id == oid, *conds)) or 0

    steps = [
        ("warehouses", "Add at least one warehouse", count(Warehouse) > 0, "/settings?tab=warehouses"),
        ("units", "Review units and conversion factors", count(UnitOfMeasure) > 0, "/settings?tab=units"),
        ("products", "Add products (or import CSV)", count(Product) > 0, "/products"),
        ("prices", "Set prices in a price list", count(PriceListItem) > 0, "/products?tab=pricing"),
        ("retailers", "Add retailers (or import CSV)", count(Retailer) > 0, "/retailers"),
        ("suppliers", "Add suppliers", count(Supplier) > 0, "/retailers?tab=suppliers"),
        (
            "opening_stock",
            "Record opening stock",
            count(InventoryMovement, InventoryMovement.movement_type == "opening_balance") > 0
            or count(InventoryMovement) > 0,
            "/inventory",
        ),
        ("team", "Invite your team", count(Membership) > 1, "/settings?tab=members"),
    ]
    items = [{"key": k, "label": label, "done": bool(done), "href": href} for k, label, done, href in steps]
    return {"items": items, "completed": sum(1 for i in items if i["done"]), "total": len(items)}


# ---------------------------------------------------------------- members & invitations


class InviteIn(BaseModel):
    email: EmailStr
    role: str


class MembershipUpdate(BaseModel):
    role: str | None = None
    is_active: bool | None = None


def _allowed_roles(ctx: Ctx):
    return MANUFACTURER_ROLES if ctx.org.org_type == "manufacturer" else DISTRIBUTOR_ROLES


@router.get("/users")
def list_members(ctx: Ctx = Depends(require("org.read"))):
    rows = ctx.db.execute(
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.organization_id == ctx.org_id)
        .order_by(User.full_name)
    ).all()
    full = ctx.can("users.manage")
    return [
        {
            "membership_id": str(m.id),
            "user_id": str(u.id),
            "full_name": u.full_name,
            "email": u.email if full else None,
            "role": m.role,
            "is_active": m.is_active,
            "last_login_at": u.last_login_at.isoformat() if (full and u.last_login_at) else None,
        }
        for m, u in rows
        if full or m.is_active
    ]


@router.patch("/users/memberships/{membership_id}")
def update_member(membership_id: uuid.UUID, body: MembershipUpdate, ctx: Ctx = Depends(require("users.manage"))):
    m = ctx.get(Membership, membership_id, "Membership")
    before = {"role": m.role, "is_active": m.is_active}
    if body.role is not None:
        if body.role not in _allowed_roles(ctx):
            raise AppError("INVALID_ROLE", f"Unknown role '{body.role}'.", 422)
        if body.role == "org_owner" and ctx.role != "org_owner":
            raise forbidden("Only an owner can grant the owner role.")
        if m.role == "org_owner" and ctx.role != "org_owner":
            raise forbidden("Only an owner can change another owner.")
        m.role = body.role
    if body.is_active is not None:
        if m.user_id == ctx.user_id and not body.is_active:
            raise AppError("CANNOT_DEACTIVATE_SELF", "You cannot deactivate your own membership.", 422)
        m.is_active = body.is_active
    ctx.db.flush()
    owners = ctx.db.scalar(
        select(func.count()).select_from(Membership).where(
            Membership.organization_id == ctx.org_id, Membership.role == "org_owner", Membership.is_active.is_(True)
        )
    )
    if not owners:
        ctx.db.rollback()
        raise AppError("LAST_OWNER", "An organisation must keep at least one active owner.", 422)
    audit.record(ctx, "membership.update", "membership", m.id, before=before, after={"role": m.role, "is_active": m.is_active})
    ctx.db.commit()
    return {"ok": True}


@router.get("/users/invitations")
def list_invitations(ctx: Ctx = Depends(require("users.manage"))):
    rows = ctx.db.scalars(
        select(Invitation).where(Invitation.organization_id == ctx.org_id).order_by(Invitation.created_at.desc())
    ).all()
    now = utcnow()
    return [
        {
            "id": str(i.id),
            "email": i.email,
            "role": i.role,
            "status": "accepted" if i.accepted_at else "revoked" if i.revoked_at else "expired" if i.expires_at < now else "pending",
            "expires_at": i.expires_at.isoformat(),
            "created_at": i.created_at.isoformat(),
        }
        for i in rows
    ]


@router.post("/users/invitations", status_code=201)
def invite(body: InviteIn, ctx: Ctx = Depends(require("users.manage"))):
    if body.role not in _allowed_roles(ctx):
        raise AppError("INVALID_ROLE", f"Unknown role '{body.role}'.", 422)
    if body.role == "org_owner" and ctx.role != "org_owner":
        raise forbidden("Only an owner can invite another owner.")
    token, th = new_opaque_token()
    inv = Invitation(
        organization_id=ctx.org_id,
        email=body.email.lower(),
        role=body.role,
        token_hash=th,
        expires_at=utcnow() + timedelta(days=7),
        invited_by_id=ctx.user_id,
    )
    ctx.db.add(inv)
    ctx.db.flush()
    audit.record(ctx, "invitation.create", "invitation", inv.id, after={"email": inv.email, "role": inv.role})
    ctx.db.commit()
    out = {"id": str(inv.id), "email": inv.email, "role": inv.role, "email_delivery": "not_configured"}
    # Email delivery is not configured: share the link manually. Never returned in production.
    if not get_settings().is_production:
        out["invite_token"] = token
    return out


@router.delete("/users/invitations/{invitation_id}")
def revoke_invitation(invitation_id: uuid.UUID, ctx: Ctx = Depends(require("users.manage"))):
    inv = ctx.get(Invitation, invitation_id, "Invitation")
    if inv.accepted_at:
        raise AppError("ALREADY_ACCEPTED", "Invitation already accepted.", 409)
    inv.revoked_at = utcnow()
    audit.record(ctx, "invitation.revoke", "invitation", inv.id)
    ctx.db.commit()
    return {"ok": True}


@router.post("/organizations/current/demo-data")
def load_demo_data(ctx: Ctx = Depends(require("org.manage"))):
    """Load a small synthetic dataset into the current (empty) organisation."""
    if get_settings().is_production:
        raise not_found()
    if ctx.org.org_type != "distributor":
        raise AppError("NOT_AVAILABLE", "Demo data is only available for distributor organisations.", 422)
    if ctx.db.scalar(select(func.count()).select_from(Product).where(Product.organization_id == ctx.org_id)):
        raise AppError("ORG_NOT_EMPTY", "Demo data can only be loaded into an organisation with no products.", 409)
    from app.seed import seed_org_data

    summary = seed_org_data(ctx.db, ctx.org, ctx.user, scale="small")
    ctx.org.is_demo = True
    audit.record(ctx, "organization.demo_data_loaded", "organization", ctx.org_id, meta=summary)
    ctx.db.commit()
    return summary
