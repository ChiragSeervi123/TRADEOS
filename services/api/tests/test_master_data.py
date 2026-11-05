from sqlalchemy import select

from app.models import AuditEvent, InventoryMovement
from tests.conftest import ok


def make_product(t, sku="SKU-1", name="Surf Excel Detergent", variant="1 kg", **kw):
    body = {"sku": sku, "name": name, "brand": "Surf", "category": "Home Care", "variant": variant, "tax_rate": "18", "cost_price": "90", **kw}
    return ok(t.post("/products", body), 201)


def test_product_crud_and_audit(tenant, db):
    p = make_product(tenant)
    assert p["sku"] == "SKU-1" and p["version"] == 1
    r = tenant.post("/products", {"sku": "sku-1", "name": "Other"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DUPLICATE_SKU"
    upd = ok(tenant.patch(f"/products/{p['id']}", {"cost_price": "92.50", "version": 1}))
    assert upd["cost_price"] == "92.50" and upd["version"] == 2
    # stale version -> conflict
    r = tenant.patch(f"/products/{p['id']}", {"name": "X", "version": 1})
    assert r.status_code == 409
    actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.entity_id == p["id"])).all()]
    assert "product.create" in actions and "product.price_change" in actions
    lst = ok(tenant.get("/products", params={"q": "surf"}))
    assert lst["total"] == 1
    detail = ok(tenant.get(f"/products/{p['id']}"))
    assert detail["units"][0]["unit"] == "PCS"


def test_duplicate_suggestions_require_confirmation(tenant):
    make_product(tenant)
    r = tenant.post("/products", {"sku": "SKU-2", "name": "Surf Excel Detergent", "variant": "1kg"})
    assert r.status_code == 409
    body = r.json()["error"]
    assert body["code"] == "POSSIBLE_DUPLICATE" and body["details"]["candidates"][0]["sku"] == "SKU-1"
    ok(tenant.post("/products", {"sku": "SKU-2", "name": "Surf Excel Detergent", "variant": "1kg", "allow_duplicate": True}), 201)
    # different pack size is not a duplicate
    ok(tenant.post("/products", {"sku": "SKU-3", "name": "Surf Excel Detergent", "variant": "500 g"}), 201)


def test_permissions_on_master_data(tenant):
    rep = tenant.user("sales_rep")
    wh = tenant.user("warehouse_operator")
    assert tenant.post("/products", {"sku": "X1", "name": "Thing"}, headers=rep).status_code == 403
    assert tenant.post("/products", {"sku": "X1", "name": "Thing"}, headers=wh).status_code == 403
    ok(tenant.get("/products", headers=rep))
    r = ok(tenant.post("/retailers", {"code": "R1", "name": "Sharma General Store", "credit_limit": "50000"}), 201)
    lst = ok(tenant.get("/retailers", headers=rep))
    assert lst["items"][0]["credit_limit"] is None  # reps don't see credit terms
    ops = tenant.user("operations_manager")
    assert tenant.patch(f"/retailers/{r['id']}", {"credit_limit": "1"}, headers=ops).status_code == 403
    acct = tenant.user("accountant")
    assert tenant.put(f"/retailers/{r['id']}/credit-terms", {"credit_limit": "1", "reason": "x y z"}, headers=ops).status_code == 403
    out = ok(tenant.put(f"/retailers/{r['id']}/credit-terms", {"credit_limit": "60000", "reason": "Annual review"}, headers=acct))
    assert out["credit_limit"] == "60000.00"


def test_retailer_duplicates_and_units(tenant):
    ok(tenant.post("/retailers", {"code": "R1", "name": "Sharma General Store", "phone": "+91 98765 43210"}), 201)
    r = tenant.post("/retailers", {"code": "R2", "name": "Totally Different", "phone": "9876543210"})
    assert r.json()["error"]["code"] == "POSSIBLE_DUPLICATE"
    p = make_product(tenant)
    ok(tenant.post("/units/conversions", {"unit_code": "CASE", "factor": "24", "product_id": p["id"]}), 201)
    units = ok(tenant.get(f"/products/{p['id']}"))["units"]
    assert {"unit": "CASE", "factor": "24.0000"} in units
    assert tenant.post("/units/conversions", {"unit_code": "NOPE", "factor": "2"}).status_code == 422


def test_warehouse_default_and_price_list(tenant):
    a = ok(tenant.post("/warehouses", {"code": "main", "name": "Main"}), 201)
    assert a["is_default"] is True
    b = ok(tenant.post("/warehouses", {"code": "WH2", "name": "Second", "is_default": True}), 201)
    whs = ok(tenant.get("/warehouses"))
    assert [w["is_default"] for w in whs if w["id"] == a["id"]] == [False] and b["is_default"]
    p = make_product(tenant)
    pls = ok(tenant.get("/price-lists"))
    std = next(x for x in pls if x["name"] == "Standard")
    ok(tenant.put(f"/price-lists/{std['id']}/items", {"items": [{"product_id": p["id"], "unit_price": "99.50"}]}))
    items = ok(tenant.get(f"/price-lists/{std['id']}/items"))
    assert items["items"][0]["unit_price"] == "99.50"


def _upload(t, entity, csv_text, headers=None):
    return t.post(f"/imports/{entity}/preview", files={"file": ("data.csv", csv_text.encode(), "text/csv")}, headers=headers)


def test_csv_import_row_errors_and_all_or_nothing(tenant):
    csv_text = "SKU Code,Product Name,Brand,Tax Rate,Base Unit\nA1,Alpha Soap,Lux,18,PCS\nA2,,Lux,abc,PCS\nA1,Dup,Lux,5,PCS\nA3,Gamma,Lux,5,KGX\n"
    prev = ok(_upload(tenant, "products", csv_text), 201)
    assert prev["total_rows"] == 4 and prev["valid_rows"] == 1
    fields = {(e["row"], e["field"]) for e in prev["errors"]}
    assert (3, "name") in fields and (3, "tax_rate") in fields and (4, "sku") in fields and (5, "base_unit") in fields
    r = tenant.post(f"/imports/{prev['id']}/commit", {"skip_invalid": False})
    assert r.status_code == 422 and r.json()["error"]["code"] == "IMPORT_HAS_ERRORS"
    assert ok(tenant.get("/products"))["total"] == 0  # nothing imported silently
    res = ok(tenant.post(f"/imports/{prev['id']}/commit", {"skip_invalid": True}))
    assert res["imported_rows"] == 1 and res["skipped_rows"] == [3, 4, 5]
    assert ok(tenant.get("/products"))["total"] == 1
    # cannot commit twice
    assert tenant.post(f"/imports/{prev['id']}/commit", {"skip_invalid": True}).status_code == 409


def test_csv_opening_stock_import_posts_ledger(tenant, db):
    ok(tenant.post("/warehouses", {"code": "MAIN", "name": "Main"}), 201)
    p = make_product(tenant, sku="S1")
    ok(tenant.post("/units/conversions", {"unit_code": "CASE", "factor": "12", "product_id": p["id"]}), 201)
    csv_text = "sku,warehouse_code,quantity,unit,batch_number,expiry_date\nS1,MAIN,2,CASE,B1,2027-01-31\nS1,MAIN,5,,B2,31-12-2027\n"
    prev = ok(_upload(tenant, "opening_stock", csv_text), 201)
    assert prev["errors"] == []
    ok(tenant.post(f"/imports/{prev['id']}/commit", {}))
    movs = db.scalars(select(InventoryMovement)).all()
    assert sorted(str(m.quantity) for m in movs) == ["24.000", "5.000"]
    detail = ok(tenant.get(f"/products/{p['id']}"))
    assert detail["stock"][0]["on_hand"] == "29.000"


def test_import_requires_entity_permission(tenant):
    rep = tenant.user("sales_rep")
    assert _upload(tenant, "products", "sku,name\nA,B\n", headers=tenant.h(rep)).status_code == 403
    acct = tenant.user("accountant")
    assert _upload(tenant, "products", "sku,name\nA,B\n", headers=tenant.h(acct)).status_code == 403


def test_template_and_export(tenant, db):
    r = tenant.get("/imports/templates/products")
    assert r.status_code == 200 and r.text.startswith("sku,name")
    make_product(tenant)
    r = tenant.get("/exports/products")
    assert r.status_code == 200 and "SKU-1" in r.text
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "data.export")).first()
    rep = tenant.user("sales_rep")
    assert tenant.get("/exports/products", headers=rep).status_code == 403
