"""Critical invariant: a tenant cannot read or mutate another tenant's records by
changing IDs in URLs, request bodies or query parameters."""

from datetime import date

from tests.conftest import ok
from tests.helpers import approve_and_dispatch, place_order, setup_catalog
from tests.test_schemes import activate, scheme_body

TODAY = date.today().isoformat()


def _a_world(t):
    c = setup_catalog(t)
    o = ok(place_order(t, c, qty="5"), 201)
    _, pl_id, d = approve_and_dispatch(t, o["id"])
    detail = ok(t.get(f"/orders/{o['id']}"))
    inv = detail["invoices"][0]
    pay = ok(t.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "100", "received_on": TODAY}), 201)
    src = ok(t.post("/order-extractions/text", {"text": "Sharma General Store here\n1 case surf excel 1kg"}), 201)
    ok(t.post("/order-extractions/text", {"text": "Sharma General Store here\n1 case surf excel 1kg"}), 201)  # duplicate -> exception
    scheme = activate(t, scheme_body())
    pi = ok(t.post("/purchase-invoices", {"supplier_id": c["supplier"]["id"], "invoice_number": "A-1", "invoice_date": TODAY, "lines": []}), 201)
    case = ok(t.post("/reconciliation/cases", {"category": "unknown_difference", "title": "A's private case", "expected_value": "1", "observed_value": "2"}), 201)
    exc = ok(t.get("/exceptions", params={"status": "all"}))["items"]
    return {**c, "order": o, "pick_list": pl_id, "dispatch": d["dispatch_id"], "invoice": inv, "payment": pay, "source": src, "scheme": scheme, "pi": pi, "case": case, "exceptions": exc}


def test_cross_tenant_reads_are_404(tenant, other_tenant):
    a = _a_world(tenant)
    b = other_tenant
    reads = [
        f"/products/{a['p']['id']}",
        f"/retailers/{a['retailer']['id']}",
        f"/suppliers/{a['supplier']['id']}",
        f"/orders/{a['order']['id']}",
        f"/orders/{a['order']['id']}/audit",
        f"/fulfilment/pick-lists/{a['pick_list']}",
        f"/sales-invoices/{a['invoice']['id']}",
        f"/payments/{a['payment']['id']}",
        f"/order-extractions/{a['source']['id']}",
        f"/schemes/{a['scheme']['id']}",
        f"/purchase-invoices/{a['pi']['id']}",
        f"/reconciliation/cases/{a['case']['id']}",
        f"/price-lists/{a['price_list']['id']}/items",
        f"/receivables/retailers/{a['retailer']['id']}",
    ] + [f"/exceptions/{e['id']}" for e in a["exceptions"][:3]]
    for path in reads:
        r = b.get(path)
        assert r.status_code == 404, f"{path} leaked: {r.status_code} {r.text[:200]}"
    if a["source"].get("document"):
        assert b.get(f"/documents/{a['source']['document']['id']}").status_code == 404


def test_cross_tenant_lists_never_include_foreign_rows(tenant, other_tenant):
    a = _a_world(tenant)
    b = other_tenant
    for path in ("/products", "/retailers", "/suppliers", "/orders", "/sales-invoices", "/payments", "/order-extractions", "/schemes", "/purchase-invoices", "/reconciliation/cases", "/exceptions", "/fulfilment/pick-lists", "/returns", "/credit-notes"):
        assert ok(b.get(path))["total"] == 0, path
    # Query-parameter injection of a foreign id returns nothing.
    assert ok(b.get("/orders", params={"retailer_id": a["retailer"]["id"]}))["total"] == 0
    assert ok(b.get("/inventory/movements", params={"product_id": a["p"]["id"]}))["total"] == 0
    assert ok(b.get("/inventory/balances"))["items"] == []
    assert b.get("/inventory/balances", params={"warehouse_id": a["wh"]["id"]}).status_code == 404
    assert ok(b.get("/warehouses")) == []
    assert ok(b.get("/inventory/reservations")) == []


def test_cross_tenant_mutations_are_rejected(tenant, other_tenant):
    a = _a_world(tenant)
    b = other_tenant
    bwh = ok(b.post("/warehouses", {"code": "BWH", "name": "B warehouse"}), 201)
    bret = ok(b.post("/retailers", {"code": "B1", "name": "B Retail Shop"}), 201)
    bpay = ok(b.post("/payments", {"retailer_id": bret["id"], "amount": "50", "received_on": TODAY}), 201)
    attempts = [
        ("patch", f"/products/{a['p']['id']}", {"name": "pwned"}),
        ("patch", f"/retailers/{a['retailer']['id']}", {"name": "pwned"}),
        ("put", f"/retailers/{a['retailer']['id']}/credit-terms", {"credit_limit": "1", "reason": "pwned"}),
        ("post", f"/orders/{a['order']['id']}/cancel", {"reason": "pwned"}),
        ("post", f"/orders/{a['order']['id']}/approve", {}),
        ("post", f"/fulfilment/pick-lists/{a['pick_list']}/dispatch", {}),
        ("post", f"/fulfilment/dispatches/{a['dispatch']}/delivery", {"outcome": "delivered"}),
        ("post", f"/payments/{a['payment']['id']}/allocations", {"allocations": [{"sales_invoice_id": a["invoice"]["id"], "amount": "1"}]}),
        ("post", f"/payments/{bpay['id']}/allocations", {"allocations": [{"sales_invoice_id": a["invoice"]["id"], "amount": "1"}]}),
        ("post", f"/schemes/{a['scheme']['id']}/deactivate", {"reason": "pwned"}),
        ("post", f"/purchase-invoices/{a['pi']['id']}/verify", {}),
        ("post", f"/reconciliation/cases/{a['case']['id']}/transition", {"status": "accepted_difference", "note": "pwned"}),
        ("post", f"/order-extractions/{a['source']['id']}/discard", {"reason": "pwned"}),
        ("put", f"/price-lists/{a['price_list']['id']}/items", {"items": [{"product_id": a["p"]["id"], "unit_price": "1"}]}),
        # foreign ids smuggled in request bodies
        ("post", "/orders", {"retailer_id": a["retailer"]["id"], "warehouse_id": bwh["id"], "lines": [{"product_id": a["p"]["id"], "quantity": "1"}]}),
        ("post", "/orders", {"retailer_id": bret["id"], "warehouse_id": bwh["id"], "lines": [{"product_id": a["p"]["id"], "quantity": "1"}]}),
        ("post", "/inventory/adjustments", {"warehouse_id": a["wh"]["id"], "product_id": a["p"]["id"], "quantity": "5", "reason": "pwned"}),
        ("post", "/payments", {"retailer_id": a["retailer"]["id"], "amount": "1", "received_on": TODAY}),
        ("post", "/credit-notes", {"direction": "issued", "retailer_id": bret["id"], "sales_invoice_id": a["invoice"]["id"], "amount": "1"}),
        ("post", f"/exceptions/{a['exceptions'][0]['id']}/resolve", {"reason": "pwned"}),
    ]
    for method, path, body in attempts:
        r = getattr(b, method)(path, body)
        assert r.status_code == 404, f"{method.upper()} {path} -> {r.status_code} {r.text[:200]}"
    # A's data is unchanged
    assert ok(tenant.get(f"/products/{a['p']['id']}"))["name"] == "Surf Excel Detergent"
    assert ok(tenant.get(f"/orders/{a['order']['id']}"))["status"] == "dispatched"
    assert ok(tenant.get(f"/payments/{a['payment']['id']}"))["unallocated"] == "100.00"


def test_org_cannot_be_chosen_by_client(tenant, other_tenant, client):
    """Organisation comes from the signed token; a body field cannot switch tenant."""
    r = other_tenant.post("/products", {"sku": "X", "name": "Thing", "organization_id": str(tenant.org_id)})
    assert r.status_code == 201
    assert ok(tenant.get("/products"))["total"] == 0
    # switching organisation requires membership
    r = other_tenant.post("/auth/switch-organization", {"organization_id": str(tenant.org_id)})
    assert r.status_code == 403
    # a forged token signed with another key is rejected
    import jwt

    forged = jwt.encode({"sub": other_tenant.owner["_user_id"], "org": str(tenant.org_id), "typ": "access", "exp": 9999999999}, "wrong-key", algorithm="HS256")
    assert client.get("/api/v1/products", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
