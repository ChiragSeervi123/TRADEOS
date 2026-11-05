import csv
import io
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app import audit
from app.db import utcnow
from app.deps import Ctx, get_ctx, require
from app.errors import AppError, not_found
from app.models import ImportBatch, Order, Product, Retailer, SalesInvoice, Supplier, Warehouse
from app.services import csv_import, inventory, receivables

router = APIRouter(tags=["imports"])

MAX_CSV_BYTES = 5 * 1024 * 1024


def batch_out(b: ImportBatch, include_rows=False):
    out = {
        "id": str(b.id),
        "entity": b.entity,
        "filename": b.filename,
        "status": b.status,
        "total_rows": b.total_rows,
        "valid_rows": b.valid_rows,
        "errors": b.errors or [],
        "column_mapping": b.column_mapping,
        "created_at": b.created_at.isoformat(),
        "committed_at": b.committed_at.isoformat() if b.committed_at else None,
    }
    if include_rows:
        out["preview"] = (b.rows or [])[:50]
    return out


@router.get("/imports/templates/{entity}")
def template(entity: str, ctx: Ctx = Depends(get_ctx)):
    if entity not in csv_import.SPECS:
        raise not_found("Template")
    return Response(
        csv_import.template_csv(entity),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="tradeos_{entity}_template.csv"'},
    )


@router.get("/imports/specs")
def specs(ctx: Ctx = Depends(get_ctx)):
    return [
        {"entity": s.entity, "permission": s.permission, "columns": [{"name": c.name, "required": c.required, "kind": c.kind, "aliases": list(c.aliases)} for c in s.columns]}
        for s in csv_import.SPECS.values()
    ]


@router.get("/imports")
def list_imports(ctx: Ctx = Depends(require("imports.run"))):
    rows = ctx.db.scalars(select(ImportBatch).where(ImportBatch.organization_id == ctx.org_id).order_by(ImportBatch.created_at.desc()).limit(50)).all()
    return [batch_out(b) for b in rows]


@router.post("/imports/{entity}/preview", status_code=201)
async def preview_import(
    entity: str,
    file: UploadFile = File(...),
    mapping: str | None = Form(default=None, description='JSON {"target_column": "source header"}'),
    ctx: Ctx = Depends(require("imports.run")),
):
    if entity not in csv_import.SPECS:
        raise not_found("Import type")
    spec = csv_import.SPECS[entity]
    ctx.require(spec.permission)
    if not (file.filename or "").lower().endswith(".csv"):
        raise AppError("INVALID_FILE_TYPE", "Please upload a .csv file.", 422)
    content = await file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise AppError("FILE_TOO_LARGE", "CSV files are limited to 5 MB.", 413)
    try:
        mp = json.loads(mapping) if mapping else None
    except json.JSONDecodeError:
        raise AppError("INVALID_MAPPING", "Mapping must be valid JSON.", 422) from None
    spec, headers, mp, rows, errors = csv_import.preview(ctx.db, ctx.org_id, entity, content, mp)
    bad_rows = {e["row"] for e in errors}
    b = ImportBatch(
        organization_id=ctx.org_id,
        entity=entity,
        filename=file.filename[:255],
        total_rows=len(rows),
        valid_rows=len([r for r in rows if r["_row"] not in bad_rows]),
        column_mapping=mp,
        errors=errors[:1000],
        rows=csv_import.store_rows(rows),
        created_by_id=ctx.user_id,
    )
    ctx.db.add(b)
    ctx.db.commit()
    out = batch_out(b, include_rows=True)
    out["headers"] = headers
    return out


class CommitIn(BaseModel):
    skip_invalid: bool = False


@router.post("/imports/{batch_id}/commit")
def commit_import(batch_id: uuid.UUID, body: CommitIn, ctx: Ctx = Depends(require("imports.run"))):
    b = ctx.get(ImportBatch, batch_id, "Import", lock=True)
    spec = csv_import.SPECS[b.entity]
    ctx.require(spec.permission)
    if b.status != "previewed":
        raise AppError("ALREADY_PROCESSED", f"This import is already {b.status}.", 409)
    rows = csv_import.load_rows(b.entity, b.rows or [])
    # Re-validate against current data: things may have changed since preview.
    errors = spec.validate(ctx.db, ctx.org_id, rows) if spec.validate else []
    errors = (b.errors or []) + [e for e in errors if e not in (b.errors or [])]
    bad_rows = {e["row"] for e in errors}
    if bad_rows and not body.skip_invalid:
        raise AppError(
            "IMPORT_HAS_ERRORS",
            f"{len(bad_rows)} row(s) have errors. Fix the file, or confirm skipping invalid rows.",
            422,
            {"errors": errors[:200]},
        )
    good = [r for r in rows if r["_row"] not in bad_rows]
    try:
        result = spec.commit(ctx, good)
        b.status = "committed"
        b.committed_at = utcnow()
        b.valid_rows = len(good)
        b.errors = errors[:1000]
        b.rows = None  # don't retain imported data in the batch after commit
        audit.record(ctx, f"import.{b.entity}", "import_batch", b.id, meta={"result": result, "skipped_rows": sorted(bad_rows)})
        ctx.db.commit()
    except AppError:
        ctx.db.rollback()
        raise
    return {"status": "committed", "imported_rows": len(good), "skipped_rows": sorted(bad_rows), "result": result}


# --------------------------------------------------------------------------- exports


def _csv(rows: list[dict], name: str) -> Response:
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


EXPORT_PERMS = {
    "products": "products.read",
    "retailers": "retailers.read",
    "suppliers": "suppliers.read",
    "stock": "inventory.read",
    "orders": "orders.read",
    "sales_invoices": "finance.read",
}


@router.get("/exports/{entity}")
def export(entity: str, ctx: Ctx = Depends(require("reports.export"))):
    if entity not in EXPORT_PERMS:
        raise not_found("Export")
    ctx.require(EXPORT_PERMS[entity])
    db, oid = ctx.db, ctx.org_id
    rows: list[dict] = []
    if entity == "products":
        for p in db.scalars(select(Product).where(Product.organization_id == oid).order_by(Product.sku)).all():
            rows.append({"sku": p.sku, "name": p.name, "brand": p.brand or "", "category": p.category or "", "variant": p.variant or "", "base_unit": p.base_unit, "tax_rate": p.tax_rate, "mrp": p.mrp or "", "cost_price": p.cost_price or "", "barcode": p.barcode or "", "is_active": p.is_active, "synthetic": p.is_synthetic})
    elif entity == "retailers":
        for r in db.scalars(select(Retailer).where(Retailer.organization_id == oid).order_by(Retailer.code)).all():
            rows.append({"code": r.code, "name": r.name, "phone": r.phone or "", "territory": r.territory or "", "route": r.route or "", "credit_limit": r.credit_limit if r.credit_limit is not None else "", "payment_terms_days": r.payment_terms_days, "is_active": r.is_active, "synthetic": r.is_synthetic})
    elif entity == "suppliers":
        for s in db.scalars(select(Supplier).where(Supplier.organization_id == oid).order_by(Supplier.code)).all():
            rows.append({"code": s.code, "name": s.name, "phone": s.phone or "", "email": s.email or "", "is_active": s.is_active})
    elif entity == "stock":
        products = {p.id: p for p in db.scalars(select(Product).where(Product.organization_id == oid)).all()}
        whs = {w.id: w for w in db.scalars(select(Warehouse).where(Warehouse.organization_id == oid)).all()}
        for (wh, pid), b in sorted(inventory.balances(db, oid).items(), key=lambda kv: (whs[kv[0][0]].code, products[kv[0][1]].sku)):
            rows.append({"warehouse": whs[wh].code, "sku": products[pid].sku, "name": products[pid].name, "on_hand": b.on_hand, "reserved": b.reserved, "blocked": b.blocked, "available": b.available})
    elif entity == "orders":
        rmap = {r.id: r for r in db.scalars(select(Retailer).where(Retailer.organization_id == oid)).all()}
        for o in db.scalars(select(Order).where(Order.organization_id == oid).order_by(Order.created_at)).all():
            rows.append({"order_number": o.order_number, "order_date": o.order_date.isoformat(), "retailer_code": rmap[o.retailer_id].code, "status": o.status, "gross_total": o.gross_total, "discount_total": o.discount_total + o.scheme_discount_total, "tax_total": o.tax_total, "net_total": o.net_total, "channel": o.channel, "synthetic": o.is_synthetic})
    elif entity == "sales_invoices":
        rmap = {r.id: r for r in db.scalars(select(Retailer).where(Retailer.organization_id == oid)).all()}
        invs = db.scalars(select(SalesInvoice).where(SalesInvoice.organization_id == oid).order_by(SalesInvoice.invoice_date)).all()
        outs = receivables.outstanding_map(db, invs)
        for i in invs:
            rows.append({"invoice_number": i.invoice_number, "invoice_date": i.invoice_date.isoformat(), "due_date": i.due_date.isoformat(), "retailer_code": rmap[i.retailer_id].code, "total": i.total, "outstanding": outs[i.id], "status": i.status})
    audit.record(ctx, "data.export", "export", entity, meta={"rows": len(rows)})
    db.commit()
    return _csv(rows, f"tradeos_{entity}")
