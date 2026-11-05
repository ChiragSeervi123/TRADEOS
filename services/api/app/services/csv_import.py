"""CSV import pipeline: parse -> map columns -> validate -> preview -> commit.

Commit semantics: all-or-nothing in one transaction. Rows are re-validated at
commit time. If any row is invalid the commit is refused unless the user
explicitly passes skip_invalid=true, in which case skipped rows are reported.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Callable

from sqlalchemy import select

from app.errors import AppError
from app.models import PriceList, PriceListItem, Product, Retailer, Supplier, UnitOfMeasure, Warehouse
from app.money import D, money, qty
from app.services import inventory, pricing

MAX_ROWS = 10000


@dataclass
class Col:
    name: str
    required: bool = False
    aliases: tuple[str, ...] = ()
    kind: str = "str"  # str|decimal|int|bool|date
    help: str = ""


@dataclass
class EntitySpec:
    entity: str
    columns: list[Col]
    permission: str
    example: list[dict] = field(default_factory=list)
    commit: Callable | None = None
    validate: Callable | None = None


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def _parse_value(col: Col, raw: str):
    v = (raw or "").strip()
    if v == "":
        return None
    if col.kind == "decimal":
        d = D(v)
        return d
    if col.kind == "int":
        return int(D(v))
    if col.kind == "bool":
        if v.lower() in ("1", "true", "yes", "y", "active"):
            return True
        if v.lower() in ("0", "false", "no", "n", "inactive"):
            return False
        raise ValueError("expected yes/no")
    if col.kind == "date":
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                from datetime import datetime

                return datetime.strptime(v, fmt).date()
            except ValueError:
                continue
        raise ValueError("expected a date (YYYY-MM-DD or DD-MM-YYYY)")
    return v


def parse_csv(content: bytes) -> tuple[list[str], list[dict]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise AppError("INVALID_ENCODING", "CSV must be UTF-8 encoded.", 422) from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError("EMPTY_FILE", "The CSV file has no header row.", 422)
    rows = []
    for i, r in enumerate(reader):
        if i >= MAX_ROWS:
            raise AppError("TOO_MANY_ROWS", f"Imports are limited to {MAX_ROWS} rows per file.", 422)
        if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
            continue
        rows.append(r)
    return list(reader.fieldnames), rows


def auto_mapping(spec: EntitySpec, headers: list[str]) -> dict[str, str]:
    """{target_column: source_header}"""
    normed = {_norm_header(h): h for h in headers}
    mapping = {}
    for col in spec.columns:
        for cand in (col.name, *col.aliases):
            if _norm_header(cand) in normed:
                mapping[col.name] = normed[_norm_header(cand)]
                break
    return mapping


def normalise_rows(spec: EntitySpec, rows: list[dict], mapping: dict[str, str]) -> tuple[list[dict], list[dict]]:
    missing = [c.name for c in spec.columns if c.required and c.name not in mapping]
    if missing:
        raise AppError("MISSING_COLUMNS", f"Required columns are not mapped: {', '.join(missing)}", 422, {"missing": missing})
    out, errors = [], []
    for idx, raw in enumerate(rows, start=2):  # row 1 is the header
        rec = {"_row": idx}
        for col in spec.columns:
            src = mapping.get(col.name)
            try:
                val = _parse_value(col, raw.get(src, "") if src else "")
            except (ValueError, ArithmeticError) as exc:
                errors.append({"row": idx, "field": col.name, "message": str(exc) or f"invalid {col.kind}"})
                val = None
            if col.required and val is None and not any(e["row"] == idx and e["field"] == col.name for e in errors):
                errors.append({"row": idx, "field": col.name, "message": "is required"})
            rec[col.name] = val
        out.append(rec)
    return out, errors


def _jsonable_rows(rows):
    def conv(v):
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, date):
            return v.isoformat()
        return v

    return [{k: conv(v) for k, v in r.items()} for r in rows]


def _rehydrate(spec: EntitySpec, rows):
    kinds = {c.name: c.kind for c in spec.columns}
    out = []
    for r in rows:
        rec = dict(r)
        for k, v in r.items():
            if v is None:
                continue
            if kinds.get(k) == "decimal":
                rec[k] = Decimal(v)
            elif kinds.get(k) == "date":
                rec[k] = date.fromisoformat(v)
        out.append(rec)
    return out


# ---------------------------------------------------------------- entity validators


def _existing(db, model, org_id, field_name):
    return {getattr(r, field_name): r for r in db.scalars(select(model).where(model.organization_id == org_id)).all()}


def _validate_products(db, org_id, rows):
    errors = []
    existing = _existing(db, Product, org_id, "sku")
    units = {u.code for u in db.scalars(select(UnitOfMeasure).where(UnitOfMeasure.organization_id == org_id)).all()}
    suppliers = _existing(db, Supplier, org_id, "code")
    seen = set()
    for r in rows:
        sku = (r.get("sku") or "").strip().upper()
        r["sku"] = sku
        if sku in seen:
            errors.append({"row": r["_row"], "field": "sku", "message": "duplicate SKU within file"})
        seen.add(sku)
        r["_action"] = "update" if sku in existing else "create"
        bu = (r.get("base_unit") or "PCS").upper()
        r["base_unit"] = bu
        if bu not in units:
            errors.append({"row": r["_row"], "field": "base_unit", "message": f"unknown unit '{bu}'"})
        if r.get("supplier_code") and r["supplier_code"].upper() not in suppliers:
            errors.append({"row": r["_row"], "field": "supplier_code", "message": "unknown supplier code"})
        for f in ("tax_rate", "mrp", "cost_price"):
            if r.get(f) is not None and D(r[f]) < 0:
                errors.append({"row": r["_row"], "field": f, "message": "must not be negative"})
        if r.get("tax_rate") is not None and D(r["tax_rate"]) > 100:
            errors.append({"row": r["_row"], "field": "tax_rate", "message": "must be <= 100"})
    return errors


def _commit_products(ctx, rows):
    existing = _existing(ctx.db, Product, ctx.org_id, "sku")
    suppliers = _existing(ctx.db, Supplier, ctx.org_id, "code")
    created = updated = 0
    for r in rows:
        data = {
            "name": r["name"],
            "brand": r.get("brand"),
            "category": r.get("category"),
            "variant": r.get("variant"),
            "product_group": r.get("product_group"),
            "base_unit": r["base_unit"],
            "tax_code": r.get("tax_code"),
            "tax_rate": money(r["tax_rate"]) if r.get("tax_rate") is not None else Decimal("0"),
            "mrp": money(r["mrp"]) if r.get("mrp") is not None else None,
            "cost_price": money(r["cost_price"]) if r.get("cost_price") is not None else None,
            "barcode": r.get("barcode"),
            "is_active": r.get("is_active") if r.get("is_active") is not None else True,
            "supplier_id": suppliers[r["supplier_code"].upper()].id if r.get("supplier_code") else None,
        }
        p = existing.get(r["sku"])
        if p:
            for k, v in data.items():
                if v is not None:
                    setattr(p, k, v)
            p.version += 1
            updated += 1
        else:
            ctx.db.add(Product(organization_id=ctx.org_id, sku=r["sku"], **data))
            created += 1
    return {"created": created, "updated": updated}


def _validate_parties(model):
    def v(db, org_id, rows):
        errors = []
        seen = set()
        existing = _existing(db, model, org_id, "code")
        for r in rows:
            code = (r.get("code") or "").strip().upper()
            r["code"] = code
            if code in seen:
                errors.append({"row": r["_row"], "field": "code", "message": "duplicate code within file"})
            seen.add(code)
            r["_action"] = "update" if code in existing else "create"
            if r.get("credit_limit") is not None and D(r["credit_limit"]) < 0:
                errors.append({"row": r["_row"], "field": "credit_limit", "message": "must not be negative"})
        return errors

    return v


def _commit_parties(model, fields):
    def c(ctx, rows):
        existing = _existing(ctx.db, model, ctx.org_id, "code")
        created = updated = 0
        for r in rows:
            data = {f: r.get(f) for f in fields if r.get(f) is not None}
            if "credit_limit" in data:
                data["credit_limit"] = money(data["credit_limit"])
            obj = existing.get(r["code"])
            if obj:
                for k, val in data.items():
                    setattr(obj, k, val)
                obj.version += 1
                updated += 1
            else:
                ctx.db.add(model(organization_id=ctx.org_id, code=r["code"], **data))
                created += 1
        return {"created": created, "updated": updated}

    return c


def _validate_opening_stock(db, org_id, rows):
    errors = []
    products = _existing(db, Product, org_id, "sku")
    whs = _existing(db, Warehouse, org_id, "code")
    for r in rows:
        sku = (r.get("sku") or "").upper()
        p = products.get(sku)
        if not p:
            errors.append({"row": r["_row"], "field": "sku", "message": "unknown SKU"})
        if (r.get("warehouse_code") or "").upper() not in whs:
            errors.append({"row": r["_row"], "field": "warehouse_code", "message": "unknown warehouse"})
        if r.get("quantity") is not None and D(r["quantity"]) <= 0:
            errors.append({"row": r["_row"], "field": "quantity", "message": "must be positive"})
        if p and r.get("unit") and pricing.conversion_factor(db, org_id, p, r["unit"]) is None:
            errors.append({"row": r["_row"], "field": "unit", "message": f"no conversion from {r['unit']} to {p.base_unit}"})
    return errors


def _commit_opening_stock(ctx, rows):
    products = _existing(ctx.db, Product, ctx.org_id, "sku")
    whs = _existing(ctx.db, Warehouse, ctx.org_id, "code")
    n = 0
    for r in rows:
        p = products[r["sku"].upper()]
        wh = whs[r["warehouse_code"].upper()]
        factor = pricing.conversion_factor(ctx.db, ctx.org_id, p, r.get("unit") or p.base_unit)
        batch = inventory.get_or_create_batch(ctx.db, ctx.org_id, p.id, r.get("batch_number"), r.get("expiry_date"))
        inventory.post(
            ctx.db,
            ctx.org_id,
            warehouse_id=wh.id,
            product_id=p.id,
            movement_type="opening_balance",
            quantity=qty(D(r["quantity"]) * factor),
            batch_id=batch.id if batch else None,
            actor_id=ctx.user.id,
            source_type="import",
            reason="Opening balance import",
        )
        n += 1
    return {"movements": n}


def _validate_prices(db, org_id, rows):
    errors = []
    products = _existing(db, Product, org_id, "sku")
    lists = _existing(db, PriceList, org_id, "name")
    seen = set()
    for r in rows:
        r["price_list"] = r.get("price_list") or "Standard"
        if r["price_list"] not in lists:
            errors.append({"row": r["_row"], "field": "price_list", "message": "unknown price list"})
        sku = (r.get("sku") or "").upper()
        if sku not in products:
            errors.append({"row": r["_row"], "field": "sku", "message": "unknown SKU"})
        key = (r["price_list"], sku)
        if key in seen:
            errors.append({"row": r["_row"], "field": "sku", "message": "duplicate price for SKU in file"})
        seen.add(key)
        if r.get("unit_price") is not None and D(r["unit_price"]) < 0:
            errors.append({"row": r["_row"], "field": "unit_price", "message": "must not be negative"})
    return errors


def _commit_prices(ctx, rows):
    products = _existing(ctx.db, Product, ctx.org_id, "sku")
    lists = _existing(ctx.db, PriceList, ctx.org_id, "name")
    n = 0
    for r in rows:
        pl = lists[r["price_list"]]
        p = products[r["sku"].upper()]
        item = ctx.db.scalars(select(PriceListItem).where(PriceListItem.price_list_id == pl.id, PriceListItem.product_id == p.id)).first()
        if item:
            item.unit_price = money(r["unit_price"])
        else:
            ctx.db.add(PriceListItem(organization_id=ctx.org_id, price_list_id=pl.id, product_id=p.id, unit_price=money(r["unit_price"])))
        n += 1
    return {"prices": n}


RETAILER_FIELDS = ("name", "contact_name", "phone", "email", "billing_address", "delivery_address", "territory", "route", "gstin", "credit_limit", "payment_terms_days", "is_active")
SUPPLIER_FIELDS = ("name", "contact_name", "phone", "email", "address", "gstin", "external_id", "is_active")

SPECS: dict[str, EntitySpec] = {
    "products": EntitySpec(
        "products",
        [
            Col("sku", True, ("sku_code", "item_code", "code")),
            Col("name", True, ("product_name", "item_name", "description")),
            Col("brand"),
            Col("category"),
            Col("variant", aliases=("pack_size", "size")),
            Col("product_group"),
            Col("base_unit", aliases=("unit", "uom")),
            Col("tax_code", aliases=("hsn",)),
            Col("tax_rate", kind="decimal", aliases=("gst_rate", "tax")),
            Col("mrp", kind="decimal"),
            Col("cost_price", kind="decimal", aliases=("purchase_price", "cost")),
            Col("barcode", aliases=("gtin", "ean")),
            Col("supplier_code"),
            Col("is_active", kind="bool", aliases=("active",)),
        ],
        "products.write",
        [{"sku": "SKU-1001", "name": "Example Detergent", "brand": "ExampleBrand", "category": "Home Care", "variant": "1 kg", "base_unit": "PCS", "tax_rate": "18", "mrp": "120", "cost_price": "95", "barcode": "", "supplier_code": "", "is_active": "yes"}],
        _commit_products,
        _validate_products,
    ),
    "retailers": EntitySpec(
        "retailers",
        [
            Col("code", True, ("retailer_code", "customer_code")),
            Col("name", True, ("business_name", "retailer_name", "customer_name")),
            Col("contact_name"),
            Col("phone", aliases=("mobile",)),
            Col("email"),
            Col("billing_address"),
            Col("delivery_address"),
            Col("territory"),
            Col("route"),
            Col("gstin"),
            Col("credit_limit", kind="decimal"),
            Col("payment_terms_days", kind="int", aliases=("payment_terms",)),
            Col("is_active", kind="bool", aliases=("active",)),
        ],
        "retailers.write",
        [{"code": "R-1001", "name": "Example General Store", "phone": "9800000000", "territory": "North", "route": "R1", "credit_limit": "50000", "payment_terms_days": "30", "is_active": "yes"}],
        _commit_parties(Retailer, RETAILER_FIELDS),
        _validate_parties(Retailer),
    ),
    "suppliers": EntitySpec(
        "suppliers",
        [
            Col("code", True, ("supplier_code",)),
            Col("name", True, ("supplier_name",)),
            Col("contact_name"),
            Col("phone"),
            Col("email"),
            Col("address"),
            Col("gstin"),
            Col("external_id"),
            Col("is_active", kind="bool"),
        ],
        "suppliers.write",
        [{"code": "SUP-01", "name": "Example FMCG Ltd", "phone": "", "email": "", "is_active": "yes"}],
        _commit_parties(Supplier, SUPPLIER_FIELDS),
        _validate_parties(Supplier),
    ),
    "opening_stock": EntitySpec(
        "opening_stock",
        [
            Col("sku", True),
            Col("warehouse_code", True, ("warehouse",)),
            Col("quantity", True, ("qty",), kind="decimal"),
            Col("unit"),
            Col("batch_number", aliases=("batch",)),
            Col("expiry_date", kind="date", aliases=("expiry",)),
        ],
        "inventory.adjust",
        [{"sku": "SKU-1001", "warehouse_code": "MAIN", "quantity": "100", "unit": "PCS", "batch_number": "B001", "expiry_date": "2027-12-31"}],
        _commit_opening_stock,
        _validate_opening_stock,
    ),
    "prices": EntitySpec(
        "prices",
        [Col("sku", True), Col("unit_price", True, ("price", "rate"), kind="decimal"), Col("price_list")],
        "pricing.write",
        [{"sku": "SKU-1001", "unit_price": "105.00", "price_list": "Standard"}],
        _commit_prices,
        _validate_prices,
    ),
}


def template_csv(entity: str) -> str:
    spec = SPECS[entity]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=[c.name for c in spec.columns])
    w.writeheader()
    for row in spec.example:
        w.writerow({c.name: row.get(c.name, "") for c in spec.columns})
    return buf.getvalue()


def preview(db, org_id, entity: str, content: bytes, mapping: dict | None):
    spec = SPECS[entity]
    headers, raw = parse_csv(content)
    mapping = mapping or auto_mapping(spec, headers)
    bad = [h for h in mapping.values() if h not in headers]
    if bad:
        raise AppError("INVALID_MAPPING", f"Mapped columns not in file: {bad}", 422)
    rows, errors = normalise_rows(spec, raw, mapping)
    if spec.validate:
        errors += spec.validate(db, org_id, rows)
    return spec, headers, mapping, rows, errors


def store_rows(rows):
    return _jsonable_rows(rows)


def load_rows(entity, rows):
    return _rehydrate(SPECS[entity], rows)
