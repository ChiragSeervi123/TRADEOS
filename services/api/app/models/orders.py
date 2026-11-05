import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Dec, IdMixin, JSONType, TimestampMixin, UTCDateTime, Uuid, utcnow
from app.models.platform import OrgScoped

ORDER_STATUSES = (
    "draft",
    "validated",
    "approved",
    "picking",
    "dispatched",
    "delivered",
    "partially_fulfilled",
    "rejected",
    "cancelled",
    "failed",
)
# Inbox (source) statuses cover the first part of the lifecycle:
# received -> extracting -> needs_review -> converted (draft order created) | failed | discarded
SOURCE_STATUSES = ("received", "extracting", "needs_review", "converted", "failed", "discarded")


class Document(IdMixin, TimestampMixin, OrgScoped, Base):
    """An uploaded file, stored privately via the storage adapter."""

    __tablename__ = "documents"
    __table_args__ = (Index("ix_doc_org_sha", "organization_id", "sha256"),)
    kind: Mapped[str] = mapped_column(String(40))  # order|purchase_invoice|scheme_circular|credit_note|evidence|other
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(300))
    text_content: Mapped[str | None] = mapped_column(Text)  # extracted text (PDF/text), if any
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class Extraction(IdMixin, TimestampMixin, OrgScoped, Base):
    """One AI/parser run over a source. Raw output is kept for provenance."""

    __tablename__ = "extractions"
    kind: Mapped[str] = mapped_column(String(30))  # order|purchase_invoice|scheme
    source_type: Mapped[str] = mapped_column(String(30))  # order_source|document
    source_id: Mapped[str] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(40))  # demo_parser|anthropic|manual
    model: Mapped[str | None] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(10), default="1.0")
    status: Mapped[str] = mapped_column(String(20), default="succeeded")  # succeeded|failed
    raw_output: Mapped[dict | None] = mapped_column(JSONType)
    result: Mapped[dict | None] = mapped_column(JSONType)  # validated + matched
    overall_confidence: Mapped[Decimal | None] = mapped_column(Dec(3))
    warnings: Mapped[list | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)


class OrderSource(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "order_sources"
    __table_args__ = (Index("ix_source_org_status", "organization_id", "status"),)
    channel: Mapped[str] = mapped_column(String(20))  # text|pdf|image|manual|api|whatsapp
    status: Mapped[str] = mapped_column(String(20), default="received")
    raw_text: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id"))
    sender: Mapped[str | None] = mapped_column(String(200))
    meta: Mapped[dict | None] = mapped_column(JSONType)
    latest_extraction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("extractions.id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Order(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("organization_id", "order_number"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_order_idem"),
        Index("ix_order_org_status", "organization_id", "status"),
        Index("ix_order_org_created", "organization_id", "created_at"),
        Index("ix_order_retailer", "retailer_id"),
    )
    order_number: Mapped[str] = mapped_column(String(40))
    retailer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("retailers.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id"))
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("order_sources.id"))
    channel: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    order_date: Mapped[date] = mapped_column(Date)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    gross_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    discount_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    scheme_discount_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    taxable_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    net_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    validation: Mapped[dict | None] = mapped_column(JSONType)  # last validation result
    stock_override_reason: Mapped[str | None] = mapped_column(Text)
    credit_override_reason: Mapped[str | None] = mapped_column(Text)
    override_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Set when remaining (undispatched) quantities are closed short; reservation released.
    fulfilment_closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", order_by="OrderLine.line_no", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version}


class OrderLine(IdMixin, OrgScoped, Base):
    __tablename__ = "order_lines"
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id"))
    source_description: Mapped[str | None] = mapped_column(String(300))
    quantity: Mapped[Decimal] = mapped_column(Dec(3))  # as entered, in `unit`
    unit: Mapped[str] = mapped_column(String(20))
    conversion_factor: Mapped[Decimal] = mapped_column(Dec(4), default=Decimal("1"))
    base_quantity: Mapped[Decimal] = mapped_column(Dec(3), default=Decimal("0"))
    list_price: Mapped[Decimal | None] = mapped_column(Dec(2))  # per base unit from price list
    unit_price: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))  # applied per base unit
    price_override_reason: Mapped[str | None] = mapped_column(Text)
    gross_amount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    manual_discount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    scheme_discount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    taxable_amount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    tax_rate: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    is_free_goods: Mapped[bool] = mapped_column(Boolean, default=False)
    free_scheme_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    dispatched_quantity: Mapped[Decimal] = mapped_column(Dec(3), default=Decimal("0"))  # base units
    # Provenance: manual | extracted | corrected
    provenance: Mapped[str] = mapped_column(String(20), default="manual")
    extracted_values: Mapped[dict | None] = mapped_column(JSONType)
    match_confidence: Mapped[Decimal | None] = mapped_column(Dec(3))
    order: Mapped[Order] = relationship(back_populates="lines")


class OrderRevision(IdMixin, OrgScoped, Base):
    __tablename__ = "order_revisions"
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONType)
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OrderStatusEvent(IdMixin, OrgScoped, Base):
    """Status timeline incl. approvals, rejections, overrides."""

    __tablename__ = "order_status_events"
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class PickList(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "pick_lists"
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id"))
    pick_number: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|picking|picked|dispatched|cancelled
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    lines: Mapped[list["PickListLine"]] = relationship(order_by="PickListLine.line_no")


class PickListLine(IdMixin, OrgScoped, Base):
    __tablename__ = "pick_list_lines"
    pick_list_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pick_lists.id"), index=True
    )
    # Nullable: if a draft order's lines are replaced after an amendment, the cancelled
    # pick list keeps its own product/qty/batch history and the link is cleared.
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("order_lines.id", ondelete="SET NULL"))
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("batches.id"))
    requested_quantity: Mapped[Decimal] = mapped_column(Dec(3))
    picked_quantity: Mapped[Decimal | None] = mapped_column(Dec(3))
    pick_list: Mapped[PickList] = relationship(back_populates="lines")


class Dispatch(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "dispatches"
    __table_args__ = (
        UniqueConstraint("organization_id", "dispatch_number"),
        UniqueConstraint("pick_list_id", name="uq_dispatch_picklist"),
    )
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), index=True)
    pick_list_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("pick_lists.id"))
    dispatch_number: Mapped[str] = mapped_column(String(40))
    vehicle: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="dispatched")  # dispatched|delivered|failed
    dispatched_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivery_note: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))


class ReturnRecord(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "returns"
    __table_args__ = (UniqueConstraint("organization_id", "return_number"),)
    return_number: Mapped[str] = mapped_column(String(40))
    return_type: Mapped[str] = mapped_column(String(20))  # customer|supplier
    retailer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("retailers.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("suppliers.id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("orders.id"))
    sales_invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sales_invoices.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|received|credited|rejected
    reason: Mapped[str | None] = mapped_column(Text)
    return_date: Mapped[date] = mapped_column(Date)
    total_value: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    lines: Mapped[list["ReturnLine"]] = relationship(order_by="ReturnLine.line_no")


class ReturnLine(IdMixin, OrgScoped, Base):
    __tablename__ = "return_lines"
    return_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("returns.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("batches.id"))
    quantity: Mapped[Decimal] = mapped_column(Dec(3))  # base units
    unit_price: Mapped[Decimal] = mapped_column(Dec(2))
    condition: Mapped[str] = mapped_column(String(20), default="sellable")  # sellable|damaged|expired
    amount: Mapped[Decimal] = mapped_column(Dec(2))
    ret: Mapped[ReturnRecord] = relationship(back_populates="lines")
