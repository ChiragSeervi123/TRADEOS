import json
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.extraction import demo_parser
from app.models import ExceptionItem
from tests.conftest import ok
from tests.helpers import setup_catalog


def make_pdf(text_lines: list[str]) -> bytes:
    """Minimal valid single-page PDF with real text (for pypdf extraction)."""
    content = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({l.replace('(', '').replace(')', '')}) Tj T*" for l in text_lines) + " ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_demo_parser_formats():
    r = demo_parser.parse_order(
        "Hi bhaiya, Sharma General Store here\n5 case surf excel 1kg\nDove soap 100g - 12 pcs\n10 x maggi noodles\n1kg tata salt 3 pcs\ndeliver by tomorrow\nthanks",
        today=date(2026, 11, 5),
    )
    assert r.retailer.name == "Sharma General Store"
    got = [(l.product_text.lower(), str(l.quantity), l.unit) for l in r.lines]
    assert ("surf excel 1kg", "5", "CASE") in got
    assert ("dove soap 100g", "12", "PCS") in got
    assert ("maggi noodles", "10", None) in got
    assert ("1kg tata salt", "3", "PCS") in got
    assert r.requested_delivery_date == date(2026, 11, 6)
    empty = demo_parser.parse_order("hello there")
    assert not empty.lines and any("No order lines" in w for w in empty.warnings)


def test_demo_invoice_and_scheme_parsers():
    inv = demo_parser.parse_invoice("Supplier: Example FMCG Ltd\nInvoice No: INV-778\nInvoice Date: 2026-11-01\nSKU-001 Surf Excel 1kg 10 40.00 400.00\nSubtotal: 400.00\nGST: 72.00\nGrand Total: 472.00")
    assert inv.invoice_number == "INV-778" and inv.invoice_date == date(2026, 11, 1)
    assert inv.lines[0].sku_hint == "SKU-001" and str(inv.lines[0].quantity) == "10"
    assert str(inv.total) == "472.00" and str(inv.tax_total) == "72.00"
    sch = demo_parser.parse_scheme("Scheme: Surf Diwali Bonanza\nBuy 10 get 1 free on Surf range\nValid from 01/11/2026 to 30/11/2026", ["Surf", "Dove"])
    c = sch.schemes[0]
    assert c.scheme_type == "buy_x_get_y" and c.brands == ["Surf"] and c.start_date == date(2026, 11, 1)


def test_text_order_to_reviewed_draft(tenant):
    c = setup_catalog(tenant)
    src = ok(tenant.post("/order-extractions/text", {"text": "Sharma General Store here\n2 case surf excel 1kg\nDove soap 100g - 6 pcs"}), 201)
    assert src["status"] == "needs_review"
    res = src["extraction"]["result"]
    assert src["extraction"]["provider"] == "demo_parser"
    assert res["retailer"]["selected_retailer_id"] == c["retailer"]["id"]
    l0, l1 = res["lines"]
    assert l0["status"] == "matched" and l0["selected_product_id"] == c["p"]["id"] and l0["base_quantity"] == "24.000"
    assert l1["selected_product_id"] == c["p2"]["id"]
    lines = [{"index": l["index"], "product_id": l["selected_product_id"], "quantity": l["quantity"], "unit": l["unit"]} for l in res["lines"]]
    lines[1]["quantity"] = "8"  # user correction
    order = ok(tenant.post(f"/order-extractions/{src['id']}/convert", {"retailer_id": c["retailer"]["id"], "lines": lines}), 201)
    assert order["status"] == "validated" and order["channel"] == "text"
    prov = {l["sku"]: l["provenance"] for l in order["lines"]}
    assert prov == {"SKU-001": "extracted", "SKU-002": "corrected"}
    assert order["lines"][1]["extracted_values"]["quantity"] == "6"
    # converting twice is refused (idempotent)
    r = tenant.post(f"/order-extractions/{src['id']}/convert", {"retailer_id": c["retailer"]["id"], "lines": lines})
    assert r.status_code == 409 and r.json()["error"]["details"]["order_id"] == order["id"]


def test_ambiguous_sku_requires_explicit_confirmation(tenant, db):
    c = setup_catalog(tenant)
    p75 = ok(tenant.post("/products", {"sku": "SKU-003", "name": "Dove Soap", "brand": "Dove", "variant": "75 g", "allow_duplicate": True}), 201)
    src = ok(tenant.post("/order-extractions/text", {"text": "From: Sharma General Store\ndove soap - 5 pcs"}), 201)
    line = src["extraction"]["result"]["lines"][0]
    assert line["status"] == "ambiguous" and line["selected_product_id"] is None
    assert {x["sku"] for x in line["sku_candidates"]} >= {"SKU-002", "SKU-003"}
    body = {"retailer_id": c["retailer"]["id"], "lines": [{"index": 0, "product_id": p75["id"], "quantity": "5", "unit": "PCS"}]}
    r = tenant.post(f"/order-extractions/{src['id']}/convert", body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "AMBIGUOUS_LINE_UNCONFIRMED"
    body["lines"][0]["confirm"] = True
    order = ok(tenant.post(f"/order-extractions/{src['id']}/convert", body), 201)
    assert order["lines"][0]["provenance"] == "corrected" and order["status"] == "draft"  # no price for SKU-003
    exc = db.scalars(select(ExceptionItem).where(ExceptionItem.exception_type == "unmatched_master_data")).first()
    assert exc is not None


def test_unknown_retailer_and_duplicate_text(tenant, db):
    setup_catalog(tenant)
    text = "Order from: Totally Unknown Mart\n3 case surf excel 1kg"
    a = ok(tenant.post("/order-extractions/text", {"text": text}), 201)
    assert a["extraction"]["result"]["retailer"]["selected_retailer_id"] is None
    b = ok(tenant.post("/order-extractions/text", {"text": text + "\n"}), 201)
    assert b["possible_duplicate_of"] == a["id"]
    assert db.scalars(select(ExceptionItem).where(ExceptionItem.exception_type == "duplicate")).first()


def test_image_without_provider_fails_gracefully_then_manual(tenant):
    c = setup_catalog(tenant)
    src = ok(tenant.post("/order-extractions/upload", files={"file": ("order.png", PNG, "image/png")}), 201)
    assert src["status"] == "failed" and "manual" in src["extraction"]["error"]
    assert src["document"]["url"].startswith("/api/v1/documents/")
    # document is downloadable only via a signed URL
    assert tenant.client.get(src["document"]["url"]).status_code == 200
    assert tenant.client.get(src["document"]["url"].replace("signature=", "signature=00")).status_code == 403
    order = ok(tenant.post(f"/order-extractions/{src['id']}/convert", {"retailer_id": c["retailer"]["id"], "lines": [{"index": None, "product_id": c["p"]["id"], "quantity": "2"}]}), 201)
    assert order["lines"][0]["provenance"] == "manual" and order["channel"] == "image"


def test_pdf_order_text_extraction(tenant):
    c = setup_catalog(tenant)
    pdf = make_pdf(["Purchase order - Sharma General Store", "3 case surf excel 1kg", "Dove soap 100g - 24 pcs"])
    src = ok(tenant.post("/order-extractions/upload", files={"file": ("po.pdf", pdf, "application/pdf")}), 201)
    assert src["channel"] == "pdf" and src["status"] == "needs_review"
    lines = src["extraction"]["result"]["lines"]
    assert [l["selected_product_id"] for l in lines] == [c["p"]["id"], c["p2"]["id"]]


def test_upload_validation(tenant):
    r = tenant.post("/order-extractions/upload", files={"file": ("x.exe", b"MZ....", "application/octet-stream")})
    assert r.status_code == 415
    r = tenant.post("/order-extractions/upload", files={"file": ("fake.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "CONTENT_MISMATCH"
    rep_wh = tenant.user("warehouse_operator")
    assert tenant.post("/order-extractions/text", {"text": "1 case x"}, headers=rep_wh).status_code == 403


# ---------------------------------------------------------------- Claude adapter (mocked SDK)


class FakeMessages:
    def __init__(self, response=None, exc=None):
        self.response, self.exc, self.calls = response, exc, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return self.response


def _fake_client(monkeypatch, response=None, exc=None):
    msgs = FakeMessages(response, exc)
    fake = SimpleNamespace(beta=SimpleNamespace(messages=msgs))
    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: fake)
    return msgs


def _resp(payload=None, stop="end_turn", text=None):
    body = text if text is not None else json.dumps(payload)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=body)], stop_reason=stop, stop_details=SimpleNamespace(category="cyber") if stop == "refusal" else None, usage=SimpleNamespace(input_tokens=1200, output_tokens=300), model="claude-opus-5-5", _request_id="req_test")


@pytest.fixture
def ai_enabled(tenant, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    ok(tenant.patch("/organizations/current", {"ai_external_enabled": True}))
    st = ok(tenant.get("/order-extractions/provider-status"))
    assert st["status"] == "configured" and st["verified"] is False


def test_anthropic_adapter_success(tenant, monkeypatch, ai_enabled):
    c = setup_catalog(tenant)
    payload = {
        "schema_version": "1.0",
        "retailer": {"name": "Sharma General Store", "phone": None, "code": None, "confidence": 0.92},
        "lines": [{"source_description": "surf 1kg 2 peti", "product_text": "Surf Excel 1kg", "quantity": 2, "unit": "case", "sku_hint": "SKU-001", "confidence": 0.9}],
        "requested_delivery_date": None,
        "notes": [],
        "warnings": [],
    }
    msgs = _fake_client(monkeypatch, _resp(payload))
    src = ok(tenant.post("/order-extractions/upload", files={"file": ("photo.png", PNG, "image/png")}), 201)
    assert src["status"] == "needs_review"
    ext = src["extraction"]
    assert ext["provider"] == "anthropic" and ext["input_tokens"] == 1200
    assert ext["result"]["lines"][0]["selected_product_id"] == c["p"]["id"]
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["messages"][0]["content"][0]["type"] == "image"


def test_anthropic_adapter_refusal_and_invalid_output(tenant, monkeypatch, ai_enabled):
    setup_catalog(tenant)
    _fake_client(monkeypatch, _resp({}, stop="refusal"))
    src = ok(tenant.post("/order-extractions/text", {"text": "2 case surf"}), 201)
    assert src["status"] == "failed" and "declined" in src["extraction"]["error"]
    _fake_client(monkeypatch, _resp(text='{"lines": "nope"}'))
    src = ok(tenant.post("/order-extractions/text", {"text": "3 case surf"}), 201)
    assert src["status"] == "failed" and "schema validation" in src["extraction"]["error"]


def test_org_without_consent_never_calls_provider(tenant, monkeypatch):
    setup_catalog(tenant)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    msgs = _fake_client(monkeypatch, _resp({}))
    src = ok(tenant.post("/order-extractions/text", {"text": "2 case surf excel 1kg"}), 201)
    assert src["extraction"]["provider"] == "demo_parser" and msgs.calls == []
