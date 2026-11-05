"""Deterministic reconciliation rules and case workflow.

A case is an exception requiring investigation - not proof of fraud, theft or loss.
Every case records the compared records, matching method, expected calculation,
observed value, difference, rule explanation and full event history.

Rules (all tolerances are organisation settings):
  R1 grn_match            supplier invoice <-> goods receipt (exact reference, else
                          suggested by supplier + date window + value)
  R2 quantity_vs_grn      invoiced qty vs received qty per product  -> quantity_mismatch
  R3 price_vs_agreed      invoiced unit price vs agreed cost price   -> price_mismatch
  R4 duplicate_invoice    same file hash or same supplier/date/total -> duplicate_document
  R5 claim_credit         scheme claim vs supplier credit notes      -> missing_credit_note / scheme_benefit_mismatch
  R6 return_credit        received customer return without credit    -> return_not_credited
  R7 unallocated_payment  payment left unallocated                    -> unmatched_payment
"""

import uuid
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app import audit
from app.common import next_number
from app.db import utcnow
from app.errors import AppError, conflict
from app.models import (
    CreditNote,
    Document,
    ExceptionItem,
    GoodsReceipt,
    GoodsReceiptLine,
    Payment,
    Product,
    PurchaseInvoice,
    ReconciliationCase,
    ReconciliationEvent,
    ReconciliationMatch,
    ReturnRecord,
    SchemeClaim,
    Supplier,
)
from app.money import ZERO, money, qty
from app.services import exceptions as exc_svc
from app.services import receivables

TRANSITIONS = {
    "open": {"investigating", "awaiting_evidence", "resolved", "accepted_difference"},
    "investigating": {"awaiting_evidence", "resolved", "accepted_difference"},
    "awaiting_evidence": {"investigating", "resolved", "accepted_difference"},
    "resolved": {"closed", "investigating"},
    "accepted_difference": {"closed", "investigating"},
    "closed": set(),
}
OPEN_STATUSES = ("open", "investigating", "awaiting_evidence")


def _rec(kind, obj, label) -> dict:
    return {"type": kind, "id": str(obj.id), "label": label}


def upsert_case(db, org, *, rule: str, key: str, category: str, title: str, description: str, expected, observed, method: str, explanation: dict, records: list, documents: list | None = None, confidence=None, is_synthetic=False, actor_id=None):
    dedupe = f"{rule}:{key}"
    expected = money(expected) if expected is not None else None
    observed = money(observed) if observed is not None else None
    diff = money(observed - expected) if expected is not None and observed is not None else None
    case = db.scalars(select(ReconciliationCase).where(ReconciliationCase.organization_id == org.id, ReconciliationCase.dedupe_key == dedupe)).first()
    if case:
        # Never reopen a case a person has decided; only refresh open cases.
        if case.status in OPEN_STATUSES and (case.expected_value, case.observed_value) != (expected, observed):
            db.add(ReconciliationEvent(organization_id=org.id, case_id=case.id, event_type="recalculated", note=f"Rule re-run: expected {case.expected_value}->{expected}, observed {case.observed_value}->{observed}", actor_id=actor_id))
            case.expected_value, case.observed_value, case.difference = expected, observed, diff
            case.explanation = audit.jsonable(explanation)
        return case, False
    case = ReconciliationCase(
        organization_id=org.id,
        case_number=next_number(db, ReconciliationCase, org.id, "case_number", "RC"),
        category=category,
        status="open",
        title=title[:300],
        description=description,
        currency=org.currency,
        expected_value=expected,
        observed_value=observed,
        difference=diff,
        matching_method=method,
        rule=rule,
        explanation=audit.jsonable(explanation),
        confidence=Decimal(str(confidence)) if confidence is not None else None,
        records=records,
        document_ids=documents or [],
        dedupe_key=dedupe,
        is_synthetic=is_synthetic,
    )
    db.add(case)
    db.flush()
    db.add(ReconciliationEvent(organization_id=org.id, case_id=case.id, event_type="created", to_status="open", note=f"Created by rule {rule}", actor_id=actor_id))
    exc_svc.raise_exception(
        db,
        org.id,
        "reconciliation_mismatch",
        f"{case.case_number}: {title}"[:300],
        severity="high" if diff is not None and abs(diff) >= Decimal("10000") else "medium",
        entity_type="reconciliation_case",
        entity_id=case.id,
        evidence={"difference": str(diff) if diff is not None else None, "category": category},
        dedupe_key=f"recon:{case.id}",
        is_synthetic=is_synthetic,
    )
    return case, True


def _match(db, org_id, left, right, method, score, status, details, actor_id=None):
    existing = db.scalars(
        select(ReconciliationMatch).where(
            ReconciliationMatch.organization_id == org_id,
            ReconciliationMatch.left_type == left[0],
            ReconciliationMatch.left_id == str(left[1]),
            ReconciliationMatch.right_type == right[0],
            ReconciliationMatch.right_id == str(right[1]),
        )
    ).first()
    if existing:
        return existing
    m = ReconciliationMatch(organization_id=org_id, left_type=left[0], left_id=str(left[1]), right_type=right[0], right_id=str(right[1]), method=method, score=Decimal(str(score)), status=status, details=audit.jsonable(details), created_by_id=actor_id)
    db.add(m)
    db.flush()
    return m


def _active_match_for(db, org_id, left_type, left_id, right_type):
    return db.scalars(
        select(ReconciliationMatch).where(
            ReconciliationMatch.organization_id == org_id,
            ReconciliationMatch.left_type == left_type,
            ReconciliationMatch.left_id == str(left_id),
            ReconciliationMatch.right_type == right_type,
            ReconciliationMatch.status.in_(("active", "suggested")),
        )
    ).all()


def _grn_value(db, g: GoodsReceipt) -> Decimal:
    total = ZERO
    for l in db.scalars(select(GoodsReceiptLine).where(GoodsReceiptLine.goods_receipt_id == g.id)).all():
        cost = l.unit_cost if l.unit_cost is not None else (db.get(Product, l.product_id).cost_price or ZERO)
        total += money(l.quantity * cost)
    return money(total)


def rule_grn_and_quantity(db, org, pi: PurchaseInvoice, today: date, actor_id=None) -> dict:
    window = int(org.setting("match_date_window_days"))
    tol = Decimal(str(org.setting("amount_tolerance")))
    sup = db.get(Supplier, pi.supplier_id)
    out = {"cases": 0}
    matches = [m for m in _active_match_for(db, org.id, "purchase_invoice", pi.id, "goods_receipt")]
    active = [m for m in matches if m.status == "active"]
    if not matches:
        exact = db.scalars(select(GoodsReceipt).where(GoodsReceipt.organization_id == org.id, GoodsReceipt.supplier_id == pi.supplier_id, GoodsReceipt.supplier_reference == pi.invoice_number)).all()
        if exact:
            for g in exact:
                active.append(_match(db, org.id, ("purchase_invoice", pi.id), ("goods_receipt", g.id), "exact_reference", 1.0, "active", {"reference": pi.invoice_number}, actor_id))
        elif pi.invoice_date:
            taken = {m.right_id for m in db.scalars(select(ReconciliationMatch).where(ReconciliationMatch.organization_id == org.id, ReconciliationMatch.right_type == "goods_receipt", ReconciliationMatch.status.in_(("active", "suggested")))).all()}
            cands = db.scalars(select(GoodsReceipt).where(GoodsReceipt.organization_id == org.id, GoodsReceipt.supplier_id == pi.supplier_id, GoodsReceipt.received_on >= pi.invoice_date - timedelta(days=window), GoodsReceipt.received_on <= pi.invoice_date + timedelta(days=window))).all()
            for g in cands:
                if str(g.id) in taken:
                    continue
                val = _grn_value(db, g)
                if abs(val - pi.subtotal) <= max(tol, pi.subtotal * Decimal("0.02")):
                    days = abs((g.received_on - pi.invoice_date).days)
                    _match(db, org.id, ("purchase_invoice", pi.id), ("goods_receipt", g.id), "amount_date_window", round(1 - days / (window + 1) * 0.3, 3), "suggested", {"grn_value": str(val), "invoice_subtotal": str(pi.subtotal), "days_apart": days}, actor_id)
                    out["suggested"] = out.get("suggested", 0) + 1
                    break
            else:
                if (today - pi.invoice_date).days > window:
                    _, created = upsert_case(
                        db, org, rule="grn_missing", key=str(pi.id), category="unknown_difference",
                        title=f"No goods receipt found for {sup.name} invoice {pi.invoice_number}",
                        description="The invoice is verified but no goods receipt with this reference, or of similar value within the date window, was found.",
                        expected=ZERO, observed=pi.subtotal, method="supplier_reference + amount/date window",
                        explanation={"window_days": window, "invoice_date": pi.invoice_date, "searched": "goods receipts for supplier"},
                        records=[_rec("purchase_invoice", pi, f"{sup.code} {pi.invoice_number}")], documents=[str(pi.document_id)] if pi.document_id else [], confidence=0.6, is_synthetic=pi.is_synthetic, actor_id=actor_id,
                    )
                    out["cases"] += int(created)
    if active:
        received: dict = defaultdict(lambda: ZERO)
        grns = []
        for m in active:
            g = db.get(GoodsReceipt, uuid.UUID(m.right_id))
            grns.append(g)
            for l in db.scalars(select(GoodsReceiptLine).where(GoodsReceiptLine.goods_receipt_id == g.id)).all():
                received[l.product_id] += l.quantity
        invoiced: dict = defaultdict(lambda: ZERO)
        price: dict = {}
        for l in pi.lines:
            if l.product_id:
                invoiced[l.product_id] += l.quantity
                price[l.product_id] = l.unit_price
        diffs = []
        exp_v = obs_v = ZERO
        for pid in set(invoiced) | set(received):
            inv_q, rec_q = qty(invoiced.get(pid, ZERO)), qty(received.get(pid, ZERO))
            if inv_q != rec_q:
                p = db.get(Product, pid)
                unit = price.get(pid) or p.cost_price or ZERO
                diffs.append({"sku": p.sku, "invoiced": str(inv_q), "received": str(rec_q), "unit_price": str(unit), "value_difference": str(money((inv_q - rec_q) * unit))})
                exp_v += money(rec_q * unit)
                obs_v += money(inv_q * unit)
        if diffs:
            _, created = upsert_case(
                db, org, rule="quantity_vs_grn", key=str(pi.id), category="quantity_mismatch",
                title=f"Invoiced vs received quantity differs on {sup.name} invoice {pi.invoice_number}",
                description=f"{len(diffs)} product(s) invoiced in a different quantity than received.",
                expected=exp_v, observed=obs_v, method=active[0].method,
                explanation={"calculation": "expected = received qty x invoiced unit price; observed = invoiced qty x invoiced unit price", "lines": diffs},
                records=[_rec("purchase_invoice", pi, f"{sup.code} {pi.invoice_number}")] + [_rec("goods_receipt", g, g.receipt_number) for g in grns],
                documents=[str(pi.document_id)] if pi.document_id else [], confidence=1.0 if active[0].method == "exact_reference" else 0.8, is_synthetic=pi.is_synthetic, actor_id=actor_id,
            )
            out["cases"] += int(created)
    return out


def rule_price(db, org, pi: PurchaseInvoice, actor_id=None) -> int:
    pct = Decimal(str(org.setting("price_tolerance_pct")))
    sup = db.get(Supplier, pi.supplier_id)
    rows, exp_v, obs_v = [], ZERO, ZERO
    for l in pi.lines:
        if not l.product_id:
            continue
        p = db.get(Product, l.product_id)
        if p.cost_price is None or p.cost_price == 0:
            continue
        dev = (l.unit_price - p.cost_price) / p.cost_price * 100
        if abs(dev) > pct:
            rows.append({"sku": p.sku, "quantity": str(l.quantity), "agreed_price": str(p.cost_price), "invoiced_price": str(l.unit_price), "deviation_pct": str(dev.quantize(Decimal("0.01")))})
            exp_v += money(l.quantity * p.cost_price)
            obs_v += money(l.quantity * l.unit_price)
    if not rows:
        return 0
    _, created = upsert_case(
        db, org, rule="price_vs_agreed", key=str(pi.id), category="price_mismatch",
        title=f"Price differs from agreed cost on {sup.name} invoice {pi.invoice_number}",
        description=f"{len(rows)} line(s) priced outside the {pct}% tolerance of the agreed supplier price.",
        expected=exp_v, observed=obs_v, method="product agreed cost price",
        explanation={"calculation": "expected = qty x agreed cost price; observed = qty x invoiced unit price", "tolerance_pct": str(pct), "lines": rows},
        records=[_rec("purchase_invoice", pi, f"{sup.code} {pi.invoice_number}")], documents=[str(pi.document_id)] if pi.document_id else [], confidence=1.0, is_synthetic=pi.is_synthetic, actor_id=actor_id,
    )
    return int(created)


def rule_duplicates(db, org, actor_id=None) -> int:
    n = 0
    invs = db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.organization_id == org.id, PurchaseInvoice.status != "rejected")).all()
    by_hash: dict = defaultdict(list)
    by_sig: dict = defaultdict(list)
    for pi in invs:
        if pi.document_id:
            d = db.get(Document, pi.document_id)
            by_hash[d.sha256].append(pi)
        if pi.supplier_id and pi.invoice_date and pi.total:
            by_sig[(pi.supplier_id, pi.invoice_date, pi.total)].append(pi)
    groups = [("same file", g) for g in by_hash.values() if len(g) > 1] + [("same supplier, date and total", g) for g in by_sig.values() if len(g) > 1]
    for why, g in groups:
        g = sorted(g, key=lambda x: (x.created_at, str(x.id)))
        key = "|".join(str(x.id) for x in g)
        _, created = upsert_case(
            db, org, rule="duplicate_invoice", key=key, category="duplicate_document",
            title=f"Possible duplicate supplier invoices ({why})",
            description="These invoices may be the same document recorded more than once. Paying both would double-count the purchase.",
            expected=g[0].total, observed=money(sum((x.total for x in g), ZERO)), method=why,
            explanation={"invoices": [{"invoice_number": x.invoice_number, "total": str(x.total), "status": x.status} for x in g]},
            records=[_rec("purchase_invoice", x, x.invoice_number) for x in g], documents=[str(x.document_id) for x in g if x.document_id], confidence=0.95 if why == "same file" else 0.7, is_synthetic=g[0].is_synthetic, actor_id=actor_id,
        )
        n += int(created)
    return n


def rule_claims(db, org, today: date, actor_id=None) -> int:
    days = int(org.setting("missing_credit_note_days"))
    tol = Decimal(str(org.setting("amount_tolerance")))
    n = 0
    for c in db.scalars(select(SchemeClaim).where(SchemeClaim.organization_id == org.id, SchemeClaim.status.in_(("submitted", "draft", "partially_credited", "credited")))).all():
        notes = db.scalars(select(CreditNote).where(CreditNote.scheme_claim_id == c.id, CreditNote.status != "void", CreditNote.direction == "received")).all()
        credited = money(sum((x.amount for x in notes), ZERO))
        recs = [{"type": "scheme_claim", "id": str(c.id), "label": c.claim_number}] + [_rec("credit_note", x, x.note_number) for x in notes]
        for x in notes:
            _match(db, org.id, ("scheme_claim", c.id), ("credit_note", x.id), "exact_reference", 1.0, "active", {"amount": str(x.amount)}, actor_id)
        if not notes:
            age_from = c.submitted_at.date() if c.submitted_at else c.period_end
            if (today - age_from).days > days:
                _, created = upsert_case(
                    db, org, rule="claim_credit", key=str(c.id), category="missing_credit_note",
                    title=f"No supplier credit note for scheme claim {c.claim_number}",
                    description=f"Expected reimbursement of {c.expected_amount} has not been credited after {days} days.",
                    expected=c.expected_amount, observed=ZERO, method="scheme claim reference",
                    explanation={"claim_period": f"{c.period_start} to {c.period_end}", "age_basis": "submitted date" if c.submitted_at else "period end", "threshold_days": days},
                    records=recs, confidence=1.0, is_synthetic=c.is_synthetic, actor_id=actor_id,
                )
                n += int(created)
        elif abs(credited - c.expected_amount) > tol:
            _, created = upsert_case(
                db, org, rule="claim_amount", key=str(c.id), category="scheme_benefit_mismatch",
                title=f"Supplier credit differs from scheme claim {c.claim_number}",
                description="The credit received does not equal the expected scheme reimbursement.",
                expected=c.expected_amount, observed=credited, method="scheme claim reference",
                explanation={"calculation": "expected = sum of recorded scheme benefits in the claim; observed = sum of linked supplier credit notes", "notes": [x.note_number for x in notes]},
                records=recs, confidence=1.0, is_synthetic=c.is_synthetic, actor_id=actor_id,
            )
            n += int(created)
    return n


def rule_returns(db, org, today: date, actor_id=None) -> int:
    days = int(org.setting("return_credit_days"))
    n = 0
    for r in db.scalars(select(ReturnRecord).where(ReturnRecord.organization_id == org.id, ReturnRecord.return_type == "customer", ReturnRecord.status == "received")).all():
        if r.received_at and (today - r.received_at.date()).days <= days:
            continue
        credited = money(db.scalar(select(func.sum(CreditNote.amount)).where(CreditNote.return_id == r.id, CreditNote.status.in_(("issued", "applied")))) or 0)
        if credited >= r.total_value:
            continue
        _, created = upsert_case(
            db, org, rule="return_credit", key=str(r.id), category="return_not_credited",
            title=f"Customer return {r.return_number} received but not credited",
            description=f"Goods were received back more than {days} days ago without a matching credit note to the retailer.",
            expected=r.total_value, observed=credited, method="return reference",
            explanation={"received_at": r.received_at, "threshold_days": days, "calculation": "expected = return value at invoiced net price; observed = credit notes issued for this return"},
            records=[_rec("return", r, r.return_number)], confidence=1.0, is_synthetic=r.is_synthetic, actor_id=actor_id,
        )
        n += int(created)
    return n


def rule_payments(db, org, today: date, actor_id=None) -> int:
    days = int(org.setting("unallocated_payment_days"))
    tol = Decimal(str(org.setting("amount_tolerance")))
    n = 0
    for p in db.scalars(select(Payment).where(Payment.organization_id == org.id, Payment.status == "recorded", Payment.received_on <= today - timedelta(days=days))).all():
        un = receivables.payment_unallocated(db, p)
        if un <= tol:
            continue
        sugg = receivables.suggest_allocations(db, org.id, p)
        _, created = upsert_case(
            db, org, rule="unallocated_payment", key=str(p.id), category="unmatched_payment",
            title=f"Payment {p.payment_number} has {un} unallocated",
            description=f"Part of this payment has not been allocated to invoices for more than {days} days.",
            expected=p.amount, observed=money(p.amount - un), method="payment allocations",
            explanation={"calculation": "expected = payment amount; observed = amount allocated to invoices", "suggested_allocations": sugg},
            records=[_rec("payment", p, p.payment_number)], confidence=1.0, is_synthetic=p.is_synthetic, actor_id=actor_id,
        )
        n += int(created)
    return n


def run_rules(db, org, actor_id=None, today: date | None = None) -> dict:
    today = today or date.today()
    summary = defaultdict(int)
    for pi in db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.organization_id == org.id, PurchaseInvoice.status == "verified")).all():
        r = rule_grn_and_quantity(db, org, pi, today, actor_id)
        summary["grn_quantity"] += r["cases"]
        summary["suggested_matches"] += r.get("suggested", 0)
        summary["price"] += rule_price(db, org, pi, actor_id)
    summary["duplicates"] += rule_duplicates(db, org, actor_id)
    summary["claims"] += rule_claims(db, org, today, actor_id)
    summary["returns"] += rule_returns(db, org, today, actor_id)
    summary["payments"] += rule_payments(db, org, today, actor_id)
    summary["new_cases"] = sum(v for k, v in summary.items() if k not in ("suggested_matches",))
    return dict(summary)


# --------------------------------------------------------------------- case workflow


def transition(ctx, case: ReconciliationCase, to: str, note: str | None, resolved_value=None, document_id=None) -> ReconciliationCase:
    if to not in TRANSITIONS.get(case.status, set()):
        raise conflict("INVALID_TRANSITION", f"Cannot move a case from {case.status} to {to}.")
    if to in ("resolved", "accepted_difference") and not note:
        raise AppError("RESOLUTION_NOTE_REQUIRED", "Record how the case was resolved.", 422)
    if to == "investigating" and case.status in ("resolved", "accepted_difference") and not note:
        raise AppError("NOTE_REQUIRED", "Give a reason for reopening.", 422)
    prev = case.status
    case.status = to
    if to in ("resolved", "accepted_difference"):
        case.resolution_note = note
        case.resolved_by_id = ctx.user.id
        case.resolved_at = utcnow()
        case.resolved_value = money(resolved_value) if resolved_value is not None else (case.difference if to == "resolved" else None)
        # Close the linked queue item: this is the user's own decision, recorded.
        for e in ctx.db.scalars(select(ExceptionItem).where(ExceptionItem.organization_id == ctx.org_id, ExceptionItem.dedupe_key == f"recon:{case.id}", ExceptionItem.status.in_(("open", "in_progress")))).all():
            e.status = "resolved"
            e.resolution_reason = f"Reconciliation case {case.case_number} {to.replace('_', ' ')}: {note}"
            e.resolved_by_id = ctx.user.id
            e.resolved_at = utcnow()
    if to == "investigating" and prev in ("resolved", "accepted_difference"):
        case.resolved_at = None
        case.resolved_value = None
    ctx.db.add(ReconciliationEvent(organization_id=ctx.org_id, case_id=case.id, event_type="status_change", from_status=prev, to_status=to, note=note, document_id=document_id, actor_id=ctx.user.id))
    audit.record(ctx, "reconciliation.status_change", "reconciliation_case", case.id, before={"status": prev}, after={"status": to, "resolved_value": str(case.resolved_value) if case.resolved_value is not None else None}, meta={"note": note})
    return case


def add_note(ctx, case: ReconciliationCase, note: str, document_id=None):
    if document_id:
        ctx.get(Document, document_id, "Document")
        case.document_ids = list(case.document_ids or []) + [str(document_id)]
    ctx.db.add(ReconciliationEvent(organization_id=ctx.org_id, case_id=case.id, event_type="evidence" if document_id else "note", note=note, document_id=document_id, actor_id=ctx.user.id))
    audit.record(ctx, "reconciliation.note", "reconciliation_case", case.id, meta={"note": note[:500], "document_id": str(document_id) if document_id else None})


def assign(ctx, case: ReconciliationCase, user_id):
    from app.models import Membership

    if user_id and not ctx.db.scalars(select(Membership).where(Membership.organization_id == ctx.org_id, Membership.user_id == user_id, Membership.is_active.is_(True))).first():
        raise AppError("INVALID_ASSIGNEE", "Assignee must be an active member of this organisation.", 422)
    prev = case.assigned_to_id
    case.assigned_to_id = user_id
    ctx.db.add(ReconciliationEvent(organization_id=ctx.org_id, case_id=case.id, event_type="assigned", note=f"Assigned to {user_id}" if user_id else "Unassigned", actor_id=ctx.user.id))
    audit.record(ctx, "reconciliation.assign", "reconciliation_case", case.id, before={"assigned_to": str(prev) if prev else None}, after={"assigned_to": str(user_id) if user_id else None})
