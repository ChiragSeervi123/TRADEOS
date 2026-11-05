"""Audit trail. Events are added to the caller's transaction so they commit (or roll
back) atomically with the mutation they describe."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import inspect

from app.models import AuditEvent

_REDACT = {"password_hash", "token_hash", "password", "api_key", "secret"}


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (uuid.UUID,)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    return str(value)


def snapshot(obj, fields: list[str] | None = None) -> dict:
    """Column values of an ORM object, JSON-safe, secrets redacted."""
    if obj is None:
        return {}
    cols = [c.key for c in inspect(obj).mapper.column_attrs]
    out = {}
    for k in fields or cols:
        if k in _REDACT:
            continue
        out[k] = jsonable(getattr(obj, k, None))
    return out


def diff(before: dict, after: dict) -> tuple[dict, dict]:
    keys = {k for k in set(before) | set(after) if before.get(k) != after.get(k) and k not in ("updated_at", "version")}
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def record(
    ctx,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    meta: dict | None = None,
    org_id: uuid.UUID | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        organization_id=org_id or (ctx.org.id if ctx and ctx.org else None),
        actor_id=ctx.user.id if ctx and getattr(ctx, "user", None) else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        request_id=getattr(ctx, "request_id", None),
        before=jsonable(before) if before else None,
        after=jsonable(after) if after else None,
        meta=jsonable(meta) if meta else None,
        ip=getattr(ctx, "ip", None),
    )
    ctx.db.add(ev)
    return ev
