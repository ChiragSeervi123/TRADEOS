"""Decimal helpers. All money is Decimal, rounded to 2 places with ROUND_HALF_UP.

Rounding rule (documented in docs/ARCHITECTURE.md): every *line-level* amount is
rounded to paise immediately after it is computed; document totals are sums of
rounded line amounts. Quantities are rounded to 3 places.
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
MILLI = Decimal("0.001")
ZERO = Decimal("0")


def D(value) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # Floats must never carry money; convert via str to avoid binary artefacts.
        value = repr(value)
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid decimal value: {value!r}") from exc


def money(value) -> Decimal:
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def qty(value) -> Decimal:
    return D(value).quantize(MILLI, rounding=ROUND_HALF_UP)


def floor_int(value) -> Decimal:
    return D(value).quantize(Decimal("1"), rounding=ROUND_DOWN)


def pct_of(amount, percent) -> Decimal:
    return money(D(amount) * D(percent) / Decimal(100))


def allocate_pro_rata(total: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `total` across weights, rounding each share to paise. The rounding
    remainder goes to the largest-weight entry (first on ties), so shares always sum
    exactly to `total`. Deterministic."""
    total = money(total)
    if not weights:
        return []
    wsum = sum(weights, ZERO)
    if wsum == ZERO:
        shares = [ZERO for _ in weights]
        shares[0] = total
        return shares
    shares = [money(total * w / wsum) for w in weights]
    remainder = total - sum(shares, ZERO)
    if remainder:
        idx = max(range(len(weights)), key=lambda i: (weights[i], -i))
        shares[idx] += remainder
    return shares


def fmt_inr(value) -> str:
    """Indian digit grouping, e.g. 1234567.5 -> '₹12,34,567.50'."""
    v = money(value)
    sign = "-" if v < 0 else ""
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"{sign}₹{whole}.{frac}"
