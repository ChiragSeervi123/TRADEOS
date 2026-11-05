"""Deterministic trade-scheme engine (pure functions, no database access).

Evaluation order and stacking rules (documented in docs/ARCHITECTURE.md#schemes):

1. A scheme version applies only if the order date is within [start_date, end_date]
   and the retailer satisfies its eligibility (retailer ids / territories / exclusions).
2. Qualifying lines: non-free lines whose product is in scope (product ids, brands,
   categories; empty scope = all products) and not explicitly excluded.
3. Schemes are evaluated by (priority ASC, code ASC).
   - An `exclusive` scheme skips lines that already carry any benefit, and lines it
     benefits cannot receive any further benefit.
   - A `stackable` scheme skips lines claimed by an exclusive scheme. Stackable
     percentage discounts are computed on the line's gross (not compounded).
4. A line's total discount is capped at its gross amount.
5. `max_benefit` caps a scheme's discount per order; the cap is re-allocated across
   lines pro rata (largest-remainder to the biggest share).
6. All money is Decimal, rounded HALF_UP to paise per line.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.money import ZERO, allocate_pro_rata, floor_int, money, pct_of, qty

# --------------------------------------------------------------------- config schemas


class Slab(BaseModel):
    min_quantity: Decimal | None = Field(default=None, gt=0)
    min_value: Decimal | None = Field(default=None, gt=0)
    percent: Decimal | None = Field(default=None, gt=0, le=100)
    amount_per_unit: Decimal | None = Field(default=None, gt=0)
    amount: Decimal | None = Field(default=None, gt=0)


class PercentageCfg(BaseModel):
    percent: Decimal = Field(gt=0, le=100)
    min_quantity: Decimal | None = Field(default=None, gt=0)
    min_value: Decimal | None = Field(default=None, gt=0)


class FixedAmountCfg(BaseModel):
    amount: Decimal = Field(gt=0)
    per: Literal["order", "unit"] = "order"
    min_quantity: Decimal | None = Field(default=None, gt=0)
    min_value: Decimal | None = Field(default=None, gt=0)


class BuyXGetYCfg(BaseModel):
    buy_quantity: Decimal = Field(gt=0)
    free_quantity: Decimal = Field(gt=0)
    free_product_id: str | None = None  # None = same product as purchased
    basis: Literal["per_line", "scope_total"] = "per_line"

    @model_validator(mode="after")
    def check(self):
        if self.free_product_id and self.basis == "per_line":
            self.basis = "scope_total"
        return self


class QuantitySlabCfg(BaseModel):
    slabs: list[Slab] = Field(min_length=1)
    basis: Literal["per_line", "scope_total"] = "scope_total"

    @model_validator(mode="after")
    def check(self):
        for s in self.slabs:
            if s.min_quantity is None:
                raise ValueError("each quantity slab needs min_quantity")
            if (s.percent is None) == (s.amount_per_unit is None):
                raise ValueError("each quantity slab needs exactly one of percent / amount_per_unit")
        mins = [s.min_quantity for s in self.slabs]
        if len(set(mins)) != len(mins):
            raise ValueError("slab thresholds must be unique")
        return self


class ValueSlabCfg(BaseModel):
    slabs: list[Slab] = Field(min_length=1)

    @model_validator(mode="after")
    def check(self):
        for s in self.slabs:
            if s.min_value is None:
                raise ValueError("each value slab needs min_value")
            if (s.percent is None) == (s.amount is None):
                raise ValueError("each value slab needs exactly one of percent / amount")
        mins = [s.min_value for s in self.slabs]
        if len(set(mins)) != len(mins):
            raise ValueError("slab thresholds must be unique")
        return self


class Requirement(BaseModel):
    product_id: str
    min_quantity: Decimal = Field(gt=0)


class CombinationCfg(BaseModel):
    required: list[Requirement] = Field(min_length=2)
    percent: Decimal | None = Field(default=None, gt=0, le=100)
    amount: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def check(self):
        if (self.percent is None) == (self.amount is None):
            raise ValueError("combination needs exactly one of percent / amount")
        return self


class ScopeCfg(BaseModel):
    product_ids: list[str] = []
    brands: list[str] = []
    categories: list[str] = []
    exclude_product_ids: list[str] = []


class EligibilityCfg(BaseModel):
    retailer_ids: list[str] = []
    territories: list[str] = []
    exclude_retailer_ids: list[str] = []


CONFIG_MODELS = {
    "percentage": PercentageCfg,
    "fixed_amount": FixedAmountCfg,
    "buy_x_get_y": BuyXGetYCfg,
    "quantity_slab": QuantitySlabCfg,
    "value_slab": ValueSlabCfg,
    "combination": CombinationCfg,
}


def validate_config(scheme_type: str, config: dict) -> dict:
    model = CONFIG_MODELS.get(scheme_type)
    if not model:
        raise ValueError(f"unknown scheme type '{scheme_type}'")
    return model.model_validate(config).model_dump(mode="json", exclude_none=True)


# --------------------------------------------------------------------- engine types


@dataclass
class Line:
    key: int
    product_id: str
    sku: str
    brand: str | None
    category: str | None
    base_qty: Decimal
    unit_price: Decimal
    gross: Decimal


@dataclass
class OrderInput:
    order_date: date
    retailer_id: str
    territory: str | None
    lines: list[Line]


@dataclass
class Rule:
    scheme_id: str
    version_id: str
    version: int
    code: str
    name: str
    scheme_type: str
    config: dict
    scope: dict
    eligibility: dict
    start_date: date
    end_date: date
    stacking: str = "exclusive"
    priority: int = 100
    max_benefit: Decimal | None = None


@dataclass
class Benefit:
    rule: Rule
    benefit_type: str  # discount | free_goods
    line_discounts: dict[int, Decimal] = field(default_factory=dict)
    free_goods: list[dict] = field(default_factory=list)  # {product_id, quantity, unit_price, value, for_line}
    amount: Decimal = ZERO  # discount amount, or value of free goods
    explanation: dict = field(default_factory=dict)


@dataclass
class Result:
    applied: list[Benefit]
    skipped: list[dict]

    def line_discount(self, key: int) -> Decimal:
        return money(sum((b.line_discounts.get(key, ZERO) for b in self.applied), ZERO))


# --------------------------------------------------------------------- helpers


def _eligible(rule: Rule, order: OrderInput) -> str | None:
    if not (rule.start_date <= order.order_date <= rule.end_date):
        return f"outside effective period {rule.start_date} to {rule.end_date}"
    el = EligibilityCfg.model_validate(rule.eligibility or {})
    if el.exclude_retailer_ids and order.retailer_id in el.exclude_retailer_ids:
        return "retailer explicitly excluded"
    if el.retailer_ids and order.retailer_id not in el.retailer_ids:
        return "retailer not in eligible list"
    if el.territories and (order.territory or "") not in el.territories:
        return f"territory '{order.territory}' not eligible"
    return None


def _in_scope(rule: Rule, line: Line) -> bool:
    sc = ScopeCfg.model_validate(rule.scope or {})
    if line.product_id in sc.exclude_product_ids:
        return False
    if not (sc.product_ids or sc.brands or sc.categories):
        return True
    return (
        line.product_id in sc.product_ids
        or (line.brand is not None and line.brand in sc.brands)
        or (line.category is not None and line.category in sc.categories)
    )


def _cap_lines(discounts: dict[int, Decimal], lines: dict[int, Line], already: dict[int, Decimal]) -> dict[int, Decimal]:
    out = {}
    for k, d in discounts.items():
        room = money(lines[k].gross - already.get(k, ZERO))
        out[k] = max(ZERO, min(money(d), room))
    return out


def _pro_rata(amount: Decimal, qual: list[Line]) -> dict[int, Decimal]:
    shares = allocate_pro_rata(amount, [l.gross for l in qual])
    return {l.key: s for l, s in zip(qual, shares, strict=True)}


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f") if d == d.to_integral() else str(d)


# --------------------------------------------------------------------- evaluation


def _evaluate_one(rule: Rule, qual: list[Line], order: OrderInput, prices: dict[str, Decimal]):
    """Returns (benefit_type, line_discounts, free_goods, explanation) or (None, reason)."""
    cfg = CONFIG_MODELS[rule.scheme_type].model_validate(rule.config)
    total_qty = qty(sum((l.base_qty for l in qual), ZERO))
    total_val = money(sum((l.gross for l in qual), ZERO))
    exp = {
        "scheme_code": rule.code,
        "scheme_name": rule.name,
        "scheme_type": rule.scheme_type,
        "version": rule.version,
        "qualifying_lines": [l.sku for l in qual],
        "qualifying_quantity": str(total_qty),
        "qualifying_value": str(total_val),
    }
    t = rule.scheme_type

    def _min_checks(c) -> str | None:
        if getattr(c, "min_quantity", None) and total_qty < c.min_quantity:
            return f"qualifying quantity {_fmt(total_qty)} below minimum {_fmt(c.min_quantity)}"
        if getattr(c, "min_value", None) and total_val < c.min_value:
            return f"qualifying value {total_val} below minimum {c.min_value}"
        return None

    if t == "percentage":
        if (why := _min_checks(cfg)) is not None:
            return None, why
        d = {l.key: pct_of(l.gross, cfg.percent) for l in qual}
        exp.update(threshold=_thr(cfg), rate=f"{_fmt(cfg.percent)}%", calculation=f"{_fmt(cfg.percent)}% of qualifying gross {total_val}")
        return ("discount", d, [], exp), None

    if t == "fixed_amount":
        if (why := _min_checks(cfg)) is not None:
            return None, why
        if cfg.per == "unit":
            d = {l.key: money(cfg.amount * l.base_qty) for l in qual}
            calc = f"{cfg.amount} per unit x {_fmt(total_qty)} units"
        else:
            d = _pro_rata(money(cfg.amount), qual)
            calc = f"flat {money(cfg.amount)} per order, allocated pro rata by line gross"
        exp.update(threshold=_thr(cfg), rate=f"{cfg.amount} per {cfg.per}", calculation=calc)
        return ("discount", d, [], exp), None

    if t == "buy_x_get_y":
        free = []
        if cfg.basis == "per_line" and not cfg.free_product_id:
            for l in qual:
                sets = floor_int(l.base_qty / cfg.buy_quantity)
                if sets > 0:
                    fq = qty(sets * cfg.free_quantity)
                    free.append({"product_id": l.product_id, "quantity": fq, "unit_price": l.unit_price, "value": money(fq * l.unit_price), "for_line": l.key})
            calc = f"per line: floor(qty / {_fmt(cfg.buy_quantity)}) x {_fmt(cfg.free_quantity)} free"
        else:
            sets = floor_int(total_qty / cfg.buy_quantity)
            if sets > 0:
                fq = qty(sets * cfg.free_quantity)
                pid = cfg.free_product_id or qual[0].product_id
                price = prices.get(pid, ZERO)
                free.append({"product_id": pid, "quantity": fq, "unit_price": price, "value": money(fq * price), "for_line": None})
            calc = f"floor({_fmt(total_qty)} / {_fmt(cfg.buy_quantity)}) = {_fmt(sets)} set(s) x {_fmt(cfg.free_quantity)} free"
        if not free:
            return None, f"quantity below buy threshold {_fmt(cfg.buy_quantity)}"
        exp.update(threshold=f"buy {_fmt(cfg.buy_quantity)}", rate=f"get {_fmt(cfg.free_quantity)} free", calculation=calc)
        return ("free_goods", {}, free, exp), None

    if t == "quantity_slab":
        slabs = sorted(cfg.slabs, key=lambda s: s.min_quantity)
        d: dict[int, Decimal] = {}
        applied_slabs = []
        groups = [[l] for l in qual] if cfg.basis == "per_line" else [qual]
        for g in groups:
            gq = sum((l.base_qty for l in g), ZERO)
            slab = next((s for s in reversed(slabs) if gq >= s.min_quantity), None)
            if not slab:
                continue
            applied_slabs.append(_fmt(slab.min_quantity))
            for l in g:
                d[l.key] = pct_of(l.gross, slab.percent) if slab.percent else money(slab.amount_per_unit * l.base_qty)
        if not d:
            return None, f"quantity {_fmt(total_qty)} below lowest slab {_fmt(slabs[0].min_quantity)}"
        top = next(s for s in reversed(slabs) if _fmt(s.min_quantity) in applied_slabs)
        rate = f"{_fmt(top.percent)}%" if top.percent else f"{top.amount_per_unit} per unit"
        exp.update(threshold=f"slab >= {', '.join(applied_slabs)} units", rate=rate, calculation=f"highest slab met ({cfg.basis}) -> {rate}", slabs=[s.model_dump(mode="json", exclude_none=True) for s in slabs])
        return ("discount", d, [], exp), None

    if t == "value_slab":
        slabs = sorted(cfg.slabs, key=lambda s: s.min_value)
        slab = next((s for s in reversed(slabs) if total_val >= s.min_value), None)
        if not slab:
            return None, f"value {total_val} below lowest slab {slabs[0].min_value}"
        if slab.percent:
            d = {l.key: pct_of(l.gross, slab.percent) for l in qual}
            rate = f"{_fmt(slab.percent)}%"
        else:
            d = _pro_rata(money(slab.amount), qual)
            rate = f"flat {money(slab.amount)}"
        exp.update(threshold=f"value >= {slab.min_value}", rate=rate, calculation=f"qualifying value {total_val} meets slab {slab.min_value} -> {rate}", slabs=[s.model_dump(mode="json", exclude_none=True) for s in slabs])
        return ("discount", d, [], exp), None

    if t == "combination":
        need = {r.product_id: r.min_quantity for r in cfg.required}
        have: dict[str, Decimal] = {}
        for l in qual:
            have[l.product_id] = have.get(l.product_id, ZERO) + l.base_qty
        missing = [pid for pid, mq in need.items() if have.get(pid, ZERO) < mq]
        if missing:
            return None, f"combination incomplete: {len(missing)} required product(s) missing or below minimum"
        combo_lines = [l for l in qual if l.product_id in need]
        if cfg.percent:
            d = {l.key: pct_of(l.gross, cfg.percent) for l in combo_lines}
            rate = f"{_fmt(cfg.percent)}%"
        else:
            d = _pro_rata(money(cfg.amount), combo_lines)
            rate = f"flat {money(cfg.amount)}"
        exp.update(threshold="all required products at minimum quantity", rate=rate, calculation=f"combination satisfied -> {rate} on combination lines")
        return ("discount", d, [], exp), None

    return None, f"unsupported scheme type {t}"


def _thr(cfg) -> str | None:
    parts = []
    if getattr(cfg, "min_quantity", None):
        parts.append(f"qty >= {_fmt(cfg.min_quantity)}")
    if getattr(cfg, "min_value", None):
        parts.append(f"value >= {cfg.min_value}")
    return " and ".join(parts) or "none"


def evaluate(order: OrderInput, rules: list[Rule], free_goods_prices: dict[str, Decimal] | None = None) -> Result:
    prices = dict(free_goods_prices or {})
    for l in order.lines:
        prices.setdefault(l.product_id, l.unit_price)
    lines = {l.key: l for l in order.lines}
    applied: list[Benefit] = []
    skipped: list[dict] = []
    claimed_exclusive: set[int] = set()
    discounted: dict[int, Decimal] = {}
    benefited: set[int] = set()

    for rule in sorted(rules, key=lambda r: (r.priority, r.code)):
        meta = {"scheme_id": rule.scheme_id, "code": rule.code, "name": rule.name, "version": rule.version}
        why = _eligible(rule, order)
        if why:
            skipped.append({**meta, "reason": why})
            continue
        qual = [l for l in order.lines if l.gross > 0 and _in_scope(rule, l)]
        if not qual:
            skipped.append({**meta, "reason": "no order lines in scheme scope"})
            continue
        blocked = benefited if rule.stacking == "exclusive" else claimed_exclusive
        eligible_lines = [l for l in qual if l.key not in blocked]
        if not eligible_lines:
            skipped.append({**meta, "reason": "lines already carry a non-stackable benefit"})
            continue
        out, reason = _evaluate_one(rule, eligible_lines, order, prices)
        if out is None:
            skipped.append({**meta, "reason": reason})
            continue
        btype, disc, free, exp = out
        disc = _cap_lines(disc, lines, discounted)
        capped = False
        total = money(sum(disc.values(), ZERO))
        if btype == "discount" and rule.max_benefit is not None and total > rule.max_benefit:
            keys = sorted(disc)
            shares = allocate_pro_rata(money(rule.max_benefit), [disc[k] for k in keys])
            disc = dict(zip(keys, shares, strict=True))
            total = money(rule.max_benefit)
            capped = True
        if btype == "discount" and total <= 0:
            skipped.append({**meta, "reason": "no remaining line value to discount"})
            continue
        amount = total if btype == "discount" else money(sum((f["value"] for f in free), ZERO))
        exp["benefit"] = str(amount)
        exp["benefit_type"] = btype
        exp["capped"] = capped
        if capped:
            exp["cap"] = str(rule.max_benefit)
        exp["stacking"] = rule.stacking
        exp["line_benefits"] = [{"sku": lines[k].sku, "gross": str(lines[k].gross), "discount": str(v)} for k, v in sorted(disc.items())]
        if free:
            exp["free_goods"] = [{"product_id": f["product_id"], "quantity": str(f["quantity"]), "value": str(f["value"])} for f in free]
        b = Benefit(rule=rule, benefit_type=btype, line_discounts=disc, free_goods=free, amount=amount, explanation=exp)
        applied.append(b)
        touched = set(disc) | {l.key for l in eligible_lines if btype == "free_goods"}
        for k, v in disc.items():
            discounted[k] = money(discounted.get(k, ZERO) + v)
        benefited |= touched
        if rule.stacking == "exclusive":
            claimed_exclusive |= touched
    return Result(applied=applied, skipped=skipped)
