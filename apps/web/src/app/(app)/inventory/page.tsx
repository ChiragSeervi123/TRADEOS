"use client";

import { Download } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CsvImportButton } from "@/components/csv-import";
import { ProductPicker, SupplierPicker } from "@/components/pickers";
import { Alert, Button, Card, DataTable, Dialog, ErrorState, Field, Input, PageHeader, Pagination, Select, Spinner, Stat, StatusChip, SyntheticTag, Tabs, Textarea } from "@/components/ui";
import { api, download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, money, qty, titleCase } from "@/lib/format";
import { useAction, useApi, useDebounced, usePage } from "@/lib/hooks";

type Wh = { id: string; code: string; name: string; is_default: boolean; is_active: boolean };
type Bal = { warehouse_id: string; warehouse: string; product_id: string; sku: string; name: string; variant: string | null; base_unit: string; on_hand: string; reserved: string; blocked: string; available: string; stock_status: string; value?: string | null };
type Mov = { id: string; created_at: string; warehouse: string; sku: string; movement_type: string; bucket: string; quantity: string; unit: string; batch: string | null; source_type: string | null; reason: string | null; actor: string | null; is_synthetic: boolean };
type Batch = { warehouse: string; sku: string; name: string; batch_number: string; expiry_date: string | null; days_to_expiry: number | null; status: string; on_hand: string; batch_id: string };
type Res = { order_id: string; order_number: string; order_status: string; retailer: string; warehouse: string; sku: string; quantity: string };
type Grn = { id: string; receipt_number: string; supplier: string; supplier_reference: string | null; received_on: string; status: string; is_synthetic: boolean };
type Count = { id: string; count_number: string; status: string; created_at: string; posted_at: string | null };
type Tab = "balances" | "ledger" | "batches" | "reservations" | "receipts" | "counts";

export default function InventoryPage() {
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>("balances");
  const [wh, setWh] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [dlg, setDlg] = useState<null | "adjust" | "transfer" | "receipt" | "count">(null);
  const dq = useDebounced(q);
  const whs = useApi<Wh[]>("/warehouses");
  const bals = usePage<Bal>(tab === "balances" ? "/inventory/balances" : "", { warehouse_id: wh, q: dq, stock_status: status, page, page_size: 50 });
  const movs = usePage<Mov>(tab === "ledger" ? "/inventory/movements" : "", { warehouse_id: wh, page, page_size: 50 });
  const batches = useApi<Batch[]>(tab === "batches" ? "/inventory/batches" : null, { warehouse_id: wh });
  const res = useApi<Res[]>(tab === "reservations" ? "/inventory/reservations" : null, { warehouse_id: wh });
  const grns = usePage<Grn>(tab === "receipts" ? "/inventory/receipts" : "", { page, page_size: 25 });
  const counts = useApi<Count[]>(tab === "counts" ? "/inventory/stock-counts" : null);
  const verify = useApi<{ consistent: boolean; mismatches: unknown[] }>("/inventory/verify");
  const summary = bals.data?.summary as { skus: number; out_of_stock: number; low_stock: number; inventory_value?: string } | undefined;

  return (
    <>
      <PageHeader title="Inventory" description="Balances are derived from the append-only movement ledger: available = on hand − reserved − blocked." actions={
        <>
          {can("reports.export") && <Button onClick={() => download("/exports/stock")}><Download className="h-4 w-4" /> Export</Button>}
          {can("inventory.adjust") && <CsvImportButton entity="opening_stock" invalidate={["/inventory"]} />}
          {can("inventory.adjust") && <Button onClick={() => setDlg("receipt")}>Receive goods</Button>}
          {can("inventory.adjust") && <Button onClick={() => setDlg("transfer")}>Transfer</Button>}
          {can("inventory.adjust") && <Button variant="primary" onClick={() => setDlg("adjust")}>Adjust stock</Button>}
        </>
      } />
      {verify.data && !verify.data.consistent && <Alert tone="danger" title="Balance cache disagrees with the ledger">{verify.data.mismatches.length} row(s) differ. The ledger is authoritative; contact support.</Alert>}
      <Tabs value={tab} onChange={(t) => { setTab(t); setPage(1); }} tabs={[{ key: "balances", label: "Balances" }, { key: "ledger", label: "Movement ledger" }, { key: "batches", label: "Batches & expiry" }, { key: "reservations", label: "Reservations" }, { key: "receipts", label: "Goods receipts" }, { key: "counts", label: "Stock counts" }]} />
      {["balances", "ledger", "batches", "reservations"].includes(tab) && (
        <div className="mb-3 flex flex-wrap gap-2">
          <Select aria-label="Warehouse" value={wh} onChange={(e) => { setWh(e.target.value); setPage(1); }} className="max-w-xs">
            <option value="">All warehouses</option>
            {whs.data?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </Select>
          {tab === "balances" && <>
            <Input aria-label="Search" placeholder="Search SKU or name" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="max-w-xs" />
            <Select aria-label="Stock status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="max-w-[160px]">
              <option value="all">All</option><option value="in_stock">In stock</option><option value="low">Low (≤ 10)</option><option value="out">Out of stock</option>
            </Select>
          </>}
        </div>
      )}
      {tab === "balances" && summary && (
        <div className="mb-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="SKUs listed" value={summary.skus} />
          <Stat label="Out of stock" value={summary.out_of_stock} tone={summary.out_of_stock ? "danger" : undefined} />
          <Stat label="Low stock" value={summary.low_stock} tone={summary.low_stock ? "warning" : undefined} />
          {summary.inventory_value !== undefined && <Stat label="Value at agreed cost" value={money(summary.inventory_value, { compact: true })} />}
        </div>
      )}
      <Card padded={false}>
        {tab === "balances" && (bals.isPending ? <Spinner /> : bals.error ? <ErrorState error={bals.error} /> : <>
          <DataTable<Bal> dense rows={bals.data.items} rowKey={(r) => `${r.warehouse_id}${r.product_id}`} columns={[
            { key: "s", header: "SKU", cell: (r) => <Link href={`/products/${r.product_id}`} className="font-medium hover:underline">{r.sku}</Link> },
            { key: "n", header: "Product", cell: (r) => `${r.name} ${r.variant ?? ""}` },
            { key: "w", header: "Warehouse", cell: (r) => r.warehouse },
            { key: "o", header: "On hand", align: "right", cell: (r) => qty(r.on_hand) },
            { key: "r", header: "Reserved", align: "right", cell: (r) => qty(r.reserved) },
            { key: "b", header: "Blocked", align: "right", cell: (r) => qty(r.blocked) },
            { key: "a", header: "Available", align: "right", cell: (r) => <b>{qty(r.available, r.base_unit)}</b> },
            ...(bals.data.items[0]?.value !== undefined ? [{ key: "v", header: "Value", align: "right" as const, cell: (r: Bal) => money(r.value) }] : []),
            { key: "st", header: "Status", cell: (r) => <StatusChip status={r.stock_status === "low" ? "low_stock" : r.stock_status} label={r.stock_status === "low" ? "Low" : titleCase(r.stock_status)} /> },
          ]} />
          <Pagination page={page} pageSize={50} total={bals.data.total} onPage={setPage} />
        </>)}
        {tab === "ledger" && (movs.isPending ? <Spinner /> : movs.error ? <ErrorState error={movs.error} /> : <>
          <DataTable<Mov> dense rows={movs.data.items} rowKey={(r) => r.id} columns={[
            { key: "t", header: "When", cell: (r) => dateTime(r.created_at) },
            { key: "w", header: "Warehouse", cell: (r) => r.warehouse },
            { key: "s", header: "SKU", cell: (r) => <span>{r.sku} <SyntheticTag show={r.is_synthetic} /></span> },
            { key: "m", header: "Movement", cell: (r) => <span>{titleCase(r.movement_type)} <span className="text-xs text-muted">({r.bucket})</span></span> },
            { key: "q", header: "Qty", align: "right", cell: (r) => <span className={Number(r.quantity) < 0 ? "text-danger" : "text-success"}>{Number(r.quantity) > 0 ? "+" : ""}{qty(r.quantity, r.unit)}</span> },
            { key: "b", header: "Batch", cell: (r) => r.batch ?? "—" },
            { key: "r", header: "Reason / source", cell: (r) => <span className="text-xs">{r.reason ?? titleCase(r.source_type)}</span> },
            { key: "a", header: "By", cell: (r) => r.actor ?? "system" },
          ]} />
          <Pagination page={page} pageSize={50} total={movs.data.total} onPage={setPage} />
        </>)}
        {tab === "batches" && (batches.isPending ? <Spinner /> : batches.error ? <ErrorState error={batches.error} /> : (
          <DataTable<Batch> dense rows={batches.data} rowKey={(r) => `${r.batch_id}${r.warehouse}`} columns={[
            { key: "s", header: "SKU", cell: (r) => r.sku }, { key: "n", header: "Product", cell: (r) => r.name }, { key: "w", header: "Warehouse", cell: (r) => r.warehouse },
            { key: "b", header: "Batch", cell: (r) => r.batch_number }, { key: "e", header: "Expiry", cell: (r) => date(r.expiry_date) },
            { key: "d", header: "Days left", align: "right", cell: (r) => r.days_to_expiry ?? "—" }, { key: "q", header: "On hand", align: "right", cell: (r) => qty(r.on_hand) },
            { key: "st", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
          ]} empty={<p className="p-6 text-center text-sm text-muted">No batch-tracked stock.</p>} />
        ))}
        {tab === "reservations" && (res.isPending ? <Spinner /> : res.error ? <ErrorState error={res.error} /> : (
          <DataTable<Res> dense rows={res.data} rowKey={(r) => `${r.order_id}${r.sku}${r.warehouse}`} columns={[
            { key: "o", header: "Order", cell: (r) => <Link className="text-info hover:underline" href={`/orders/${r.order_id}`}>{r.order_number}</Link> },
            { key: "st", header: "Status", cell: (r) => <StatusChip status={r.order_status} /> }, { key: "r", header: "Retailer", cell: (r) => r.retailer },
            { key: "w", header: "Warehouse", cell: (r) => r.warehouse }, { key: "s", header: "SKU", cell: (r) => r.sku }, { key: "q", header: "Reserved", align: "right", cell: (r) => qty(r.quantity) },
          ]} empty={<p className="p-6 text-center text-sm text-muted">No active reservations.</p>} />
        ))}
        {tab === "receipts" && (grns.isPending ? <Spinner /> : grns.error ? <ErrorState error={grns.error} /> : <>
          <DataTable<Grn> dense rows={grns.data.items} rowKey={(r) => r.id} columns={[
            { key: "n", header: "GRN", cell: (r) => <span className="font-medium">{r.receipt_number} <SyntheticTag show={r.is_synthetic} /></span> }, { key: "s", header: "Supplier", cell: (r) => r.supplier },
            { key: "r", header: "Supplier invoice ref", cell: (r) => r.supplier_reference ?? "—" }, { key: "d", header: "Received", cell: (r) => date(r.received_on) }, { key: "st", header: "Status", cell: (r) => <StatusChip status={r.status === "posted" ? "verified" : r.status} label={titleCase(r.status)} /> },
          ]} />
          <Pagination page={page} pageSize={25} total={grns.data.total} onPage={setPage} />
        </>)}
        {tab === "counts" && (counts.isPending ? <Spinner /> : counts.error ? <ErrorState error={counts.error} /> : (
          <>
            {can("inventory.adjust") && <div className="border-b border-border p-3"><Button size="sm" onClick={() => setDlg("count")}>Start stock count</Button></div>}
            <DataTable<Count> dense rows={counts.data} rowKey={(r) => r.id} columns={[
              { key: "n", header: "Count", cell: (r) => <Link className="text-info hover:underline" href={`/inventory/counts/${r.id}`}>{r.count_number}</Link> },
              { key: "c", header: "Started", cell: (r) => dateTime(r.created_at) }, { key: "p", header: "Posted", cell: (r) => dateTime(r.posted_at) }, { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status === "posted" ? "verified" : r.status} label={titleCase(r.status)} /> },
            ]} empty={<p className="p-6 text-center text-sm text-muted">No stock counts yet.</p>} />
          </>
        ))}
      </Card>
      <AdjustDialog open={dlg === "adjust"} onClose={() => setDlg(null)} whs={whs.data ?? []} />
      <TransferDialog open={dlg === "transfer"} onClose={() => setDlg(null)} whs={whs.data ?? []} />
      <ReceiptDialog open={dlg === "receipt"} onClose={() => setDlg(null)} whs={whs.data ?? []} />
      <CountDialog open={dlg === "count"} onClose={() => setDlg(null)} whs={whs.data ?? []} />
    </>
  );
}

function WhSelect({ whs, value, onChange, label }: { whs: Wh[]; value: string; onChange: (v: string) => void; label: string }) {
  return <Field label={label} required>{(id) => <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}><option value="">Select…</option>{whs.filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</Select>}</Field>;
}

function AdjustDialog({ open, onClose, whs }: { open: boolean; onClose: () => void; whs: Wh[] }) {
  const [f, setF] = useState({ warehouse_id: "", product_id: null as string | null, movement_type: "manual_adjustment", quantity: "", reason: "", evidence: "", batch_number: "" });
  const m = useAction(() => api("/inventory/adjustments", { body: { ...f, evidence: f.evidence || null, batch_number: f.batch_number || null } }), { invalidate: ["/inventory", "/products"], success: "Stock adjusted", onSuccess: onClose });
  const signed = f.movement_type === "manual_adjustment";
  return (
    <Dialog open={open} onClose={onClose} title="Adjust stock" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.warehouse_id || !f.product_id || !Number(f.quantity) || f.reason.length < 3} loading={m.isPending} onClick={() => m.mutate(undefined)}>Post adjustment</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <WhSelect whs={whs} value={f.warehouse_id} onChange={(v) => setF({ ...f, warehouse_id: v })} label="Warehouse" />
        <Field label="Type">{(id) => <Select id={id} value={f.movement_type} onChange={(e) => setF({ ...f, movement_type: e.target.value })}><option value="manual_adjustment">Manual adjustment (±)</option><option value="write_off">Damage / write-off</option><option value="block">Block (quarantine)</option><option value="unblock">Unblock</option></Select>}</Field>
        <Field label="Product" required className="sm:col-span-2">{(id) => <ProductPicker id={id} value={f.product_id} onChange={(v) => setF({ ...f, product_id: v })} />}</Field>
        <Field label={signed ? "Change in base units (+/−)" : "Quantity (base units)"} required>{(id) => <Input id={id} type="number" step="any" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} />}</Field>
        <Field label="Batch (optional)">{(id) => <Input id={id} value={f.batch_number} onChange={(e) => setF({ ...f, batch_number: e.target.value })} />}</Field>
        <Field label="Reason" required className="sm:col-span-2">{(id) => <Textarea id={id} rows={2} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />}</Field>
        <Field label="Evidence reference (optional)" className="sm:col-span-2" hint="Photo ID, report number, etc.">{(id) => <Input id={id} value={f.evidence} onChange={(e) => setF({ ...f, evidence: e.target.value })} />}</Field>
      </div>
    </Dialog>
  );
}

function TransferDialog({ open, onClose, whs }: { open: boolean; onClose: () => void; whs: Wh[] }) {
  const [f, setF] = useState({ from_warehouse_id: "", to_warehouse_id: "", product_id: null as string | null, quantity: "", reason: "" });
  const m = useAction(() => api("/inventory/transfers", { body: f }), { invalidate: ["/inventory"], success: "Transfer posted", onSuccess: onClose });
  return (
    <Dialog open={open} onClose={onClose} title="Transfer between warehouses" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.from_warehouse_id || !f.to_warehouse_id || !f.product_id || !(Number(f.quantity) > 0) || f.reason.length < 3} loading={m.isPending} onClick={() => m.mutate(undefined)}>Transfer</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <WhSelect whs={whs} value={f.from_warehouse_id} onChange={(v) => setF({ ...f, from_warehouse_id: v })} label="From" />
        <WhSelect whs={whs} value={f.to_warehouse_id} onChange={(v) => setF({ ...f, to_warehouse_id: v })} label="To" />
        <Field label="Product" required className="sm:col-span-2">{(id) => <ProductPicker id={id} value={f.product_id} onChange={(v) => setF({ ...f, product_id: v })} />}</Field>
        <Field label="Quantity (base units)" required>{(id) => <Input id={id} type="number" min="0" step="any" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} />}</Field>
        <Field label="Reason" required>{(id) => <Input id={id} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />}</Field>
      </div>
    </Dialog>
  );
}

type RLine = { product_id: string | null; quantity: string; unit: string; batch_number: string; expiry_date: string; unit_cost: string };
function ReceiptDialog({ open, onClose, whs }: { open: boolean; onClose: () => void; whs: Wh[] }) {
  const [f, setF] = useState({ supplier_id: null as string | null, warehouse_id: "", supplier_reference: "", received_on: "" });
  const [lines, setLines] = useState<RLine[]>([{ product_id: null, quantity: "", unit: "PCS", batch_number: "", expiry_date: "", unit_cost: "" }]);
  const m = useAction(() => api("/inventory/receipts", { body: { ...f, supplier_reference: f.supplier_reference || null, received_on: f.received_on || null, lines: lines.filter((l) => l.product_id && Number(l.quantity) > 0).map((l) => ({ product_id: l.product_id, quantity: l.quantity, unit: l.unit, batch_number: l.batch_number || null, expiry_date: l.expiry_date || null, unit_cost: l.unit_cost || null })) } }), { invalidate: ["/inventory", "/products"], success: "Goods receipt posted", onSuccess: onClose });
  const setL = (i: number, p: Partial<RLine>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...p } : l)));
  return (
    <Dialog wide open={open} onClose={onClose} title="Receive goods (GRN)" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.supplier_id || !f.warehouse_id || !lines.some((l) => l.product_id && Number(l.quantity) > 0)} loading={m.isPending} onClick={() => m.mutate(undefined)}>Post receipt</Button></>}>
      <div className="grid gap-3 md:grid-cols-4">
        <Field label="Supplier" required className="md:col-span-2">{(id) => <SupplierPicker id={id} value={f.supplier_id} onChange={(v) => setF({ ...f, supplier_id: v })} />}</Field>
        <WhSelect whs={whs} value={f.warehouse_id} onChange={(v) => setF({ ...f, warehouse_id: v })} label="Warehouse" />
        <Field label="Received on">{(id) => <Input id={id} type="date" value={f.received_on} onChange={(e) => setF({ ...f, received_on: e.target.value })} />}</Field>
        <Field label="Supplier invoice number" hint="Used to match the purchase invoice." className="md:col-span-2">{(id) => <Input id={id} value={f.supplier_reference} onChange={(e) => setF({ ...f, supplier_reference: e.target.value })} />}</Field>
      </div>
      <div className="mt-3 space-y-2">
        {lines.map((l, i) => (
          <div key={i} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-[1fr_90px_90px_110px_140px_100px]">
            <ProductPicker value={l.product_id} onChange={(v) => setL(i, { product_id: v })} />
            <Input aria-label="Quantity" placeholder="Qty" type="number" min="0" step="any" value={l.quantity} onChange={(e) => setL(i, { quantity: e.target.value })} />
            <Select aria-label="Unit" value={l.unit} onChange={(e) => setL(i, { unit: e.target.value })}>{["PCS", "CASE", "DOZEN", "BOX"].map((u) => <option key={u}>{u}</option>)}</Select>
            <Input aria-label="Batch" placeholder="Batch" value={l.batch_number} onChange={(e) => setL(i, { batch_number: e.target.value })} />
            <Input aria-label="Expiry date" type="date" value={l.expiry_date} onChange={(e) => setL(i, { expiry_date: e.target.value })} />
            <Input aria-label="Unit cost" placeholder="Cost/unit" type="number" min="0" step="0.01" value={l.unit_cost} onChange={(e) => setL(i, { unit_cost: e.target.value })} />
          </div>
        ))}
        <Button size="sm" onClick={() => setLines([...lines, { product_id: null, quantity: "", unit: "PCS", batch_number: "", expiry_date: "", unit_cost: "" }])}>Add line</Button>
      </div>
    </Dialog>
  );
}

function CountDialog({ open, onClose, whs }: { open: boolean; onClose: () => void; whs: Wh[] }) {
  const router = useRouter();
  const [wh, setWh] = useState("");
  const m = useAction(() => api<{ id: string }>("/inventory/stock-counts", { body: { warehouse_id: wh } }), { invalidate: ["/inventory"], success: "Stock count started", onSuccess: (r) => { onClose(); router.push(`/inventory/counts/${r.id}`); } });
  return (
    <Dialog open={open} onClose={onClose} title="Start a stock count" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!wh} loading={m.isPending} onClick={() => m.mutate(undefined)}>Start</Button></>}>
      <p className="mb-3 text-sm text-muted">System quantities for every product with stock are snapshotted now. Variances post as audited stock-count adjustments.</p>
      <WhSelect whs={whs} value={wh} onChange={setWh} label="Warehouse" />
    </Dialog>
  );
}
