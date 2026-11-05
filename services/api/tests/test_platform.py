from datetime import date
from decimal import Decimal

from tests.conftest import Tenant, ok
from tests.helpers import approve_and_dispatch, place_order, setup_catalog

TODAY = date.today().isoformat()


def test_dashboard_totals_reconcile_with_transactions(tenant):
    c = setup_catalog(tenant)
    orders = [ok(place_order(tenant, c, qty=q), 201) for q in ("10", "4", "6")]
    approve_and_dispatch(tenant, orders[0]["id"])
    approve_and_dispatch(tenant, orders[1]["id"], picks=["3"])  # partial
    ok(tenant.post(f"/orders/{orders[2]['id']}/approve", {}))
    invs = ok(tenant.get("/sales-invoices"))["items"]
    gross = sum(Decimal(i["gross_amount"]) for i in invs)
    disc = sum(Decimal(i["discount_amount"]) for i in invs)
    inv0 = next(i for i in invs if i["total"] == "590.00")
    p = ok(tenant.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "200", "received_on": TODAY}), 201)
    ok(tenant.post(f"/payments/{p['id']}/allocations", {"allocations": [{"sales_invoice_id": inv0["id"], "amount": "200"}]}), 201)
    ov = ok(tenant.get("/analytics/overview"))
    assert ov["orders"]["orders_received"] == 3 and ov["orders"]["orders_approved"] == 3 and ov["orders"]["orders_dispatched"] == 2
    assert Decimal(ov["sales"]["gross_sales"]) == gross == Decimal("650.00")
    assert Decimal(ov["sales"]["net_sales"]) == gross - disc
    outstanding = sum(Decimal(i["outstanding"]) for i in ok(tenant.get("/sales-invoices"))["items"])
    assert Decimal(ov["receivables"]["total_outstanding"]) == outstanding == Decimal("567.00")
    # inventory value = sum(on_hand x cost) from balances
    bals = ok(tenant.get("/inventory/balances"))["items"]
    assert Decimal(ov["inventory"]["inventory_value"]) == sum(Decimal(b["value"]) for b in bals if b["value"])
    assert ov["is_demo"] is False
    ops = ok(tenant.get("/analytics/operations"))
    assert ops["pick_queue"] == 1 and ops["partially_fulfilled_open"] == 1
    acc = ok(tenant.get("/analytics/accounting"))
    assert acc["receivables"]["total_outstanding"] == "567.00"
    tr = ok(tenant.get("/analytics/trends"))
    assert sum(d["orders"] for d in tr["daily"]) == 3
    assert tr["top_products"][0]["sku"] == "SKU-001"
    r = tenant.get("/analytics/export", params={"report": "sales_by_product"})
    assert r.status_code == 200 and "SKU-001" in r.text


def test_dashboard_role_scoping(tenant):
    setup_catalog(tenant)
    ops = tenant.user("operations_manager")
    ov = ok(tenant.get("/analytics/overview", headers=ops))
    assert "sales" not in ov and "receivables" not in ov and "inventory_value" not in ov["inventory"]
    rep = tenant.user("sales_rep")
    assert tenant.get("/analytics/overview", headers=rep).status_code == 403
    wh = tenant.user("warehouse_operator")
    ok(tenant.get("/analytics/operations", headers=wh))
    assert tenant.get("/analytics/accounting", headers=wh).status_code == 403


def test_audit_log_and_permissions(tenant):
    setup_catalog(tenant)
    log = ok(tenant.get("/audit", params={"action": "product."}))
    assert log["total"] >= 2 and all(e["action"].startswith("product.") for e in log["items"])
    assert tenant.get("/audit", headers=tenant.user("sales_rep")).status_code == 403


def test_webhook_integration_and_simulated_tally(tenant, client):
    c = setup_catalog(tenant)
    st = {i["provider"]: i for i in ok(tenant.get("/integrations"))["integrations"]}
    assert st["webhook"]["status"] == "not_configured" and st["tally"]["status"] == "simulated" and st["whatsapp"]["status"] == "not_configured"
    tok = ok(tenant.post("/integrations/webhook/token"), 201)["token"]
    assert client.post("/api/v1/integrations/webhook/orders", json={"text": "x"}, headers={"X-TRADEOS-Webhook-Token": "bad"}).status_code == 401
    body = {"text": "Sharma General Store here\n2 case surf excel 1kg", "external_id": "wa-123"}
    r1 = ok(client.post("/api/v1/integrations/webhook/orders", json=body, headers={"X-TRADEOS-Webhook-Token": tok}), 202)
    r2 = ok(client.post("/api/v1/integrations/webhook/orders", json=body, headers={"X-TRADEOS-Webhook-Token": tok}), 202)
    assert r2["status"] == "duplicate" and r2["source_id"] == r1["source_id"]
    src = ok(tenant.get(f"/order-extractions/{r1['source_id']}"))
    assert src["channel"] == "api" and src["status"] == "needs_review"
    o = ok(place_order(tenant, c, qty="2"), 201)
    approve_and_dispatch(tenant, o["id"])
    x = tenant.get("/integrations/tally/export")
    assert x.status_code == 200 and "<VOUCHER" in x.text and "simulated" in x.text
    runs = ok(tenant.get("/integrations"))["recent_runs"]
    assert {r["provider"] for r in runs} == {"webhook", "tally"}


def test_manufacturer_scoped_access(tenant, client):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10", extra_lines=[{"product_id": c["p2"]["id"], "quantity": "5"}]), 201)
    approve_and_dispatch(tenant, o["id"])
    mfr = Tenant(client, "Maker Co", org_type="manufacturer")
    from app import db as dbmod
    from app.models import Organization

    s = dbmod.SessionLocal()
    slug = s.get(Organization, mfr.org_id).slug
    s.close()
    # manufacturer cannot touch distributor endpoints even in its own org
    assert mfr.get("/orders").status_code == 403
    assert ok(mfr.get("/shared/distributors")) == []
    r = tenant.post("/data-sharing/grants", {"manufacturer_slug": slug, "metrics": ["sales", "stock"], "brands": ["Surf"]})
    g = ok(r, 201)
    acct = tenant.user("accountant")
    assert tenant.post("/data-sharing/grants", {"manufacturer_slug": slug, "metrics": ["sales"], "brands": ["Dove"]}, headers=acct).status_code == 403
    grants = ok(mfr.get("/shared/distributors"))
    m = ok(mfr.get(f"/shared/distributors/{grants[0]['id']}/metrics"))
    assert [x["sku"] for x in m["sales"]["by_product"]] == ["SKU-001"]  # Dove is out of scope
    assert m["sales"]["by_product"][0]["net_value"] == "500.00"
    assert "by_retailer" not in m["sales"] and "schemes" not in m
    assert m["stock"][0]["sku"] == "SKU-001" and m["data_freshness"]["latest_invoice_date"] == TODAY
    ok(tenant.post(f"/data-sharing/grants/{g['id']}/revoke"))
    assert mfr.get(f"/shared/distributors/{g['id']}/metrics").status_code == 404
    # a distributor user cannot read the manufacturer endpoints
    assert tenant.get("/shared/distributors").status_code == 403
