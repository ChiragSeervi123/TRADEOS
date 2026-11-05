"""Pure unit tests: money, matching, receivable buckets."""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.money import D, allocate_pro_rata, fmt_inr, money, pct_of
from app.services import matching
from app.services.receivables import bucket_for, bucket_labels


def test_money_rounding_half_up_and_no_float_artifacts():
    assert money("2.675") == Decimal("2.68")  # float(2.675) would round down
    assert money(Decimal("1.005")) == Decimal("1.01")
    assert D(0.1) + D(0.2) == Decimal("0.3")
    assert pct_of("999.99", "12.5") == Decimal("125.00")
    with pytest.raises(ValueError):
        D("abc")


def test_pro_rata_allocation_sums_exactly():
    shares = allocate_pro_rata(Decimal("100.00"), [Decimal("1"), Decimal("1"), Decimal("1")])
    assert sum(shares) == Decimal("100.00")
    assert shares == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert allocate_pro_rata(Decimal("10"), [Decimal("0"), Decimal("0")]) == [Decimal("10.00"), Decimal("0")]


def test_inr_formatting():
    assert fmt_inr("1234567.5") == "₹12,34,567.50"
    assert fmt_inr("-999") == "-₹999.00"


def P(name, variant=None, sku="X", brand=None, aliases=None, barcode=None):
    return SimpleNamespace(name=name, variant=variant, sku=sku, brand=brand, aliases=aliases, barcode=barcode)


def test_matching_handles_abbreviations_and_misspellings():
    assert matching.similarity("surf exel 1kg", "Surf Excel 1 kg") > 0.75
    s_ok, _ = matching.product_score("surf exel 1kg", P("Surf Excel Detergent", "1 kg", brand="Surf"))
    s_bad, why = matching.product_score("surf exel 1kg", P("Surf Excel Detergent", "500 g", brand="Surf"))
    assert s_ok > 0.8 and s_bad <= 0.45 and "pack size differs" in why
    assert matching.sizes("Dove 1000gm") == {"1 kg"}


def test_ambiguity_rule():
    assert matching.is_unambiguous([(0.95, [], 1), (0.70, [], 2)])
    assert not matching.is_unambiguous([(0.95, [], 1), (0.90, [], 2)])  # too close
    assert not matching.is_unambiguous([(0.80, [], 1)])  # not confident
    assert not matching.is_unambiguous([])


def test_ageing_buckets():
    b = [30, 60, 90]
    assert bucket_labels(b) == ["current", "1-30", "31-60", "61-90", "90+"]
    assert bucket_for(0, b) == "current"
    assert bucket_for(1, b) == "1-30"
    assert bucket_for(30, b) == "1-30"
    assert bucket_for(31, b) == "31-60"
    assert bucket_for(91, b) == "90+"
