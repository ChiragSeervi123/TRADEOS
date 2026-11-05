from datetime import date, timedelta
from decimal import Decimal

from app import db as dbmod
from app.models import Organization
from app.services.reconciliation import run_rules
from tests.conftest import ok
from tests.helpers import approve_and_dispatch, place_order, setup_catalog
from tests.test_inbox import make_pdf

TODAY = date.today()


def invoice_text(number="SUP-9001", qty="100", price="42.00"):
    total_line = Decimal(qty) * Decimal(price)
    tax = (total_line * Decimal("0.18")).quantize(Decimal("0.01"))
    return [
        "Supplier: Hindustan Unilever (demo)",
        f"Invoice No: {number}",
        f"Invoice Date: {TODAY.isoformat()}",
        f"SKU-001 Surf Excel Detergent 1 kg {qty} {price} {total_line:.2f}",
        f"Subtotal: {total_line:.2f}",
        f"GST: {tax}",
        f"Grand Total: {total_line + tax:.2f}",
    ]


def test_invoice_upload_extract_verify_and_reconcile(tenant):
    c = setup_catalog(tenant, stock=None)
    # GRN references the supplier invoice number, but only 90 units were received.
    ok(tenant.post("/inventory/receipts", {"supplier_id": c["supplier"]["id"], "warehouse_id": c["wh"]["id"], "supplier_reference": "SUP-9001", "lines": [{"product_id": c["p"]["id"], "quantity": "90"}]}), 201)
    pi = ok(tenant.post("/purchase-invoices/upload", files={"file": ("inv.pdf", make_pdf(invoice_text()), "application/pdf")}), 201)
    assert pi["status"] == "needs_review" and pi["invoice_number"] == "SUP-9001"
    assert pi["supplier_id"] == c["supplier"]["id"] and pi["lines"][0]["product_id"] == c["p"]["id"]
    assert pi["total"] == "4956.00" and pi["validation"]["valid"] is True
    assert pi["extraction"]["provider"] == "demo_parser" and pi["document"]["url"]
    rep = tenant.user("sales_rep")
    assert tenant.get(f"/purchase-invoices/{pi['id']}", headers=rep).status_code == 403
    v = ok(tenant.post(f"/purchase-invoices/{pi['id']}/verify", {}))
    assert v["status"] == "verified"
    cats = {x["category"]: x for x in v["cases"]}
    # price 42 vs agreed 40 (5% > 0.5% tolerance); qty 100 invoiced vs 90 received
    assert set(cats) == {"price_mismatch", "quantity_mismatch"}
    case = ok(tenant.get(f"/reconciliation/cases/{cats['price_mismatch']['id']}"))
    assert case["expected_value"] == "4000.00" and case["observed_value"] == "4200.00" and case["difference"] == "200.00"
    assert case["explanation"]["lines"][0]["deviation_pct"] == "5.00"
    qcase = ok(tenant.get(f"/reconciliation/cases/{cats['quantity_mismatch']['id']}"))
    assert qcase["difference"] == "420.00" and qcase["matching_method"] == "exact_reference"
    # re-running rules does not duplicate cases
    assert ok(tenant.post("/reconciliation/run"))["new_cases"] == 0
    # workflow + linked exception
    exc = ok(tenant.get("/exceptions", params={"exception_type": "reconciliation_mismatch"}))
    assert exc["total"] == 2
    r = tenant.post(f"/reconciliation/cases/{case['id']}/transition", {"status": "resolved"})
    assert r.status_code == 422  # resolution note required
    ok(tenant.post(f"/reconciliation/cases/{case['id']}/transition", {"status": "investigating", "note": "Asked supplier for rate card"}))
    ok(tenant.client.post(f"/api/v1/reconciliation/cases/{case['id']}/notes", data={"note": "Supplier confirmed old rate applies"}, files={"file": ("mail.txt", b"Supplier email: price should be 40.00", "text/plain")}, headers=tenant.h(tenant.owner)), 201)
    done = ok(tenant.post(f"/reconciliation/cases/{case['id']}/transition", {"status": "resolved", "note": "Supplier issued debit note correction", "resolved_value": "200"}))
    assert done["status"] == "resolved" and done["resolved_value"] == "200.00" and {d["filename"] for d in done["documents"]} == {"inv.pdf", "mail.txt"}
    assert [h["event"] for h in done["history"]] == ["created", "status_change", "evidence", "status_change"]
    assert ok(tenant.get("/exceptions", params={"exception_type": "reconciliation_mismatch"}))["total"] == 1
    assert tenant.post(f"/reconciliation/cases/{case['id']}/transition", {"status": "awaiting_evidence"}).status_code == 409


def test_manual_invoice_arithmetic_and_duplicate(tenant):
    c = setup_catalog(tenant)
    body = {"supplier_id": c["supplier"]["id"], "invoice_number": "M-1", "invoice_date": TODAY.isoformat(), "lines": [{"product_id": c["p"]["id"], "description": "Surf", "quantity": "10", "unit_price": "40", "line_total": "410"}], "tax_total": "0"}
    pi = ok(tenant.post("/purchase-invoices", body), 201)
    codes = {i["code"] for i in pi["validation"]["issues"]}
    assert "LINE_ARITHMETIC" in codes
    r = tenant.post(f"/purchase-invoices/{pi['id']}/verify", {})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVOICE_ARITHMETIC"
    fixed = ok(tenant.patch(f"/purchase-invoices/{pi['id']}", {"lines": [{"product_id": c["p"]["id"], "description": "Surf", "quantity": "10", "unit_price": "40", "line_total": "400"}], "subtotal": "400", "total": "400"}))
    assert fixed["validation"]["valid"] is True
    assert tenant.post("/purchase-invoices", body).status_code == 409  # same supplier + number
    ok(tenant.post(f"/purchase-invoices/{pi['id']}/verify", {}))
    assert tenant.patch(f"/purchase-invoices/{pi['id']}", {"total": "1"}).status_code == 409  # verified is authoritative


def test_duplicate_file_creates_case(tenant):
    setup_catalog(tenant, stock=None)
    pdf = make_pdf(invoice_text("DUP-1", "10", "40.00"))
    a = ok(tenant.post("/purchase-invoices/upload", files={"file": ("a.pdf", pdf, "application/pdf")}), 201)
    b = ok(tenant.post("/purchase-invoices/upload", files={"file": ("b.pdf", pdf, "application/pdf")}), 201)
    assert b["invoice_number"].startswith("PENDING-")  # number clash kept for review
    assert any("already exists" in n for n in b["validation"]["extraction_notes"])
    ok(tenant.post(f"/purchase-invoices/{a['id']}/verify", {}))
    run = ok(tenant.post("/reconciliation/run"))
    assert run["duplicates"] == 1
    cases = ok(tenant.get("/reconciliation/cases", params={"category": "duplicate_document"}))
    assert cases["total"] == 1


def test_payments_allocation_invariants(tenant):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10"), 201)
    approve_and_dispatch(tenant, o["id"])
    inv = ok(tenant.get(f"/orders/{o['id']}"))["invoices"][0]  # 590.00
    hdr = {**tenant.owner, "Idempotency-Key": "pay-1"}
    p = ok(tenant.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "400", "received_on": TODAY.isoformat(), "method": "upi", "reference": "UPI123"}, headers=hdr), 201)
    again = ok(tenant.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "400", "received_on": TODAY.isoformat(), "method": "upi", "reference": "UPI123"}, headers=hdr), 201)
    assert again["id"] == p["id"] and again["idempotent_replay"] is True
    assert p["suggestions"][0]["amount"] == "400.00"
    r = tenant.post(f"/payments/{p['id']}/allocations", {"allocations": [{"sales_invoice_id": inv["id"], "amount": "500"}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "OVER_ALLOCATION_PAYMENT"
    out = ok(tenant.post(f"/payments/{p['id']}/allocations", {"allocations": [{"sales_invoice_id": inv["id"], "amount": "300"}]}), 201)
    assert out["unallocated"] == "100.00"
    si = ok(tenant.get(f"/sales-invoices/{inv['id']}"))
    assert si["outstanding"] == "290.00" and si["status"] == "partially_paid"
    p2 = ok(tenant.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "1000", "received_on": TODAY.isoformat()}), 201)
    r = tenant.post(f"/payments/{p2['id']}/allocations", {"allocations": [{"sales_invoice_id": inv["id"], "amount": "300"}]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "OVER_ALLOCATION_INVOICE"
    ok(tenant.post(f"/payments/{p2['id']}/allocations", {"allocations": [{"sales_invoice_id": inv["id"], "amount": "290"}]}), 201)
    assert ok(tenant.get(f"/sales-invoices/{inv['id']}"))["status"] == "paid"
    alloc_id = ok(tenant.get(f"/payments/{p2['id']}"))["allocations"][0]["id"]
    ok(tenant.post(f"/payments/{p2['id']}/allocations/{alloc_id}/reverse", {"reason": "Wrong invoice"}))
    assert ok(tenant.get(f"/sales-invoices/{inv['id']}"))["outstanding"] == "290.00"
    assert tenant.post(f"/payments/{p['id']}/void", {"reason": "test"}).status_code == 409
    acct = tenant.user("accountant")
    rep = tenant.user("sales_rep")
    assert tenant.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "1", "received_on": TODAY.isoformat()}, headers=rep).status_code == 403
    ok(tenant.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "1", "received_on": TODAY.isoformat()}, headers=acct), 201)
    ageing = ok(tenant.get("/receivables/ageing"))
    assert ageing["total_outstanding"] == "290.00" and ageing["retailers"][0]["total"] == "290.00"
    future = ok(tenant.get("/receivables/ageing", params={"as_of": (TODAY + timedelta(days=50)).isoformat()}))
    assert future["totals"]["31-60"] == "290.00"  # due in 15 days -> 35 days past due


def test_return_credit_note_flow_and_rule(tenant):
    c = setup_catalog(tenant)
    o = ok(place_order(tenant, c, qty="10"), 201)
    approve_and_dispatch(tenant, o["id"])
    inv = ok(tenant.get(f"/orders/{o['id']}"))["invoices"][0]
    r = ok(tenant.post("/returns", {"return_type": "customer", "warehouse_id": c["wh"]["id"], "retailer_id": c["retailer"]["id"], "sales_invoice_id": inv["id"], "lines": [{"product_id": c["p"]["id"], "quantity": "2"}]}), 201)
    ok(tenant.post(f"/returns/{r['id']}/receive"))
    s = dbmod.SessionLocal()
    org = s.get(Organization, tenant.org_id)
    out = run_rules(s, org, today=TODAY + timedelta(days=10))
    s.commit()
    s.close()
    assert out["returns"] == 1
    cn = ok(tenant.post("/credit-notes", {"direction": "issued", "retailer_id": c["retailer"]["id"], "return_id": r["id"], "reason": "return"}), 201)
    assert cn["amount"] == "100.00" and cn["status"] == "draft"
    ok(tenant.post(f"/credit-notes/{cn['id']}/issue"))
    assert ok(tenant.get(f"/returns/{r['id']}"))["status"] == "credited"
    ok(tenant.post(f"/credit-notes/{cn['id']}/apply", {"sales_invoice_id": inv["id"]}))
    assert ok(tenant.get(f"/sales-invoices/{inv['id']}"))["outstanding"] == "490.00"


def test_scheme_claim_credit_rules(tenant):
    from tests.test_schemes import activate, scheme_body

    c = setup_catalog(tenant)
    s = activate(tenant, scheme_body())
    o = ok(place_order(tenant, c, qty="10"), 201)
    approve_and_dispatch(tenant, o["id"])
    claim = ok(tenant.post("/schemes/claims", {"scheme_id": s["id"], "period_start": (TODAY - timedelta(days=1)).isoformat(), "period_end": TODAY.isoformat()}), 201)
    sess = dbmod.SessionLocal()
    org = sess.get(Organization, tenant.org_id)
    assert run_rules(sess, org, today=TODAY + timedelta(days=45))["claims"] == 1
    sess.commit()
    sess.close()
    cases = ok(tenant.get("/reconciliation/cases", params={"category": "missing_credit_note"}))
    assert cases["items"][0]["expected_value"] == "25.00"
    ok(tenant.post("/credit-notes", {"direction": "received", "supplier_id": c["supplier"]["id"], "note_number": "HUL-CN-1", "amount": "20", "reason": "scheme", "scheme_claim_id": claim["id"]}), 201)
    claims = ok(tenant.get("/schemes/claims"))
    assert claims[0]["status"] == "partially_credited"
    assert ok(tenant.post("/reconciliation/run"))["claims"] == 1  # scheme_benefit_mismatch 20 vs 25
    mm = ok(tenant.get("/reconciliation/cases", params={"category": "scheme_benefit_mismatch"}))["items"][0]
    assert mm["difference"] == "-5.00"


def test_unallocated_payment_rule_and_manual_match(tenant):
    c = setup_catalog(tenant)
    ok(tenant.post("/payments", {"retailer_id": c["retailer"]["id"], "amount": "750", "received_on": (TODAY - timedelta(days=10)).isoformat()}), 201)
    assert ok(tenant.post("/reconciliation/run"))["payments"] == 1
    grn = ok(tenant.get("/inventory/receipts"))["items"][0]
    pi = ok(tenant.post("/purchase-invoices", {"supplier_id": c["supplier"]["id"], "invoice_number": "X-1", "invoice_date": TODAY.isoformat(), "lines": []}), 201)
    m = ok(tenant.post("/reconciliation/matches", {"left_type": "purchase_invoice", "left_id": pi["id"], "right_type": "goods_receipt", "right_id": grn["id"], "note": "Confirmed by phone"}), 201)
    assert m["method"] == "manual" and m["status"] == "active"
    ok(tenant.post(f"/reconciliation/matches/{m['id']}/unmatch", {"reason": "Wrong GRN"}))
    assert ok(tenant.get("/reconciliation/matches", params={"status": "unmatched"}))[0]["id"] == m["id"]


def test_exception_queue_workflow_and_visibility(tenant):
    c = setup_catalog(tenant, credit_limit="100")
    ok(place_order(tenant, c, qty="10"), 201)  # credit limit exceeded -> exception
    lst = ok(tenant.get("/exceptions"))
    exc = next(e for e in lst["items"] if e["exception_type"] == "credit_limit_exceeded")
    assert exc["suggested_action"]
    wh = tenant.user("warehouse_operator")
    assert all(e["exception_type"] != "credit_limit_exceeded" for e in ok(tenant.get("/exceptions", headers=wh))["items"])
    assert tenant.get(f"/exceptions/{exc['id']}", headers=wh).status_code == 404
    acct = tenant.user("accountant")
    ok(tenant.post(f"/exceptions/{exc['id']}/assign", {"user_id": acct["_user_id"]}))
    ok(tenant.post(f"/exceptions/{exc['id']}/comments", {"body": "Calling the retailer"}, headers=acct), 201)
    assert tenant.post(f"/exceptions/{exc['id']}/resolve", {"reason": "x"}, headers=wh).status_code == 403
    res = ok(tenant.post(f"/exceptions/{exc['id']}/resolve", {"reason": "Retailer paid overdue balance"}, headers=acct))
    assert res["status"] == "resolved" and res["comments"][0]["body"] == "Calling the retailer"
    re = ok(tenant.post(f"/exceptions/{exc['id']}/reopen", {"reason": "Payment bounced"}))
    assert re["status"] == "open" and [h["action"] for h in re["history"]][-1] == "exception.reopen"
