"""Order-extraction evaluation harness.

    python -m app.extraction.evaluate                      # demo parser (offline)
    python -m app.extraction.evaluate --provider anthropic # needs ANTHROPIC_API_KEY (costs money)

Runs the full parse + deterministic matching pipeline against a labelled,
versioned synthetic dataset (data/fixtures/eval/order_eval_v1.json) in an
in-memory database and prints metrics as JSON. Results are reported in
docs/AI_EVALUATION.md. The dataset is small and synthetic: numbers are a
regression signal for development, not a claim about real-world accuracy.
"""

import argparse
import json
import time
import uuid
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.extraction.providers import AnthropicProvider, DemoProvider, ExtractionInput
from app.models import Organization, Product, Retailer, UnitConversion, UnitOfMeasure
from app.services.extraction import match_order

DATASET = Path(__file__).resolve().parents[4] / "data" / "fixtures" / "eval" / "order_eval_v1.json"


def _load_catalog(db, catalog) -> Organization:
    org = Organization(name="Eval Org (synthetic)", slug=f"eval-{uuid.uuid4().hex[:6]}", settings={})
    db.add(org)
    db.flush()
    for code in ("PCS", "CASE", "DOZEN", "BOX"):
        db.add(UnitOfMeasure(organization_id=org.id, code=code, name=code, is_base=code == "PCS"))
    db.add(UnitConversion(organization_id=org.id, unit_code="DOZEN", factor=Decimal(12)))
    for p in catalog["products"]:
        prod = Product(organization_id=org.id, sku=p["sku"], name=p["name"], brand=p.get("brand"), variant=p.get("variant"), base_unit="PCS", aliases=p.get("aliases"))
        db.add(prod)
        db.flush()
        db.add(UnitConversion(organization_id=org.id, product_id=prod.id, unit_code="CASE", factor=Decimal(p.get("case", 12))))
        db.add(UnitConversion(organization_id=org.id, product_id=prod.id, unit_code="BOX", factor=Decimal(6)))
    for r in catalog["retailers"]:
        db.add(Retailer(organization_id=org.id, code=r["code"], name=r["name"], phone=r.get("phone")))
    db.flush()
    return org


def run(provider_name: str = "demo", dataset: Path = DATASET) -> dict:
    data = json.loads(dataset.read_text(encoding="utf-8"))
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    org = _load_catalog(db, data["catalog"])
    sku_by_id = {str(p.id): p.sku for p in db.query(Product).all()}
    code_by_id = {str(r.id): r.code for r in db.query(Retailer).all()}
    provider = AnthropicProvider() if provider_name == "anthropic" else DemoProvider()

    c = defaultdict(int)
    per_cat: dict = defaultdict(lambda: defaultdict(int))
    latencies, cost_tokens, failures = [], [0, 0], []
    for case in data["cases"]:
        t0 = time.perf_counter()
        res = provider.extract(ExtractionInput(kind="order", text=case["text"], catalog_hint=[f"{p['sku']} | {p['name']} {p['variant']}" for p in data["catalog"]["products"]]))
        latencies.append((time.perf_counter() - t0) * 1000)
        cost_tokens[0] += res.input_tokens or 0
        cost_tokens[1] += res.output_tokens or 0
        if not res.ok:
            failures.append({"id": case["id"], "error": res.error})
            c["doc_failures"] += 1
            c["gt_lines"] += len(case["lines"])
            continue
        result = match_order(db, org.id, res.parsed)
        raw_lines = res.parsed.lines
        pred = result["lines"]
        cat = per_cat[case["category"]]
        # retailer
        sel_r = result["retailer"]["selected_retailer_id"]
        c["retailer_total"] += 1
        if (code_by_id.get(sel_r) if sel_r else None) == case["retailer"]:
            c["retailer_correct"] += 1
        c["gt_lines"] += len(case["lines"])
        c["pred_lines"] += len(pred)
        for i, gt in enumerate(case["lines"]):
            cat["lines"] += 1
            if i >= len(pred):
                continue
            c["aligned"] += 1
            p, raw = pred[i], raw_lines[i]
            top = [sku_by_id[x["product_id"]] for x in p["sku_candidates"]]
            selected = sku_by_id.get(p["selected_product_id"]) if p["selected_product_id"] else None
            if selected:
                c["auto_selected"] += 1
            if gt["sku"]:
                c["sku_lines"] += 1
                if top[:1] == [gt["sku"]]:
                    c["top1"] += 1
                    cat["top1"] += 1
                if gt["sku"] in top[:3]:
                    c["top3"] += 1
                if selected == gt["sku"]:
                    c["auto_correct"] += 1
                    cat["auto_correct"] += 1
                elif selected:
                    c["false_auto"] += 1
            else:
                c["should_not_select"] += 1
                if selected:
                    c["false_auto"] += 1
                else:
                    c["correctly_held"] += 1
            gq = Decimal(gt["quantity"]) if gt["quantity"] is not None else None
            if (raw.quantity is None and gq is None) or (raw.quantity is not None and gq is not None and Decimal(raw.quantity) == gq):
                c["qty_exact"] += 1
            if (raw.unit or None) == gt["unit"]:
                c["unit_exact"] += 1
    n = lambda a, b: round(c[a] / c[b], 3) if c[b] else None  # noqa: E731
    return {
        "dataset": f"{data['dataset']} v{data['version']}",
        "provider": provider_name,
        "model": getattr(provider, "model", None),
        "cases": len(data["cases"]),
        "ground_truth_lines": c["gt_lines"],
        "metrics": {
            "line_recall": n("aligned", "gt_lines"),
            "line_precision": round(min(c["aligned"], c["pred_lines"]) / c["pred_lines"], 3) if c["pred_lines"] else None,
            "sku_top1_accuracy": n("top1", "sku_lines"),
            "sku_top3_accuracy": n("top3", "sku_lines"),
            "auto_selection_rate": n("auto_selected", "aligned"),
            "auto_selection_precision": round(c["auto_correct"] / c["auto_selected"], 3) if c["auto_selected"] else None,
            "false_auto_match_rate": round(c["false_auto"] / c["auto_selected"], 3) if c["auto_selected"] else 0.0,
            "ambiguous_lines_held_for_review": n("correctly_held", "should_not_select"),
            "quantity_exact_match": n("qty_exact", "aligned"),
            "unit_exact_match": n("unit_exact", "aligned"),
            "retailer_accuracy": n("retailer_correct", "retailer_total"),
            "document_failure_rate": round(c["doc_failures"] / len(data["cases"]), 3),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1),
            "tokens": {"input": cost_tokens[0], "output": cost_tokens[1]},
        },
        "per_category_auto_correct": {k: f"{v['auto_correct']}/{v['lines']}" for k, v in sorted(per_cat.items())},
        "failures": failures,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["demo", "anthropic"], default="demo")
    args = ap.parse_args()
    print(json.dumps(run(args.provider), indent=2))
