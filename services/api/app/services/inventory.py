"""Inventory ledger service.

Source of truth: `inventory_movements` (append-only). Every row moves stock inside
one bucket (on_hand | reserved | blocked):

    on_hand   = SUM(quantity WHERE bucket='on_hand')
    reserved  = SUM(quantity WHERE bucket='reserved')
    blocked   = SUM(quantity WHERE bucket='blocked')
    available = on_hand - reserved - blocked

`inventory_balances` is a cache row per (warehouse, product) that is locked with
SELECT ... FOR UPDATE before any mutation to serialise concurrent writers (on
PostgreSQL). `verify_balances` proves the cache equals the ledger.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.errors import AppError
from app.models import Batch, InventoryBalance, InventoryMovement, Product, Warehouse
from app.models.inventory import MOVEMENT_TYPES
from app.money import ZERO, qty


@dataclass
class Bal:
    on_hand: Decimal = ZERO
    reserved: Decimal = ZERO
    blocked: Decimal = ZERO

    @property
    def available(self) -> Decimal:
        return self.on_hand - self.reserved - self.blocked

    def as_dict(self):
        return {
            "on_hand": str(self.on_hand),
            "reserved": str(self.reserved),
            "blocked": str(self.blocked),
            "available": str(self.available),
        }


def lock_balance(db, org_id, warehouse_id, product_id) -> InventoryBalance:
    stmt = (
        select(InventoryBalance)
        .where(
            InventoryBalance.organization_id == org_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.product_id == product_id,
        )
        .with_for_update()
    )
    row = db.scalars(stmt).first()
    if row:
        return row
    try:
        with db.begin_nested():
            row = InventoryBalance(organization_id=org_id, warehouse_id=warehouse_id, product_id=product_id)
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return db.scalars(stmt).one()


def ledger_balance(db, org_id, warehouse_id, product_id) -> Bal:
    rows = db.execute(
        select(InventoryMovement.bucket, func.sum(InventoryMovement.quantity))
        .where(
            InventoryMovement.organization_id == org_id,
            InventoryMovement.warehouse_id == warehouse_id,
            InventoryMovement.product_id == product_id,
        )
        .group_by(InventoryMovement.bucket)
    ).all()
    b = Bal()
    for bucket, total in rows:
        setattr(b, bucket, qty(total or 0))
    return b


def balances(db, org_id, warehouse_id=None, product_ids=None) -> dict[tuple, Bal]:
    """{(warehouse_id, product_id): Bal} computed from the ledger."""
    stmt = (
        select(
            InventoryMovement.warehouse_id,
            InventoryMovement.product_id,
            InventoryMovement.bucket,
            func.sum(InventoryMovement.quantity),
        )
        .where(InventoryMovement.organization_id == org_id)
        .group_by(InventoryMovement.warehouse_id, InventoryMovement.product_id, InventoryMovement.bucket)
    )
    if warehouse_id:
        stmt = stmt.where(InventoryMovement.warehouse_id == warehouse_id)
    if product_ids is not None:
        stmt = stmt.where(InventoryMovement.product_id.in_(list(product_ids)))
    out: dict[tuple, Bal] = defaultdict(Bal)
    for wh, pid, bucket, total in db.execute(stmt).all():
        setattr(out[(wh, pid)], bucket, qty(total or 0))
    return out


def batch_on_hand(db, org_id, warehouse_id, product_id) -> list[tuple[Batch | None, Decimal]]:
    """On-hand per batch, FEFO order (earliest expiry first, unbatched last)."""
    rows = db.execute(
        select(InventoryMovement.batch_id, func.sum(InventoryMovement.quantity))
        .where(
            InventoryMovement.organization_id == org_id,
            InventoryMovement.warehouse_id == warehouse_id,
            InventoryMovement.product_id == product_id,
            InventoryMovement.bucket == "on_hand",
        )
        .group_by(InventoryMovement.batch_id)
    ).all()
    out = []
    for bid, total in rows:
        q = qty(total or 0)
        if q <= 0:
            continue
        out.append((db.get(Batch, bid) if bid else None, q))
    out.sort(
        key=lambda x: (
            x[0] is None,
            (x[0].expiry_date if x[0] and x[0].expiry_date else date.max),
            x[0].batch_number if x[0] else "",
        )
    )
    return out


def _check_refs(db, org_id, warehouse_id, product_id, batch_id=None):
    wh = db.get(Warehouse, warehouse_id)
    if not wh or wh.organization_id != org_id:
        raise AppError("NOT_FOUND", "Warehouse not found.", 404)
    p = db.get(Product, product_id)
    if not p or p.organization_id != org_id:
        raise AppError("NOT_FOUND", "Product not found.", 404)
    if batch_id:
        b = db.get(Batch, batch_id)
        if not b or b.organization_id != org_id or b.product_id != product_id:
            raise AppError("NOT_FOUND", "Batch not found for this product.", 404)
    return wh, p


def post(
    db,
    org_id: uuid.UUID,
    *,
    warehouse_id,
    product_id,
    movement_type: str,
    quantity,
    actor_id=None,
    batch_id=None,
    source_type: str | None = None,
    source_id=None,
    reason: str | None = None,
    evidence: str | None = None,
    allow_negative: bool = False,
    is_synthetic: bool = False,
    balance_row: InventoryBalance | None = None,
) -> InventoryMovement:
    """Append one movement and update the cache row. Caller owns the transaction."""
    if movement_type not in MOVEMENT_TYPES:
        raise AppError("INVALID_MOVEMENT_TYPE", f"Unknown movement type '{movement_type}'.", 422)
    q = qty(quantity)
    if q == 0:
        raise AppError("ZERO_QUANTITY", "Quantity must not be zero.", 422)
    _, product = _check_refs(db, org_id, warehouse_id, product_id, batch_id)
    bucket = MOVEMENT_TYPES[movement_type]
    bal = balance_row or lock_balance(db, org_id, warehouse_id, product_id)
    new_val = qty(getattr(bal, bucket) + q)
    if bucket == "on_hand" and q < 0 and new_val < 0 and not allow_negative:
        raise AppError(
            "NEGATIVE_STOCK",
            "This movement would make on-hand stock negative.",
            409,
            {"sku": product.sku, "on_hand": str(bal.on_hand), "change": str(q)},
        )
    if bucket in ("reserved", "blocked") and new_val < 0:
        raise AppError("INVALID_RELEASE", f"Cannot release more than is {bucket}.", 409, {"sku": product.sku})
    mv = InventoryMovement(
        organization_id=org_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        batch_id=batch_id,
        movement_type=movement_type,
        bucket=bucket,
        quantity=q,
        unit=product.base_unit,
        source_type=source_type,
        source_id=str(source_id) if source_id is not None else None,
        reason=reason,
        evidence=evidence,
        actor_id=actor_id,
        is_synthetic=is_synthetic,
    )
    db.add(mv)
    setattr(bal, bucket, new_val)
    return mv


def reserved_for_source(db, org_id, source_type: str, source_id) -> dict[tuple, Decimal]:
    """Outstanding reservation per (warehouse, product) held by a source document."""
    rows = db.execute(
        select(InventoryMovement.warehouse_id, InventoryMovement.product_id, func.sum(InventoryMovement.quantity))
        .where(
            InventoryMovement.organization_id == org_id,
            InventoryMovement.bucket == "reserved",
            InventoryMovement.source_type == source_type,
            InventoryMovement.source_id == str(source_id),
        )
        .group_by(InventoryMovement.warehouse_id, InventoryMovement.product_id)
    ).all()
    return {(wh, pid): qty(total) for wh, pid, total in rows if qty(total) != 0}


def reserve(
    db,
    org_id,
    *,
    warehouse_id,
    requirements: dict,
    source_type: str,
    source_id,
    actor_id=None,
    allow_shortfall: bool = False,
    reason: str | None = None,
    is_synthetic: bool = False,
) -> list[dict]:
    """Reserve {product_id: base_qty}. Locks balance rows in a deterministic order to
    avoid deadlocks. Raises INSUFFICIENT_AVAILABLE_STOCK unless allow_shortfall."""
    shortfalls = []
    locked = {}
    for pid in sorted(requirements, key=str):
        locked[pid] = lock_balance(db, org_id, warehouse_id, pid)
    for pid in sorted(requirements, key=str):
        need = qty(requirements[pid])
        if need <= 0:
            continue
        led = ledger_balance(db, org_id, warehouse_id, pid)
        if need > led.available:
            p = db.get(Product, pid)
            shortfalls.append({"sku": p.sku, "product_id": str(pid), "requested": str(need), "available": str(led.available)})
    if shortfalls and not allow_shortfall:
        first = shortfalls[0]
        raise AppError(
            "INSUFFICIENT_AVAILABLE_STOCK",
            "The requested quantity exceeds available stock.",
            409,
            {**first, "lines": shortfalls},
        )
    for pid in sorted(requirements, key=str):
        need = qty(requirements[pid])
        if need > 0:
            post(
                db,
                org_id,
                warehouse_id=warehouse_id,
                product_id=pid,
                movement_type="sales_reservation",
                quantity=need,
                actor_id=actor_id,
                source_type=source_type,
                source_id=source_id,
                reason=reason,
                is_synthetic=is_synthetic,
                balance_row=locked[pid],
            )
    return shortfalls


def release_all(db, org_id, *, source_type: str, source_id, actor_id=None, reason: str | None = None) -> int:
    held = reserved_for_source(db, org_id, source_type, source_id)
    for (wh, pid), q in sorted(held.items(), key=lambda kv: str(kv[0][1])):
        if q > 0:
            post(
                db,
                org_id,
                warehouse_id=wh,
                product_id=pid,
                movement_type="reservation_release",
                quantity=-q,
                actor_id=actor_id,
                source_type=source_type,
                source_id=source_id,
                reason=reason,
            )
    return len(held)


def verify_balances(db, org_id) -> list[dict]:
    """Return cache rows that disagree with the ledger (should always be empty)."""
    ledger = balances(db, org_id)
    mismatches = []
    rows = db.scalars(select(InventoryBalance).where(InventoryBalance.organization_id == org_id)).all()
    seen = set()
    for r in rows:
        key = (r.warehouse_id, r.product_id)
        seen.add(key)
        led = ledger.get(key, Bal())
        if (r.on_hand, r.reserved, r.blocked) != (led.on_hand, led.reserved, led.blocked):
            mismatches.append({"warehouse_id": str(key[0]), "product_id": str(key[1]), "cache": [str(r.on_hand), str(r.reserved), str(r.blocked)], "ledger": led.as_dict()})
    for key, led in ledger.items():
        if key not in seen and (led.on_hand or led.reserved or led.blocked):
            mismatches.append({"warehouse_id": str(key[0]), "product_id": str(key[1]), "cache": None, "ledger": led.as_dict()})
    return mismatches


def get_or_create_batch(db, org_id, product_id, batch_number: str | None, expiry_date=None, manufactured_on=None):
    if not batch_number:
        return None
    b = db.scalars(
        select(Batch).where(Batch.organization_id == org_id, Batch.product_id == product_id, Batch.batch_number == batch_number)
    ).first()
    if b:
        if expiry_date and b.expiry_date and b.expiry_date != expiry_date:
            raise AppError("BATCH_EXPIRY_CONFLICT", f"Batch {batch_number} already exists with expiry {b.expiry_date}.", 409)
        if expiry_date and not b.expiry_date:
            b.expiry_date = expiry_date
        return b
    b = Batch(organization_id=org_id, product_id=product_id, batch_number=batch_number, expiry_date=expiry_date, manufactured_on=manufactured_on)
    db.add(b)
    db.flush()
    return b
