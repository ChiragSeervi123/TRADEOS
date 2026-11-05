"""Deterministic fuzzy matching for master-data resolution and duplicate detection.

Scores are in [0, 1]. They are heuristics with documented behaviour, not
probabilities; thresholds are set conservatively to keep false auto-matches low.
"""

import re
from difflib import SequenceMatcher

ABBREVIATIONS = {
    "pkt": "packet",
    "pkts": "packet",
    "pcs": "piece",
    "pc": "piece",
    "nos": "piece",
    "no": "piece",
    "btl": "bottle",
    "btls": "bottle",
    "ltr": "l",
    "litre": "l",
    "liter": "l",
    "ltrs": "l",
    "gm": "g",
    "gms": "g",
    "gram": "g",
    "grams": "g",
    "kgs": "kg",
    "kilo": "kg",
    "ml.": "ml",
    "shmp": "shampoo",
    "shampo": "shampoo",
    "tp": "toothpaste",
    "paste": "toothpaste",
    "det": "detergent",
    "bisc": "biscuit",
    "biscuits": "biscuit",
    "choc": "chocolate",
    "soaps": "soap",
    "str": "store",
    "strs": "store",
    "gen": "general",
    "genl": "general",
    "kirana": "kirana",
    "&": "and",
}
STOPWORDS = {"the", "of", "m/s", "ms", "shop", "and"}
_size_re = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|l|ml|piece|packet)\b")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    t = text.lower()
    t = re.sub(r"(\d)\s*(kgs?|gms?|grams?|ltrs?|litres?|liters?|ml|g|l)\b", r"\1 \2", t)
    t = re.sub(r"[^a-z0-9.&/ ]+", " ", t)
    words = [ABBREVIATIONS.get(w, w) for w in t.split()]
    return " ".join(w for w in words if w)


def tokens(text: str | None) -> set[str]:
    return {w for w in normalize(text).split() if w not in STOPWORDS}


def sizes(text: str | None) -> set[str]:
    """Normalised pack sizes in a description, e.g. {'1 kg', '500 g'}; 1000 g == 1 kg."""
    out = set()
    for num, unit in _size_re.findall(normalize(text)):
        n = float(num)
        if unit == "g" and n >= 1000:
            n, unit = n / 1000, "kg"
        if unit == "ml" and n >= 1000:
            n, unit = n / 1000, "l"
        out.add(f"{n:g} {unit}")
    return out


def similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = tokens(a), tokens(b)
    jacc = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    contain = len(ta & tb) / min(len(ta), len(tb)) if ta and tb else 0.0
    seq = SequenceMatcher(None, na, nb).ratio()
    # Fuzzy token overlap catches misspellings (e.g. "surf exel" vs "surf excel").
    fuzzy_hits = 0
    for x in ta:
        if x in tb or any(len(x) > 3 and SequenceMatcher(None, x, y).ratio() >= 0.8 for y in tb):
            fuzzy_hits += 1
    fuzzy = fuzzy_hits / max(len(ta), 1)
    return round(0.3 * seq + 0.25 * jacc + 0.2 * contain + 0.25 * fuzzy, 4)


def product_score(description: str, product) -> tuple[float, list[str]]:
    """Score a product against an order-line description. Returns (score, reasons).
    Pack size is a hard signal: a size present in both that disagrees caps the score."""
    reasons: list[str] = []
    desc_norm = normalize(description)
    if product.sku and product.sku.lower() in desc_norm.split():
        return 0.99, ["exact SKU code in text"]
    if product.barcode and product.barcode in description:
        return 0.99, ["barcode in text"]
    names = [product.name] + list(product.aliases or [])
    if product.variant:
        names.append(f"{product.name} {product.variant}")
    best = max(similarity(description, n) for n in names)
    reasons.append(f"name similarity {best:.2f}")
    if product.brand and normalize(product.brand) in desc_norm:
        best = min(1.0, best + 0.05)
        reasons.append("brand mentioned")
    ds = sizes(description)
    ps = sizes(f"{product.name} {product.variant or ''}")
    if ds and ps:
        if ds & ps:
            best = min(1.0, best + 0.1)
            reasons.append("pack size matches")
        else:
            best = min(best, 0.45)
            reasons.append("pack size differs")
    elif ds and not ps:
        best = min(best, 0.7)
    return round(best, 4), reasons


def rank(description: str, candidates, scorer, limit: int = 3, floor: float = 0.35):
    scored = []
    for c in candidates:
        s, why = scorer(description, c)
        if s >= floor:
            scored.append((s, why, c))
    scored.sort(key=lambda x: (-x[0], str(getattr(x[2], "sku", getattr(x[2], "code", "")))))
    return scored[:limit]


def retailer_score(text: str, retailer) -> tuple[float, list[str]]:
    if retailer.code and retailer.code.lower() in normalize(text).split():
        return 0.99, ["exact retailer code"]
    if retailer.phone and re.sub(r"\D", "", retailer.phone)[-10:] in re.sub(r"\D", "", text or ""):
        if len(re.sub(r"\D", "", retailer.phone)) >= 10:
            return 0.97, ["phone number matches"]
    s = similarity(text, retailer.name)
    return s, [f"name similarity {s:.2f}"]


# Thresholds used by the order inbox. A line auto-selects a candidate only if it is
# confident AND clearly ahead of the runner-up; otherwise it requires human review.
AUTO_SELECT_MIN = 0.85
AUTO_SELECT_MARGIN = 0.12


def is_unambiguous(ranked: list) -> bool:
    if not ranked:
        return False
    top = ranked[0][0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    return top >= AUTO_SELECT_MIN and (top - second) >= AUTO_SELECT_MARGIN
