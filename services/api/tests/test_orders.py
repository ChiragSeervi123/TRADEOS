from decimal import Decimal

from sqlalchemy import select

from app.models import AuditEvent, InventoryMovement
from app.services import inventory
from tests.conftest import ok
from tests.helpers import approve_and_dispatch, place_order, setup_catalog


def stock_of(t, c):
    rows = ok(t.get("/inventory/balances", params={"q": c["p"]["sku"]}))["items"]
    return rows[0]


def test_manual_order_pricing_units_and_tax(tenant):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="2", unit="CASE"), 201)
    line = o["lines"][0]
    assert line["base_quantity"] == "24.000" and line["unit_price"] == "50.00"
    assert o["totals"] == {"gross": "1200.00", "scheme_discount": "0.00", "manual_discount": "0.00", "taxable": "1200.00", "tax": "216.00", "net": "1416.00"}
    assert o["status"] == "validated"
    assert o["timeline"][0]["action"] == "create"


def test_order_idempotent_create(tenant):
    c = setup_catalog(tenant)
    a = ok(place_order(tenant, c, key="abc-123"), 201)
    b = ok(place_order(tenant, c, key="abc-123"), 201)
    assert a["id"] == b["id"] and b["idempotent_replay"] is True
    assert ok(tenant.get("/orders"))["total"] == 1
    r = place_order(tenant, c, qty="11", key="abc-123")
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_full_order_to_delivery_flow_with_ledger(tenant, db):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10"), 201)
    approved, pl_id, d = approve_and_dispatch(tenant, o["id"])
    assert approved["status"] == "approved"
    s = stock_of(tenant, c)
    assert s["on_hand"] == "90.000" and s["reserved"] == "0.000" and s["available"] == "90.000"
    detail = ok(tenant.get(f"/orders/{o['id']}"))
    assert detail["status"] == "dispatched" and detail["invoices"][0]["total"] == "590.00"
    ok(tenant.post(f"/fulfilment/dispatches/{d['dispatch_id']}/delivery", {"outcome": "delivered"}))
    assert ok(tenant.get(f"/orders/{o['id']}"))["status"] == "delivered"
    assert inventory.verify_balances(db, tenant.org_id) == []
    actions = {e.action for e in db.scalars(select(AuditEvent)).all()}
    assert {"order.create", "order.approve", "fulfilment.dispatch", "fulfilment.deliver"} <= actions


def test_reservation_cannot_exceed_available(tenant):
    c = setup_catalog(tenant, stock="10")
    a = ok(place_order(tenant, c, qty="8"), 201)
    ok(tenant.post(f"/orders/{a['id']}/approve", {}))
    b = ok(place_order(tenant, c, qty="5"), 201)
    issues = {i["code"] for i in b["validation"]["issues"]}
    assert "INSUFFICIENT_AVAILABLE_STOCK" in issues
    r = tenant.post(f"/orders/{b['id']}/approve", {})
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_AVAILABLE_STOCK"
    assert r.json()["error"]["details"]["available"] == "2.000"
    # override blocked by default policy
    r = tenant.post(f"/orders/{b['id']}/approve", {"stock_override_reason": "Urgent customer request"})
    assert r.json()["error"]["code"] == "OVERRIDE_NOT_PERMITTED"
    ok(tenant.patch("/organizations/current", {"settings": {"negative_stock_policy": "allow_with_override"}}))
    rep = tenant.user("accountant")  # no orders.approve
    assert tenant.post(f"/orders/{b['id']}/approve", {"stock_override_reason": "Urgent customer request"}, headers=rep).status_code == 403
    out = ok(tenant.post(f"/orders/{b['id']}/approve", {"stock_override_reason": "Urgent customer request"}))
    assert out["status"] == "approved" and out["stock_override_reason"] == "Urgent customer request"


def test_approve_is_idempotent_and_dispatch_not_double_counted(tenant, db):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10"), 201)
    first = ok(tenant.post(f"/orders/{o['id']}/approve", {}))
    second = ok(tenant.post(f"/orders/{o['id']}/approve", {}))
    assert second["already_approved"] is True
    assert stock_of(tenant, c)["reserved"] == "10.000"
    pl = first["pick_lists"][0]["id"]
    d1 = ok(tenant.post(f"/fulfilment/pick-lists/{pl}/dispatch", {}))
    d2 = ok(tenant.post(f"/fulfilment/pick-lists/{pl}/dispatch", {}))
    assert d1["dispatch_id"] == d2["dispatch_id"] and d2["already_dispatched"] is True
    assert stock_of(tenant, c)["on_hand"] == "90.000"
    dispatch_movs = db.scalars(select(InventoryMovement).where(InventoryMovement.movement_type == "dispatch")).all()
    assert len(dispatch_movs) == 1


def test_partial_fulfilment_backorder_and_close_short(tenant, db):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10"), 201)
    _, pl_id, d = approve_and_dispatch(tenant, o["id"], picks=["6"])
    detail = ok(tenant.get(f"/orders/{o['id']}"))
    assert detail["status"] == "partially_fulfilled"
    s = stock_of(tenant, c)
    assert s["on_hand"] == "94.000" and s["reserved"] == "4.000"
    inv1 = detail["invoices"][0]
    assert inv1["total"] == "354.00"  # 6 x 50 = 300 + 18%
    pl2 = ok(tenant.post(f"/orders/{o['id']}/backorder-pick-list"), 201)
    assert pl2["lines"][0]["requested_quantity"] == "4.000"
    ok(tenant.post(f"/fulfilment/pick-lists/{pl2['id']}/picks", {"picks": {pl2["lines"][0]["id"]: "1"}}))
    ok(tenant.post(f"/fulfilment/pick-lists/{pl2['id']}/dispatch", {}))
    detail = ok(tenant.get(f"/orders/{o['id']}"))
    assert detail["status"] == "partially_fulfilled"
    ok(tenant.post(f"/orders/{o['id']}/close-short", {"reason": "Customer accepted short supply"}))
    s = stock_of(tenant, c)
    assert s["reserved"] == "0.000" and s["on_hand"] == "93.000"
    # invoice totals never exceed order totals
    totals = sum(Decimal(i["total"]) for i in ok(tenant.get(f"/orders/{o['id']}"))["invoices"])
    assert totals == Decimal("413.00")
    assert inventory.verify_balances(db, tenant.org_id) == []


def test_cancel_releases_reservation_and_amend_flow(tenant):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10"), 201)
    ok(tenant.post(f"/orders/{o['id']}/approve", {}))
    assert stock_of(tenant, c)["reserved"] == "10.000"
    am = ok(tenant.post(f"/orders/{o['id']}/amend", {"reason": "Customer changed quantity"}))
    assert am["status"] in ("draft", "validated") and am["revision"] == 2 and len(am["revisions"]) == 1
    assert stock_of(tenant, c)["reserved"] == "0.000"
    upd = ok(tenant.patch(f"/orders/{o['id']}", {"lines": [{"product_id": c["p"]["id"], "quantity": "4"}]}))
    assert upd["lines"][0]["base_quantity"] == "4.000"
    ok(tenant.post(f"/orders/{o['id']}/approve", {}))
    assert stock_of(tenant, c)["reserved"] == "4.000"
    ok(tenant.post(f"/orders/{o['id']}/cancel", {"reason": "Retailer closed"}))
    assert stock_of(tenant, c)["reserved"] == "0.000"
    # a confirmed order cannot be edited directly
    o2 = ok(place_order(tenant, c, qty="1"), 201)
    ok(tenant.post(f"/orders/{o2['id']}/approve", {}))
    r = tenant.patch(f"/orders/{o2['id']}", {"notes": "x"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_EDITABLE"


def test_cannot_cancel_after_dispatch(tenant):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="2"), 201)
    approve_and_dispatch(tenant, o["id"])
    r = tenant.post(f"/orders/{o['id']}/cancel", {"reason": "too late"})
    assert r.status_code == 409


def test_credit_limit_blocks_then_authorised_override(tenant):
    c = setup_catalog(tenant, credit_limit="500")
    o = ok(place_order(tenant, c, qty="10"), 201)  # 590 > 500
    issue = next(i for i in o["validation"]["issues"] if i["code"] == "CREDIT_LIMIT_EXCEEDED")
    assert issue["details"]["exposure"] == "590.00"
    r = tenant.post(f"/orders/{o['id']}/approve", {})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CREDIT_LIMIT_EXCEEDED"
    out = ok(tenant.post(f"/orders/{o['id']}/approve", {"credit_override_reason": "Owner approved: cheque received"}))
    assert out["credit_override_reason"].startswith("Owner approved")


def test_validation_errors_block_approval(tenant):
    c = setup_catalog(tenant)
    p3 = ok(tenant.post("/products", {"sku": "NOPRICE", "name": "Unpriced Thing"}), 201)
    r = tenant.post("/orders", {"retailer_id": c["retailer"]["id"], "lines": [{"product_id": p3["id"], "quantity": "1"}, {"product_id": c["p"]["id"], "quantity": "1", "unit": "BOX"}]})
    o = ok(r, 201)
    codes = {i["code"] for i in o["validation"]["issues"]}
    assert {"PRICE_NOT_FOUND", "UNIT_CONVERSION_MISSING"} <= codes and o["status"] == "draft"
    r = tenant.post(f"/orders/{o['id']}/approve", {})
    assert r.status_code == 422 and r.json()["error"]["code"] == "ORDER_INVALID"


def test_price_override_needs_reason_and_flags_warning(tenant):
    c = setup_catalog(tenant)
    r = tenant.post("/orders", {"retailer_id": c["retailer"]["id"], "lines": [{"product_id": c["p"]["id"], "quantity": "1", "unit_price_override": "45"}]})
    assert r.status_code == 422
    o = ok(tenant.post("/orders", {"retailer_id": c["retailer"]["id"], "lines": [{"product_id": c["p"]["id"], "quantity": "1", "unit_price_override": "45", "price_override_reason": "Matched competitor"}]}), 201)
    assert o["lines"][0]["unit_price"] == "45.00"
    assert any(i["code"] == "PRICE_OVERRIDE" for i in o["validation"]["issues"])


def test_role_permissions_on_orders(tenant):
    c = setup_catalog(tenant)
    rep = tenant.user("sales_rep")
    wh = tenant.user("warehouse_operator")
    o = ok(place_order(tenant, c, qty="1", headers=rep), 201)
    assert tenant.post(f"/orders/{o['id']}/approve", {}, headers=rep).status_code == 403
    assert tenant.post(f"/orders/{o['id']}/approve", {}, headers=wh).status_code == 403
    assert place_order(tenant, c, qty="1", headers=wh).status_code == 403
    approved = ok(tenant.post(f"/orders/{o['id']}/approve", {}))
    pl = approved["pick_lists"][0]["id"]
    assert tenant.post(f"/fulfilment/pick-lists/{pl}/dispatch", {}, headers=rep).status_code == 403
    ok(tenant.post(f"/fulfilment/pick-lists/{pl}/dispatch", {}, headers=wh))
    # reps only see their own orders
    other = ok(place_order(tenant, c, qty="1"), 201)
    assert tenant.get(f"/orders/{other['id']}", headers=rep).status_code == 404
    assert ok(tenant.get("/orders", headers=rep))["total"] == 1


def test_customer_return_damaged_goods_blocked(tenant, db):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10"), 201)
    approve_and_dispatch(tenant, o["id"])
    inv = ok(tenant.get(f"/orders/{o['id']}"))["invoices"][0]
    r = ok(tenant.post("/returns", {"return_type": "customer", "warehouse_id": c["wh"]["id"], "retailer_id": c["retailer"]["id"], "order_id": o["id"], "sales_invoice_id": inv["id"], "reason": "Damaged in transit", "lines": [
        {"product_id": c["p"]["id"], "quantity": "2", "condition": "sellable"},
        {"product_id": c["p"]["id"], "quantity": "1", "condition": "damaged"},
    ]}), 201)
    assert r["total_value"] == "150.00"  # reference price from invoice: 50/unit net of discount
    ok(tenant.post(f"/returns/{r['id']}/receive"))
    s = stock_of(tenant, c)
    assert s["on_hand"] == "93.000" and s["blocked"] == "1.000" and s["available"] == "92.000"
    assert tenant.post(f"/returns/{r['id']}/receive").status_code == 409
    assert inventory.verify_balances(db, tenant.org_id) == []


def test_inventory_adjust_transfer_count(tenant, db):
    c = setup_catalog(tenant)
    wh2 = ok(tenant.post("/warehouses", {"code": "WH2", "name": "Second"}), 201)
    r = tenant.post("/inventory/adjustments", {"warehouse_id": c["wh"]["id"], "product_id": c["p"]["id"], "quantity": "-500", "reason": "test"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "NEGATIVE_STOCK"
    ok(tenant.post("/inventory/adjustments", {"warehouse_id": c["wh"]["id"], "product_id": c["p"]["id"], "quantity": "-3", "reason": "Breakage found"}), 201)
    ok(tenant.post("/inventory/transfers", {"from_warehouse_id": c["wh"]["id"], "to_warehouse_id": wh2["id"], "product_id": c["p"]["id"], "quantity": "7", "reason": "Rebalance"}), 201)
    sc = ok(tenant.post("/inventory/stock-counts", {"warehouse_id": c["wh"]["id"], "product_ids": [c["p"]["id"]]}), 201)
    assert sc["lines"][0]["system_quantity"] == "90.000"
    ok(tenant.patch(f"/inventory/stock-counts/{sc['id']}", {"counts": {sc["lines"][0]["id"]: "88"}}))
    res = ok(tenant.post(f"/inventory/stock-counts/{sc['id']}/post", {}))
    assert res["adjustments"][0]["delta"] == "-2.000"
    assert ok(tenant.get("/inventory/verify"))["consistent"] is True
    wh_op = tenant.user("warehouse_operator")
    rep = tenant.user("sales_rep")
    assert tenant.post("/inventory/adjustments", {"warehouse_id": c["wh"]["id"], "product_id": c["p"]["id"], "quantity": "1", "reason": "found"}, headers=rep).status_code == 403
    ok(tenant.post("/inventory/adjustments", {"warehouse_id": c["wh"]["id"], "product_id": c["p"]["id"], "quantity": "1", "reason": "found"}, headers=wh_op), 201)
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "inventory.manual_adjustment")).first()


def test_fefo_pick_list_batches(tenant):
    c = setup_catalog(tenant, stock=None)
    sup = c["supplier"]
    ok(tenant.post("/inventory/receipts", {"supplier_id": sup["id"], "warehouse_id": c["wh"]["id"], "lines": [
        {"product_id": c["p"]["id"], "quantity": "5", "batch_number": "LATE", "expiry_date": "2028-01-01"},
        {"product_id": c["p"]["id"], "quantity": "5", "batch_number": "EARLY", "expiry_date": "2027-01-01"},
    ]}), 201)
    o = ok(place_order(tenant, c, qty="7"), 201)
    a = ok(tenant.post(f"/orders/{o['id']}/approve", {}))
    pl = ok(tenant.get(f"/fulfilment/pick-lists/{a['pick_lists'][0]['id']}"))
    assert [(l["batch_number"], l["requested_quantity"]) for l in pl["lines"]] == [("EARLY", "5.000"), ("LATE", "2.000")]
