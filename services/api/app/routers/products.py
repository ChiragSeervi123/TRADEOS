import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import distinct, or_, select

from app import audit
from app.common import Money, PageParams, Schema, commit_or_conflict, page_out, paginate
from app.deps import Ctx, require
from app.errors import AppError, conflict
from app.models import (
    AuditEvent,
    InventoryMovement,
    PriceListItem,
    Product,
    Supplier,
    UnitConversion,
    UnitOfMeasure,
    Warehouse,
)
from app.services import inventory, matching, pricing

router = APIRouter(tags=["products"])


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=250)
    brand: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    product_group: str | None = Field(default=None, max_length=200)
    variant: str | None = Field(default=None, max_length=80)
    base_unit: str = Field(default="PCS", max_length=20)
    tax_code: str | None = Field(default=None, max_length=20)
    tax_rate: Money = Field(default=Decimal("0"), ge=0, le=100)
    mrp: Money | None = Field(default=None, ge=0)
    cost_price: Money | None = Field(default=None, ge=0)
    barcode: str | None = Field(default=None, max_length=40)
    image_url: str | None = Field(default=None, max_length=500)
    supplier_id: uuid.UUID | None = None
    aliases: list[str] | None = None
    is_active: bool = True
    allow_duplicate: bool = False  # set after reviewing duplicate suggestions

    @field_validator("sku")
    @classmethod
    def sku_clean(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("base_unit")
    @classmethod
    def unit_upper(cls, v: str) -> str:
        return v.strip().upper()


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=250)
    brand: str | None = None
    category: str | None = None
    product_group: str | None = None
    variant: str | None = None
    tax_code: str | None = None
    tax_rate: Money | None = Field(default=None, ge=0, le=100)
    mrp: Money | None = Field(default=None, ge=0)
    cost_price: Money | None = Field(default=None, ge=0)
    barcode: str | None = None
    image_url: str | None = None
    supplier_id: uuid.UUID | None = None
    aliases: list[str] | None = None
    is_active: bool | None = None
    version: int | None = Field(default=None, description="Optimistic lock: current version")


class ProductOut(Schema):
    id: uuid.UUID
    sku: str
    name: str
    brand: str | None
    category: str | None
    product_group: str | None
    variant: str | None
    base_unit: str
    tax_code: str | None
    tax_rate: Money
    mrp: Money | None
    cost_price: Money | None
    barcode: str | None
    image_url: str | None
    supplier_id: uuid.UUID | None
    aliases: list[str] | None
    is_active: bool
    is_synthetic: bool
    version: int
    created_at: datetime
    updated_at: datetime


def find_product_duplicates(db, org_id, name: str, variant: str | None, barcode: str | None, exclude_id=None, limit=5):
    rows = db.scalars(select(Product).where(Product.organization_id == org_id)).all()
    out = []
    for p in rows:
        if exclude_id and p.id == exclude_id:
            continue
        reasons = []
        score = 0.0
        if barcode and p.barcode and barcode == p.barcode:
            score, reasons = 1.0, ["same barcode"]
        else:
            s = matching.similarity(f"{name} {variant or ''}", f"{p.name} {p.variant or ''}")
            if s >= 0.82:
                score, reasons = s, [f"similar name ({s:.2f})"]
        if score:
            out.append({"id": str(p.id), "sku": p.sku, "name": p.name, "variant": p.variant, "score": round(score, 3), "reasons": reasons})
    out.sort(key=lambda d: -d["score"])
    return out[:limit]


def _check_supplier(ctx: Ctx, supplier_id):
    if supplier_id:
        ctx.get(Supplier, supplier_id, "Supplier")


def _check_unit(ctx: Ctx, code: str):
    if not ctx.db.scalars(select(UnitOfMeasure).where(UnitOfMeasure.organization_id == ctx.org_id, UnitOfMeasure.code == code)).first():
        raise AppError("UNKNOWN_UNIT", f"Unit '{code}' is not configured.", 422)


@router.get("/products")
def list_products(
    params: PageParams = Depends(),
    brand: str | None = None,
    category: str | None = None,
    is_active: bool | None = None,
    supplier_id: uuid.UUID | None = None,
    ctx: Ctx = Depends(require("products.read")),
):
    stmt = select(Product).where(Product.organization_id == ctx.org_id)
    if params.q:
        like = f"%{params.q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like), Product.barcode.ilike(like), Product.brand.ilike(like)))
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if category:
        stmt = stmt.where(Product.category == category)
    if is_active is not None:
        stmt = stmt.where(Product.is_active.is_(is_active))
    if supplier_id:
        stmt = stmt.where(Product.supplier_id == supplier_id)
    rows, total = paginate(
        ctx.db, stmt, params, Product, ("sku", "name", "brand", "category", "created_at", "updated_at"), Product.sku
    )
    return page_out([ProductOut.model_validate(r) for r in rows], total, params)


@router.get("/products/facets")
def product_facets(ctx: Ctx = Depends(require("products.read"))):
    brands = ctx.db.scalars(select(distinct(Product.brand)).where(Product.organization_id == ctx.org_id, Product.brand.is_not(None)).order_by(Product.brand)).all()
    cats = ctx.db.scalars(select(distinct(Product.category)).where(Product.organization_id == ctx.org_id, Product.category.is_not(None)).order_by(Product.category)).all()
    return {"brands": brands, "categories": cats}


@router.get("/products/duplicate-check")
def duplicate_check(
    name: str = Query(..., max_length=250),
    variant: str | None = None,
    barcode: str | None = None,
    exclude_id: uuid.UUID | None = None,
    ctx: Ctx = Depends(require("products.read")),
):
    return find_product_duplicates(ctx.db, ctx.org_id, name, variant, barcode, exclude_id)


@router.post("/products", status_code=201)
def create_product(body: ProductIn, ctx: Ctx = Depends(require("products.write"))):
    _check_supplier(ctx, body.supplier_id)
    _check_unit(ctx, body.base_unit)
    if ctx.db.scalars(select(Product).where(Product.organization_id == ctx.org_id, Product.sku == body.sku)).first():
        raise conflict("DUPLICATE_SKU", f"SKU {body.sku} already exists.")
    if not body.allow_duplicate:
        dups = find_product_duplicates(ctx.db, ctx.org_id, body.name, body.variant, body.barcode)
        if dups:
            raise conflict("POSSIBLE_DUPLICATE", "Similar products already exist. Review them or confirm to create anyway.", {"candidates": dups})
    p = Product(organization_id=ctx.org_id, **body.model_dump(exclude={"allow_duplicate"}))
    ctx.db.add(p)
    ctx.db.flush()
    audit.record(ctx, "product.create", "product", p.id, after=audit.snapshot(p))
    commit_or_conflict(ctx.db, "DUPLICATE_SKU", "SKU already exists.")
    return ProductOut.model_validate(p)


@router.get("/products/{product_id}")
def get_product(product_id: uuid.UUID, ctx: Ctx = Depends(require("products.read"))):
    p = ctx.get(Product, product_id, "Product")
    out = ProductOut.model_validate(p).model_dump(mode="json")
    out["units"] = pricing.units_for_product(ctx.db, ctx.org_id, p)
    if ctx.can("inventory.read"):
        whs = {w.id: w for w in ctx.db.scalars(select(Warehouse).where(Warehouse.organization_id == ctx.org_id)).all()}
        bals = inventory.balances(ctx.db, ctx.org_id, product_ids=[p.id])
        out["stock"] = [
            {"warehouse_id": str(wh), "warehouse": whs[wh].name if wh in whs else "?", **b.as_dict()}
            for (wh, _pid), b in bals.items()
        ]
    if ctx.can("pricing.read"):
        prices = ctx.db.execute(
            select(PriceListItem.unit_price, PriceListItem.price_list_id).where(PriceListItem.product_id == p.id, PriceListItem.organization_id == ctx.org_id)
        ).all()
        out["prices"] = [{"price_list_id": str(pl), "unit_price": str(up)} for up, pl in prices]
    events = ctx.db.scalars(
        select(AuditEvent).where(AuditEvent.organization_id == ctx.org_id, AuditEvent.entity_type == "product", AuditEvent.entity_id == str(p.id)).order_by(AuditEvent.created_at.desc()).limit(20)
    ).all()
    out["activity"] = [{"action": e.action, "at": e.created_at.isoformat(), "actor_id": str(e.actor_id) if e.actor_id else None, "after": e.after} for e in events]
    if ctx.can("inventory.read"):
        mv = ctx.db.scalars(
            select(InventoryMovement).where(InventoryMovement.organization_id == ctx.org_id, InventoryMovement.product_id == p.id).order_by(InventoryMovement.created_at.desc()).limit(10)
        ).all()
        out["recent_movements"] = [{"type": m.movement_type, "bucket": m.bucket, "quantity": str(m.quantity), "at": m.created_at.isoformat()} for m in mv]
    return out


@router.patch("/products/{product_id}")
def update_product(product_id: uuid.UUID, body: ProductUpdate, ctx: Ctx = Depends(require("products.write"))):
    p = ctx.get(Product, product_id, "Product", lock=True)
    data = body.model_dump(exclude_unset=True)
    expected = data.pop("version", None)
    if expected is not None and expected != p.version:
        raise conflict("CONCURRENT_MODIFICATION", "This product was changed by someone else. Reload and retry.")
    if "supplier_id" in data:
        _check_supplier(ctx, data["supplier_id"])
    before = audit.snapshot(p)
    for k, v in data.items():
        setattr(p, k, v)
    p.version += 1
    b, a = audit.diff(before, audit.snapshot(p))
    action = "product.price_change" if {"cost_price", "mrp", "tax_rate"} & set(a) else "product.update"
    audit.record(ctx, action, "product", p.id, before=b, after=a)
    commit_or_conflict(ctx.db)
    return ProductOut.model_validate(p)


# ------------------------------------------------------------------ units & conversions


class UnitIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=80)


class ConversionIn(BaseModel):
    unit_code: str = Field(min_length=1, max_length=20)
    factor: Decimal = Field(gt=0)
    product_id: uuid.UUID | None = None
    effective_from: date | None = None
    effective_to: date | None = None


@router.get("/units")
def list_units(ctx: Ctx = Depends(require("products.read"))):
    units = ctx.db.scalars(select(UnitOfMeasure).where(UnitOfMeasure.organization_id == ctx.org_id).order_by(UnitOfMeasure.code)).all()
    convs = ctx.db.scalars(select(UnitConversion).where(UnitConversion.organization_id == ctx.org_id)).all()
    pmap = {}
    pids = {c.product_id for c in convs if c.product_id}
    if pids:
        pmap = {p.id: p.sku for p in ctx.db.scalars(select(Product).where(Product.id.in_(pids))).all()}
    return {
        "units": [{"id": str(u.id), "code": u.code, "name": u.name, "is_base": u.is_base} for u in units],
        "conversions": [
            {
                "id": str(c.id),
                "unit_code": c.unit_code,
                "factor": str(c.factor),
                "product_id": str(c.product_id) if c.product_id else None,
                "sku": pmap.get(c.product_id),
                "effective_from": c.effective_from.isoformat() if c.effective_from else None,
                "effective_to": c.effective_to.isoformat() if c.effective_to else None,
            }
            for c in convs
        ],
    }


@router.post("/units", status_code=201)
def create_unit(body: UnitIn, ctx: Ctx = Depends(require("products.write"))):
    u = UnitOfMeasure(organization_id=ctx.org_id, code=body.code.strip().upper(), name=body.name.strip())
    ctx.db.add(u)
    ctx.db.flush()
    audit.record(ctx, "unit.create", "unit", u.id, after=audit.snapshot(u))
    commit_or_conflict(ctx.db, "DUPLICATE_UNIT", "Unit code already exists.")
    return {"id": str(u.id), "code": u.code, "name": u.name}


@router.post("/units/conversions", status_code=201)
def create_conversion(body: ConversionIn, ctx: Ctx = Depends(require("products.write"))):
    code = body.unit_code.strip().upper()
    _check_unit(ctx, code)
    if body.product_id:
        p = ctx.get(Product, body.product_id, "Product")
        if p.base_unit == code:
            raise AppError("INVALID_CONVERSION", "A product's base unit always has factor 1.", 422)
    if body.effective_from and body.effective_to and body.effective_to < body.effective_from:
        raise AppError("INVALID_DATES", "effective_to must be on or after effective_from.", 422)
    c = UnitConversion(organization_id=ctx.org_id, unit_code=code, factor=body.factor, product_id=body.product_id, effective_from=body.effective_from, effective_to=body.effective_to)
    ctx.db.add(c)
    ctx.db.flush()
    audit.record(ctx, "unit_conversion.create", "unit_conversion", c.id, after=audit.snapshot(c))
    ctx.db.commit()
    return {"id": str(c.id)}


@router.delete("/units/conversions/{conversion_id}")
def delete_conversion(conversion_id: uuid.UUID, ctx: Ctx = Depends(require("products.write"))):
    c = ctx.get(UnitConversion, conversion_id, "Conversion")
    audit.record(ctx, "unit_conversion.delete", "unit_conversion", c.id, before=audit.snapshot(c))
    ctx.db.delete(c)
    ctx.db.commit()
    return {"ok": True}
