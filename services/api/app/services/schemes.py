"""Scheme versioning, review workflow, extracted drafts and supplier claims.

Workflow: draft -> pending_approval -> active (previous active version becomes
superseded) | rejected; active -> inactive. Only `active` versions are applied by
the pricing engine. Submitted versions are immutable; edits create a new version.
Extracted schemes are always created as drafts and never activated automatically.
"""

import uuid
from datetime import date

from sqlalchemy import func, select

from app import audit
from app.common import next_number
from app.db import utcnow
from app.errors import AppError, conflict
from app.models import Order, Product, Retailer, Scheme, SchemeApplication, SchemeClaim, SchemeVersion
from app.money import ZERO, money
from app.services import exceptions as exc_svc
from app.services import matching, scheme_engine
from app.services.scheme_engine import EligibilityCfg, ScopeCfg


def validate_version_fields(ctx, data: dict) -> dict:
    stype = data.get("scheme_type")
    if stype not in scheme_engine.CONFIG_MODELS:
        raise AppError("INVALID_SCHEME_TYPE", f"Unknown scheme type '{stype}'.", 422)
    try:
        data["config"] = scheme_engine.validate_config(stype, data.get("config") or {})
        scope = ScopeCfg.model_validate(data.get("scope") or {})
        elig = EligibilityCfg.model_validate(data.get("eligibility") or {})
    except ValueError as exc:
        raise AppError("INVALID_SCHEME_RULE", str(exc)[:500], 422) from None
    pids = set(scope.product_ids) | set(scope.exclude_product_ids)
    if stype == "buy_x_get_y" and data["config"].get("free_product_id"):
        pids.add(data["config"]["free_product_id"])
    if stype == "combination":
        pids |= {r["product_id"] for r in data["config"]["required"]}
    for pid in pids:
        ctx.get(Product, pid, "Product")
    for rid in set(elig.retailer_ids) | set(elig.exclude_retailer_ids):
        ctx.get(Retailer, rid, "Retailer")
    data["scope"] = scope.model_dump()
    data["eligibility"] = elig.model_dump()
    if data["start_date"] > data["end_date"]:
        raise AppError("INVALID_DATES", "end_date must be on or after start_date.", 422)
    if data.get("stacking", "exclusive") not in ("exclusive", "stackable"):
        raise AppError("INVALID_STACKING", "stacking must be exclusive or stackable.", 422)
    return data


def submit(ctx, scheme: Scheme, v: SchemeVersion) -> SchemeVersion:
    if v.status != "draft":
        raise conflict("INVALID_STATUS", f"Version is {v.status}; only drafts can be submitted.")
    validate_version_fields(ctx, {"scheme_type": v.scheme_type, "config": v.config, "scope": v.scope, "eligibility": v.eligibility, "start_date": v.start_date, "end_date": v.end_date, "stacking": v.stacking})
    v.status = "pending_approval"
    v.submitted_by_id = ctx.user.id
    scheme.status = "pending_approval" if scheme.status != "active" else scheme.status
    audit.record(ctx, "scheme.submit", "scheme_version", v.id, meta={"scheme": scheme.code, "version": v.version})
    return v


def approve(ctx, scheme: Scheme, v: SchemeVersion) -> SchemeVersion:
    if v.status != "pending_approval":
        raise conflict("INVALID_STATUS", f"Version is {v.status}; only submitted versions can be approved.")
    prev = None
    if scheme.active_version_id and scheme.active_version_id != v.id:
        prev = ctx.db.get(SchemeVersion, scheme.active_version_id)
        if prev and prev.status == "active":
            prev.status = "superseded"
    v.status = "active"
    v.approved_by_id = ctx.user.id
    v.approved_at = utcnow()
    scheme.status = "active"
    scheme.active_version_id = v.id
    scheme.current_version = v.version
    audit.record(
        ctx,
        "scheme.approve",
        "scheme_version",
        v.id,
        before={"active_version": prev.version if prev else None},
        after={"active_version": v.version, "scheme_type": v.scheme_type, "config": v.config, "start": v.start_date, "end": v.end_date},
        meta={"scheme": scheme.code, "self_approved": v.submitted_by_id == ctx.user.id},
    )
    return v


def reject(ctx, scheme: Scheme, v: SchemeVersion, reason: str) -> SchemeVersion:
    if v.status not in ("pending_approval", "draft"):
        raise conflict("INVALID_STATUS", f"Version is {v.status}.")
    v.status = "rejected"
    v.change_note = ((v.change_note or "") + f"\nRejected: {reason}").strip()
    if not scheme.active_version_id:
        scheme.status = "rejected"
    audit.record(ctx, "scheme.reject", "scheme_version", v.id, meta={"reason": reason, "scheme": scheme.code})
    return v


def deactivate(ctx, scheme: Scheme, reason: str) -> Scheme:
    if not scheme.active_version_id:
        raise conflict("INVALID_STATUS", "Scheme has no active version.")
    v = ctx.db.get(SchemeVersion, scheme.active_version_id)
    v.status = "inactive"
    scheme.status = "inactive"
    scheme.active_version_id = None
    audit.record(ctx, "scheme.deactivate", "scheme", scheme.id, meta={"reason": reason, "version": v.version})
    return scheme


# --------------------------------------------------------------- extraction -> draft


def _config_from_candidate(c, db, org_id) -> tuple[dict, list[str]]:
    warns: list[str] = []
    t = c.scheme_type
    cfg: dict = {}
    if t == "percentage":
        cfg = {"percent": c.percent, "min_quantity": c.min_quantity, "min_value": c.min_value}
    elif t == "fixed_amount":
        cfg = {"amount": c.amount, "per": c.amount_per or "order", "min_quantity": c.min_quantity, "min_value": c.min_value}
    elif t == "buy_x_get_y":
        cfg = {"buy_quantity": c.buy_quantity, "free_quantity": c.free_quantity}
        if c.free_product_text:
            products = db.scalars(select(Product).where(Product.organization_id == org_id, Product.is_active.is_(True))).all()
            ranked = matching.rank(c.free_product_text, products, matching.product_score)
            if matching.is_unambiguous(ranked):
                cfg["free_product_id"] = str(ranked[0][2].id)
            else:
                warns.append(f"Free product '{c.free_product_text}' could not be matched unambiguously; set it manually.")
    elif t == "quantity_slab":
        cfg = {"slabs": [s.model_dump(exclude_none=True) for s in c.slabs], "basis": "scope_total"}
    elif t == "value_slab":
        cfg = {"slabs": [s.model_dump(exclude_none=True) for s in c.slabs]}
    elif t == "combination":
        warns.append("Combination schemes must have their required products selected manually.")
    cfg = {k: (str(v) if v is not None and not isinstance(v, (list, dict, str)) else v) for k, v in cfg.items() if v is not None}
    return cfg, warns


def apply_scheme_extraction(db, org, doc, ext, parsed, actor_id) -> dict:
    """Create draft scheme(s) from an extraction. Never activates anything."""
    if ext.status != "succeeded" or parsed is None:
        exc_svc.raise_exception(db, org.id, "scheme_ambiguity", f"Scheme circular {doc.filename} could not be read", severity="medium", description=ext.error, entity_type="document", entity_id=doc.id, evidence={"extraction_id": str(ext.id)})
        return {"status": "failed", "error": ext.error}
    created = []
    for c in parsed.schemes:
        warns = list(c.warnings)
        cfg, w2 = _config_from_candidate(c, db, org.id)
        warns += w2
        stype = c.scheme_type if c.scheme_type in scheme_engine.CONFIG_MODELS else "percentage"
        if c.scheme_type not in scheme_engine.CONFIG_MODELS:
            warns.append(f"Scheme type '{c.scheme_type}' not recognised; defaulted to percentage for editing.")
        try:
            cfg = scheme_engine.validate_config(stype, cfg)
        except ValueError as exc:
            warns.append(f"Rule incomplete: {str(exc)[:200]}")
        scope = {"product_ids": [], "brands": [], "categories": [], "exclude_product_ids": []}
        known_brands = {b.lower(): b for b in db.scalars(select(Product.brand).where(Product.organization_id == org.id, Product.brand.is_not(None)).distinct()).all()}
        known_cats = {x.lower(): x for x in db.scalars(select(Product.category).where(Product.organization_id == org.id, Product.category.is_not(None)).distinct()).all()}
        for b in c.brands:
            (scope["brands"].append(known_brands[b.lower()]) if b.lower() in known_brands else warns.append(f"Brand '{b}' is not in the catalogue."))
        for cat in c.categories:
            (scope["categories"].append(known_cats[cat.lower()]) if cat.lower() in known_cats else warns.append(f"Category '{cat}' is not in the catalogue."))
        if c.product_texts:
            products = db.scalars(select(Product).where(Product.organization_id == org.id, Product.is_active.is_(True))).all()
            for t in c.product_texts:
                ranked = matching.rank(t, products, matching.product_score)
                if matching.is_unambiguous(ranked):
                    scope["product_ids"].append(str(ranked[0][2].id))
                else:
                    warns.append(f"Product '{t}' could not be matched unambiguously.")
        start, end = c.start_date, c.end_date
        if not start or not end:
            start = start or date.today()
            end = end or start
            warns.append("Effective dates were not found; placeholder dates set - correct them before submitting.")
        scheme = Scheme(organization_id=org.id, code=next_number(db, Scheme, org.id, "code", "EXT", 4), name=c.name[:200], funded_by="supplier", status="draft", created_by_id=uuid.UUID(actor_id) if actor_id else None)
        db.add(scheme)
        db.flush()
        v = SchemeVersion(
            organization_id=org.id,
            scheme_id=scheme.id,
            version=1,
            scheme_type=stype,
            config=cfg,
            scope=scope,
            eligibility={"retailer_ids": [], "territories": list(c.territories), "exclude_retailer_ids": []},
            start_date=start,
            end_date=end,
            max_benefit=money(c.max_benefit) if c.max_benefit is not None else None,
            source_reference=(c.source_section or doc.filename)[:300],
            source_document_id=doc.id,
            extraction_id=ext.id,
            status="draft",
            change_note="Drafted from document extraction. Review warnings: " + ("; ".join(warns) if warns else "none"),
            created_by_id=uuid.UUID(actor_id) if actor_id else None,
        )
        db.add(v)
        db.flush()
        if warns or c.confidence < 0.8:
            exc_svc.raise_exception(db, org.id, "scheme_ambiguity", f"Draft scheme '{scheme.name}' needs review ({len(warns)} warning(s))", severity="medium", entity_type="scheme", entity_id=scheme.id, evidence={"warnings": warns, "extraction_id": str(ext.id), "confidence": c.confidence})
        created.append({"scheme_id": str(scheme.id), "code": scheme.code, "warnings": warns})
    ext.result = {"drafts": created}
    return {"status": "drafted", "drafts": created}


# --------------------------------------------------------------------------- claims

CLAIMABLE_ORDER_STATUSES = ("dispatched", "delivered", "partially_fulfilled")


def generate_claim(ctx, scheme: Scheme, period_start: date, period_end: date) -> SchemeClaim:
    if scheme.funded_by != "supplier":
        raise AppError("NOT_SUPPLIER_FUNDED", "Only supplier-funded schemes generate claims.", 422)
    if period_end < period_start:
        raise AppError("INVALID_DATES", "period_end must be on or after period_start.", 422)
    apps = ctx.db.scalars(
        select(SchemeApplication)
        .join(Order, Order.id == SchemeApplication.order_id)
        .where(
            SchemeApplication.organization_id == ctx.org_id,
            SchemeApplication.scheme_id == scheme.id,
            SchemeApplication.claim_id.is_(None),
            Order.status.in_(CLAIMABLE_ORDER_STATUSES),
            Order.order_date >= period_start,
            Order.order_date <= period_end,
        )
        .with_for_update()
    ).all()
    if not apps:
        raise AppError("NOTHING_TO_CLAIM", "No unclaimed scheme benefits on dispatched orders in this period.", 422)
    total = money(sum((a.benefit_amount for a in apps), ZERO))
    claim = SchemeClaim(
        organization_id=ctx.org_id,
        claim_number=next_number(ctx.db, SchemeClaim, ctx.org_id, "claim_number", "CLM"),
        scheme_id=scheme.id,
        supplier_id=scheme.supplier_id,
        period_start=period_start,
        period_end=period_end,
        expected_amount=total,
        created_by_id=ctx.user.id,
    )
    ctx.db.add(claim)
    ctx.db.flush()
    for a in apps:
        a.claim_id = claim.id
    audit.record(ctx, "scheme_claim.create", "scheme_claim", claim.id, after={"claim_number": claim.claim_number, "expected_amount": str(total), "applications": len(apps)})
    return claim


def scheme_stats(db, scheme_id) -> dict:
    row = db.execute(select(func.count(SchemeApplication.id), func.sum(SchemeApplication.benefit_amount)).where(SchemeApplication.scheme_id == scheme_id)).one()
    return {"applications": row[0] or 0, "benefit_total": str(money(row[1] or 0))}
