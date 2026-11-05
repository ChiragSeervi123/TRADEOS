"""Unified exception queue. Rules raise exceptions; only users resolve them."""

from sqlalchemy import select

from app.models import ExceptionItem

EXCEPTION_TYPES = {
    "low_confidence_extraction": "Low-confidence order extraction",
    "unmatched_master_data": "Unmatched retailer / SKU",
    "insufficient_stock": "Insufficient inventory",
    "credit_limit_exceeded": "Credit limit exceeded",
    "price_mismatch": "Price mismatch",
    "scheme_ambiguity": "Scheme ambiguity",
    "invoice_extraction_failure": "Invoice extraction failure",
    "reconciliation_mismatch": "Financial reconciliation mismatch",
    "expiring_stock": "Expiring / expired stock",
    "duplicate": "Duplicate document / order",
}

SUGGESTED = {
    "low_confidence_extraction": "Open the inbox item and confirm or correct the extracted lines.",
    "unmatched_master_data": "Select the correct retailer/SKU, or create the missing master record.",
    "insufficient_stock": "Reduce quantity, choose another warehouse, receive stock, or request an authorised override.",
    "credit_limit_exceeded": "Collect payment, reduce the order, or request an authorised credit override with a reason.",
    "price_mismatch": "Compare the document price with the agreed price and raise a reconciliation case if needed.",
    "scheme_ambiguity": "Review the draft scheme against the source circular before submitting it for approval.",
    "invoice_extraction_failure": "Open the document and enter the invoice fields manually.",
    "reconciliation_mismatch": "Investigate the linked reconciliation case and attach evidence.",
    "expiring_stock": "Prioritise dispatch (FEFO), arrange a supplier return, or block the batch.",
    "duplicate": "Compare with the existing record and cancel/reject the duplicate if confirmed.",
}


def raise_exception(
    db,
    org_id,
    exception_type: str,
    title: str,
    *,
    severity: str = "medium",
    description: str | None = None,
    entity_type: str | None = None,
    entity_id=None,
    evidence: dict | None = None,
    dedupe_key: str | None = None,
    suggested_action: str | None = None,
    is_synthetic: bool = False,
) -> ExceptionItem:
    """Create an open exception, or refresh the existing open one with the same
    dedupe key. A resolved exception with the same key is reopened only if the
    condition recurs (new evidence)."""
    assert exception_type in EXCEPTION_TYPES, exception_type
    key = dedupe_key or f"{exception_type}:{entity_type}:{entity_id}"
    existing = db.scalars(
        select(ExceptionItem).where(ExceptionItem.organization_id == org_id, ExceptionItem.dedupe_key == key)
    ).first()
    if existing:
        if existing.status in ("open", "in_progress"):
            existing.title = title
            existing.description = description
            existing.evidence = evidence
            existing.severity = severity
            return existing
        # Previously resolved/dismissed: re-open with fresh evidence.
        existing.status = "open"
        existing.title = title
        existing.description = description
        existing.evidence = evidence
        existing.severity = severity
        existing.resolution_reason = None
        existing.resolved_at = None
        existing.resolved_by_id = None
        return existing
    item = ExceptionItem(
        organization_id=org_id,
        exception_type=exception_type,
        severity=severity,
        title=title,
        description=description,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        evidence=evidence,
        suggested_action=suggested_action or SUGGESTED.get(exception_type),
        dedupe_key=key,
        is_synthetic=is_synthetic,
    )
    db.add(item)
    db.flush()
    return item
