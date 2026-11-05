import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Dec, IdMixin, JSONType, TimestampMixin, UTCDateTime, Uuid, utcnow
from app.models.platform import OrgScoped

SCHEME_TYPES = (
    "percentage",
    "fixed_amount",
    "buy_x_get_y",
    "quantity_slab",
    "value_slab",
    "combination",
)
# draft -> pending_approval -> active -> inactive (or rejected). Only `active` versions apply.
SCHEME_STATUSES = ("draft", "pending_approval", "active", "inactive", "rejected", "superseded")


class Scheme(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "schemes"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("suppliers.id"))
    funded_by: Mapped[str] = mapped_column(String(20), default="supplier")  # supplier|distributor
    status: Mapped[str] = mapped_column(String(20), default="draft")  # mirrors latest version
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class SchemeVersion(IdMixin, TimestampMixin, OrgScoped, Base):
    """Immutable once submitted. Edits create a new version."""

    __tablename__ = "scheme_versions"
    __table_args__ = (
        UniqueConstraint("scheme_id", "version"),
        Index("ix_sv_org_status", "organization_id", "status"),
    )
    scheme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("schemes.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    scheme_type: Mapped[str] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSONType)  # benefit formula, validated per type
    scope: Mapped[dict] = mapped_column(JSONType, default=dict)  # products/brands/categories/exclusions
    eligibility: Mapped[dict] = mapped_column(JSONType, default=dict)  # retailers/territories
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    stacking: Mapped[str] = mapped_column(String(20), default="exclusive")  # exclusive|stackable
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower applies first
    max_benefit: Mapped[Decimal | None] = mapped_column(Dec(2))  # cap per order
    source_reference: Mapped[str | None] = mapped_column(String(300))
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id"))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("extractions.id"))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    change_note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    submitted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SchemeApplication(IdMixin, OrgScoped, Base):
    """Persisted explanation of a scheme benefit on an order."""

    __tablename__ = "scheme_applications"
    __table_args__ = (Index("ix_sa_order", "order_id"), Index("ix_sa_scheme", "scheme_id"))
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"))
    scheme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("schemes.id"))
    scheme_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("scheme_versions.id"))
    scheme_version: Mapped[int] = mapped_column(Integer)
    benefit_type: Mapped[str] = mapped_column(String(20))  # discount|free_goods
    benefit_amount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    free_quantity: Mapped[Decimal] = mapped_column(Dec(3), default=Decimal("0"))
    explanation: Mapped[dict] = mapped_column(JSONType)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("scheme_claims.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SchemeClaim(IdMixin, TimestampMixin, OrgScoped, Base):
    """Expected reimbursement from a supplier for supplier-funded scheme benefits.
    Claims are prepared here; TRADEOS never submits them externally."""

    __tablename__ = "scheme_claims"
    __table_args__ = (UniqueConstraint("organization_id", "claim_number"),)
    claim_number: Mapped[str] = mapped_column(String(40))
    scheme_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("schemes.id"), index=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("suppliers.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    expected_amount: Mapped[Decimal] = mapped_column(Dec(2))
    # draft | submitted (recorded manually) | credited | partially_credited | rejected
    status: Mapped[str] = mapped_column(String(20), default="draft")
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
