"""Versioned extraction schemas. Provider output is untrusted: it is validated against
these Pydantic models before anything else touches it.

The JSON schemas below are sent to the LLM as structured-output constraints; the
Pydantic models re-validate the result locally (defence in depth).
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "1.0"


def _nullable(t: str) -> dict:
    return {"type": [t, "null"]}


class RetailerHint(BaseModel):
    name: str | None = None
    phone: str | None = None
    code: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class OrderLineRaw(BaseModel):
    source_description: str = Field(max_length=500)
    product_text: str = Field(max_length=300)
    quantity: Decimal | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=30)
    sku_hint: str | None = Field(default=None, max_length=60)
    confidence: float = Field(default=0.0, ge=0, le=1)


class OrderExtractionRaw(BaseModel):
    schema_version: str = SCHEMA_VERSION
    retailer: RetailerHint = RetailerHint()
    lines: list[OrderLineRaw] = Field(default_factory=list, max_length=300)
    requested_delivery_date: date | None = None
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("requested_delivery_date", mode="before")
    @classmethod
    def _date(cls, v):
        if v in ("", None):
            return None
        return v


ORDER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "retailer": {
            "type": "object",
            "properties": {
                "name": _nullable("string"),
                "phone": _nullable("string"),
                "code": _nullable("string"),
                "confidence": {"type": "number"},
            },
            "required": ["name", "phone", "code", "confidence"],
            "additionalProperties": False,
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_description": {"type": "string"},
                    "product_text": {"type": "string"},
                    "quantity": _nullable("number"),
                    "unit": _nullable("string"),
                    "sku_hint": _nullable("string"),
                    "confidence": {"type": "number"},
                },
                "required": ["source_description", "product_text", "quantity", "unit", "sku_hint", "confidence"],
                "additionalProperties": False,
            },
        },
        "requested_delivery_date": _nullable("string"),
        "notes": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["schema_version", "retailer", "lines", "requested_delivery_date", "notes", "warnings"],
    "additionalProperties": False,
}


class InvoiceLineRaw(BaseModel):
    description: str = Field(max_length=300)
    sku_hint: str | None = Field(default=None, max_length=60)
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    line_total: Decimal | None = None


class InvoiceExtractionRaw(BaseModel):
    schema_version: str = SCHEMA_VERSION
    supplier_name: str | None = None
    supplier_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = "INR"
    lines: list[InvoiceLineRaw] = Field(default_factory=list, max_length=500)
    subtotal: Decimal | None = None
    tax_total: Decimal | None = None
    other_charges: Decimal | None = None
    total: Decimal | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("invoice_date", "due_date", mode="before")
    @classmethod
    def _date(cls, v):
        return None if v in ("", None) else v


INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "supplier_name": _nullable("string"),
        "supplier_gstin": _nullable("string"),
        "invoice_number": _nullable("string"),
        "invoice_date": _nullable("string"),
        "due_date": _nullable("string"),
        "currency": _nullable("string"),
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "sku_hint": _nullable("string"),
                    "quantity": _nullable("number"),
                    "unit_price": _nullable("number"),
                    "tax_rate": _nullable("number"),
                    "tax_amount": _nullable("number"),
                    "line_total": _nullable("number"),
                },
                "required": ["description", "sku_hint", "quantity", "unit_price", "tax_rate", "tax_amount", "line_total"],
                "additionalProperties": False,
            },
        },
        "subtotal": _nullable("number"),
        "tax_total": _nullable("number"),
        "other_charges": _nullable("number"),
        "total": _nullable("number"),
        "confidence": {"type": "number"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "schema_version", "supplier_name", "supplier_gstin", "invoice_number", "invoice_date", "due_date", "currency",
        "lines", "subtotal", "tax_total", "other_charges", "total", "confidence", "warnings",
    ],
    "additionalProperties": False,
}


class SlabRaw(BaseModel):
    min_quantity: Decimal | None = None
    min_value: Decimal | None = None
    percent: Decimal | None = None
    amount: Decimal | None = None
    amount_per_unit: Decimal | None = None


class SchemeCandidateRaw(BaseModel):
    name: str = Field(max_length=200)
    scheme_type: str | None = None
    percent: Decimal | None = None
    amount: Decimal | None = None
    amount_per: str | None = None  # order|unit
    buy_quantity: Decimal | None = None
    free_quantity: Decimal | None = None
    free_product_text: str | None = None
    min_quantity: Decimal | None = None
    min_value: Decimal | None = None
    slabs: list[SlabRaw] = Field(default_factory=list)
    brands: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    product_texts: list[str] = Field(default_factory=list)
    territories: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    max_benefit: Decimal | None = None
    source_section: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _date(cls, v):
        return None if v in ("", None) else v


class SchemeExtractionRaw(BaseModel):
    schema_version: str = SCHEMA_VERSION
    schemes: list[SchemeCandidateRaw] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list)


_SLAB = {
    "type": "object",
    "properties": {k: _nullable("number") for k in ("min_quantity", "min_value", "percent", "amount", "amount_per_unit")},
    "required": ["min_quantity", "min_value", "percent", "amount", "amount_per_unit"],
    "additionalProperties": False,
}
_SCHEME_PROPS = {
    "name": {"type": "string"},
    # One of: percentage, fixed_amount, buy_x_get_y, quantity_slab, value_slab, combination
    # (validated locally against scheme_engine).
    "scheme_type": _nullable("string"),
    "percent": _nullable("number"),
    "amount": _nullable("number"),
    "amount_per": _nullable("string"),
    "buy_quantity": _nullable("number"),
    "free_quantity": _nullable("number"),
    "free_product_text": _nullable("string"),
    "min_quantity": _nullable("number"),
    "min_value": _nullable("number"),
    "slabs": {"type": "array", "items": _SLAB},
    "brands": {"type": "array", "items": {"type": "string"}},
    "categories": {"type": "array", "items": {"type": "string"}},
    "product_texts": {"type": "array", "items": {"type": "string"}},
    "territories": {"type": "array", "items": {"type": "string"}},
    "start_date": _nullable("string"),
    "end_date": _nullable("string"),
    "max_benefit": _nullable("number"),
    "source_section": _nullable("string"),
    "confidence": {"type": "number"},
    "warnings": {"type": "array", "items": {"type": "string"}},
}
SCHEME_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "schemes": {
            "type": "array",
            "items": {"type": "object", "properties": _SCHEME_PROPS, "required": list(_SCHEME_PROPS), "additionalProperties": False},
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["schema_version", "schemes", "warnings"],
    "additionalProperties": False,
}

RAW_MODELS = {"order": OrderExtractionRaw, "purchase_invoice": InvoiceExtractionRaw, "scheme": SchemeExtractionRaw}
JSON_SCHEMAS = {"order": ORDER_JSON_SCHEMA, "purchase_invoice": INVOICE_JSON_SCHEMA, "scheme": SCHEME_JSON_SCHEMA}
