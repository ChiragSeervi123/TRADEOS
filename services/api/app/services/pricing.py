"""Unit conversion and price resolution (deterministic)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import or_, select

from app.models import PriceList, PriceListItem, Product, Retailer, UnitConversion


def conversion_factor(db, org_id, product: Product, unit_code: str, on: date | None = None) -> Decimal | None:
    """Factor converting 1 `unit_code` into the product's base unit, or None if unknown.
    Product-specific conversions take precedence over organisation-wide ones."""
    unit = (unit_code or "").upper()
    if not unit or unit == product.base_unit.upper():
        return Decimal("1")
    on = on or date.today()
    rows = db.scalars(
        select(UnitConversion).where(
            UnitConversion.organization_id == org_id,
            UnitConversion.unit_code == unit,
            or_(UnitConversion.product_id == product.id, UnitConversion.product_id.is_(None)),
            or_(UnitConversion.effective_from.is_(None), UnitConversion.effective_from <= on),
            or_(UnitConversion.effective_to.is_(None), UnitConversion.effective_to >= on),
        )
    ).all()
    specific = [r for r in rows if r.product_id == product.id]
    pick = specific or [r for r in rows if r.product_id is None]
    if not pick:
        return None
    # Latest effective_from wins if several are valid.
    pick.sort(key=lambda r: (r.effective_from or date.min), reverse=True)
    return pick[0].factor


def units_for_product(db, org_id, product: Product, on: date | None = None) -> list[dict]:
    on = on or date.today()
    rows = db.scalars(
        select(UnitConversion).where(
            UnitConversion.organization_id == org_id,
            or_(UnitConversion.product_id == product.id, UnitConversion.product_id.is_(None)),
        )
    ).all()
    out = {product.base_unit: Decimal("1")}
    for r in sorted(rows, key=lambda r: r.product_id is not None):
        if (r.effective_from is None or r.effective_from <= on) and (r.effective_to is None or r.effective_to >= on):
            out[r.unit_code] = r.factor
    return [{"unit": u, "factor": str(f)} for u, f in out.items()]


def _valid(pl: PriceList, on: date) -> bool:
    return pl.is_active and (pl.valid_from is None or pl.valid_from <= on) and (pl.valid_to is None or pl.valid_to >= on)


def applicable_price_lists(db, org_id, retailer: Retailer | None, on: date) -> list[PriceList]:
    lists = []
    if retailer and retailer.price_list_id:
        pl = db.get(PriceList, retailer.price_list_id)
        if pl and pl.organization_id == org_id and _valid(pl, on):
            lists.append(pl)
    for pl in db.scalars(
        select(PriceList).where(PriceList.organization_id == org_id, PriceList.is_default.is_(True))
    ).all():
        if _valid(pl, on) and pl not in lists:
            lists.append(pl)
    return lists


def resolve_prices(db, org_id, product_ids, retailer: Retailer | None, on: date) -> dict:
    """{product_id: (unit_price_per_base_unit, price_list_name)}; missing => not priced."""
    out: dict = {}
    remaining = set(product_ids)
    for pl in applicable_price_lists(db, org_id, retailer, on):
        if not remaining:
            break
        items = db.scalars(
            select(PriceListItem).where(PriceListItem.price_list_id == pl.id, PriceListItem.product_id.in_(list(remaining)))
        ).all()
        for it in items:
            out[it.product_id] = (it.unit_price, pl.name)
            remaining.discard(it.product_id)
    return out
