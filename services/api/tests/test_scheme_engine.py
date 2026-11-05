from datetime import date
from decimal import Decimal as D

import pytest

from app.services.scheme_engine import Line, OrderInput, Rule, evaluate, validate_config

TODAY = date(2026, 11, 5)


def line(key, pid, qty, price, brand="B", category="C"):
    return Line(key=key, product_id=pid, sku=f"SKU-{pid}", brand=brand, category=category, base_qty=D(qty), unit_price=D(price), gross=(D(qty) * D(price)).quantize(D("0.01")))


def rule(code, stype, config, scope=None, elig=None, stacking="exclusive", priority=100, cap=None, start=date(2026, 1, 1), end=date(2026, 12, 31)):
    return Rule(scheme_id=code, version_id=code + "v", version=1, code=code, name=code, scheme_type=stype, config=config, scope=scope or {}, eligibility=elig or {}, start_date=start, end_date=end, stacking=stacking, priority=priority, max_benefit=D(cap) if cap else None)


def order(*lines, retailer="r1", territory="North", when=TODAY):
    return OrderInput(order_date=when, retailer_id=retailer, territory=territory, lines=list(lines))


def test_percentage_with_min_quantity():
    o = order(line(1, "p1", 10, "100.00"), line(2, "p2", 3, "33.33"))
    r = evaluate(o, [rule("PCT", "percentage", {"percent": "5", "min_quantity": "12"})])
    assert len(r.applied) == 1
    assert r.line_discount(1) == D("50.00") and r.line_discount(2) == D("5.00")  # 99.99*5% = 4.9995 -> 5.00
    r = evaluate(o, [rule("PCT", "percentage", {"percent": "5", "min_quantity": "14"})])
    assert not r.applied and "below minimum" in r.skipped[0]["reason"]


def test_fixed_amount_per_order_allocated_pro_rata_and_per_unit():
    o = order(line(1, "p1", 1, "100"), line(2, "p2", 1, "200"))
    r = evaluate(o, [rule("FIX", "fixed_amount", {"amount": "100", "per": "order"})])
    assert r.line_discount(1) == D("33.33") and r.line_discount(2) == D("66.67")
    assert r.applied[0].amount == D("100.00")
    r = evaluate(o, [rule("FIXU", "fixed_amount", {"amount": "2.5", "per": "unit"})])
    assert r.applied[0].amount == D("5.00")


def test_buy_x_get_y_same_product_and_other_product():
    o = order(line(1, "p1", 25, "10"))
    r = evaluate(o, [rule("BXGY", "buy_x_get_y", {"buy_quantity": "10", "free_quantity": "1"})])
    fg = r.applied[0].free_goods
    assert fg[0]["quantity"] == D("2.000") and fg[0]["product_id"] == "p1" and fg[0]["value"] == D("20.00")
    assert r.line_discount(1) == 0  # free goods, not a price discount
    r = evaluate(o, [rule("BXGY2", "buy_x_get_y", {"buy_quantity": "12", "free_quantity": "2", "free_product_id": "p9"})], {"p9": D("4.50")})
    assert r.applied[0].free_goods[0]["product_id"] == "p9" and r.applied[0].free_goods[0]["quantity"] == D("4.000")
    r = evaluate(order(line(1, "p1", 9, "10")), [rule("BXGY", "buy_x_get_y", {"buy_quantity": "10", "free_quantity": "1"})])
    assert not r.applied


def test_quantity_slab_highest_met():
    cfg = {"slabs": [{"min_quantity": "10", "percent": "2"}, {"min_quantity": "50", "percent": "5"}, {"min_quantity": "100", "percent": "8"}]}
    r = evaluate(order(line(1, "p1", 30, "10"), line(2, "p2", 30, "10")), [rule("QS", "quantity_slab", cfg)])
    assert r.applied[0].amount == D("30.00")  # 60 units -> 5% of 600
    assert r.applied[0].explanation["rate"] == "5%"
    r = evaluate(order(line(1, "p1", 5, "10")), [rule("QS", "quantity_slab", cfg)])
    assert not r.applied and "below lowest slab" in r.skipped[0]["reason"]


def test_value_slab_flat_amount():
    cfg = {"slabs": [{"min_value": "1000", "amount": "50"}, {"min_value": "5000", "amount": "400"}]}
    r = evaluate(order(line(1, "p1", 10, "150")), [rule("VS", "value_slab", cfg)])
    assert r.applied[0].amount == D("50.00")


def test_combination_requires_all_products():
    cfg = {"required": [{"product_id": "p1", "min_quantity": "2"}, {"product_id": "p2", "min_quantity": "1"}], "percent": "10"}
    r = evaluate(order(line(1, "p1", 2, "50"), line(2, "p2", 1, "100"), line(3, "p3", 1, "100")), [rule("CMB", "combination", cfg)])
    assert r.applied[0].amount == D("20.00")
    assert r.line_discount(3) == 0
    r = evaluate(order(line(1, "p1", 1, "50"), line(2, "p2", 1, "100")), [rule("CMB", "combination", cfg)])
    assert not r.applied


def test_scope_brand_category_and_exclusions():
    o = order(line(1, "p1", 1, "100", brand="Surf"), line(2, "p2", 1, "100", brand="Rin"), line(3, "p3", 1, "100", brand="Surf"))
    r = evaluate(o, [rule("BR", "percentage", {"percent": "10"}, scope={"brands": ["Surf"], "exclude_product_ids": ["p3"]})])
    assert r.line_discount(1) == D("10.00") and r.line_discount(2) == 0 and r.line_discount(3) == 0


def test_date_bounds_and_retailer_eligibility():
    o = order(line(1, "p1", 1, "100"))
    expired = rule("OLD", "percentage", {"percent": "10"}, start=date(2026, 1, 1), end=date(2026, 11, 4))
    future = rule("NEW", "percentage", {"percent": "10"}, start=date(2026, 11, 6), end=date(2026, 12, 31))
    r = evaluate(o, [expired, future])
    assert not r.applied and all("outside effective period" in s["reason"] for s in r.skipped)
    # boundary days are inclusive
    assert evaluate(o, [rule("EDGE", "percentage", {"percent": "1"}, start=TODAY, end=TODAY)]).applied
    assert not evaluate(o, [rule("R", "percentage", {"percent": "10"}, elig={"retailer_ids": ["r2"]})]).applied
    assert not evaluate(o, [rule("T", "percentage", {"percent": "10"}, elig={"territories": ["South"]})]).applied
    assert not evaluate(o, [rule("X", "percentage", {"percent": "10"}, elig={"exclude_retailer_ids": ["r1"]})]).applied
    assert evaluate(o, [rule("T", "percentage", {"percent": "10"}, elig={"territories": ["North"]})]).applied


def test_stacking_exclusive_vs_stackable_and_priority():
    o = order(line(1, "p1", 10, "100"))
    a = rule("A", "percentage", {"percent": "10"}, priority=1, stacking="exclusive")
    b = rule("B", "percentage", {"percent": "5"}, priority=2, stacking="stackable")
    r = evaluate(o, [b, a])
    assert [x.rule.code for x in r.applied] == ["A"]
    assert "non-stackable" in r.skipped[0]["reason"]
    s1 = rule("S1", "percentage", {"percent": "10"}, priority=1, stacking="stackable")
    s2 = rule("S2", "percentage", {"percent": "5"}, priority=2, stacking="stackable")
    r = evaluate(o, [s1, s2])
    assert r.line_discount(1) == D("150.00")  # 10% + 5% of gross, not compounded
    # an exclusive scheme after a stackable one cannot touch the already-benefited line
    r = evaluate(o, [s1, rule("EX", "percentage", {"percent": "20"}, priority=5)])
    assert [x.rule.code for x in r.applied] == ["S1"]


def test_max_benefit_cap_and_line_cap():
    o = order(line(1, "p1", 10, "100"), line(2, "p2", 10, "100"))
    r = evaluate(o, [rule("CAP", "percentage", {"percent": "50"}, cap="150")])
    assert r.applied[0].amount == D("150.00") and r.applied[0].explanation["capped"] is True
    assert r.line_discount(1) + r.line_discount(2) == D("150.00")
    # Stacked discounts can never exceed the line gross.
    r = evaluate(order(line(1, "p1", 1, "100")), [rule("S1", "percentage", {"percent": "80"}, stacking="stackable"), rule("S2", "percentage", {"percent": "80"}, priority=101, stacking="stackable")])
    assert r.line_discount(1) == D("100.00")


def test_explanation_is_persistable():
    r = evaluate(order(line(1, "p1", 10, "100")), [rule("PCT", "percentage", {"percent": "5"})])
    exp = r.applied[0].explanation
    for k in ("qualifying_quantity", "qualifying_value", "threshold", "rate", "calculation", "benefit"):
        assert k in exp
    assert exp["benefit"] == "50.00"


def test_config_validation():
    with pytest.raises(ValueError):
        validate_config("percentage", {"percent": "150"})
    with pytest.raises(ValueError):
        validate_config("quantity_slab", {"slabs": [{"min_quantity": "10"}]})
    with pytest.raises(ValueError):
        validate_config("combination", {"required": [{"product_id": "a", "min_quantity": 1}], "percent": 5})
    with pytest.raises(ValueError):
        validate_config("mystery", {})
    assert validate_config("buy_x_get_y", {"buy_quantity": 10, "free_quantity": 1}) == {"buy_quantity": "10", "free_quantity": "1", "basis": "per_line"}
