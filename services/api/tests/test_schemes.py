import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.models import SchemeApplication
from tests.conftest import ok
from tests.helpers import approve_and_dispatch, place_order, setup_catalog

TODAY = date.today()


def scheme_body(code="SURF5", stype="percentage", config=None, start=None, end=None, **kw):
    return {
        "code": code,
        "name": f"{code} scheme",
        "funded_by": "supplier",
        "version": {
            "scheme_type": stype,
            "config": config or {"percent": "5"},
            "scope": kw.pop("scope", {"brands": ["Surf"]}),
            "start_date": (start or TODAY - timedelta(days=1)).isoformat(),
            "end_date": (end or TODAY + timedelta(days=30)).isoformat(),
            **kw,
        },
    }


def activate(t, body, approver=None):
    s = ok(t.post("/schemes", body), 201)
    vid = s["versions"][-1]["id"]
    ok(t.post(f"/schemes/{s['id']}/versions/{vid}/submit"))
    return ok(t.post(f"/schemes/{s['id']}/versions/{vid}/approve", headers=approver))


def test_scheme_lifecycle_and_order_integration(tenant, db):
    c = setup_catalog(tenant)
    s = ok(tenant.post("/schemes", scheme_body()), 201)
    vid = s["versions"][0]["id"]
    # draft schemes never apply
    o = ok(place_order(tenant, c, qty="10"), 201)
    assert o["totals"]["scheme_discount"] == "0.00"
    acct = tenant.user("accountant")  # schemes.manage but not schemes.approve
    ops = tenant.user("operations_manager")
    ok(tenant.post(f"/schemes/{s['id']}/versions/{vid}/submit", headers=acct))
    assert tenant.post(f"/schemes/{s['id']}/versions/{vid}/approve", headers=acct).status_code == 403
    assert tenant.post(f"/schemes/{s['id']}/versions/{vid}/submit", headers=ops).status_code == 403
    out = ok(tenant.post(f"/schemes/{s['id']}/versions/{vid}/approve"))
    assert out["status"] == "active" and out["active_version"] == 1
    o2 = ok(place_order(tenant, c, qty="10"), 201)
    # 10 x 50 = 500 gross, 5% = 25 discount, taxable 475, tax 85.50
    assert o2["totals"] == {"gross": "500.00", "scheme_discount": "25.00", "manual_discount": "0.00", "taxable": "475.00", "tax": "85.50", "net": "560.50"}
    exp = o2["schemes"][0]["explanation"]
    assert exp["rate"] == "5%" and exp["benefit"] == "25.00" and exp["qualifying_quantity"] == "10.000"
    # submitted versions are immutable; edits need a new version
    assert tenant.put(f"/schemes/{s['id']}/versions/{vid}", scheme_body()["version"]).status_code == 409
    v2 = ok(tenant.post(f"/schemes/{s['id']}/versions", {**scheme_body()["version"], "config": {"percent": "8"}}), 201)
    v2id = v2["versions"][-1]["id"]
    ok(tenant.post(f"/schemes/{s['id']}/versions/{v2id}/submit"))
    out = ok(tenant.post(f"/schemes/{s['id']}/versions/{v2id}/approve"))
    assert [v["status"] for v in out["versions"]] == ["superseded", "active"]
    # the approved order keeps the explanation from the version that applied
    apps = db.scalars(select(SchemeApplication).where(SchemeApplication.order_id == uuid.UUID(o2["id"]))).all()
    assert apps[0].scheme_version == 1
    ok(tenant.post(f"/schemes/{s['id']}/deactivate", {"reason": "Ended early by supplier"}))
    o3 = ok(place_order(tenant, c, qty="10"), 201)
    assert o3["totals"]["scheme_discount"] == "0.00"


def test_expired_scheme_does_not_apply(tenant):
    c = setup_catalog(tenant)
    activate(tenant, scheme_body(start=TODAY - timedelta(days=30), end=TODAY - timedelta(days=1)))
    o = ok(place_order(tenant, c, qty="10"), 201)
    assert o["totals"]["scheme_discount"] == "0.00"
    info = next(i for i in o["validation"]["issues"] if i["code"] == "SCHEMES_NOT_APPLIED")
    assert "outside effective period" in info["details"]["skipped"][0]["reason"]


def test_retailer_eligibility(tenant):
    c = setup_catalog(tenant)
    other = ok(tenant.post("/retailers", {"code": "R999", "name": "Other Shop Elsewhere"}), 201)
    activate(tenant, scheme_body(eligibility={"retailer_ids": [other["id"]]}))
    o = ok(place_order(tenant, c, qty="10"), 201)
    assert o["totals"]["scheme_discount"] == "0.00"


def test_buy_x_get_y_adds_free_goods_and_reserves_stock(tenant):
    c = setup_catalog(tenant)
    activate(tenant, scheme_body("BXGY", "buy_x_get_y", {"buy_quantity": "10", "free_quantity": "1"}))
    o = ok(place_order(tenant, c, qty="25"), 201)
    free = [l for l in o["lines"] if l["is_free_goods"]]
    assert len(free) == 1 and free[0]["base_quantity"] == "2.000" and free[0]["unit_price"] == "0.00"
    assert o["totals"]["net"] == "1475.00"  # 25 x 50 x 1.18, free goods not charged
    approve_and_dispatch(tenant, o["id"])
    bal = ok(tenant.get("/inventory/balances", params={"q": "SKU-001"}))["items"][0]
    assert bal["on_hand"] == "73.000"  # 25 + 2 free left the warehouse


def test_preview_and_validation_errors(tenant):
    c = setup_catalog(tenant)
    s = ok(tenant.post("/schemes", scheme_body("QS", "quantity_slab", {"slabs": [{"min_quantity": "10", "percent": "2"}, {"min_quantity": "20", "percent": "4"}]})), 201)
    pv = ok(tenant.post("/schemes/preview", {"retailer_id": c["retailer"]["id"], "lines": [{"product_id": c["p"]["id"], "quantity": "2", "unit": "CASE"}], "include_version_id": s["versions"][0]["id"]}))
    assert pv["applied"][0]["rate"] == "4%" and pv["total_discount"] == "48.00"
    bad = scheme_body("BAD", "quantity_slab", {"slabs": [{"min_quantity": "10"}]})
    r = tenant.post("/schemes", bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_SCHEME_RULE"
    r = tenant.post("/schemes", scheme_body("DATES", start=TODAY, end=TODAY - timedelta(days=1)))
    assert r.status_code == 422


def test_extracted_scheme_is_draft_only(tenant):
    setup_catalog(tenant)
    text = "Scheme: Surf Winter Offer\n5% off on Surf range\nValid from 01/12/2026 to 31/12/2026"
    r = ok(tenant.post("/schemes/extract", data={"text": text}), 201)
    assert r["job_status"] == "succeeded" and r["result"]["status"] == "drafted"
    sid = r["result"]["drafts"][0]["scheme_id"]
    s = ok(tenant.get(f"/schemes/{sid}"))
    v = s["versions"][0]
    assert s["status"] == "draft" and v["scheme_type"] == "percentage" and v["config"]["percent"] == "5"
    assert v["scope"]["brands"] == ["Surf"] and v["start_date"] == "2026-12-01"
    assert v["source_document_id"] and v["extraction_id"]


def test_claim_generation_from_dispatched_orders(tenant):
    c = setup_catalog(tenant)
    s = activate(tenant, scheme_body())
    o = ok(place_order(tenant, c, qty="10"), 201)
    r = tenant.post("/schemes/claims", {"scheme_id": s["id"], "period_start": (TODAY - timedelta(days=1)).isoformat(), "period_end": TODAY.isoformat()})
    assert r.status_code == 422  # not dispatched yet
    approve_and_dispatch(tenant, o["id"])
    claim = ok(tenant.post("/schemes/claims", {"scheme_id": s["id"], "period_start": (TODAY - timedelta(days=1)).isoformat(), "period_end": TODAY.isoformat()}), 201)
    assert claim["expected_amount"] == "25.00" and claim["status"] == "draft"
    # benefits can't be claimed twice
    assert tenant.post("/schemes/claims", {"scheme_id": s["id"], "period_start": (TODAY - timedelta(days=1)).isoformat(), "period_end": TODAY.isoformat()}).status_code == 422
    ok(tenant.post(f"/schemes/claims/{claim['id']}/mark-submitted", {"notes": "Emailed to supplier ASM"}))
