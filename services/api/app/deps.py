"""Request context: authenticated user + tenant derived from the access token.

The organisation is NEVER taken from the request body/query: it comes from the
signed token and is re-validated against an active membership on every request.
"""

import uuid
from dataclasses import dataclass, field
from typing import TypeVar

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.errors import AppError, forbidden, not_found
from app.models import Membership, Organization, User
from app.permissions import permissions_for
from app.security import decode_access_token

T = TypeVar("T")


@dataclass
class Ctx:
    db: Session
    user: User
    org: Organization | None
    role: str | None
    permissions: frozenset[str] = field(default_factory=frozenset)
    request_id: str | None = None
    ip: str | None = None

    @property
    def org_id(self) -> uuid.UUID:
        if self.org is None:
            raise AppError("NO_ORGANIZATION", "Select or create an organisation first.", 403)
        return self.org.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    def can(self, perm: str) -> bool:
        return perm in self.permissions

    def require(self, perm: str) -> None:
        if perm not in self.permissions:
            raise forbidden(f"Missing permission: {perm}")

    def get(self, model: type[T], obj_id, entity: str | None = None, lock: bool = False) -> T:
        """Fetch a tenant-owned row by id, enforcing organisation ownership.
        Returns 404 (not 403) for other tenants' ids to avoid leaking existence."""
        try:
            oid = obj_id if isinstance(obj_id, uuid.UUID) else uuid.UUID(str(obj_id))
        except (ValueError, TypeError):
            raise not_found(entity or model.__name__) from None
        stmt = select(model).where(model.id == oid, model.organization_id == self.org_id)  # type: ignore[attr-defined]
        if lock:
            stmt = stmt.with_for_update()
        obj = self.db.scalars(stmt).first()
        if obj is None:
            raise not_found(entity or model.__name__)
        return obj

    def maybe(self, model: type[T], obj_id) -> T | None:
        if obj_id is None:
            return None
        return self.get(model, obj_id)


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def get_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    token = _bearer(request)
    data = decode_access_token(token) if token else None
    if not data:
        raise AppError("UNAUTHENTICATED", "Authentication required.", 401)
    user = db.get(User, uuid.UUID(data["sub"]))
    if not user or not user.is_active:
        raise AppError("UNAUTHENTICATED", "Authentication required.", 401)
    org = None
    role = None
    perms: frozenset[str] = frozenset()
    if data.get("org"):
        m = db.scalars(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.organization_id == uuid.UUID(data["org"]),
                Membership.is_active.is_(True),
            )
        ).first()
        if m is None:
            raise AppError("UNAUTHENTICATED", "Membership is no longer active.", 401)
        org = db.get(Organization, m.organization_id)
        role = m.role
        perms = permissions_for(role, org.org_type if org else "distributor")
    return Ctx(
        db=db,
        user=user,
        org=org,
        role=role,
        permissions=perms,
        request_id=getattr(request.state, "request_id", None),
        ip=request.client.host if request.client else None,
    )


def require(*perms: str):
    """Dependency factory: `ctx: Ctx = Depends(require("orders.approve"))`."""

    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        ctx.org_id  # noqa: B018 - ensures a tenant is selected
        for p in perms:
            ctx.require(p)
        return ctx

    return dep


def require_org_type(*types: str):
    def dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        if ctx.org is None or ctx.org.org_type not in types:
            raise forbidden("This feature is not available for your organisation type.")
        return ctx

    return dep
