import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Dec, IdMixin, JSONType, TimestampMixin, UTCDateTime, Uuid, utcnow
from app.models.platform import OrgScoped


class PurchaseInvoice(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "purchase_invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "supplier_id", "invoice_number", name="uq_pi_number"),
        Index("ix_pi_org_status", "organization_id", "status"),
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("suppliers.id"))
    invoice_number: Mapped[str] = mapped_column(String(80))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    subtotal: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    tax_total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    other_charges: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    # needs_review -> verified (authoritative) | rejected
    status: Mapped[str] = mapped_column(String(20), default="needs_review")
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id"))
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("extractions.id"))
    validation: Mapped[dict | None] = mapped_column(JSONType)
    verified_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    lines: Mapped[list["PurchaseInvoiceLine"]] = relationship(
        order_by="PurchaseInvoiceLine.line_no", cascade="all, delete-orphan"
    )


class PurchaseInvoiceLine(IdMixin, OrgScoped, Base):
    __tablename__ = "purchase_invoice_lines"
    purchase_invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("purchase_invoices.id"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id"))
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[Decimal] = mapped_column(Dec(3))
    unit_price: Mapped[Decimal] = mapped_column(Dec(2))
    tax_rate: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    tax_amount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    line_total: Mapped[Decimal] = mapped_column(Dec(2))  # quantity*unit_price (excl. tax)
    match_confidence: Mapped[Decimal | None] = mapped_column(Dec(3))


class SalesInvoice(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "sales_invoices"
    __table_args__ = (
        UniqueConstraint("organization_id", "invoice_number"),
        Index("ix_si_org_retailer", "organization_id", "retailer_id"),
        Index("ix_si_org_date", "organization_id", "invoice_date"),
    )
    invoice_number: Mapped[str] = mapped_column(String(40))
    retailer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("retailers.id"))
    order_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("orders.id"))
    dispatch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("dispatches.id"), unique=True)
    invoice_date: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    gross_amount: Mapped[Decimal] = mapped_column(Dec(2))  # before discounts, excl. tax
    discount_amount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))  # manual+scheme
    tax_amount: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Dec(2))  # gross - discount + tax
    status: Mapped[str] = mapped_column(String(20), default="open")  # open|partially_paid|paid|void
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    lines: Mapped[list["SalesInvoiceLine"]] = relationship(order_by="SalesInvoiceLine.line_no")


class SalesInvoiceLine(IdMixin, OrgScoped, Base):
    __tablename__ = "sales_invoice_lines"
    sales_invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sales_invoices.id"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"))
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("order_lines.id"))
    quantity: Mapped[Decimal] = mapped_column(Dec(3))  # base units
    unit_price: Mapped[Decimal] = mapped_column(Dec(2))
    gross_amount: Mapped[Decimal] = mapped_column(Dec(2))
    discount_amount: Mapped[Decimal] = mapped_column(Dec(2))
    tax_amount: Mapped[Decimal] = mapped_column(Dec(2))
    line_total: Mapped[Decimal] = mapped_column(Dec(2))
    is_free_goods: Mapped[bool] = mapped_column(Boolean, default=False)


class CreditNote(IdMixin, TimestampMixin, OrgScoped, Base):
    """Credit or debit note, issued to a retailer or received from a supplier."""

    __tablename__ = "credit_notes"
    __table_args__ = (
        UniqueConstraint("organization_id", "direction", "note_number", name="uq_note_number"),
    )
    note_type: Mapped[str] = mapped_column(String(10), default="credit")  # credit|debit
    direction: Mapped[str] = mapped_column(String(20))  # issued|received
    note_number: Mapped[str] = mapped_column(String(80))
    retailer_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("retailers.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("suppliers.id"))
    note_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Dec(2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    reason: Mapped[str] = mapped_column(String(40))  # return|scheme|price_adjustment|other
    description: Mapped[str | None] = mapped_column(Text)
    return_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("returns.id"))
    sales_invoice_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sales_invoices.id"))
    purchase_invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("purchase_invoices.id")
    )
    scheme_claim_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("scheme_claims.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id"))
    # draft -> issued/recorded -> applied (to a sales invoice) | void
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class Payment(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("organization_id", "payment_number"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_payment_idem"),
        Index("ix_pay_org_retailer", "organization_id", "retailer_id"),
    )
    payment_number: Mapped[str] = mapped_column(String(40))
    retailer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("retailers.id"))
    amount: Mapped[Decimal] = mapped_column(Dec(2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    received_on: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(20), default="bank_transfer")  # cash|upi|cheque|bank_transfer
    reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="recorded")  # recorded|void
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class PaymentAllocation(IdMixin, OrgScoped, Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (Index("ix_alloc_invoice", "sales_invoice_id"), Index("ix_alloc_payment", "payment_id"))
    payment_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("payments.id"))
    sales_invoice_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sales_invoices.id"))
    amount: Mapped[Decimal] = mapped_column(Dec(2))
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reversed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reversed_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    reversal_reason: Mapped[str | None] = mapped_column(Text)


RECON_STATUSES = ("open", "investigating", "awaiting_evidence", "resolved", "accepted_difference", "closed")
RECON_CATEGORIES = (
    "missing_credit_note",
    "price_mismatch",
    "quantity_mismatch",
    "scheme_benefit_mismatch",
    "duplicate_document",
    "unmatched_payment",
    "return_not_credited",
    "unknown_difference",
)


class ReconciliationCase(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "reconciliation_cases"
    __table_args__ = (
        UniqueConstraint("organization_id", "case_number"),
        UniqueConstraint("organization_id", "dedupe_key", name="uq_recon_dedupe"),
        Index("ix_recon_org_status", "organization_id", "status"),
    )
    case_number: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="open")
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    expected_value: Mapped[Decimal | None] = mapped_column(Dec(2))
    observed_value: Mapped[Decimal | None] = mapped_column(Dec(2))
    difference: Mapped[Decimal | None] = mapped_column(Dec(2))  # observed - expected
    matching_method: Mapped[str | None] = mapped_column(String(60))
    rule: Mapped[str | None] = mapped_column(String(60))
    explanation: Mapped[dict | None] = mapped_column(JSONType)
    confidence: Mapped[Decimal | None] = mapped_column(Dec(3))
    records: Mapped[list | None] = mapped_column(JSONType)  # [{type,id,label}]
    document_ids: Mapped[list | None] = mapped_column(JSONType)
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_value: Mapped[Decimal | None] = mapped_column(Dec(2))  # amount recovered/corrected
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)


class ReconciliationMatch(IdMixin, OrgScoped, Base):
    __tablename__ = "reconciliation_matches"
    __table_args__ = (
        Index("ix_match_left", "left_type", "left_id"),
        Index("ix_match_right", "right_type", "right_id"),
    )
    left_type: Mapped[str] = mapped_column(String(40))
    left_id: Mapped[str] = mapped_column(String(64))
    right_type: Mapped[str] = mapped_column(String(40))
    right_id: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(40))  # exact_reference|amount_date_window|manual
    score: Mapped[Decimal | None] = mapped_column(Dec(3))
    status: Mapped[str] = mapped_column(String(20), default="active")  # suggested|active|unmatched
    details: Mapped[dict | None] = mapped_column(JSONType)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    unmatched_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    unmatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ReconciliationEvent(IdMixin, OrgScoped, Base):
    __tablename__ = "reconciliation_events"
    case_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("reconciliation_cases.id"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(40))  # created|status_change|note|assigned|evidence
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str | None] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("documents.id"))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
