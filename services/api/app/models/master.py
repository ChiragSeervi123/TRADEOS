import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, Dec, IdMixin, JSONType, TimestampMixin, Uuid
from app.models.platform import OrgScoped


class UnitOfMeasure(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "units_of_measure"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(80))
    is_base: Mapped[bool] = mapped_column(Boolean, default=False)


class Supplier(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), index=True)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    address: Mapped[str | None] = mapped_column(String(500))
    gstin: Mapped[str | None] = mapped_column(String(20))
    external_id: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Product(IdMixin, TimestampMixin, OrgScoped, Base):
    """A sellable SKU. Each pack-size variant is its own SKU (ADR 0004)."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("organization_id", "sku"),
        Index("ix_product_org_brand", "organization_id", "brand"),
        Index("ix_product_org_category", "organization_id", "category"),
    )
    sku: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(250), index=True)
    brand: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(120))
    product_group: Mapped[str | None] = mapped_column(String(200))  # family shared by variants
    variant: Mapped[str | None] = mapped_column(String(80))  # pack size e.g. "1 kg"
    base_unit: Mapped[str] = mapped_column(String(20), default="PCS")
    tax_code: Mapped[str | None] = mapped_column(String(20))  # e.g. HSN, metadata only
    tax_rate: Mapped[Decimal] = mapped_column(Dec(2), default=Decimal("0"))  # configured, not advice
    mrp: Mapped[Decimal | None] = mapped_column(Dec(2))
    cost_price: Mapped[Decimal | None] = mapped_column(Dec(2))  # agreed supplier price / base unit
    barcode: Mapped[str | None] = mapped_column(String(40), index=True)
    image_url: Mapped[str | None] = mapped_column(String(500))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("suppliers.id"))
    aliases: Mapped[list | None] = mapped_column(JSONType)  # alternate names for matching
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supplier: Mapped[Supplier | None] = relationship()


class UnitConversion(IdMixin, TimestampMixin, OrgScoped, Base):
    """Factor to convert `unit_code` into the product's base unit.

    product_id NULL means the conversion applies to every product (e.g. DOZEN = 12).
    """

    __tablename__ = "unit_conversions"
    __table_args__ = (Index("ix_uc_lookup", "organization_id", "unit_code", "product_id"),)
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id"))
    unit_code: Mapped[str] = mapped_column(String(20))
    factor: Mapped[Decimal] = mapped_column(Dec(4))
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class Retailer(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "retailers"
    __table_args__ = (
        UniqueConstraint("organization_id", "code"),
        Index("ix_retailer_org_territory", "organization_id", "territory"),
    )
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200), index=True)
    contact_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(254))
    billing_address: Mapped[str | None] = mapped_column(String(500))
    delivery_address: Mapped[str | None] = mapped_column(String(500))
    territory: Mapped[str | None] = mapped_column(String(100))
    route: Mapped[str | None] = mapped_column(String(100))
    gstin: Mapped[str | None] = mapped_column(String(20))
    credit_limit: Mapped[Decimal | None] = mapped_column(Dec(2))  # NULL = no limit configured
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=30)
    price_list_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("price_lists.id"))
    external_ids: Mapped[dict | None] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Warehouse(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "warehouses"
    __table_args__ = (UniqueConstraint("organization_id", "code"),)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    address: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)


class PriceList(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)
    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    items: Mapped[list["PriceListItem"]] = relationship(back_populates="price_list")


class PriceListItem(IdMixin, TimestampMixin, OrgScoped, Base):
    __tablename__ = "price_list_items"
    __table_args__ = (UniqueConstraint("price_list_id", "product_id"),)
    price_list_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("price_lists.id"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("products.id"), index=True)
    unit_price: Mapped[Decimal] = mapped_column(Dec(2))  # per base unit, excl. tax
    price_list: Mapped[PriceList] = relationship(back_populates="items")
