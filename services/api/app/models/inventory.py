import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Dec, IdMixin, TimestampMixin, UTCDateTime, Uuid, utcnow
from app.models.platform import OrgScoped

# Each ledger row moves stock within one bucket. Balances are SUM(quantity) per bucket.
BUCKETS = ("on_hand", "reserved", "blocked")

MOVEMENT_TYPES = {
    # type: bucket
    "opening_balance": "on_hand",
    "purchase_receipt": "on_hand",
    "sales_reservation": "reserved",
    "reservation_release": "reserved",
    "dispatch": "on_hand",
    "customer_return": "on_hand",
    "supplier_return": "on_hand",
    "write_off": "on_hand",
    "manual_adjustment": "on_hand",
    "transfer_out": "on_hand",
    "transfer_in": "on_hand",
    "stock_count_adjustment": "on_hand",
    "block": "blocked",
    "unblock": "blocked",
}


class Batch(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "batches"
    __table_args__ = (UniqueConstraint("organization_id", "product_id", "batch_number"),)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"), index=True)
    batch_number: Mapped[str] = mapped_column(String(60))
    expiry_date: Mapped[date | None] = mapped_column(Date, index=True)
    manufactured_on: Mapped[date | None] = mapped_column(Date)


class InventoryMovement(IdMixin, OrgScoped, Base):
    """Append-only stock ledger. Never updated or deleted."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        Index("ix_mov_balance", "organization_id", "warehouse_id", "product_id", "bucket"),
        Index("ix_mov_source", "source_type", "source_id"),
        Index("ix_mov_org_created", "organization_id", "created_at"),
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("batches.id"))
    movement_type: Mapped[str] = mapped_column(String(40))
    bucket: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[Decimal] = mapped_column(Dec(3))  # signed, base units
    unit: Mapped[str] = mapped_column(String(20))
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    is_synthetic: Mapped[bool] = mapped_column(default=False)


class InventoryBalance(IdMixin, OrgScoped, Base):
    """Derived cache + row lock per (warehouse, product). Never the source of truth;
    `inventory.verify_balances` proves it equals the ledger."""

    __tablename__ = "inventory_balances"
    __table_args__ = (UniqueConstraint("organization_id", "warehouse_id", "product_id"),)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id"))
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"))
    on_hand: Mapped[Decimal] = mapped_column(Dec(3), default=Decimal("0"))
    reserved: Mapped[Decimal] = mapped_column(Dec(3), default=Decimal("0"))
    blocked: Mapped[Decimal] = mapped_column(Dec(3), default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class StockCount(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "stock_counts"
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id"))
    count_number: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|posted|cancelled
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    posted_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    posted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class StockCountLine(IdMixin, OrgScoped, Base):
    __tablename__ = "stock_count_lines"
    stock_count_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("stock_counts.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"))
    system_quantity: Mapped[Decimal] = mapped_column(Dec(3))
    counted_quantity: Mapped[Decimal | None] = mapped_column(Dec(3))
    line_no: Mapped[int] = mapped_column(Integer, default=0)


class GoodsReceipt(IdMixin, TimestampMixin, OrgScoped, Base):
    """Supplier stock receipt (GRN). Posting writes purchase_receipt movements."""

    __tablename__ = "goods_receipts"
    __table_args__ = (UniqueConstraint("organization_id", "receipt_number"),)
    receipt_number: Mapped[str] = mapped_column(String(40))
    supplier_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("suppliers.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("warehouses.id"))
    supplier_reference: Mapped[str | None] = mapped_column(String(80), index=True)  # supplier inv no
    received_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="posted")
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(default=False)


class GoodsReceiptLine(IdMixin, OrgScoped, Base):
    __tablename__ = "goods_receipt_lines"
    goods_receipt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("goods_receipts.id"), index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("batches.id"))
    quantity: Mapped[Decimal] = mapped_column(Dec(3))
    unit_cost: Mapped[Decimal | None] = mapped_column(Dec(2))
