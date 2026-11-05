import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import distinct, func, or_, select

from app import audit
from app.common import Money, PageParams, Schema, commit_or_conflict, page_out, paginate
from app.deps import Ctx, require
from app.errors import AppError, conflict
from app.models import AuditEvent, Order, PriceList, PriceListItem, Product, Retailer, Supplier, Warehouse
from app.services import matching

router = APIRouter(tags=["parties"])


# ------------------------------------------------------------------------ retailers


class RetailerIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    billing_address: str | None = Field(default=None, max_length=500)
    delivery_address: str | None = Field(default=None, max_length=500)
    territory: str | None = Field(default=None, max_length=100)
    route: str | None = Field(default=None, max_length=100)
    gstin: str | None = Field(default=None, max_length=20)
    credit_limit: Money | None = Field(default=None, ge=0)
    payment_terms_days: int = Field(default=30, ge=0, le=365)
    price_list_id: uuid.UUID | None = None
    external_ids: dict[str, str] | None = None
    is_active: bool = True
    allow_duplicate: bool = False

    @field_validator("code")
    @classmethod
    def code_clean(cls, v):
        return v.strip().upper()


class RetailerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    billing_address: str | None = None
    delivery_address: str | None = None
    territory: str | None = None
    route: str | None = None
    gstin: str | None = None
    credit_limit: Money | None = Field(default=None, ge=0)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    price_list_id: uuid.UUID | None = None
    external_ids: dict[str, str] | None = None
    is_active: bool | None = None
    version: int | None = None


class RetailerOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    contact_name: str | None
    phone: str | None
    email: str | None
    billing_address: str | None
    delivery_address: str | None
    territory: str | None
    route: str | None
    gstin: str | None
    credit_limit: Money | None
    payment_terms_days: int
    price_list_id: uuid.UUID | None
    external_ids: dict | None
    is_active: bool
    is_synthetic: bool
    version: int
    created_at: datetime


def find_retailer_duplicates(db, org_id, name: str, phone: str | None, exclude_id=None, limit=5):
    out = []
    digits = "".join(c for c in (phone or "") if c.isdigit())[-10:]
    for r in db.scalars(select(Retailer).where(Retailer.organization_id == org_id)).all():
        if exclude_id and r.id == exclude_id:
            continue
        rdigits = "".join(c for c in (r.phone or "") if c.isdigit())[-10:]
        if digits and len(digits) == 10 and digits == rdigits:
            out.append({"id": str(r.id), "code": r.code, "name": r.name, "score": 1.0, "reasons": ["same phone number"]})
            continue
        s = matching.similarity(name, r.name)
        if s >= 0.85:
            out.append({"id": str(r.id), "code": r.code, "name": r.name, "score": round(s, 3), "reasons": [f"similar name ({s:.2f})"]})
    out.sort(key=lambda d: -d["score"])
    return out[:limit]


@router.get("/retailers")
def list_retailers(
    params: PageParams = Depends(),
    territory: str | None = None,
    route: str | None = None,
    is_active: bool | None = None,
    ctx: Ctx = Depends(require("retailers.read")),
):
    stmt = select(Retailer).where(Retailer.organization_id == ctx.org_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Retailer.name.ilike(like), Retailer.code.ilike(like), Retailer.phone.ilike(like)))
    if territory:
        stmt = stmt.where(Retailer.territory == territory)
    if route:
        stmt = stmt.where(Retailer.route == route)
    if is_active is not None:
        stmt = stmt.where(Retailer.is_active.is_(is_active))
    rows, total = paginate(ctx.db, stmt, params, Retailer, ("code", "name", "territory", "created_at", "credit_limit"), Retailer.code)
    items = [RetailerOut.model_validate(r).model_dump(mode="json") for r in rows]
    if not ctx.can("finance.read"):
        for i in items:  # sales reps see customers, not credit terms
            i["credit_limit"] = None
    return page_out(items, total, params)


@router.get("/retailers/facets")
def retailer_facets(ctx: Ctx = Depends(require("retailers.read"))):
    t = ctx.db.scalars(select(distinct(Retailer.territory)).where(Retailer.organization_id == ctx.org_id, Retailer.territory.is_not(None)).order_by(Retailer.territory)).all()
    r = ctx.db.scalars(select(distinct(Retailer.route)).where(Retailer.organization_id == ctx.org_id, Retailer.route.is_not(None)).order_by(Retailer.route)).all()
    return {"territories": t, "routes": r}


@router.get("/retailers/duplicate-check")
def retailer_duplicate_check(
    name: str = Query(..., max_length=200), phone: str | None = None, exclude_id: uuid.UUID | None = None, ctx: Ctx = Depends(require("retailers.read"))
):
    return find_retailer_duplicates(ctx.db, ctx.org_id, name, phone, exclude_id)


@router.post("/retailers", status_code=201)
def create_retailer(body: RetailerIn, ctx: Ctx = Depends(require("retailers.write"))):
    if body.credit_limit is not None and not ctx.can("finance.approve"):
        raise AppError("FORBIDDEN", "Setting a credit limit requires the finance.approve permission.", 403)
    if body.price_list_id:
        ctx.get(PriceList, body.price_list_id, "Price list")
    if ctx.db.scalars(select(Retailer).where(Retailer.organization_id == ctx.org_id, Retailer.code == body.code)).first():
        raise conflict("DUPLICATE_CODE", f"Retailer code {body.code} already exists.")
    if not body.allow_duplicate:
        dups = find_retailer_duplicates(ctx.db, ctx.org_id, body.name, body.phone)
        if dups:
            raise conflict("POSSIBLE_DUPLICATE", "Similar retailers already exist. Review them or confirm to create anyway.", {"candidates": dups})
    r = Retailer(organization_id=ctx.org_id, **body.model_dump(exclude={"allow_duplicate"}))
    ctx.db.add(r)
    ctx.db.flush()
    audit.record(ctx, "retailer.create", "retailer", r.id, after=audit.snapshot(r))
    commit_or_conflict(ctx.db, "DUPLICATE_CODE", "Retailer code already exists.")
    return RetailerOut.model_validate(r)


@router.get("/retailers/{retailer_id}")
def get_retailer(retailer_id: uuid.UUID, ctx: Ctx = Depends(require("retailers.read"))):
    r = ctx.get(Retailer, retailer_id, "Retailer")
    out = RetailerOut.model_validate(r).model_dump(mode="json")
    orders = ctx.db.scalars(select(Order).where(Order.organization_id == ctx.org_id, Order.retailer_id == r.id).order_by(Order.created_at.desc()).limit(10)).all()
    out["recent_orders"] = [{"id": str(o.id), "order_number": o.order_number, "status": o.status, "net_total": str(o.net_total), "order_date": o.order_date.isoformat()} for o in orders]
    out["order_count"] = ctx.db.scalar(select(func.count()).select_from(Order).where(Order.organization_id == ctx.org_id, Order.retailer_id == r.id))
    if ctx.can("finance.read"):
        from app.services.receivables import credit_exposure

        out["credit"] = credit_exposure(ctx.db, ctx.org, r)
    else:
        out["credit_limit"] = None
    events = ctx.db.scalars(
        select(AuditEvent).where(AuditEvent.organization_id == ctx.org_id, AuditEvent.entity_type == "retailer", AuditEvent.entity_id == str(r.id)).order_by(AuditEvent.created_at.desc()).limit(20)
    ).all()
    out["activity"] = [{"action": e.action, "at": e.created_at.isoformat(), "after": e.after} for e in events]
    return out


@router.patch("/retailers/{retailer_id}")
def update_retailer(retailer_id: uuid.UUID, body: RetailerUpdate, ctx: Ctx = Depends(require("retailers.write"))):
    r = ctx.get(Retailer, retailer_id, "Retailer", lock=True)
    data = body.model_dump(exclude_unset=True)
    expected = data.pop("version", None)
    if expected is not None and expected != r.version:
        raise conflict("CONCURRENT_MODIFICATION", "This retailer was changed by someone else. Reload and retry.")
    if "credit_limit" in data and not ctx.can("finance.approve"):
        raise AppError("FORBIDDEN", "Changing a credit limit requires the finance.approve permission.", 403)
    if data.get("price_list_id"):
        ctx.get(PriceList, data["price_list_id"], "Price list")
    before = audit.snapshot(r)
    for k, v in data.items():
        setattr(r, k, v)
    r.version += 1
    b, a = audit.diff(before, audit.snapshot(r))
    audit.record(ctx, "retailer.credit_limit_change" if "credit_limit" in a else "retailer.update", "retailer", r.id, before=b, after=a)
    commit_or_conflict(ctx.db)
    return RetailerOut.model_validate(r)


class CreditTermsIn(BaseModel):
    credit_limit: Money | None = Field(default=None, ge=0)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    reason: str = Field(min_length=3, max_length=500)


@router.put("/retailers/{retailer_id}/credit-terms")
def set_credit_terms(retailer_id: uuid.UUID, body: CreditTermsIn, ctx: Ctx = Depends(require("finance.approve"))):
    """Credit limit / terms are a financial control, managed separately from contact data."""
    r = ctx.get(Retailer, retailer_id, "Retailer", lock=True)
    before = {"credit_limit": audit.jsonable(r.credit_limit), "payment_terms_days": r.payment_terms_days}
    r.credit_limit = body.credit_limit
    if body.payment_terms_days is not None:
        r.payment_terms_days = body.payment_terms_days
    r.version += 1
    after = {"credit_limit": audit.jsonable(r.credit_limit), "payment_terms_days": r.payment_terms_days}
    audit.record(ctx, "retailer.credit_limit_change", "retailer", r.id, before=before, after=after, meta={"reason": body.reason})
    ctx.db.commit()
    return RetailerOut.model_validate(r)


# ------------------------------------------------------------------------ suppliers


class SupplierIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None
    address: str | None = Field(default=None, max_length=500)
    gstin: str | None = Field(default=None, max_length=20)
    external_id: str | None = Field(default=None, max_length=100)
    is_active: bool = True
    allow_duplicate: bool = False

    @field_validator("code")
    @classmethod
    def code_clean(cls, v):
        return v.strip().upper()


class SupplierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    address: str | None = None
    gstin: str | None = None
    external_id: str | None = None
    is_active: bool | None = None
    version: int | None = None


class SupplierOut(Schema):
    id: uuid.UUID
    code: str
    name: str
    contact_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    gstin: str | None
    external_id: str | None
    is_active: bool
    is_synthetic: bool
    version: int


@router.get("/suppliers")
def list_suppliers(params: PageParams = Depends(), is_active: bool | None = None, ctx: Ctx = Depends(require("suppliers.read"))):
    stmt = select(Supplier).where(Supplier.organization_id == ctx.org_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Supplier.name.ilike(like), Supplier.code.ilike(like)))
    if is_active is not None:
        stmt = stmt.where(Supplier.is_active.is_(is_active))
    rows, total = paginate(ctx.db, stmt, params, Supplier, ("code", "name"), Supplier.name)
    return page_out([SupplierOut.model_validate(r) for r in rows], total, params)


@router.post("/suppliers", status_code=201)
def create_supplier(body: SupplierIn, ctx: Ctx = Depends(require("suppliers.write"))):
    if ctx.db.scalars(select(Supplier).where(Supplier.organization_id == ctx.org_id, Supplier.code == body.code)).first():
        raise conflict("DUPLICATE_CODE", f"Supplier code {body.code} already exists.")
    if not body.allow_duplicate:
        dups = [
            {"id": str(s.id), "code": s.code, "name": s.name, "score": round(sc, 3)}
            for s in ctx.db.scalars(select(Supplier).where(Supplier.organization_id == ctx.org_id)).all()
            if (sc := matching.similarity(body.name, s.name)) >= 0.85
        ]
        if dups:
            raise conflict("POSSIBLE_DUPLICATE", "Similar suppliers already exist.", {"candidates": dups})
    s = Supplier(organization_id=ctx.org_id, **body.model_dump(exclude={"allow_duplicate"}))
    ctx.db.add(s)
    ctx.db.flush()
    audit.record(ctx, "supplier.create", "supplier", s.id, after=audit.snapshot(s))
    commit_or_conflict(ctx.db, "DUPLICATE_CODE", "Supplier code already exists.")
    return SupplierOut.model_validate(s)


@router.get("/suppliers/{supplier_id}")
def get_supplier(supplier_id: uuid.UUID, ctx: Ctx = Depends(require("suppliers.read"))):
    s = ctx.get(Supplier, supplier_id, "Supplier")
    out = SupplierOut.model_validate(s).model_dump(mode="json")
    out["product_count"] = ctx.db.scalar(select(func.count()).select_from(Product).where(Product.organization_id == ctx.org_id, Product.supplier_id == s.id))
    return out


@router.patch("/suppliers/{supplier_id}")
def update_supplier(supplier_id: uuid.UUID, body: SupplierUpdate, ctx: Ctx = Depends(require("suppliers.write"))):
    s = ctx.get(Supplier, supplier_id, "Supplier", lock=True)
    data = body.model_dump(exclude_unset=True)
    expected = data.pop("version", None)
    if expected is not None and expected != s.version:
        raise conflict("CONCURRENT_MODIFICATION", "This supplier was changed by someone else.")
    before = audit.snapshot(s)
    for k, v in data.items():
        setattr(s, k, v)
    s.version += 1
    b, a = audit.diff(before, audit.snapshot(s))
    audit.record(ctx, "supplier.update", "supplier", s.id, before=b, after=a)
    commit_or_conflict(ctx.db)
    return SupplierOut.model_validate(s)


# ------------------------------------------------------------------------ warehouses


class WarehouseIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=500)
    is_default: bool = False
    is_active: bool = True


class WarehouseUpdate(BaseModel):
    name: str | None = None
    address: str | None = None
    is_default: bool | None = None
    is_active: bool | None = None


def wh_out(w: Warehouse):
    return {"id": str(w.id), "code": w.code, "name": w.name, "address": w.address, "is_default": w.is_default, "is_active": w.is_active}


@router.get("/warehouses")
def list_warehouses(ctx: Ctx = Depends(require("org.read"))):
    rows = ctx.db.scalars(select(Warehouse).where(Warehouse.organization_id == ctx.org_id).order_by(Warehouse.code)).all()
    return [wh_out(w) for w in rows]


def _single_default(ctx, keep: Warehouse):
    for w in ctx.db.scalars(select(Warehouse).where(Warehouse.organization_id == ctx.org_id, Warehouse.id != keep.id, Warehouse.is_default.is_(True))).all():
        w.is_default = False


@router.post("/warehouses", status_code=201)
def create_warehouse(body: WarehouseIn, ctx: Ctx = Depends(require("warehouses.manage"))):
    w = Warehouse(organization_id=ctx.org_id, code=body.code.strip().upper(), name=body.name, address=body.address, is_default=body.is_default, is_active=body.is_active)
    if not ctx.db.scalar(select(func.count()).select_from(Warehouse).where(Warehouse.organization_id == ctx.org_id)):
        w.is_default = True
    ctx.db.add(w)
    ctx.db.flush()
    if w.is_default:
        _single_default(ctx, w)
    audit.record(ctx, "warehouse.create", "warehouse", w.id, after=wh_out(w))
    commit_or_conflict(ctx.db, "DUPLICATE_CODE", "Warehouse code already exists.")
    return wh_out(w)


@router.patch("/warehouses/{warehouse_id}")
def update_warehouse(warehouse_id: uuid.UUID, body: WarehouseUpdate, ctx: Ctx = Depends(require("warehouses.manage"))):
    w = ctx.get(Warehouse, warehouse_id, "Warehouse")
    before = wh_out(w)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(w, k, v)
    if w.is_default:
        _single_default(ctx, w)
    b, a = audit.diff(before, wh_out(w))
    audit.record(ctx, "warehouse.update", "warehouse", w.id, before=b, after=a)
    ctx.db.commit()
    return wh_out(w)


# ------------------------------------------------------------------------ price lists


class PriceListIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    is_default: bool = False
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool = True
    notes: str | None = None


class PriceListUpdate(BaseModel):
    name: str | None = None
    is_default: bool | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool | None = None
    notes: str | None = None


class PriceItemIn(BaseModel):
    product_id: uuid.UUID
    unit_price: Money = Field(ge=0)


class PriceItemsIn(BaseModel):
    items: list[PriceItemIn] = Field(min_length=1, max_length=2000)


def pl_out(pl: PriceList, count: int | None = None):
    return {
        "id": str(pl.id),
        "name": pl.name,
        "currency": pl.currency,
        "is_default": pl.is_default,
        "valid_from": pl.valid_from.isoformat() if pl.valid_from else None,
        "valid_to": pl.valid_to.isoformat() if pl.valid_to else None,
        "is_active": pl.is_active,
        "notes": pl.notes,
        "item_count": count,
    }


@router.get("/price-lists")
def list_price_lists(ctx: Ctx = Depends(require("pricing.read"))):
    rows = ctx.db.execute(
        select(PriceList, func.count(PriceListItem.id))
        .outerjoin(PriceListItem, PriceListItem.price_list_id == PriceList.id)
        .where(PriceList.organization_id == ctx.org_id)
        .group_by(PriceList.id)
        .order_by(PriceList.name)
    ).all()
    return [pl_out(pl, c) for pl, c in rows]


@router.post("/price-lists", status_code=201)
def create_price_list(body: PriceListIn, ctx: Ctx = Depends(require("pricing.write"))):
    if body.valid_from and body.valid_to and body.valid_to < body.valid_from:
        raise AppError("INVALID_DATES", "valid_to must be on or after valid_from.", 422)
    pl = PriceList(organization_id=ctx.org_id, **body.model_dump())
    ctx.db.add(pl)
    ctx.db.flush()
    audit.record(ctx, "price_list.create", "price_list", pl.id, after=pl_out(pl))
    commit_or_conflict(ctx.db, "DUPLICATE_NAME", "A price list with this name exists.")
    return pl_out(pl)


@router.patch("/price-lists/{price_list_id}")
def update_price_list(price_list_id: uuid.UUID, body: PriceListUpdate, ctx: Ctx = Depends(require("pricing.write"))):
    pl = ctx.get(PriceList, price_list_id, "Price list")
    before = pl_out(pl)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(pl, k, v)
    b, a = audit.diff(before, pl_out(pl))
    audit.record(ctx, "price_list.update", "price_list", pl.id, before=b, after=a)
    commit_or_conflict(ctx.db)
    return pl_out(pl)


@router.get("/price-lists/{price_list_id}/items")
def list_price_items(price_list_id: uuid.UUID, params: PageParams = Depends(), ctx: Ctx = Depends(require("pricing.read"))):
    pl = ctx.get(PriceList, price_list_id, "Price list")
    stmt = (
        select(PriceListItem, Product)
        .join(Product, Product.id == PriceListItem.product_id)
        .where(PriceListItem.price_list_id == pl.id, PriceListItem.organization_id == ctx.org_id)
    )
    if params.q:
        stmt = stmt.where(or_(Product.name.ilike(f"%{params.q}%"), Product.sku.ilike(f"%{params.q}%")))
    total = ctx.db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = ctx.db.execute(stmt.order_by(Product.sku).offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    items = [
        {"id": str(i.id), "product_id": str(p.id), "sku": p.sku, "name": p.name, "variant": p.variant, "base_unit": p.base_unit, "unit_price": str(i.unit_price)}
        for i, p in rows
    ]
    return page_out(items, total, params)


@router.put("/price-lists/{price_list_id}/items")
def upsert_price_items(price_list_id: uuid.UUID, body: PriceItemsIn, ctx: Ctx = Depends(require("pricing.write"))):
    pl = ctx.get(PriceList, price_list_id, "Price list")
    pids = {i.product_id for i in body.items}
    owned = set(ctx.db.scalars(select(Product.id).where(Product.organization_id == ctx.org_id, Product.id.in_(pids))).all())
    if owned != pids:
        raise AppError("NOT_FOUND", "One or more products were not found.", 404)
    existing = {i.product_id: i for i in ctx.db.scalars(select(PriceListItem).where(PriceListItem.price_list_id == pl.id, PriceListItem.product_id.in_(pids))).all()}
    changes = []
    for it in body.items:
        cur = existing.get(it.product_id)
        if cur:
            if cur.unit_price != it.unit_price:
                changes.append({"product_id": str(it.product_id), "from": str(cur.unit_price), "to": str(it.unit_price)})
                cur.unit_price = it.unit_price
        else:
            ctx.db.add(PriceListItem(organization_id=ctx.org_id, price_list_id=pl.id, product_id=it.product_id, unit_price=it.unit_price))
            changes.append({"product_id": str(it.product_id), "from": None, "to": str(it.unit_price)})
    if changes:
        audit.record(ctx, "price_list.items_update", "price_list", pl.id, meta={"changes": changes[:500], "count": len(changes)})
    ctx.db.commit()
    return {"updated": len(changes)}
