"""Shared API helpers: pagination envelope, sorting, document numbering, idempotency."""

import hashlib
import json
from decimal import Decimal
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Query
from pydantic import AfterValidator, BaseModel, ConfigDict
from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.exc import IntegrityError

from app.errors import AppError, conflict
from app.models import IdempotencyKey

T = TypeVar("T")


def _money(v: Decimal) -> Decimal:
    from app.money import money

    return money(v)


def _qty(v: Decimal) -> Decimal:
    from app.money import qty

    return qty(v)


# Input types: quantised on the way in, so responses are consistent before/after reload.
Money = Annotated[Decimal, AfterValidator(_money)]
Qty = Annotated[Decimal, AfterValidator(_qty)]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PageParams:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(25, ge=1, le=200),
        sort: str | None = Query(None, description="Field name; prefix with '-' for descending"),
        q: str | None = Query(None, max_length=200, description="Free-text search"),
    ):
        self.page = page
        self.page_size = page_size
        self.sort = sort
        self.q = q.strip() if q else None


def paginate(db, stmt: Select, params: PageParams, model=None, sortable: tuple[str, ...] = (), default_sort=None):
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    if params.sort and model is not None:
        field = params.sort.lstrip("-")
        if field not in sortable:
            raise AppError("INVALID_SORT", f"Cannot sort by '{field}'.", 400, {"allowed": list(sortable)})
        col = getattr(model, field)
        stmt = stmt.order_by(desc(col) if params.sort.startswith("-") else asc(col))
    elif default_sort is not None:
        stmt = stmt.order_by(default_sort)
    rows = db.scalars(stmt.offset((params.page - 1) * params.page_size).limit(params.page_size)).all()
    return rows, total


def page_out(items: list, total: int, params: PageParams) -> dict:
    return {"items": items, "total": total, "page": params.page, "page_size": params.page_size}


def next_number(db, model, org_id, field: str, prefix: str, width: int = 6) -> str:
    """Sequential, tenant-scoped document number (e.g. SO-000123). Uniqueness is
    enforced by a DB constraint; callers run inside the creating transaction."""
    col = getattr(model, field)
    count = db.scalar(select(func.count()).select_from(model).where(model.organization_id == org_id)) or 0
    n = count + 1
    while True:
        candidate = f"{prefix}-{n:0{width}d}"
        exists = db.scalar(select(func.count()).select_from(model).where(model.organization_id == org_id, col == candidate))
        if not exists:
            return candidate
        n += 1


def request_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def idempotency_lookup(db, org_id, scope: str, key: str | None, payload: Any) -> IdempotencyKey | None:
    """Return the stored key if this (scope, key) was already used with the same payload.
    Raises if the key was reused with a different payload."""
    if not key:
        return None
    if len(key) > 200:
        raise AppError("INVALID_IDEMPOTENCY_KEY", "Idempotency-Key is too long.", 400)
    existing = db.scalars(
        select(IdempotencyKey).where(
            IdempotencyKey.organization_id == org_id, IdempotencyKey.scope == scope, IdempotencyKey.key == key
        )
    ).first()
    if existing and existing.request_hash != request_hash(payload):
        raise conflict("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used with a different request.")
    return existing


def idempotency_store(db, org_id, scope: str, key: str | None, payload: Any, resource_type: str, resource_id) -> None:
    if not key:
        return
    db.add(
        IdempotencyKey(
            organization_id=org_id,
            scope=scope,
            key=key,
            request_hash=request_hash(payload),
            resource_type=resource_type,
            resource_id=str(resource_id),
        )
    )


def commit_or_conflict(db, code: str = "DUPLICATE", message: str = "A record with these values already exists."):
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise conflict(code, message) from exc
