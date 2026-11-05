import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.config import get_settings
from app.db import get_db, utcnow
from app.deps import Ctx, get_ctx
from app.errors import AppError
from app.models import Invitation, Membership, Organization, PasswordResetToken, RefreshToken, User
from app.permissions import permissions_for
from app.security import (
    create_access_token,
    hash_password,
    hash_token,
    new_opaque_token,
    rate_limiter,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])
REFRESH_COOKIE = "tradeos_refresh"


class _Sys:
    """Minimal ctx for audit events emitted before a user context exists."""

    def __init__(self, db, user=None, org=None, request: Request | None = None):
        self.db = db
        self.user = user
        self.org = org
        self.request_id = getattr(request.state, "request_id", None) if request else None
        self.ip = request.client.host if request and request.client else None


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    full_name: str = Field(min_length=1, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)
    organization_id: uuid.UUID | None = None


class RefreshIn(BaseModel):
    refresh_token: str | None = None


class SwitchOrgIn(BaseModel):
    organization_id: uuid.UUID


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str
    password: str = Field(min_length=10, max_length=200)


class AcceptInviteIn(BaseModel):
    token: str
    full_name: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, min_length=10, max_length=200)


class DemoLoginIn(BaseModel):
    role: str = "org_owner"


def _rate_limit(request: Request, key: str):
    ip = request.client.host if request.client else "unknown"
    if not rate_limiter.allow(f"{key}:{ip}", get_settings().auth_rate_limit_per_minute):
        raise AppError("RATE_LIMITED", "Too many attempts. Please wait a minute and try again.", 429)


def _password_strength(pw: str):
    if pw.lower() == pw or not any(c.isdigit() for c in pw):
        raise AppError(
            "WEAK_PASSWORD", "Password must be at least 10 characters and include an uppercase letter and a digit.", 422
        )


def issue_tokens(db: Session, response: Response, user: User, org_id: uuid.UUID | None, family=None) -> dict:
    s = get_settings()
    token, token_hash = new_opaque_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            organization_id=org_id,
            token_hash=token_hash,
            family_id=family or uuid.uuid4(),
            expires_at=utcnow() + timedelta(days=s.refresh_token_days),
        )
    )
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        secure=s.is_production,
        samesite="strict",
        max_age=s.refresh_token_days * 86400,
        path="/api/v1/auth",
    )
    return {
        "access_token": create_access_token(user.id, org_id),
        "refresh_token": token,
        "token_type": "bearer",
        "expires_in": s.access_token_minutes * 60,
        "organization_id": str(org_id) if org_id else None,
    }


def _default_org(db: Session, user: User, wanted: uuid.UUID | None) -> uuid.UUID | None:
    q = select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
    if wanted:
        m = db.scalars(q.where(Membership.organization_id == wanted)).first()
        if not m:
            raise AppError("FORBIDDEN", "You are not a member of that organisation.", 403)
        return m.organization_id
    m = db.scalars(q.order_by(Membership.created_at)).first()
    return m.organization_id if m else None


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _rate_limit(request, "register")
    _password_strength(body.password)
    email = body.email.lower()
    if db.scalars(select(User).where(User.email == email)).first():
        raise AppError("EMAIL_TAKEN", "An account with this email already exists.", 409)
    user = User(email=email, full_name=body.full_name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.flush()
    audit.record(_Sys(db, user, request=request), "auth.register", "user", user.id)
    tokens = issue_tokens(db, response, user, None)
    db.commit()
    return tokens


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _rate_limit(request, f"login:{body.email.lower()}")
    user = db.scalars(select(User).where(User.email == body.email.lower())).first()
    if not user or not user.is_active or not verify_password(body.password, user.password_hash):
        audit.record(
            _Sys(db, None, request=request), "auth.login_failed", "user", None, meta={"email": body.email.lower()}
        )
        db.commit()
        raise AppError("INVALID_CREDENTIALS", "Email or password is incorrect.", 401)
    org_id = _default_org(db, user, body.organization_id)
    user.last_login_at = utcnow()
    audit.record(_Sys(db, user, request=request), "auth.login", "user", user.id, org_id=org_id)
    tokens = issue_tokens(db, response, user, org_id)
    db.commit()
    return tokens


@router.post("/refresh")
def refresh(request: Request, response: Response, body: RefreshIn | None = None, db: Session = Depends(get_db)):
    token = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise AppError("UNAUTHENTICATED", "No refresh token.", 401)
    rt = db.scalars(select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))).first()
    if not rt:
        raise AppError("UNAUTHENTICATED", "Invalid refresh token.", 401)
    if rt.revoked_at is not None:
        # Reuse of a rotated token: revoke the whole family (possible theft).
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        audit.record(_Sys(db, None, request=request), "auth.refresh_reuse_detected", "user", rt.user_id)
        db.commit()
        raise AppError("UNAUTHENTICATED", "Session expired. Please sign in again.", 401)
    if rt.expires_at < utcnow():
        raise AppError("UNAUTHENTICATED", "Session expired. Please sign in again.", 401)
    user = db.get(User, rt.user_id)
    if not user or not user.is_active:
        raise AppError("UNAUTHENTICATED", "Account disabled.", 401)
    rt.revoked_at = utcnow()
    org_id = rt.organization_id
    if org_id:
        still = db.scalars(
            select(Membership).where(
                Membership.user_id == user.id, Membership.organization_id == org_id, Membership.is_active.is_(True)
            )
        ).first()
        org_id = org_id if still else _default_org(db, user, None)
    tokens = issue_tokens(db, response, user, org_id, family=rt.family_id)
    db.commit()
    return tokens


@router.post("/logout")
def logout(request: Request, response: Response, body: RefreshIn | None = None, db: Session = Depends(get_db)):
    token = (body.refresh_token if body else None) or request.cookies.get(REFRESH_COOKIE)
    if token:
        rt = db.scalars(select(RefreshToken).where(RefreshToken.token_hash == hash_token(token))).first()
        if rt:
            db.execute(
                update(RefreshToken)
                .where(RefreshToken.family_id == rt.family_id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            db.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")
    return {"ok": True}


@router.post("/switch-organization")
def switch_org(body: SwitchOrgIn, response: Response, ctx: Ctx = Depends(get_ctx)):
    org_id = _default_org(ctx.db, ctx.user, body.organization_id)
    audit.record(ctx, "auth.switch_organization", "organization", org_id, org_id=org_id)
    tokens = issue_tokens(ctx.db, response, ctx.user, org_id)
    ctx.db.commit()
    return tokens


@router.get("/me")
def me(ctx: Ctx = Depends(get_ctx)):
    rows = ctx.db.execute(
        select(Membership, Organization)
        .join(Organization, Organization.id == Membership.organization_id)
        .where(Membership.user_id == ctx.user.id, Membership.is_active.is_(True))
        .order_by(Organization.name)
    ).all()
    return {
        "user": {
            "id": str(ctx.user.id),
            "email": ctx.user.email,
            "full_name": ctx.user.full_name,
            "is_platform_admin": ctx.user.is_platform_admin,
        },
        "organization": (
            {
                "id": str(ctx.org.id),
                "name": ctx.org.name,
                "org_type": ctx.org.org_type,
                "currency": ctx.org.currency,
                "timezone": ctx.org.timezone,
                "is_demo": ctx.org.is_demo,
            }
            if ctx.org
            else None
        ),
        "role": ctx.role,
        "permissions": sorted(ctx.permissions),
        "memberships": [
            {
                "organization_id": str(o.id),
                "organization_name": o.name,
                "org_type": o.org_type,
                "role": m.role,
                "is_demo": o.is_demo,
            }
            for m, o in rows
        ],
    }


@router.post("/forgot-password")
def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request, "forgot")
    s = get_settings()
    user = db.scalars(select(User).where(User.email == body.email.lower())).first()
    out: dict = {
        "ok": True,
        "message": "If an account exists for this email, a reset link has been issued.",
        "email_delivery": "not_configured",
    }
    if user and user.is_active:
        token, th = new_opaque_token()
        db.add(
            PasswordResetToken(
                user_id=user.id, token_hash=th, expires_at=utcnow() + timedelta(minutes=s.password_reset_minutes)
            )
        )
        audit.record(_Sys(db, user, request=request), "auth.password_reset_requested", "user", user.id)
        db.commit()
        # Email is not configured. Outside production the token is returned so the flow
        # can be completed locally; in production it is never exposed in the response.
        if not s.is_production:
            out["dev_reset_token"] = token
    return out


@router.post("/reset-password")
def reset_password(body: ResetIn, request: Request, db: Session = Depends(get_db)):
    _rate_limit(request, "reset")
    _password_strength(body.password)
    rt = db.scalars(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(body.token))).first()
    if not rt or rt.used_at is not None or rt.expires_at < utcnow():
        raise AppError("INVALID_TOKEN", "This reset link is invalid or has expired.", 400)
    user = db.get(User, rt.user_id)
    user.password_hash = hash_password(body.password)
    rt.used_at = utcnow()
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    audit.record(_Sys(db, user, request=request), "auth.password_reset", "user", user.id)
    db.commit()
    return {"ok": True}


@router.post("/accept-invitation")
def accept_invitation(body: AcceptInviteIn, request: Request, response: Response, db: Session = Depends(get_db)):
    _rate_limit(request, "invite")
    inv = db.scalars(select(Invitation).where(Invitation.token_hash == hash_token(body.token))).first()
    if not inv or inv.accepted_at or inv.revoked_at or inv.expires_at < utcnow():
        raise AppError("INVALID_TOKEN", "This invitation is invalid or has expired.", 400)
    user = db.scalars(select(User).where(User.email == inv.email)).first()
    if not user:
        if not body.password or not body.full_name:
            raise AppError("ACCOUNT_DETAILS_REQUIRED", "Name and password are required to create your account.", 422)
        _password_strength(body.password)
        user = User(email=inv.email, full_name=body.full_name.strip(), password_hash=hash_password(body.password))
        db.add(user)
        db.flush()
    existing = db.scalars(
        select(Membership).where(Membership.user_id == user.id, Membership.organization_id == inv.organization_id)
    ).first()
    if existing:
        existing.role = inv.role
        existing.is_active = True
    else:
        db.add(Membership(user_id=user.id, organization_id=inv.organization_id, role=inv.role))
    inv.accepted_at = utcnow()
    org = db.get(Organization, inv.organization_id)
    audit.record(
        _Sys(db, user, org, request), "auth.invitation_accepted", "membership", user.id, meta={"role": inv.role}
    )
    tokens = issue_tokens(db, response, user, inv.organization_id)
    db.commit()
    return tokens


@router.post("/demo-login")
def demo_login(body: DemoLoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    """Development-only shortcut into the seeded demo organisation."""
    s = get_settings()
    if s.is_production or not s.enable_demo_login:
        raise AppError("NOT_AVAILABLE", "Demo login is disabled.", 404)
    org = db.scalars(select(Organization).where(Organization.is_demo.is_(True), Organization.org_type == "distributor")).first()
    if not org:
        raise AppError("DEMO_NOT_SEEDED", "Demo data has not been seeded. Run `python -m app.seed`.", 404)
    m = db.scalars(
        select(Membership).where(
            Membership.organization_id == org.id, Membership.role == body.role, Membership.is_active.is_(True)
        )
    ).first()
    if not m:
        raise AppError("NOT_FOUND", "No demo user with that role.", 404)
    user = db.get(User, m.user_id)
    audit.record(_Sys(db, user, org, request), "auth.demo_login", "user", user.id)
    tokens = issue_tokens(db, response, user, org.id)
    db.commit()
    return tokens


@router.get("/roles")
def roles(ctx: Ctx = Depends(get_ctx)):
    org_type = ctx.org.org_type if ctx.org else "distributor"
    from app.permissions import DISTRIBUTOR_ROLES, MANUFACTURER_ROLES

    names = MANUFACTURER_ROLES if org_type == "manufacturer" else DISTRIBUTOR_ROLES
    return [{"role": r, "permissions": sorted(permissions_for(r, org_type))} for r in names]
