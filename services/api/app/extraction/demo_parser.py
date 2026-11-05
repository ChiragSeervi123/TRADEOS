"""Deterministic demo/fallback parsers. No network, no randomness.

They handle common, reasonably structured inputs (one item per line, "qty unit
product" or "product - qty unit"; key/value invoice headers and tabular lines;
"x% off" / "buy x get y" circulars). Anything they cannot parse becomes a
warning for human review — they never invent values.
"""

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from app.extraction.schemas import (
    InvoiceExtractionRaw,
    InvoiceLineRaw,
    OrderExtractionRaw,
    OrderLineRaw,
    RetailerHint,
    SchemeCandidateRaw,
    SchemeExtractionRaw,
    SlabRaw,
)

UNIT_WORDS = {
    "case": "CASE", "cases": "CASE", "cs": "CASE", "ctn": "CASE", "ctns": "CASE", "carton": "CASE", "cartons": "CASE", "peti": "CASE",
    "box": "BOX", "boxes": "BOX", "bx": "BOX",
    "pcs": "PCS", "pc": "PCS", "piece": "PCS", "pieces": "PCS", "nos": "PCS", "no": "PCS", "units": "PCS", "unit": "PCS",
    "pkt": "PCS", "pkts": "PCS", "packet": "PCS", "packets": "PCS", "pack": "PCS", "packs": "PCS", "btl": "PCS", "bottles": "PCS", "bottle": "PCS",
    "dozen": "DOZEN", "dz": "DOZEN", "doz": "DOZEN",
}
SIZE_UNITS = {"kg", "kgs", "g", "gm", "gms", "gram", "grams", "ml", "l", "ltr", "ltrs", "litre", "liter"}
RETAILER_HINT = re.compile(r"\b(store|stores|kirana|traders|trading|mart|general|provision|provisions|agency|agencies|enterprises|supermarket|bazaar|bazar|shop)\b", re.I)
GREETING = re.compile(r"^(?:(?:hi|hello|namaste|dear|sir|madam|bhaiya|bhai|ji|good (?:morning|evening|afternoon)|please|pls|plz|thanks|thank you|regards|order)\b[\s,!.:-]*)+", re.I)
FROM_RE = re.compile(r"^(?:from|order from|retailer|shop|customer|party)\s*[:\-]\s*(.+)$", re.I)
HERE_RE = re.compile(r"^(?:this is\s+)?(.+?)\s+here\b", re.I)
PHONE_RE = re.compile(r"(?:\+?91[\s-]?)?([6-9]\d{4}[\s-]?\d{5})")
NUM = r"(\d+(?:\.\d+)?)"
QTY_FIRST = re.compile(rf"^{NUM}\s*([a-zA-Z]+\.?)?\s*(?:x|of|-)?\s+(.+)$", re.I)
QTY_LAST = re.compile(rf"^(.+?)\s*(?:[-:=x×*@]|qty)\s*{NUM}\s*([a-zA-Z]+)?\.?$", re.I)
QTY_TRAIL = re.compile(rf"^(.+?)\s+{NUM}\s*([a-zA-Z]+)\.?$", re.I)
DATE_PATTERNS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y", "%d %b %Y", "%d %B %Y", "%b %d, %Y")


def _dec(s) -> Decimal | None:
    if s is None:
        return None
    try:
        return Decimal(str(s).replace(",", "").replace("₹", "").replace("Rs.", "").replace("Rs", "").strip())
    except (InvalidOperation, ValueError):
        return None


def parse_date(text: str, today: date | None = None) -> date | None:
    t = text.strip().strip(".,")
    today = today or date.today()
    low = t.lower()
    if low in ("today", "aaj"):
        return today
    if low in ("tomorrow", "kal"):
        return today + timedelta(days=1)
    for fmt in DATE_PATTERNS:
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})$", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        try:
            cand = date(today.year, mo, d)
        except ValueError:
            return None
        return cand if cand >= today - timedelta(days=30) else date(today.year + 1, mo, d)
    return None


def _split_unit(word: str | None) -> str | None:
    if not word:
        return None
    return UNIT_WORDS.get(word.lower().rstrip("."))


def _parse_line(raw: str) -> OrderLineRaw | None:
    line = re.sub(r"^\s*(?:\d+[.)]|[-*•])\s+", "", raw).strip()
    if not line or len(line) < 2:
        return None
    # "5 case surf excel 1kg" / "10 x maggi"
    m = QTY_FIRST.match(line)
    if m:
        qty, word, rest = m.group(1), m.group(2), m.group(3)
        if word and word.lower().rstrip(".") in SIZE_UNITS:
            m = None  # "1kg surf" -> the number is a pack size, not a quantity
        else:
            unit = _split_unit(word)
            product = rest if unit or not word else f"{word} {rest}"
            if word and word.lower() == "x":
                product = rest
            return OrderLineRaw(source_description=raw.strip(), product_text=product.strip(" -:"), quantity=_dec(qty), unit=unit, confidence=0.9 if unit else 0.8)
    # "surf excel 1kg - 10 pcs" / "dove soap x 12"
    for rx in (QTY_LAST, QTY_TRAIL):
        m = rx.match(line)
        if m:
            product, qty, word = m.group(1), m.group(2), m.group(3)
            if word and word.lower() in SIZE_UNITS:
                continue
            unit = _split_unit(word)
            if word and not unit:
                continue
            return OrderLineRaw(source_description=raw.strip(), product_text=product.strip(" -:"), quantity=_dec(qty), unit=unit, confidence=0.88 if unit else 0.78)
    return None


def parse_order(text: str, today: date | None = None) -> OrderExtractionRaw:
    out = OrderExtractionRaw()
    retailer = RetailerHint()
    unparsed = []
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        phone = PHONE_RE.search(s)
        if phone and not retailer.phone:
            retailer.phone = re.sub(r"\D", "", phone.group(1))
        m = re.search(r"(?:deliver(?:y)?|by|before|on)\s*(?:date)?\s*[:\-]?\s*(today|tomorrow|kal|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{4}-\d{2}-\d{2})", s, re.I)
        if m and not out.requested_delivery_date:
            d = parse_date(m.group(1), today)
            if d:
                out.requested_delivery_date = d
                if not re.search(r"\d+\s*(case|pcs|box|dozen)", s, re.I):
                    continue
        fm = FROM_RE.match(s) or HERE_RE.match(GREETING.sub("", s))
        if fm and not retailer.name and not re.search(r"\d", fm.group(1)[:3]):
            retailer.name = fm.group(1).strip(" .,!-")
            retailer.confidence = 0.85
            continue
        body = GREETING.sub("", s).strip()
        if not body:
            continue
        line = _parse_line(body)
        if line:
            out.lines.append(line)
            continue
        if RETAILER_HINT.search(body) and not retailer.name and not re.search(r"\d{2,}", body):
            retailer.name = body.strip(" .,!-")
            retailer.confidence = 0.7
            continue
        if re.search(r"\d", body):
            unparsed.append(body)
        else:
            out.notes.append(body)
    out.retailer = retailer
    if not retailer.name and not retailer.phone:
        out.warnings.append("Retailer could not be identified from the text.")
    for u in unparsed:
        out.warnings.append(f"Could not parse line: '{u[:120]}'")
    if not out.lines:
        out.warnings.append("No order lines were recognised.")
    return out


# ------------------------------------------------------------------------- invoices

_INV_NO = re.compile(r"(?:invoice|inv|bill)\s*(?:no|number|#)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-]{1,40})", re.I)
_INV_DATE = re.compile(r"(?:invoice|inv|bill)?\s*date\s*[:\-]\s*([0-9A-Za-z ,./-]{6,20})", re.I)
_DUE = re.compile(r"due\s*(?:date|on)?\s*[:\-]\s*([0-9A-Za-z ,./-]{6,20})", re.I)
_SUPPLIER = re.compile(r"^(?:supplier|from|seller|vendor|sold by)\s*[:\-]\s*(.+)$", re.I)
_GSTIN = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d])\b")
_AMT = r"(?:rs\.?|inr|₹)?\s*([0-9][0-9,]*(?:\.\d{1,2})?)"
_SUBTOTAL = re.compile(rf"^(?:sub\s*-?\s*total|taxable\s*(?:value|amount)?)\s*[:\-]?\s*{_AMT}\s*$", re.I)
_TAX = re.compile(rf"^(?:total\s*)?(?:tax|gst|igst|cgst\s*\+\s*sgst)(?:\s*total)?(?:\s*\(?\d+(?:\.\d+)?%\)?)?\s*[:\-]?\s*{_AMT}\s*$", re.I)
_OTHER = re.compile(rf"^(?:freight|other\s*charges|charges|round(?:ing)?\s*off)\s*[:\-]?\s*(-?){_AMT}\s*$", re.I)
_TOTAL = re.compile(rf"^(?:grand\s*total|invoice\s*total|total\s*(?:amount|payable|due)?|amount\s*payable|net\s*payable)\s*[:\-]?\s*{_AMT}\s*$", re.I)
_LINE = re.compile(r"^(?:(?P<sku>[A-Z]{2,}[A-Z0-9]*-[A-Z0-9-]+)\s+)?(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?:x\s+)?(?P<price>\d[\d,]*(?:\.\d{1,2})?)\s+(?:(?P<rate>\d{1,2}(?:\.\d+)?)%\s+)?(?P<total>\d[\d,]*(?:\.\d{1,2})?)$", re.I)


def parse_invoice(text: str) -> InvoiceExtractionRaw:
    out = InvoiceExtractionRaw()
    lines = [l.strip() for l in (text or "").splitlines() if l.strip() and not l.strip().startswith("[page")]
    found = 0
    for i, s in enumerate(lines):
        if out.invoice_number is None and (m := _INV_NO.search(s)):
            out.invoice_number = m.group(1).strip()
            found += 1
        if out.due_date is None and (m := _DUE.search(s)):
            out.due_date = parse_date(m.group(1))
        elif out.invoice_date is None and (m := _INV_DATE.search(s)) and "due" not in s.lower():
            out.invoice_date = parse_date(m.group(1))
            found += 1 if out.invoice_date else 0
        if out.supplier_name is None and (m := _SUPPLIER.match(s)):
            out.supplier_name = m.group(1).strip()
            found += 1
        if out.supplier_gstin is None and (m := _GSTIN.search(s)):
            out.supplier_gstin = m.group(1)
        if (m := _SUBTOTAL.match(s)):
            out.subtotal = _dec(m.group(1))
            continue
        if (m := _TOTAL.match(s)):
            out.total = _dec(m.group(1))
            continue
        if (m := _TAX.match(s)):
            out.tax_total = (out.tax_total or Decimal(0)) + (_dec(m.group(1)) or Decimal(0))
            continue
        if (m := _OTHER.match(s)):
            v = _dec(m.group(2)) or Decimal(0)
            out.other_charges = (out.other_charges or Decimal(0)) + (-v if m.group(1) else v)
            continue
        if (m := _LINE.match(s)):
            out.lines.append(
                InvoiceLineRaw(
                    description=m.group("desc").strip(),
                    sku_hint=m.group("sku"),
                    quantity=_dec(m.group("qty")),
                    unit_price=_dec(m.group("price")),
                    tax_rate=_dec(m.group("rate")),
                    line_total=_dec(m.group("total")),
                )
            )
    if out.supplier_name is None and lines:
        first = lines[0]
        if not re.search(r"invoice|bill|tax", first, re.I):
            out.supplier_name = first
            out.warnings.append("Supplier name taken from the first line of the document; please verify.")
    for name, val in (("invoice number", out.invoice_number), ("invoice date", out.invoice_date), ("total", out.total)):
        if val is None:
            out.warnings.append(f"Could not find the {name}.")
    if not out.lines:
        out.warnings.append("No line items were recognised.")
    out.confidence = round(min(0.9, 0.3 + 0.15 * (found + (1 if out.total else 0) + (1 if out.lines else 0))), 2) if found else 0.2
    return out


# -------------------------------------------------------------------------- schemes

_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*(?:off|discount|cash\s*discount|cd)", re.I)
_BXGY = re.compile(r"buy\s+(\d+)\s*(?:\w+\s+)?(?:and\s+)?get\s+(\d+)\s*(?:\w+\s+)?free", re.I)
_VALUE = re.compile(r"(?:rs\.?|₹|inr)\s*(\d[\d,]*)\s*(?:off|discount).{0,40}?(?:above|over|of|minimum)\s*(?:rs\.?|₹|inr)?\s*(\d[\d,]*)", re.I)
_SLAB = re.compile(r"(\d+)\s*(?:\+|or more|and above)?\s*(?:units|pcs|cases)?\s*[:\-=]\s*(\d+(?:\.\d+)?)\s*%", re.I)
_VALIDITY = re.compile(r"(?:valid|validity|period|from)\s*(?:from)?\s*[:\-]?\s*([0-9A-Za-z ,./-]{6,20}?)\s*(?:to|till|until|-)\s*([0-9A-Za-z ,./-]{6,20})", re.I)
_MIN_QTY = re.compile(r"(?:minimum|min\.?|on purchase of)\s*(\d+)\s*(?:units|pcs|pieces|cases)", re.I)
_CAP = re.compile(r"(?:maximum|max\.?|capped at|cap of)\s*(?:benefit|discount)?\s*(?:of)?\s*(?:rs\.?|₹)\s*(\d[\d,]*)", re.I)


def parse_scheme(text: str, known_brands: list[str] | None = None, known_categories: list[str] | None = None) -> SchemeExtractionRaw:
    out = SchemeExtractionRaw()
    body = text or ""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    name = next((re.sub(r"^(scheme|offer|promotion)\s*(name)?\s*[:\-]\s*", "", l, flags=re.I) for l in lines if re.match(r"^(scheme|offer|promotion)\b", l, re.I)), lines[0][:200] if lines else "Untitled scheme")
    cand = SchemeCandidateRaw(name=name[:200])
    low = body.lower()
    for b in known_brands or []:
        if re.search(rf"\b{re.escape(b.lower())}\b", low):
            cand.brands.append(b)
    for c in known_categories or []:
        if re.search(rf"\b{re.escape(c.lower())}\b", low):
            cand.categories.append(c)
    slabs = _SLAB.findall(body)
    if (m := _BXGY.search(body)):
        cand.scheme_type = "buy_x_get_y"
        cand.buy_quantity, cand.free_quantity = _dec(m.group(1)), _dec(m.group(2))
        cand.source_section = m.group(0)
    elif len(slabs) >= 2:
        cand.scheme_type = "quantity_slab"
        cand.slabs = [SlabRaw(min_quantity=_dec(q), percent=_dec(p)) for q, p in slabs]
        cand.source_section = "; ".join(f"{q}: {p}%" for q, p in slabs)
    elif (m := _VALUE.search(body)):
        cand.scheme_type = "value_slab"
        cand.slabs = [SlabRaw(min_value=_dec(m.group(2)), amount=_dec(m.group(1)))]
        cand.source_section = m.group(0)
    elif (m := _PCT.search(body)):
        cand.scheme_type = "percentage"
        cand.percent = _dec(m.group(1))
        cand.source_section = m.group(0)
    else:
        cand.warnings.append("Benefit type could not be determined from the circular.")
    if (m := _MIN_QTY.search(body)):
        cand.min_quantity = _dec(m.group(1))
    if (m := _CAP.search(body)):
        cand.max_benefit = _dec(m.group(1))
    if (m := _VALIDITY.search(body)):
        cand.start_date, cand.end_date = parse_date(m.group(1)), parse_date(m.group(2))
    if not cand.start_date or not cand.end_date:
        cand.warnings.append("Effective period not found; set start and end dates before submitting.")
    if not (cand.brands or cand.categories or cand.product_texts):
        cand.warnings.append("No product scope recognised; the draft applies to all products until scope is set.")
    filled = sum(x is not None for x in (cand.scheme_type, cand.start_date, cand.end_date)) + (1 if cand.brands or cand.categories else 0)
    cand.confidence = round(0.2 + 0.15 * filled, 2)
    out.schemes.append(cand)
    return out
