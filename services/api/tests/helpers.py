"""Shared setup for workflow tests."""

from tests.conftest import ok


def setup_catalog(t, stock="100", price="50.00", credit_limit=None, tax="18"):
    wh = ok(t.post("/warehouses", {"code": "MAIN", "name": "Main Warehouse"}), 201)
    p = ok(t.post("/products", {"sku": "SKU-001", "name": "Surf Excel Detergent", "brand": "Surf", "category": "Home Care", "variant": "1 kg", "tax_rate": tax, "cost_price": "40.00"}), 201)
    p2 = ok(t.post("/products", {"sku": "SKU-002", "name": "Dove Soap", "brand": "Dove", "category": "Personal Care", "variant": "100 g", "tax_rate": "18", "cost_price": "30.00"}), 201)
    ok(t.post("/units/conversions", {"unit_code": "CASE", "factor": "12", "product_id": p["id"]}), 201)
    std = next(x for x in ok(t.get("/price-lists")) if x["name"] == "Standard")
    ok(t.put(f"/price-lists/{std['id']}/items", {"items": [{"product_id": p["id"], "unit_price": price}, {"product_id": p2["id"], "unit_price": "35.00"}]}))
    sup = ok(t.post("/suppliers", {"code": "HUL", "name": "Hindustan Unilever (demo)"}), 201)
    if stock:
        ok(t.post("/inventory/receipts", {"supplier_id": sup["id"], "warehouse_id": wh["id"], "supplier_reference": "OPEN-1", "lines": [
            {"product_id": p["id"], "quantity": stock, "batch_number": "B1", "expiry_date": "2027-06-30"},
            {"product_id": p2["id"], "quantity": stock, "batch_number": "D1", "expiry_date": "2027-03-31"},
        ]}), 201)
    body = {"code": "R001", "name": "Sharma General Store", "territory": "North", "payment_terms_days": 15}
    if credit_limit is not None:
        body["credit_limit"] = credit_limit
    r = ok(t.post("/retailers", body), 201)
    return {"wh": wh, "p": p, "p2": p2, "retailer": r, "supplier": sup, "price_list": std}


def place_order(t, c, qty="10", unit=None, headers=None, key=None, extra_lines=None):
    lines = [{"product_id": c["p"]["id"], "quantity": qty, **({"unit": unit} if unit else {})}] + (extra_lines or [])
    hdrs = dict(headers or t.owner)
    if key:
        hdrs["Idempotency-Key"] = key
    return t.post("/orders", {"retailer_id": c["retailer"]["id"], "lines": lines}, headers=hdrs)


def approve_and_dispatch(t, order_id, picks=None):
    o = ok(t.post(f"/orders/{order_id}/approve", {}))
    pl_id = o["pick_lists"][-1]["id"]
    if picks is not None:
        pl = ok(t.get(f"/fulfilment/pick-lists/{pl_id}"))
        ok(t.post(f"/fulfilment/pick-lists/{pl_id}/picks", {"picks": {pl["lines"][i]["id"]: q for i, q in enumerate(picks)}}))
    d = ok(t.post(f"/fulfilment/pick-lists/{pl_id}/dispatch", {}))
    return o, pl_id, d
