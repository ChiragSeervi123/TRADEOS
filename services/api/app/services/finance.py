"""Purchase invoices, credit/debit notes and payments.

TRADEOS is an operational reconciliation tool, not a statutory ledger. A purchase
invoice only becomes authoritative after a human verifies it; extracted values are
kept alongside for provenance.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app import audit
from app.common import next_number
from app.db import utcnow
from app.errors import AppError, conflict
from app.models import (
    CreditNote,
    Document,
    Payment,
    Product,
    PurchaseInvoice,
    PurchaseInvoiceLine,
    ReturnRecord,
    SalesInvoice,
    SchemeClaim,
    Supplier,
)
from app.money import ZERO, money, qty
from app.services import exceptions as exc_svc
from app.services import matching, receivables

# ----------------------------------------------------------------- purchase invoices


def validate_invoice(org, pi: PurchaseInvoice) -> dict:
    """Arithmetic and completeness checks. Never changes values."""
    tol = Decimal(str(org.setting("amount_tolerance")))
    issues: list[dict] = []
    if not pi.supplier_id:
        issues.append({"code": "SUPPLIER_MISSING", "severity": "error", "message": "Supplier is not identified."})
    if not pi.invoice_number or pi.invoice_number.startswith("PENDING-"):
        issues.append({"code": "NUMBER_MISSING", "severity": "error", "message": "Invoice number is missing."})
    if not pi.invoice_date:
        issues.append({"code": "DATE_MISSING", "severity": "error", "message": "Invoice date is missing."})
    if not pi.lines:
        issues.append({"code": "NO_LINES", "severity": "warning", "message": "No line items; only header totals can be reconciled."})
    for l in pi.lines:
        expect = money(l.quantity * l.unit_price)
        if abs(expect - l.line_total) > tol:
            issues.append({"code": "LINE_ARITHMETIC", "severity": "error", "line_no": l.line_no, "message": f"Line {l.line_no}: {l.quantity} x {l.unit_price} = {expect}, document shows {l.line_total}.", "details": {"expected": str(expect), "observed": str(l.line_total)}})
        if not l.product_id:
            issues.append({"code": "LINE_UNMATCHED", "severity": "warning", "line_no": l.line_no, "message": f"Line {l.line_no} ('{l.description[:60]}') is not linked to a product."})
    if pi.lines:
        lines_sum = money(sum((l.line_total for l in pi.lines), ZERO))
        if abs(lines_sum - pi.subtotal) > tol:
            issues.append({"code": "SUBTOTAL_MISMATCH", "severity": "error", "message": f"Line totals sum to {lines_sum}; subtotal shows {pi.subtotal}.", "details": {"expected": str(lines_sum), "observed": str(pi.subtotal)}})
    computed_total = money(pi.subtotal + pi.tax_total + pi.other_charges)
    if abs(computed_total - pi.total) > tol:
        issues.append({"code": "TOTAL_MISMATCH", "severity": "error", "message": f"Subtotal + tax + charges = {computed_total}; total shows {pi.total}.", "details": {"expected": str(computed_total), "observed": str(pi.total)}})
    errors = [i for i in issues if i["severity"] == "error"]
    return {"valid": not errors, "errors": len(errors), "issues": issues, "checked_at": utcnow().isoformat()}


def _match_supplier(db, org_id, name: str | None, gstin: str | None) -> tuple[Supplier | None, float]:
    sups = db.scalars(select(Supplier).where(Supplier.organization_id == org_id, Supplier.is_active.is_(True))).all()
    if gstin:
        for s in sups:
            if s.gstin and s.gstin.upper() == gstin.upper():
                return s, 0.99
    if not name:
        return None, 0.0
    ranked = sorted(((matching.similarity(name, s.name), s) for s in sups), key=lambda x: -x[0])
    if ranked and ranked[0][0] >= 0.8 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.1):
        return ranked[0][1], ranked[0][0]
    return None, ranked[0][0] if ranked else 0.0


def set_invoice_lines(db, org_id, pi: PurchaseInvoice, lines: list[dict]):
    for l in list(pi.lines):
        pi.lines.remove(l)
    db.flush()
    for i, li in enumerate(lines, start=1):
        pi.lines.append(
            PurchaseInvoiceLine(
                organization_id=org_id,
                line_no=i,
                product_id=uuid.UUID(str(li["product_id"])) if li.get("product_id") else None,
                description=(li.get("description") or "")[:300] or "(no description)",
                quantity=qty(li.get("quantity") or 0),
                unit_price=money(li.get("unit_price") or 0),
                tax_rate=money(li.get("tax_rate") or 0),
                tax_amount=money(li.get("tax_amount") or 0),
                line_total=money(li["line_total"]) if li.get("line_total") is not None else money(qty(li.get("quantity") or 0) * money(li.get("unit_price") or 0)),
                match_confidence=Decimal(str(li["match_confidence"])) if li.get("match_confidence") is not None else None,
            )
        )
    db.flush()


def apply_invoice_extraction(db, org, doc: Document, ext, parsed, target_id) -> dict:
    pi = db.get(PurchaseInvoice, target_id)
    if pi is None or pi.organization_id != org.id:
        return {"status": "missing_target"}
    pi.extraction_id = ext.id
    if ext.status != "succeeded" or parsed is None:
        pi.status = "needs_review"
        pi.validation = {"valid": False, "errors": 1, "issues": [{"code": "EXTRACTION_FAILED", "severity": "error", "message": ext.error or "Extraction failed; enter the invoice manually."}]}
        exc_svc.raise_exception(db, org.id, "invoice_extraction_failure", f"Invoice {doc.filename} could not be extracted", severity="medium", description=ext.error, entity_type="purchase_invoice", entity_id=pi.id, evidence={"document_id": str(doc.id), "extraction_id": str(ext.id)})
        return {"status": "failed"}
    sup, sup_conf = _match_supplier(db, org.id, parsed.supplier_name, parsed.supplier_gstin)
    pi.supplier_id = sup.id if sup else None
    notes = list(parsed.warnings)
    if parsed.invoice_number:
        clash = db.scalars(
            select(PurchaseInvoice).where(
                PurchaseInvoice.organization_id == org.id,
                PurchaseInvoice.id != pi.id,
                PurchaseInvoice.supplier_id == pi.supplier_id,
                PurchaseInvoice.invoice_number == parsed.invoice_number,
                PurchaseInvoice.status != "rejected",
            )
        ).first()
        if clash:
            notes.append(f"Invoice number {parsed.invoice_number} already exists for this supplier ({clash.id}).")
            exc_svc.raise_exception(db, org.id, "duplicate", f"Possible duplicate supplier invoice {parsed.invoice_number}", severity="high", entity_type="purchase_invoice", entity_id=pi.id, evidence={"existing_invoice_id": str(clash.id), "document_id": str(doc.id)})
        else:
            pi.invoice_number = parsed.invoice_number[:80]
    pi.invoice_date = parsed.invoice_date
    pi.due_date = parsed.due_date
    pi.currency = (parsed.currency or org.currency or "INR")[:3].upper()
    products = db.scalars(select(Product).where(Product.organization_id == org.id, Product.is_active.is_(True))).all()
    by_sku = {p.sku.upper(): p for p in products}
    lines = []
    for l in parsed.lines:
        prod, conf = None, None
        if l.sku_hint and l.sku_hint.upper() in by_sku:
            prod, conf = by_sku[l.sku_hint.upper()], 0.99
        else:
            ranked = matching.rank(l.description, products, matching.product_score)
            if matching.is_unambiguous(ranked):
                prod, conf = ranked[0][2], ranked[0][0]
            elif ranked:
                conf = ranked[0][0]
        lines.append({"product_id": prod.id if prod else None, "description": l.description, "quantity": l.quantity or 0, "unit_price": l.unit_price or 0, "tax_rate": l.tax_rate or 0, "tax_amount": l.tax_amount or 0, "line_total": l.line_total, "match_confidence": conf})
    set_invoice_lines(db, org.id, pi, lines)
    pi.subtotal = money(parsed.subtotal) if parsed.subtotal is not None else money(sum((money(l["line_total"] or 0) for l in lines), ZERO))
    pi.tax_total = money(parsed.tax_total or 0)
    pi.other_charges = money(parsed.other_charges or 0)
    pi.total = money(parsed.total) if parsed.total is not None else money(pi.subtotal + pi.tax_total + pi.other_charges)
    if parsed.total is None:
        notes.append("Total not found on the document; computed from subtotal + tax + charges.")
    pi.status = "needs_review"
    v = validate_invoice(org, pi)
    v["extraction_notes"] = notes
    v["supplier_match_confidence"] = round(sup_conf, 3)
    pi.validation = v
    if not v["valid"]:
        exc_svc.raise_exception(db, org.id, "invoice_extraction_failure", f"Invoice {pi.invoice_number}: {v['errors']} validation error(s) to review", severity="low", entity_type="purchase_invoice", entity_id=pi.id, evidence={"issues": v["issues"][:20]})
    ext.result = {"purchase_invoice_id": str(pi.id), "validation": v}
    return {"status": "needs_review", "valid": v["valid"]}


def verify_invoice(ctx, pi: PurchaseInvoice, accept_warnings_reason: str | None = None) -> PurchaseInvoice:
    if pi.status == "verified":
        return pi
    if pi.status not in ("needs_review", "draft"):
        raise conflict("INVALID_STATUS", f"Invoice is {pi.status}.")
    v = validate_invoice(ctx.org, pi)
    pi.validation = {**(pi.validation or {}), **v}
    blocking = [i for i in v["issues"] if i["severity"] == "error" and i["code"] in ("SUPPLIER_MISSING", "NUMBER_MISSING", "DATE_MISSING")]
    if blocking:
        raise AppError("INVOICE_INCOMPLETE", "Supplier, invoice number and date are required before verification.", 422, {"issues": blocking})
    arithmetic = [i for i in v["issues"] if i["severity"] == "error"]
    if arithmetic and not accept_warnings_reason:
        raise AppError("INVOICE_ARITHMETIC", "The invoice has arithmetic differences. Correct them or verify with a reason (the difference stays visible for reconciliation).", 422, {"issues": arithmetic})
    pi.status = "verified"
    pi.verified_by_id = ctx.user.id
    pi.verified_at = utcnow()
    audit.record(ctx, "purchase_invoice.verify", "purchase_invoice", pi.id, after={"invoice_number": pi.invoice_number, "total": str(pi.total), "lines": len(pi.lines)}, meta={"accepted_with_reason": accept_warnings_reason, "issues": [i["code"] for i in v["issues"]]})
    return pi


# ----------------------------------------------------------------------- credit notes


def create_note(ctx, data: dict) -> CreditNote:
    direction, note_type = data["direction"], data.get("note_type", "credit")
    if direction == "issued":
        if not data.get("retailer_id"):
            raise AppError("RETAILER_REQUIRED", "Issued notes need a retailer.", 422)
        from app.models import Retailer

        ctx.get(Retailer, data["retailer_id"], "Retailer")
    else:
        if not data.get("supplier_id"):
            raise AppError("SUPPLIER_REQUIRED", "Received notes need a supplier.", 422)
        ctx.get(Supplier, data["supplier_id"], "Supplier")
        if not data.get("note_number"):
            raise AppError("NUMBER_REQUIRED", "Enter the supplier's note number.", 422)
    ret = ctx.maybe(ReturnRecord, data.get("return_id"))
    if ret:
        if direction == "issued" and (ret.return_type != "customer" or str(ret.retailer_id) != str(data["retailer_id"])):
            raise AppError("RETURN_MISMATCH", "The return belongs to another party.", 422)
        if direction == "received" and (ret.return_type != "supplier" or str(ret.supplier_id) != str(data["supplier_id"])):
            raise AppError("RETURN_MISMATCH", "The return belongs to another party.", 422)
    inv = ctx.maybe(SalesInvoice, data.get("sales_invoice_id"))
    if inv and str(inv.retailer_id) != str(data.get("retailer_id")):
        raise AppError("RETAILER_MISMATCH", "The invoice belongs to another retailer.", 422)
    claim = ctx.maybe(SchemeClaim, data.get("scheme_claim_id"))
    if claim and direction != "received":
        raise AppError("INVALID_LINK", "Scheme claims are credited by notes received from the supplier.", 422)
    pinv = ctx.maybe(PurchaseInvoice, data.get("purchase_invoice_id"))
    doc = ctx.maybe(Document, data.get("document_id"))
    amount = money(data["amount"]) if data.get("amount") is not None else (ret.total_value if ret else None)
    if amount is None or amount <= 0:
        raise AppError("INVALID_AMOUNT", "Amount must be positive.", 422)
    number = data.get("note_number") or next_number(ctx.db, CreditNote, ctx.org_id, "note_number", "CN" if note_type == "credit" else "DN")
    n = CreditNote(
        organization_id=ctx.org_id,
        note_type=note_type,
        direction=direction,
        note_number=number[:80],
        retailer_id=data.get("retailer_id"),
        supplier_id=data.get("supplier_id"),
        note_date=data["note_date"],
        amount=amount,
        currency=ctx.org.currency,
        reason=data.get("reason") or "other",
        description=data.get("description"),
        return_id=ret.id if ret else None,
        sales_invoice_id=inv.id if inv else None,
        purchase_invoice_id=pinv.id if pinv else None,
        scheme_claim_id=claim.id if claim else None,
        document_id=doc.id if doc else None,
        status="draft" if direction == "issued" else "recorded",
        created_by_id=ctx.user.id,
    )
    ctx.db.add(n)
    ctx.db.flush()
    if direction == "received" and claim:
        refresh_claim_status(ctx.db, claim)
    audit.record(ctx, "credit_note.create", "credit_note", n.id, after=audit.snapshot(n, ["note_type", "direction", "note_number", "amount", "reason", "status"]))
    return n


def refresh_claim_status(db, claim: SchemeClaim) -> None:
    credited = money(db.scalar(select(func.sum(CreditNote.amount)).where(CreditNote.scheme_claim_id == claim.id, CreditNote.status != "void", CreditNote.direction == "received")) or 0)
    if credited <= 0:
        return
    claim.status = "credited" if credited >= claim.expected_amount else "partially_credited"


def issue_note(ctx, n: CreditNote) -> CreditNote:
    if n.direction != "issued":
        raise conflict("INVALID_DIRECTION", "Only notes issued to retailers are issued here.")
    if n.status != "draft":
        raise conflict("INVALID_STATUS", f"Note is {n.status}.")
    n.status = "issued"
    if n.return_id:
        r = ctx.db.get(ReturnRecord, n.return_id)
        if r.status == "received":
            r.status = "credited"
    audit.record(ctx, "credit_note.issue", "credit_note", n.id, after={"status": "issued", "amount": str(n.amount)})
    return n


def apply_note(ctx, n: CreditNote, inv: SalesInvoice) -> CreditNote:
    if n.direction != "issued" or n.note_type != "credit":
        raise conflict("INVALID_NOTE", "Only credit notes issued to a retailer can be applied to its invoices.")
    if n.status != "issued":
        raise conflict("INVALID_STATUS", "Issue the credit note before applying it.")
    if inv.retailer_id != n.retailer_id:
        raise AppError("RETAILER_MISMATCH", "The invoice belongs to another retailer.", 422)
    out = receivables.invoice_outstanding(ctx.db, inv)
    if n.amount > out:
        raise conflict("OVER_APPLICATION", "Credit note exceeds the invoice's outstanding amount.", {"amount": str(n.amount), "outstanding": str(out)})
    n.sales_invoice_id = inv.id
    n.status = "applied"
    ctx.db.flush()
    receivables.refresh_invoice_status(ctx.db, inv)
    audit.record(ctx, "credit_note.apply", "credit_note", n.id, after={"sales_invoice_id": str(inv.id), "amount": str(n.amount)})
    return n


def void_note(ctx, n: CreditNote, reason: str) -> CreditNote:
    if n.status == "applied":
        raise conflict("INVALID_STATUS", "Applied notes cannot be voided.")
    if n.status == "void":
        return n
    prev = n.status
    n.status = "void"
    if n.scheme_claim_id:
        claim = ctx.db.get(SchemeClaim, n.scheme_claim_id)
        claim.status = "submitted" if claim.submitted_at else "draft"
        refresh_claim_status(ctx.db, claim)
    audit.record(ctx, "credit_note.void", "credit_note", n.id, before={"status": prev}, after={"status": "void"}, meta={"reason": reason})
    return n


# --------------------------------------------------------------------------- payments


def record_payment(ctx, data: dict, idempotency_key: str | None) -> tuple[Payment, bool]:
    from app.models import Retailer

    if idempotency_key:
        existing = ctx.db.scalars(select(Payment).where(Payment.organization_id == ctx.org_id, Payment.idempotency_key == idempotency_key)).first()
        if existing:
            if existing.amount != money(data["amount"]) or str(existing.retailer_id) != str(data["retailer_id"]):
                raise conflict("IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used for a different payment.")
            return existing, False
    ctx.get(Retailer, data["retailer_id"], "Retailer")
    amt = money(data["amount"])
    if amt <= 0:
        raise AppError("INVALID_AMOUNT", "Payment amount must be positive.", 422)
    p = Payment(
        organization_id=ctx.org_id,
        payment_number=next_number(ctx.db, Payment, ctx.org_id, "payment_number", "PAY"),
        retailer_id=data["retailer_id"],
        amount=amt,
        currency=ctx.org.currency,
        received_on=data["received_on"],
        method=data.get("method") or "bank_transfer",
        reference=data.get("reference"),
        notes=data.get("notes"),
        idempotency_key=idempotency_key,
        created_by_id=ctx.user.id,
    )
    ctx.db.add(p)
    ctx.db.flush()
    audit.record(ctx, "payment.record", "payment", p.id, after={"payment_number": p.payment_number, "amount": str(amt), "method": p.method, "reference": p.reference})
    return p, True


def allocate_many(ctx, payment: Payment, allocations: list[dict]) -> list:
    """All-or-nothing allocation. Rows are locked in a deterministic order."""
    invoice_ids = sorted({str(a["sales_invoice_id"]) for a in allocations})
    if len(invoice_ids) != len(allocations):
        raise AppError("DUPLICATE_INVOICE", "Each invoice may appear only once per allocation request.", 422)
    invoices = {str(i): ctx.get(SalesInvoice, i, "Sales invoice", lock=True) for i in invoice_ids}
    created = []
    for a in allocations:
        created.append(receivables.allocate(ctx.db, ctx.org_id, payment, invoices[str(a["sales_invoice_id"])], a["amount"], ctx.user.id))
    audit.record(ctx, "payment.allocate", "payment", payment.id, after={"allocations": [{"sales_invoice_id": str(c.sales_invoice_id), "amount": str(c.amount)} for c in created], "unallocated": str(receivables.payment_unallocated(ctx.db, payment))})
    return created


def reverse_allocation(ctx, payment: Payment, alloc, reason: str):
    if alloc.payment_id != payment.id:
        raise AppError("NOT_FOUND", "Allocation not found.", 404)
    if alloc.reversed_at:
        return alloc
    alloc.reversed_at = utcnow()
    alloc.reversed_by_id = ctx.user.id
    alloc.reversal_reason = reason
    ctx.db.flush()
    inv = ctx.get(SalesInvoice, alloc.sales_invoice_id, "Sales invoice", lock=True)
    receivables.refresh_invoice_status(ctx.db, inv)
    audit.record(ctx, "payment.allocation_reverse", "payment_allocation", alloc.id, meta={"reason": reason, "amount": str(alloc.amount)})
    return alloc
