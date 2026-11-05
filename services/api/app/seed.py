"""Deterministic synthetic demo data (fixed seed). ALL DATA IS SYNTHETIC.

    python -m app.seed                 # full demo (1 distributor, 1 manufacturer, 1000+ orders)
    python -m app.seed --scale small   # quick demo
    python -m app.seed --reset         # SQLite + development only: recreate the database first

Records are created through the real services (pricing, schemes, reservations,
dispatch, invoices, allocations, reconciliation), so every balance and relation
is consistent. Brands, suppliers, retailers and people are fictional. Injected
anomalies are written to data/synthetic/demo_ground_truth.json.
"""

import argparse
import json
import random
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, update

from app import db as dbmod
from app.config import get_settings
from app.db import Base, utcnow
from app.deps import Ctx
from app.models import (
    DataSharingGrant,
    Dispatch,
    InventoryMovement,
    Membership,
    Order,
    OrderStatusEvent,
    Organization,
    Payment,
    PickList,
    PriceList,
    PriceListItem,
    Product,
    Retailer,
    ReturnRecord,
    SalesInvoice,
    Scheme,
    SchemeClaim,
    SchemeVersion,
    Supplier,
    UnitConversion,
    User,
    Warehouse,
)
from app.money import money, qty
from app.permissions import ALL_PERMISSIONS
from app.security import hash_password

DEMO_PASSWORD = "Demo@12345"
SEED = 42
GROUND_TRUTH = Path(__file__).resolve().parents[3] / "data" / "synthetic" / "demo_ground_truth.json"

SUPPLIERS = [
    ("SUNR", "Sunrise Consumer Products (demo)", {"Brightwash": "Home Care", "Snowdrop": "Home Care"}),
    ("SPRK", "Spark Home Care (demo)", {"Sparkle": "Home Care", "Shinex": "Home Care"}),
    ("LOTS", "Lotus Personal Care (demo)", {"Lotus Glow": "Personal Care", "Herbalis": "Personal Care"}),
    ("NOVA", "Nova Foods (demo)", {"Crunchies": "Biscuits & Snacks", "Nova Noodles": "Packaged Foods"}),
    ("HIMB", "Himalaya Beverages (demo)", {"Himtea": "Beverages", "Coolsip": "Beverages"}),
    ("GNGA", "Ganga Staples (demo)", {"Ganga Atta": "Staples", "Ganga Salt": "Staples", "Golden Dal": "Staples"}),
    ("DFRS", "Dairyfresh Co-op (demo)", {"Dairyfresh": "Dairy"}),
    ("SNKW", "Snackwell Foods (demo)", {"Snackwell": "Biscuits & Snacks", "Masala Munch": "Biscuits & Snacks"}),
]
LINES = {
    "Home Care": [("Detergent Powder", ["500 g", "1 kg", "2 kg"]), ("Dishwash Bar", ["200 g", "500 g"]), ("Floor Cleaner", ["500 ml", "1 l"]), ("Liquid Detergent", ["1 l", "2 l"])],
    "Personal Care": [("Beauty Soap", ["75 g", "100 g", "150 g"]), ("Shampoo", ["180 ml", "340 ml"]), ("Toothpaste", ["100 g", "150 g"]), ("Face Wash", ["50 ml", "100 ml"])],
    "Biscuits & Snacks": [("Glucose Biscuits", ["100 g", "250 g"]), ("Cream Biscuits", ["75 g", "150 g"]), ("Potato Chips", ["52 g", "90 g"])],
    "Packaged Foods": [("Instant Noodles", ["70 g", "280 g"]), ("Pasta", ["400 g"])],
    "Beverages": [("Premium Tea", ["250 g", "500 g", "1 kg"]), ("Mango Drink", ["200 ml", "600 ml", "1.2 l"])],
    "Staples": [("Chakki Atta", ["5 kg", "10 kg"]), ("Iodised Salt", ["1 kg"]), ("Toor Dal", ["500 g", "1 kg"])],
    "Dairy": [("Butter", ["100 g", "500 g"]), ("Ghee", ["500 ml", "1 l"]), ("Milk Powder", ["500 g"])],
}
TAX = {"Home Care": 18, "Personal Care": 18, "Biscuits & Snacks": 18, "Packaged Foods": 12, "Beverages": 12, "Staples": 5, "Dairy": 12}
SURNAMES = ["Sharma", "Gupta", "Agarwal", "Patel", "Reddy", "Iyer", "Khan", "Singh", "Jain", "Mehta", "Nair", "Das", "Yadav", "Verma", "Rao", "Kulkarni", "Bose", "Pillai"]
SHOPS = ["General Store", "Kirana", "Provision Stores", "Traders", "Supermart", "Mart", "Enterprises", "Medical & General"]
AREAS = {"North": ["Model Town", "Rohini", "Civil Lines"], "South": ["Jayanagar", "Adyar", "Kukatpally"], "East": ["Salt Lake", "Howrah", "Kasba"], "West": ["Andheri", "Kothrud", "Navrangpura"], "Central": ["Arera Colony", "Vijay Nagar"]}
ROLES = ["org_owner", "org_admin", "operations_manager", "accountant", "sales_rep", "warehouse_operator"]


def _ctx(db, org, user) -> Ctx:
    return Ctx(db=db, user=user, org=org, role="org_owner", permissions=frozenset(ALL_PERMISSIONS))


def _dt(d: date, hour: float) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC) + timedelta(hours=hour)


def seed_org_data(db, org: Organization, user: User, scale: str = "small") -> dict:
    """Populate an existing (empty) distributor organisation with synthetic data."""
    from app.services import fulfilment, inventory, receivables, reconciliation, scheduled
    from app.services import orders as order_svc
    from app.services import schemes as scheme_svc
    from app.services.extraction import ingest_text
    from app.services.jobs import run_pending_inline

    rng = random.Random(SEED)
    full = scale == "full"
    n_retailers, n_orders, days_back = (150, 1050, 90) if full else (30, 120, 45)
    ctx = _ctx(db, org, user)
    today = date.today()
    truth: list[dict] = []
    org.is_demo = True

    # ---------------------------------------------------------------- master data
    whs = []
    for i, (code, name) in enumerate([("MAIN", "Main Warehouse (demo)"), ("NORTH", "North Depot (demo)"), ("SOUTH", "South Depot (demo)")]):
        w = Warehouse(organization_id=org.id, code=code, name=name, is_default=i == 0)
        db.add(w)
        whs.append(w)
    db.flush()
    suppliers, products = {}, []
    for code, name, brands in SUPPLIERS:
        s = Supplier(organization_id=org.id, code=code, name=name, email=f"orders@{code.lower()}.example", is_synthetic=True)
        db.add(s)
        db.flush()
        suppliers[code] = s
        for brand, cat in brands.items():
            lines = LINES[cat] if full else LINES[cat][:2]
            for pname, sizes in lines:
                for size in sizes if full else sizes[:2]:
                    cost = money(Decimal(rng.randint(16, 700)) / 2)
                    p = Product(
                        organization_id=org.id,
                        sku=f"{code}-{brand[:3].upper()}-{len(products) + 1:03d}",
                        name=f"{brand} {pname}",
                        brand=brand,
                        category=cat,
                        product_group=f"{brand} {pname}",
                        variant=size,
                        base_unit="PCS",
                        tax_rate=Decimal(TAX[cat]),
                        cost_price=cost,
                        mrp=money(cost * Decimal("1.45")),
                        barcode=f"890{rng.randint(10**9, 10**10 - 1)}",
                        supplier_id=s.id,
                        is_synthetic=True,
                    )
                    db.add(p)
                    products.append(p)
    db.flush()
    std = db.scalars(select(PriceList).where(PriceList.organization_id == org.id, PriceList.is_default.is_(True))).first()
    for p in products:
        db.add(PriceListItem(organization_id=org.id, price_list_id=std.id, product_id=p.id, unit_price=money(p.cost_price * Decimal(rng.choice(["1.12", "1.15", "1.18", "1.22"])))))
        db.add(UnitConversion(organization_id=org.id, product_id=p.id, unit_code="CASE", factor=Decimal(rng.choice([12, 24, 36, 48]))))
    retailers = []
    terr_list = list(AREAS)
    for i in range(n_retailers):
        terr = terr_list[i % len(terr_list)]
        name = f"{rng.choice(SURNAMES)} {rng.choice(SHOPS)}"
        r = Retailer(
            organization_id=org.id,
            code=f"R-{i + 1:04d}",
            name=f"{name}, {rng.choice(AREAS[terr])}",
            contact_name=f"{rng.choice(['Ramesh', 'Suresh', 'Anita', 'Farhan', 'Lakshmi', 'Vikram', 'Pooja', 'Arjun'])} (demo)",
            phone=f"90000{i:05d}",
            territory=terr,
            route=f"{terr[:1]}-{(i % 4) + 1}",
            credit_limit=None if i % 9 == 0 else Decimal(rng.choice([150000, 250000, 400000, 600000])),
            payment_terms_days=rng.choice([7, 15, 30]),
            delivery_address=f"Shop {i + 1}, {rng.choice(AREAS[terr])} (synthetic address)",
            is_synthetic=True,
        )
        db.add(r)
        retailers.append(r)
    tight = retailers[1]
    tight.credit_limit = Decimal("5000")
    truth.append({"scenario": "credit_limit_breach_setup", "retailer": tight.code, "credit_limit": "5000"})
    db.flush()

    # ---------------------------------------------------------------- opening stock
    for w in whs:
        for p in products:
            if w.code != "MAIN" and rng.random() < 0.4:
                continue
            base = rng.randint(200, 900) if w.code == "MAIN" else rng.randint(40, 200)
            exp_days = rng.choice([25, 120, 240, 400])
            b = inventory.get_or_create_batch(db, org.id, p.id, f"B{p.sku[-3:]}{w.code[:1]}1", today + timedelta(days=exp_days))
            inventory.post(db, org.id, warehouse_id=w.id, product_id=p.id, batch_id=b.id, movement_type="opening_balance", quantity=Decimal(base), actor_id=user.id, source_type="seed", reason="Opening balance (synthetic)", is_synthetic=True)
    low = products[3]
    main = whs[0]
    bal = inventory.ledger_balance(db, org.id, main.id, low.id)
    inventory.post(db, org.id, warehouse_id=main.id, product_id=low.id, movement_type="write_off", quantity=-(bal.on_hand - Decimal(6)), actor_id=user.id, source_type="adjustment", reason="Water damage in storage (synthetic scenario)", evidence="Photo ref DMG-001 (synthetic)")
    truth.append({"scenario": "inventory_adjustment_with_reason", "sku": low.sku, "reason": "Water damage in storage"})
    expired = products[5]
    eb = inventory.get_or_create_batch(db, org.id, expired.id, "EXP-OLD", today - timedelta(days=3))
    inventory.post(db, org.id, warehouse_id=main.id, product_id=expired.id, batch_id=eb.id, movement_type="purchase_receipt", quantity=Decimal(20), actor_id=user.id, source_type="seed", reason="Old batch (synthetic)")
    truth.append({"scenario": "expired_stock", "sku": expired.sku, "batch": "EXP-OLD"})

    # ---------------------------------------------------------------- schemes
    def make_scheme(code, name, stype, cfg, scope, elig=None, start=None, end=None, approve=True, sup="SUNR"):
        s = Scheme(organization_id=org.id, code=code, name=name, supplier_id=suppliers[sup].id, funded_by="supplier", created_by_id=user.id, is_synthetic=True)
        db.add(s)
        db.flush()
        data = scheme_svc.validate_version_fields(ctx, {"scheme_type": stype, "config": cfg, "scope": scope, "eligibility": elig or {}, "start_date": start or today - timedelta(days=days_back + 5), "end_date": end or today + timedelta(days=60), "stacking": "exclusive"})
        v = SchemeVersion(organization_id=org.id, scheme_id=s.id, version=1, status="draft", created_by_id=user.id, source_reference=f"Synthetic circular {code}", **data)
        db.add(v)
        db.flush()
        if approve:
            scheme_svc.submit(ctx, s, v)
            scheme_svc.approve(ctx, s, v)
        return s

    slab = make_scheme("BW-SLAB", "Brightwash volume slab (demo)", "quantity_slab", {"slabs": [{"min_quantity": "24", "percent": "2"}, {"min_quantity": "72", "percent": "4"}]}, {"brands": ["Brightwash"]})
    north = make_scheme("CR-NORTH", "Crunchies North promo (demo)", "percentage", {"percent": "5"}, {"brands": ["Crunchies"]}, {"territories": ["North"]}, sup="NOVA")
    make_scheme("CS-BXGY", "Coolsip buy 12 get 1 (demo)", "buy_x_get_y", {"buy_quantity": "12", "free_quantity": "1"}, {"brands": ["Coolsip"]}, sup="HIMB")
    make_scheme("LG-NEXT", "Lotus Glow next-month offer (demo, pending)", "percentage", {"percent": "3"}, {"brands": ["Lotus Glow"]}, start=today + timedelta(days=10), approve=False, sup="LOTS")
    truth.append({"scenario": "valid_quantity_scheme", "scheme": "BW-SLAB"})
    truth.append({"scenario": "ineligible_scheme", "scheme": "CR-NORTH", "rule": "only North territory retailers qualify"})
    db.flush()

    db.commit()

    # ---------------------------------------------------------------- orders
    order_meta = []
    errors: list[str] = []
    start_date = today - timedelta(days=days_back)
    by_brand: dict = {}
    for p in products:
        by_brand.setdefault(p.brand, []).append(p)
    for i in range(n_orders):
        od = start_date + timedelta(days=int(days_back * i / n_orders))
        r = retailers[rng.randrange(len(retailers))]
        if r is tight:
            r = retailers[2]
        wh = main if rng.random() < 0.8 else rng.choice(whs[1:])
        picks = rng.sample(products, rng.randint(1, 4))
        if rng.random() < 0.15:
            picks[0] = rng.choice(by_brand["Brightwash"])
        lines = []
        for p in picks:
            case = rng.random() < 0.25
            lines.append({"product_id": p.id, "quantity": rng.choice([1, 2]) if case else rng.choice([6, 12, 18, 24]), "unit": "CASE" if case else "PCS"})
        if i % 40 == 0:  # periodic replenishment keeps the synthetic history realistic
            for w in whs:
                for p in products:
                    b = inventory.ledger_balance(db, org.id, w.id, p.id)
                    if b.available < 150:
                        inventory.post(db, org.id, warehouse_id=w.id, product_id=p.id, movement_type="purchase_receipt", quantity=Decimal(400), actor_id=user.id, source_type="seed", reason="Replenishment (synthetic)", is_synthetic=True)
        age = (today - od).days
        fate = rng.random()
        sp = db.begin_nested()
        try:
            o = order_svc.create_order(ctx, retailer_id=r.id, warehouse_id=wh.id, lines=lines, order_date=od, is_synthetic=True)
            if o.status == "validated" and fate > 0.03 and age > 0:
                inner = db.begin_nested()
                try:
                    order_svc.approve(ctx, o)
                    inner.commit()
                except Exception:  # noqa: BLE001 - blocked by stock/credit: stays as a reviewable order
                    inner.rollback()
                    sp.commit()
                    order_meta.append((o.id, od))
                    continue
                pl = db.scalars(select(PickList).where(PickList.order_id == o.id)).first()
                if pl and age > 1 and fate > 0.08:
                    if fate < 0.12:  # partial picks
                        for ln in pl.lines:
                            ln.picked_quantity = qty(ln.requested_quantity * Decimal("0.5")).to_integral_value()
                    d, _ = fulfilment.dispatch(ctx, pl, o)
                    if age > 3 and fate > 0.15 and o.status == "dispatched":
                        fulfilment.deliver(ctx, d, o, "Delivered (synthetic)")
            elif fate <= 0.03:
                order_svc.cancel(ctx, o, "Retailer cancelled (synthetic)")
            sp.commit()
        except Exception as exc:  # noqa: BLE001 - an unexpected combination skips this order only
            sp.rollback()
            if len(errors) < 5:
                errors.append(f"{type(exc).__name__}: {exc}")
            continue
        order_meta.append((o.id, od))
        if i % 50 == 0:
            db.commit()
    db.commit()

    # scenario orders (left for review)
    dup_lines = [{"product_id": products[10].id, "quantity": 12, "unit": "PCS"}, {"product_id": products[11].id, "quantity": 6, "unit": "PCS"}]
    d1 = order_svc.create_order(ctx, retailer_id=retailers[4].id, warehouse_id=main.id, lines=dup_lines, order_date=today, is_synthetic=True)
    d2 = order_svc.create_order(ctx, retailer_id=retailers[4].id, warehouse_id=main.id, lines=dup_lines, order_date=today, is_synthetic=True)
    truth.append({"scenario": "duplicate_order", "orders": [d1.order_number, d2.order_number]})
    ins = order_svc.create_order(ctx, retailer_id=retailers[5].id, warehouse_id=main.id, lines=[{"product_id": low.id, "quantity": 50, "unit": "PCS"}], order_date=today, is_synthetic=True)
    truth.append({"scenario": "insufficient_stock", "order": ins.order_number, "sku": low.sku})
    big = order_svc.create_order(ctx, retailer_id=tight.id, warehouse_id=main.id, lines=[{"product_id": products[0].id, "quantity": 5, "unit": "CASE"}], order_date=today, is_synthetic=True)
    truth.append({"scenario": "credit_limit_breach", "order": big.order_number, "retailer": tight.code})
    db.commit()

    # ---------------------------------------------------------------- backdate timestamps
    def backdate():
        for oid, od in order_meta:
            o = db.get(Order, oid)
            created = _dt(od, 9 + rng.random() * 3)
            o.created_at = created
            if o.approved_at:
                o.approved_at = created + timedelta(hours=rng.choice([0.5, 1, 2, 4, 6]))
            disp = db.scalars(select(Dispatch).where(Dispatch.order_id == oid)).all()
            for k, d in enumerate(disp):
                dd = min(od + timedelta(days=1 + k), today)
                d.dispatched_at = _dt(dd, 15)
                if d.delivered_at:
                    d.delivered_at = _dt(min(dd + timedelta(days=1), today), 12)
                inv = db.scalars(select(SalesInvoice).where(SalesInvoice.dispatch_id == d.id)).first()
                if inv:
                    retailer = db.get(Retailer, inv.retailer_id)
                    inv.invoice_date = dd
                    inv.due_date = dd + timedelta(days=retailer.payment_terms_days)
                db.execute(update(InventoryMovement).where(InventoryMovement.source_type == "dispatch", InventoryMovement.source_id == str(d.id)).values(created_at=_dt(dd, 15)))
            if disp:
                o.dispatched_at = disp[0].dispatched_at
            if o.delivered_at and disp:
                o.delivered_at = disp[-1].delivered_at or o.delivered_at
            db.execute(update(InventoryMovement).where(InventoryMovement.source_type == "order", InventoryMovement.source_id == str(oid)).values(created_at=o.approved_at or created))
            db.execute(update(OrderStatusEvent).where(OrderStatusEvent.order_id == oid, OrderStatusEvent.action == "create").values(created_at=created))
        db.commit()

    backdate()

    # ---------------------------------------------------------------- payments
    invoices = db.scalars(select(SalesInvoice).where(SalesInvoice.organization_id == org.id).order_by(SalesInvoice.invoice_date)).all()
    partial_done = False
    for inv in invoices:
        if inv.due_date > today + timedelta(days=5):
            continue
        roll = rng.random()
        if roll < 0.62:
            amount = inv.total
        elif roll < 0.8:
            amount = money(inv.total * Decimal("0.4"))
        else:
            continue
        p = Payment(organization_id=org.id, payment_number=f"PAY-{len(invoices) + 1:06d}-{inv.invoice_number[-6:]}", retailer_id=inv.retailer_id, amount=amount, currency=org.currency, received_on=min(inv.due_date - timedelta(days=rng.randint(0, 5)), today), method=rng.choice(["upi", "bank_transfer", "cheque", "cash"]), reference=f"SYN{rng.randint(100000, 999999)}", created_by_id=user.id, is_synthetic=True)
        db.add(p)
        db.flush()
        receivables.allocate(db, org.id, p, inv, amount, user.id)
        if amount < inv.total and not partial_done:
            truth.append({"scenario": "partial_payment", "invoice": inv.invoice_number, "paid": str(amount), "total": str(inv.total)})
            partial_done = True
    stray = Payment(organization_id=org.id, payment_number="PAY-UNALLOC-1", retailer_id=retailers[6].id, amount=Decimal("12500.00"), currency=org.currency, received_on=today - timedelta(days=12), method="bank_transfer", reference="NEFT-SYN-777", created_by_id=user.id, is_synthetic=True)
    db.add(stray)
    truth.append({"scenario": "unmatched_payment", "payment": "PAY-UNALLOC-1"})
    db.commit()

    # ---------------------------------------------------------------- returns & credit notes
    from app.services import finance

    delivered_invs = [i for i in invoices if i.status != "void"][:40]
    r_ok = fulfilment.create_return(ctx, return_type="customer", warehouse_id=main.id, retailer_id=delivered_invs[0].retailer_id, sales_invoice_id=delivered_invs[0].id, lines=[{"product_id": delivered_invs[0].lines[0].product_id, "quantity": 1, "condition": "damaged"}], reason="Damaged pack (synthetic)", is_synthetic=True)
    fulfilment.receive_return(ctx, r_ok)
    cn = finance.create_note(ctx, {"direction": "issued", "note_type": "credit", "retailer_id": r_ok.retailer_id, "return_id": r_ok.id, "reason": "return", "note_date": today})
    finance.issue_note(ctx, cn)
    r_wait = fulfilment.create_return(ctx, return_type="customer", warehouse_id=main.id, retailer_id=delivered_invs[1].retailer_id, sales_invoice_id=delivered_invs[1].id, lines=[{"product_id": delivered_invs[1].lines[0].product_id, "quantity": 2, "condition": "sellable"}], reason="Wrong item delivered (synthetic)", is_synthetic=True)
    fulfilment.receive_return(ctx, r_wait)
    r_wait.received_at = utcnow() - timedelta(days=12)
    truth.append({"scenario": "return_awaiting_credit", "return": r_wait.return_number})
    db.commit()

    # ---------------------------------------------------------------- purchases (GRN + supplier invoices)
    from app.models import GoodsReceipt, GoodsReceiptLine, PurchaseInvoice

    pi_count = 0
    for code, s in suppliers.items():
        sp = [p for p in products if p.supplier_id == s.id][:4]
        for k, offset in enumerate((40, 12)):
            ref = f"{code}-INV-{2600 + k}"
            rd = today - timedelta(days=offset)
            g = GoodsReceipt(organization_id=org.id, receipt_number=f"GRN-{code}-{k}", supplier_id=s.id, warehouse_id=main.id, supplier_reference=ref, received_on=rd, created_by_id=user.id, is_synthetic=True)
            db.add(g)
            db.flush()
            pi = PurchaseInvoice(organization_id=org.id, supplier_id=s.id, invoice_number=ref, invoice_date=rd, due_date=rd + timedelta(days=30), currency=org.currency, status="needs_review", created_by_id=user.id, is_synthetic=True)
            db.add(pi)
            db.flush()
            lines = []
            for p in sp:
                q = Decimal(rng.choice([48, 96, 144]))
                db.add(GoodsReceiptLine(organization_id=org.id, goods_receipt_id=g.id, product_id=p.id, quantity=q, unit_cost=p.cost_price))
                inventory.post(db, org.id, warehouse_id=main.id, product_id=p.id, movement_type="purchase_receipt", quantity=q, actor_id=user.id, source_type="goods_receipt", source_id=g.id, reason=f"GRN {g.receipt_number}", is_synthetic=True)
                inv_q, price = q, p.cost_price
                if code == "SUNR" and k == 1 and p is sp[0]:
                    price = money(p.cost_price * Decimal("1.06"))
                    truth.append({"scenario": "price_mismatch", "invoice": ref, "sku": p.sku, "agreed": str(p.cost_price), "invoiced": str(price)})
                if code == "NOVA" and k == 1 and p is sp[0]:
                    inv_q = q + 12
                    truth.append({"scenario": "quantity_mismatch", "invoice": ref, "sku": p.sku, "received": str(q), "invoiced": str(inv_q)})
                lines.append({"product_id": p.id, "description": f"{p.name} {p.variant}", "quantity": inv_q, "unit_price": price, "tax_rate": p.tax_rate})
            finance.set_invoice_lines(db, org.id, pi, lines)
            pi.subtotal = money(sum((l.line_total for l in pi.lines), Decimal(0)))
            pi.tax_total = money(sum((l.line_total * l.tax_rate / 100 for l in pi.lines), Decimal(0)))
            pi.total = money(pi.subtotal + pi.tax_total)
            pi.validation = finance.validate_invoice(org, pi)
            finance.verify_invoice(ctx, pi)
            pi_count += 1
    dup_src = db.scalars(select(PurchaseInvoice).where(PurchaseInvoice.organization_id == org.id, PurchaseInvoice.invoice_number == "GNGA-INV-2601")).first()
    dup = PurchaseInvoice(organization_id=org.id, supplier_id=dup_src.supplier_id, invoice_number="GNGA-INV-2601-A", invoice_date=dup_src.invoice_date, due_date=dup_src.due_date, subtotal=dup_src.subtotal, tax_total=dup_src.tax_total, total=dup_src.total, currency=org.currency, status="needs_review", created_by_id=user.id, is_synthetic=True)
    db.add(dup)
    truth.append({"scenario": "duplicate_document", "invoices": ["GNGA-INV-2601", "GNGA-INV-2601-A"]})
    db.commit()

    # ---------------------------------------------------------------- scheme claims
    try:
        claim = scheme_svc.generate_claim(ctx, slab, start_date, today - timedelta(days=35))
        claim.status = "submitted"
        claim.submitted_at = utcnow() - timedelta(days=34)
        truth.append({"scenario": "missing_credit_note", "claim": claim.claim_number, "expected": str(claim.expected_amount)})
    except Exception:  # noqa: BLE001
        db.rollback()
    try:
        c2 = scheme_svc.generate_claim(ctx, slab, today - timedelta(days=34), today)
        c2.status = "submitted"
        c2.submitted_at = utcnow()
        finance.create_note(ctx, {"direction": "received", "note_type": "credit", "supplier_id": suppliers["SUNR"].id, "note_number": "SUNR-CN-881", "amount": c2.expected_amount, "reason": "scheme", "scheme_claim_id": c2.id, "note_date": today})
    except Exception:  # noqa: BLE001
        db.rollback()
    db.commit()

    # ---------------------------------------------------------------- inbox scenarios
    samples = [
        ("correct", f"{retailers[7].name.split(',')[0]} here\n2 case {products[0].name} {products[0].variant}\n12 pcs {products[1].name} {products[1].variant}"),
        ("ambiguous_sku", f"From: {retailers[8].name.split(',')[0]}\n10 pcs {products[0].brand} {products[0].name.split()[-1].lower()}"),
        ("unknown_retailer", f"Order from: Brand New Mart Bhopal\n5 case {products[2].name} {products[2].variant}"),
    ]
    for label, text in samples:
        src, job_ids = ingest_text(ctx, text, sender="+91 90000 99999 (synthetic)", is_synthetic=True)
        db.commit()
        run_pending_inline(job_ids)
        truth.append({"scenario": f"inbox_{label}", "source_id": str(src.id)})
    db.expire_all()
    org = db.get(Organization, org.id)

    # ---------------------------------------------------------------- rules + scans
    summary_rules = reconciliation.run_rules(db, org, actor_id=user.id)
    scheduled.scan_expiry(db, org)
    db.commit()
    counts = {
        "products": len(products),
        "retailers": len(retailers),
        "suppliers": len(suppliers),
        "warehouses": len(whs),
        "orders": db.query(Order).filter(Order.organization_id == org.id).count(),
        "sales_invoices": db.query(SalesInvoice).filter(SalesInvoice.organization_id == org.id).count(),
        "purchase_invoices": pi_count + 1,
        "payments": db.query(Payment).filter(Payment.organization_id == org.id).count(),
        "returns": db.query(ReturnRecord).filter(ReturnRecord.organization_id == org.id).count(),
        "claims": db.query(SchemeClaim).filter(SchemeClaim.organization_id == org.id).count(),
        "reconciliation": summary_rules,
        "skipped_orders_sample_errors": errors,
    }
    try:
        GROUND_TRUTH.parent.mkdir(parents=True, exist_ok=True)
        GROUND_TRUTH.write_text(json.dumps({"note": "SYNTHETIC demo anomalies with ground-truth labels (seed 42).", "organization": org.slug, "scale": scale, "generated_at": utcnow().isoformat(), "scenarios": truth}, indent=2), encoding="utf-8")
    except OSError:
        pass
    _ = (north, d2, DataSharingGrant)
    return {"scale": scale, "counts": counts, "scenarios": len(truth)}


def seed_demo(scale: str = "full") -> dict:
    from app.routers.organizations import bootstrap_org

    db = dbmod.SessionLocal()
    try:
        if db.scalars(select(Organization).where(Organization.is_demo.is_(True), Organization.org_type == "distributor")).first():
            return {"status": "exists", "message": "Demo organisation already seeded. Use --reset (SQLite/development only) to recreate."}
        pw = hash_password(DEMO_PASSWORD)
        org = Organization(name="Demo Distributors Pvt Ltd (SYNTHETIC)", slug="demo-distributor", org_type="distributor", is_demo=True, settings={}, address="Synthetic demo address, Indore")
        db.add(org)
        db.flush()
        bootstrap_org(db, org)
        users = {}
        for role in ROLES:
            u = User(email=f"{role.replace('_', '.')}@demo.tradeos.example", full_name=f"Demo {role.replace('_', ' ').title()}", password_hash=pw)
            db.add(u)
            db.flush()
            db.add(Membership(user_id=u.id, organization_id=org.id, role=role))
            users[role] = u
        db.commit()
        t0 = time.time()
        summary = seed_org_data(db, org, users["org_owner"], scale)
        mfr = Organization(name="Demo Manufacturer Ltd (SYNTHETIC)", slug="demo-manufacturer", org_type="manufacturer", is_demo=True, settings={})
        db.add(mfr)
        db.flush()
        mu = User(email="manufacturer.manager@demo.tradeos.example", full_name="Demo Manufacturer Manager", password_hash=pw)
        db.add(mu)
        db.flush()
        db.add(Membership(user_id=mu.id, organization_id=mfr.id, role="manufacturer_manager"))
        org = db.get(Organization, org.id)
        db.add(DataSharingGrant(organization_id=org.id, grantee_organization_id=mfr.id, scope={"metrics": ["sales", "stock", "schemes"], "brands": ["Brightwash", "Snowdrop"], "include_retailer_detail": False}, granted_by_id=users["org_owner"].id))
        db.commit()
        summary["seconds"] = round(time.time() - t0, 1)
        summary["logins"] = {r: f"{r.replace('_', '.')}@demo.tradeos.example" for r in ROLES} | {"manufacturer_manager": "manufacturer.manager@demo.tradeos.example"}
        summary["password"] = DEMO_PASSWORD
        return summary
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scale", choices=["small", "full"], default="full")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    if args.reset:
        if s.is_production or not s.is_sqlite or s.environment not in ("development", "test"):
            raise SystemExit("--reset is only allowed for a SQLite database in development/test.")
        Base.metadata.drop_all(dbmod.engine)
        from sqlalchemy import text

        with dbmod.engine.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS alembic_version"))
    from app.migrate import upgrade

    upgrade()
    print(json.dumps(seed_demo(args.scale), indent=2, default=str))


if __name__ == "__main__":
    main()
