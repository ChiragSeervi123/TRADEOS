import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, IdMixin, JSONType, TimestampMixin, UTCDateTime, Uuid

ORG_TYPES = ("platform", "distributor", "manufacturer")

DEFAULT_ORG_SETTINGS: dict = {
    # Inventory: "block" refuses reservations beyond available stock; "allow_with_override"
    # permits an authorised user to override with a reason (audited).
    "negative_stock_policy": "block",
    # Receivable ageing bucket upper bounds in days past due.
    "ageing_buckets": [30, 60, 90],
    # Reconciliation tolerances.
    "amount_tolerance": "1.00",
    "price_tolerance_pct": "0.5",
    "match_date_window_days": 7,
    "missing_credit_note_days": 30,
    "return_credit_days": 7,
    "unallocated_payment_days": 7,
    "expiry_alert_days": 30,
    # Orders with net total above this require approval by org_owner/org_admin.
    "approval_threshold": None,
    "default_payment_terms_days": 30,
}


class Organization(IdMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    org_type: Mapped[str] = mapped_column(String(20), default="distributor")
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata")
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    gstin: Mapped[str | None] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(500))
    settings: Mapped[dict] = mapped_column(JSONType, default=dict)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # Organisation-level consent to send documents to the configured external AI provider.
    ai_external_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    def setting(self, key: str):
        return (self.settings or {}).get(key, DEFAULT_ORG_SETTINGS.get(key))


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(300))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    memberships: Mapped[list["Membership"]] = relationship(back_populates="user")


class Membership(IdMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship()


class Invitation(IdMixin, TimestampMixin, Base):
    __tablename__ = "invitations"
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    email: Mapped[str] = mapped_column(String(254))
    role: Mapped[str] = mapped_column(String(40))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class RefreshToken(IdMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("organizations.id"))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class PasswordResetToken(IdMixin, TimestampMixin, Base):
    __tablename__ = "password_reset_tokens"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class DataSharingGrant(IdMixin, TimestampMixin, Base):
    """Explicit, revocable grant from a distributor to a manufacturer organisation."""

    __tablename__ = "data_sharing_grants"
    __table_args__ = (Index("ix_grant_pair", "organization_id", "grantee_organization_id"),)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )  # the distributor (data owner)
    grantee_organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id"), index=True
    )
    # {"metrics": ["sales","stock","schemes"], "brands": ["..."], "include_retailer_detail": false}
    scope: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | revoked
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
