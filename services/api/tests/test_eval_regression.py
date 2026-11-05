"""Guards the safety property of order extraction on the synthetic eval set:
the pipeline must never auto-select a wrong SKU, and must hold ambiguous lines."""

from app.extraction.evaluate import run


def test_demo_pipeline_eval_floor():
    m = run("demo")["metrics"]
    assert m["false_auto_match_rate"] == 0.0
    assert m["ambiguous_lines_held_for_review"] == 1.0
    assert m["sku_top3_accuracy"] >= 0.95
    assert m["quantity_exact_match"] >= 0.95
