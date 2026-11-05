"""Order / document understanding pipeline.

    validate upload -> store privately -> extract text -> provider (Claude or demo
    parser) -> schema validation -> deterministic master-data matching -> field-level
    confidence + warnings -> save Extraction -> human review -> draft order.

The pipeline never confirms anything by itself: sources end in `needs_review`, and
conversion to a draft order is an explicit user action in which every ambiguous line
must be explicitly confirmed.
"""

import hashlib
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db import utcnow
from app.errors import AppError, conflict
from app.extraction.providers import ExtractionInput, get_provider
from app.extraction.text import extract_text, normalize_text
from app.models import Document, Extraction, OrderSource, Organization, Product, Retailer
from app.money import qty
from app.services import exceptions as exc_svc
from app.services import jobs, matching, pricing
from app.services.storage import storage

LOW_CONFIDENCE = 0.85


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(normalize_text(text).lower().split()).encode()).hexdigest()


def _catalog_hint(db, org_id) -> list[str]:
    rows = db.scalars(select(Product).where(Product.organization_id == org_id, Product.is_active.is_(True)).order_by(Product.sku).limit(400)).all()
    return [f"{p.sku} | {p.name} {p.variant or ''}".strip() for p in rows]


def _input_for(db, org: Organization, kind: str, text: str | None, doc: Document | None) -> ExtractionInput:
    inp = ExtractionInput(kind=kind, text=text)
    if doc is not None:
        inp.content = storage().get(doc.storage_key)
        inp.content_type = doc.content_type
    if kind == "order":
        inp.catalog_hint = _catalog_hint(db, org.id)
    if kind == "scheme":
        from sqlalchemy import distinct

        inp.known_brands = [b for b in db.scalars(select(distinct(Product.brand)).where(Product.organization_id == org.id, Product.brand.is_not(None))).all()]
        inp.known_categories = [c for c in db.scalars(select(distinct(Product.category)).where(Product.organization_id == org.id, Product.category.is_not(None))).all()]
    return inp


def run_provider(db, org: Organization, kind: str, source_type: str, source_id, text: str | None, doc: Document | None) -> tuple[Extraction, object | None]:
    provider = get_provider(org)
    res = provider.extract(_input_for(db, org, kind, text, doc))
    ext = Extraction(
        organization_id=org.id,
        kind=kind,
        source_type=source_type,
        source_id=str(source_id),
        provider=res.provider,
        model=res.model,
        status="succeeded" if res.ok else "failed",
        raw_output=res.raw,
        error=res.error,
        latency_ms=res.latency_ms,
        input_tokens=res.input_tokens,
        output_tokens=res.output_tokens,
        warnings=list(getattr(res.parsed, "warnings", []) or []),
    )
    db.add(ext)
    db.flush()
    if not res.ok and res.meta.get("retryable"):
        ext.warnings = (ext.warnings or []) + ["Provider error was transient; use Re-extract to retry."]
    return ext, res.parsed


# ------------------------------------------------------------------------- matching


def match_order(db, org_id, raw) -> dict:
    """Deterministically resolve retailer and SKU candidates. Returns the review
    payload stored in Extraction.result."""
    retailers = db.scalars(select(Retailer).where(Retailer.organization_id == org_id, Retailer.is_active.is_(True))).all()
    products = db.scalars(select(Product).where(Product.organization_id == org_id, Product.is_active.is_(True))).all()
    by_sku = {p.sku.upper(): p for p in products}
    warnings = list(raw.warnings)
    # Retailer
    r_text = " ".join(x for x in (raw.retailer.name, raw.retailer.code, raw.retailer.phone) if x)
    r_ranked = matching.rank(r_text, retailers, matching.retailer_score, limit=3, floor=0.5) if r_text else []
    r_selected = r_ranked[0][2] if matching.is_unambiguous(r_ranked) else None
    if not r_ranked:
        warnings.append("No matching retailer found in the directory." if r_text else "Retailer not identified.")
    elif not r_selected:
        warnings.append("Retailer match is ambiguous; please choose.")
    retailer_conf = r_ranked[0][0] if r_selected else (r_ranked[0][0] * 0.6 if r_ranked else 0.0)

    lines = []
    for idx, l in enumerate(raw.lines):
        lw: list[str] = []
        desc = l.product_text or l.source_description
        ranked = []
        if l.sku_hint and l.sku_hint.upper() in by_sku:
            hinted = by_sku[l.sku_hint.upper()]
            s, why = matching.product_score(desc, hinted)
            # A provider hint is only trusted if the text agrees with it.
            if s >= 0.6:
                ranked = [(max(s, 0.95), ["SKU hint", *why], hinted)]
        if not ranked:
            ranked = matching.rank(desc, products, matching.product_score, limit=3, floor=0.35)
        selected = ranked[0][2] if matching.is_unambiguous(ranked) else None
        unit = (l.unit or "").upper() or None
        qty_conf = l.confidence if l.quantity is not None else 0.0
        factor = None
        if selected:
            if unit is None:
                unit = selected.base_unit
                lw.append(f"Unit not stated; assumed {selected.base_unit}.")
                qty_conf = min(qty_conf, 0.75)
            factor = pricing.conversion_factor(db, org_id, selected, unit)
            if factor is None:
                lw.append(f"No conversion from {unit} to {selected.base_unit} for {selected.sku}.")
        if l.quantity is None:
            lw.append("Quantity not found.")
        if not ranked:
            status = "unmatched"
            lw.append("No catalogue product matches this description.")
        elif not selected:
            status = "ambiguous"
            lw.append("Several products could match; choose the correct SKU.")
        elif l.quantity is None:
            status = "needs_input"
        else:
            status = "matched"
        product_conf = ranked[0][0] if selected else (ranked[0][0] * 0.6 if ranked else 0.0)
        lines.append(
            {
                "index": idx,
                "source_description": l.source_description,
                "product_text": l.product_text,
                "quantity": str(l.quantity) if l.quantity is not None else None,
                "unit": unit,
                "base_quantity": str(qty(l.quantity * factor)) if (factor and l.quantity is not None) else None,
                "status": status,
                "selected_product_id": str(selected.id) if selected else None,
                "sku_candidates": [
                    {"product_id": str(p.id), "sku": p.sku, "name": p.name, "variant": p.variant, "base_unit": p.base_unit, "confidence": round(s, 3), "reasons": why}
                    for s, why, p in ranked
                ],
                "confidence": {"product": round(product_conf, 3), "quantity": round(qty_conf, 3), "extraction": round(l.confidence, 3)},
                "warnings": lw,
            }
        )
    line_confs = [min(x["confidence"]["product"], x["confidence"]["quantity"] or 0.0) for x in lines]
    overall = round(min([retailer_conf] + line_confs) if lines else 0.0, 3)
    counts = {s: sum(1 for x in lines if x["status"] == s) for s in ("matched", "ambiguous", "unmatched", "needs_input")}
    return {
        "schema_version": raw.schema_version,
        "retailer": {
            "extracted": raw.retailer.model_dump(),
            "selected_retailer_id": str(r_selected.id) if r_selected else None,
            "candidates": [{"retailer_id": str(r.id), "code": r.code, "name": r.name, "territory": r.territory, "confidence": round(s, 3), "reasons": why} for s, why, r in r_ranked],
            "confidence": round(retailer_conf, 3),
        },
        "lines": lines,
        "requested_delivery_date": raw.requested_delivery_date.isoformat() if raw.requested_delivery_date else None,
        "notes": raw.notes,
        "warnings": warnings,
        "summary": {**counts, "total": len(lines)},
        "overall_confidence": overall,
        "requires_review": True,
    }


# ------------------------------------------------------------------------- jobs


@jobs.handler("extract_order_source")
def _job_extract_order(db, job):
    source = db.get(OrderSource, uuid.UUID(job.payload["source_id"]))
    if source is None or source.status not in ("received", "extracting", "failed", "needs_review"):
        return {"skipped": True}
    org = db.get(Organization, source.organization_id)
    source.status = "extracting"
    db.flush()
    doc = db.get(Document, source.document_id) if source.document_id else None
    text = source.raw_text
    if doc is not None and text is None:
        text = doc.text_content
    ext, parsed = run_provider(db, org, "order", "order_source", source.id, text, doc)
    source.latest_extraction_id = ext.id
    if ext.status != "succeeded":
        source.status = "failed"
        exc_svc.raise_exception(db, org.id, "low_confidence_extraction", "Order could not be extracted automatically", severity="high", description=ext.error, entity_type="order_source", entity_id=source.id, evidence={"extraction_id": str(ext.id)}, suggested_action="Open the inbox item and enter the order lines manually.", is_synthetic=source.is_synthetic)
        db.commit()
        return {"status": "failed", "error": ext.error}
    result = match_order(db, org.id, parsed)
    ext.result = result
    ext.overall_confidence = Decimal(str(result["overall_confidence"]))
    ext.warnings = result["warnings"]
    source.status = "needs_review"
    s = result["summary"]
    if s["ambiguous"] or s["unmatched"] or not result["retailer"]["selected_retailer_id"]:
        exc_svc.raise_exception(db, org.id, "unmatched_master_data", f"Inbox order needs matching: {s['ambiguous']} ambiguous, {s['unmatched']} unmatched line(s)", severity="medium", entity_type="order_source", entity_id=source.id, evidence={"extraction_id": str(ext.id), "summary": s, "retailer_matched": bool(result["retailer"]["selected_retailer_id"])}, is_synthetic=source.is_synthetic)
    elif result["overall_confidence"] < LOW_CONFIDENCE:
        exc_svc.raise_exception(db, org.id, "low_confidence_extraction", f"Low-confidence extraction ({result['overall_confidence']:.2f})", severity="low", entity_type="order_source", entity_id=source.id, evidence={"extraction_id": str(ext.id)}, is_synthetic=source.is_synthetic)
    db.commit()
    return {"status": "needs_review", "overall_confidence": result["overall_confidence"]}


@jobs.handler("extract_document")
def _job_extract_document(db, job):
    """Purchase invoice / scheme circular extraction into a draft record."""
    doc = db.get(Document, uuid.UUID(job.payload["document_id"]))
    org = db.get(Organization, doc.organization_id)
    kind = job.payload["kind"]
    ext, parsed = run_provider(db, org, kind, "document", doc.id, doc.text_content, doc)
    if kind == "purchase_invoice":
        from app.services.finance import apply_invoice_extraction

        out = apply_invoice_extraction(db, org, doc, ext, parsed, uuid.UUID(job.payload["target_id"]))
    else:
        from app.services.schemes import apply_scheme_extraction

        out = apply_scheme_extraction(db, org, doc, ext, parsed, job.payload.get("actor_id"))
    db.commit()
    return out


# ------------------------------------------------------------------------- ingest


def ingest_text(ctx, text: str, sender: str | None = None, channel: str = "text", is_synthetic: bool = False) -> tuple[OrderSource, list]:
    if not text or not text.strip():
        raise AppError("EMPTY_INPUT", "Paste the order text first.", 422)
    if len(text) > 50000:
        raise AppError("INPUT_TOO_LARGE", "Order text is limited to 50,000 characters.", 413)
    h = content_hash(text)
    dup = ctx.db.scalars(
        select(OrderSource).where(OrderSource.organization_id == ctx.org_id, OrderSource.content_hash == h, OrderSource.created_at >= utcnow() - timedelta(days=7), OrderSource.status != "discarded")
    ).first()
    src = OrderSource(organization_id=ctx.org_id, channel=channel, status="received", raw_text=text, sender=sender, content_hash=h, created_by_id=ctx.user.id, meta={"received_via": "paste"}, is_synthetic=is_synthetic)
    ctx.db.add(src)
    ctx.db.flush()
    if dup:
        src.meta = {**(src.meta or {}), "possible_duplicate_of": str(dup.id)}
        exc_svc.raise_exception(ctx.db, ctx.org_id, "duplicate", "Identical order text received again within 7 days", entity_type="order_source", entity_id=src.id, evidence={"previous_source_id": str(dup.id)})
    job = jobs.enqueue(ctx.db, "extract_order_source", {"source_id": str(src.id)}, ctx.org_id, idempotency_key=f"extract:{src.id}:1")
    return src, [job.id]


def ingest_document(ctx, doc: Document, channel: str) -> tuple[OrderSource, list]:
    src = OrderSource(organization_id=ctx.org_id, channel=channel, status="received", document_id=doc.id, raw_text=None, content_hash=doc.sha256, created_by_id=ctx.user.id, meta={"filename": doc.filename})
    ctx.db.add(src)
    ctx.db.flush()
    dup = ctx.db.scalars(select(OrderSource).where(OrderSource.organization_id == ctx.org_id, OrderSource.content_hash == doc.sha256, OrderSource.id != src.id, OrderSource.status != "discarded")).first()
    if dup:
        src.meta = {**src.meta, "possible_duplicate_of": str(dup.id)}
        exc_svc.raise_exception(ctx.db, ctx.org_id, "duplicate", f"File {doc.filename} was already uploaded", entity_type="order_source", entity_id=src.id, evidence={"previous_source_id": str(dup.id)})
    job = jobs.enqueue(ctx.db, "extract_order_source", {"source_id": str(src.id)}, ctx.org_id, idempotency_key=f"extract:{src.id}:1")
    return src, [job.id]


def store_document(ctx, filename: str, content_type: str, content: bytes, kind: str) -> Document:
    from app.services.storage import sha256

    key = storage().put(ctx.org_id, content, "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
    text, meta = extract_text(content, content_type)
    doc = Document(organization_id=ctx.org_id, kind=kind, filename=filename[:255], content_type=content_type, size_bytes=len(content), sha256=sha256(content), storage_key=key, text_content=normalize_text(text) if text else None, uploaded_by_id=ctx.user.id)
    ctx.db.add(doc)
    ctx.db.flush()
    return doc


def reextract(ctx, source: OrderSource) -> list:
    if source.status in ("converted", "discarded"):
        raise conflict("INVALID_STATUS", f"Source is {source.status}.")
    source.status = "received"
    n = ctx.db.query(Extraction).filter(Extraction.source_type == "order_source", Extraction.source_id == str(source.id)).count()
    job = jobs.enqueue(ctx.db, "extract_order_source", {"source_id": str(source.id)}, ctx.org_id, idempotency_key=f"extract:{source.id}:{n + 1}")
    return [job.id]


# ------------------------------------------------------------------------- convert


def convert_to_order(ctx, source: OrderSource, *, retailer_id, warehouse_id, lines: list[dict], requested_delivery_date=None, notes=None):
    """Create a draft order from reviewed lines. Lines whose extraction was ambiguous
    or unmatched must carry confirm=True (explicit human selection)."""
    from app.audit import record
    from app.models import Order
    from app.services import orders as order_svc

    if source.status == "converted" and source.order_id:
        raise conflict("ALREADY_CONVERTED", "This inbox item has already been converted.", {"order_id": str(source.order_id)})
    if source.status == "discarded":
        raise conflict("INVALID_STATUS", "This inbox item was discarded.")
    ext = ctx.db.get(Extraction, source.latest_extraction_id) if source.latest_extraction_id else None
    ext_lines = {l["index"]: l for l in ((ext.result or {}).get("lines", []) if ext else [])}
    out_lines = []
    unconfirmed = []
    for li in lines:
        if not li.get("include", True):
            continue
        if not li.get("product_id"):
            raise AppError("LINE_UNRESOLVED", "Every included line needs a selected product.", 422, {"index": li.get("index")})
        ctx.get(Product, li["product_id"], "Product")
        el = ext_lines.get(li.get("index")) if li.get("index") is not None else None
        if el:
            if el["status"] in ("ambiguous", "unmatched") and not li.get("confirm"):
                unconfirmed.append(el["index"])
                continue
            chosen = str(li["product_id"])
            cand = next((c for c in el["sku_candidates"] if c["product_id"] == chosen), None)
            unchanged = (
                chosen == el.get("selected_product_id")
                and el.get("quantity") is not None
                and qty(li["quantity"]) == qty(el["quantity"])
                and (li.get("unit") or "").upper() == (el.get("unit") or "")
            )
            out_lines.append(
                {
                    "product_id": li["product_id"],
                    "quantity": li["quantity"],
                    "unit": li.get("unit") or el.get("unit"),
                    "source_description": el["source_description"][:300],
                    "provenance": "extracted" if unchanged else "corrected",
                    "extracted_values": {
                        "extraction_id": str(ext.id),
                        "source_description": el["source_description"],
                        "quantity": el.get("quantity"),
                        "unit": el.get("unit"),
                        "status": el["status"],
                        "selected_product_id": el.get("selected_product_id"),
                        "candidates": el["sku_candidates"],
                    },
                    "match_confidence": Decimal(str(cand["confidence"])) if cand else None,
                }
            )
        else:
            out_lines.append({"product_id": li["product_id"], "quantity": li["quantity"], "unit": li.get("unit"), "provenance": "manual", "source_description": li.get("source_description")})
    if unconfirmed:
        raise AppError("AMBIGUOUS_LINE_UNCONFIRMED", "Ambiguous or unmatched lines must be explicitly confirmed before conversion.", 422, {"indexes": unconfirmed})
    if not out_lines:
        raise AppError("NO_LINES", "Select at least one line to create an order.", 422)
    channel = {"text": "text", "pdf": "pdf", "image": "image"}.get(source.channel, source.channel)
    order = order_svc.create_order(
        ctx,
        retailer_id=retailer_id,
        warehouse_id=warehouse_id,
        lines=out_lines,
        requested_delivery_date=requested_delivery_date,
        notes=notes,
        channel=channel,
        source_id=source.id,
        is_synthetic=source.is_synthetic,
    )
    source.status = "converted"
    source.order_id = order.id
    record(ctx, "inbox.convert", "order_source", source.id, after={"order_id": str(order.id), "order_number": order.order_number, "corrected_lines": sum(1 for l in out_lines if l["provenance"] == "corrected")})
    _ = Order
    return order
