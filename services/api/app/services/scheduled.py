"""Periodic jobs (run by the worker or on demand): expiry scan, reconciliation rules."""

import uuid
from datetime import date

from sqlalchemy import func, select

from app.models import Batch, InventoryMovement, Organization, Product, Warehouse
from app.money import qty
from app.services import exceptions as exc_svc
from app.services import jobs


def scan_expiry(db, org: Organization, today: date | None = None) -> dict:
    """Raise `expiring_stock` exceptions for batches with stock that are expired or
    within the organisation's alert window."""
    today = today or date.today()
    window = int(org.setting("expiry_alert_days"))
    rows = db.execute(
        select(InventoryMovement.warehouse_id, InventoryMovement.batch_id, func.sum(InventoryMovement.quantity))
        .where(InventoryMovement.organization_id == org.id, InventoryMovement.bucket == "on_hand", InventoryMovement.batch_id.is_not(None))
        .group_by(InventoryMovement.warehouse_id, InventoryMovement.batch_id)
    ).all()
    raised = 0
    for wh_id, batch_id, total in rows:
        q = qty(total or 0)
        if q <= 0:
            continue
        b = db.get(Batch, batch_id)
        if not b or not b.expiry_date:
            continue
        days = (b.expiry_date - today).days
        if days > window:
            continue
        p = db.get(Product, b.product_id)
        wh = db.get(Warehouse, wh_id)
        expired = days < 0
        exc_svc.raise_exception(
            db,
            org.id,
            "expiring_stock",
            f"{p.sku} batch {b.batch_number} {'expired' if expired else f'expires in {days} day(s)'} ({q} {p.base_unit} at {wh.code})",
            severity="high" if expired else "medium",
            entity_type="batch",
            entity_id=b.id,
            evidence={"sku": p.sku, "batch": b.batch_number, "expiry_date": b.expiry_date.isoformat(), "on_hand": str(q), "warehouse": wh.code},
            dedupe_key=f"expiry:{b.id}:{wh_id}:{'expired' if expired else 'expiring'}",
            is_synthetic=p.is_synthetic,
        )
        raised += 1
    return {"batches_flagged": raised}


@jobs.handler("expiry_scan")
def _job_expiry(db, job):
    org = db.get(Organization, uuid.UUID(job.payload["organization_id"]))
    out = scan_expiry(db, org)
    db.commit()
    return out


@jobs.handler("reconciliation_run")
def _job_recon(db, job):
    from app.services.reconciliation import run_rules

    org = db.get(Organization, uuid.UUID(job.payload["organization_id"]))
    out = run_rules(db, org, actor_id=None)
    db.commit()
    return out
