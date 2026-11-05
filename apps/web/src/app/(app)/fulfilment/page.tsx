"use client";

import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ProductPicker, RetailerPicker, SupplierPicker } from "@/components/pickers";
import { Button, Card, DataTable, Dialog, ErrorState, Field, Input, PageHeader, Pagination, Select, Spinner, StatusChip, SyntheticTag, Tabs, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, money, titleCase } from "@/lib/format";
import { useAction, useApi, usePage } from "@/lib/hooks";

type PL = { id: string; pick_number: string; status: string; order_id: string; order_number: string; retailer: string; route: string | null; warehouse: string; requested_delivery_date: string | null; created_at: string };
type D = { id: string; dispatch_number: string; status: string; order_id: string; order_number: string; retailer: string; vehicle: string | null; dispatched_at: string; delivered_at: string | null; delivery_note: string | null };
type R = { id: string; return_number: string; return_type: string; status: string; party: string; return_date: string; total_value: string; reason: string | null; is_synthetic: boolean };
type Tab = "picks" | "dispatches" | "returns";

function Inner() {
  const sp = useSearchParams();
  const router = useRouter();
  const { can } = useAuth();
  const [tab, setTab] = useState<Tab>((sp.get("tab") as Tab) ?? "picks");
  const [status, setStatus] = useState("open,picking,picked");
  const [page, setPage] = useState(1);
  const [deliver, setDeliver] = useState<D | null>(null);
  const [outcome, setOutcome] = useState("delivered");
  const [note, setNote] = useState("");
  const [newReturn, setNewReturn] = useState(false);
  const picks = usePage<PL>(tab === "picks" ? "/fulfilment/pick-lists" : "", { status, page, page_size: 25 });
  const disp = usePage<D>(tab === "dispatches" ? "/fulfilment/dispatches" : "", { status: status === "dispatched" ? "dispatched" : "", page, page_size: 25 });
  const rets = usePage<R>(tab === "returns" ? "/returns" : "", { page, page_size: 25 });
  const doDeliver = useAction(() => api(`/fulfilment/dispatches/${deliver!.id}/delivery`, { body: { outcome, note: note || null } }), { invalidate: ["/fulfilment", "/orders"], success: "Delivery updated", onSuccess: () => { setDeliver(null); setNote(""); } });
  const receive = useAction((id: string) => api(`/returns/${id}/receive`, { method: "POST" }), { invalidate: ["/returns", "/inventory"], success: "Return received into stock" });

  return (
    <>
      <PageHeader title="Fulfilment" description="Pick lists are created when orders are approved. Dispatch posts stock movements and raises the sales invoice exactly once." actions={tab === "returns" && can("orders.create") && <Button variant="primary" onClick={() => setNewReturn(true)}><Plus className="h-4 w-4" /> New return</Button>} />
      <Tabs value={tab} onChange={(t) => { setTab(t); setPage(1); setStatus(t === "picks" ? "open,picking,picked" : t === "dispatches" ? "dispatched" : ""); }} tabs={[{ key: "picks", label: "Pick lists" }, { key: "dispatches", label: "Dispatches & delivery" }, { key: "returns", label: "Returns" }]} />
      {tab !== "returns" && (
        <div className="mb-3">
          <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="max-w-[220px]">
            {tab === "picks" ? <><option value="open,picking,picked">To do</option><option value="dispatched">Dispatched</option><option value="cancelled">Cancelled</option><option value="">All</option></> : <><option value="dispatched">In transit</option><option value="">All</option></>}
          </Select>
        </div>
      )}
      <Card padded={false}>
        {tab === "picks" && (picks.isPending ? <Spinner /> : picks.error ? <ErrorState error={picks.error} /> : <>
          <DataTable<PL> rows={picks.data.items} rowKey={(r) => r.id} onRowClick={(r) => router.push(`/fulfilment/pick-lists/${r.id}`)} columns={[
            { key: "n", header: "Pick list", cell: (r) => <b>{r.pick_number}</b> },
            { key: "o", header: "Order", cell: (r) => r.order_number },
            { key: "r", header: "Retailer", cell: (r) => r.retailer },
            { key: "rt", header: "Route", cell: (r) => r.route ?? "—" },
            { key: "w", header: "Warehouse", cell: (r) => r.warehouse },
            { key: "d", header: "Deliver by", cell: (r) => date(r.requested_delivery_date) },
            { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
          ]} empty={<p className="p-6 text-center text-sm text-muted">No pick lists in this view.</p>} />
          <Pagination page={page} pageSize={25} total={picks.data.total} onPage={setPage} />
        </>)}
        {tab === "dispatches" && (disp.isPending ? <Spinner /> : disp.error ? <ErrorState error={disp.error} /> : <>
          <DataTable<D> rows={disp.data.items} rowKey={(r) => r.id} columns={[
            { key: "n", header: "Dispatch", cell: (r) => <b>{r.dispatch_number}</b> },
            { key: "o", header: "Order", cell: (r) => <Link className="text-info hover:underline" href={`/orders/${r.order_id}`}>{r.order_number}</Link> },
            { key: "r", header: "Retailer", cell: (r) => r.retailer },
            { key: "v", header: "Vehicle", cell: (r) => r.vehicle ?? "—" },
            { key: "d", header: "Dispatched", cell: (r) => dateTime(r.dispatched_at) },
            { key: "dl", header: "Delivered", cell: (r) => dateTime(r.delivered_at) },
            { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
            { key: "x", header: "", cell: (r) => r.status === "dispatched" && can("fulfilment.execute") ? <Button size="sm" onClick={() => setDeliver(r)}>Update delivery</Button> : r.delivery_note ? <span className="text-xs text-muted">{r.delivery_note}</span> : null },
          ]} />
          <Pagination page={page} pageSize={25} total={disp.data.total} onPage={setPage} />
        </>)}
        {tab === "returns" && (rets.isPending ? <Spinner /> : rets.error ? <ErrorState error={rets.error} /> : <>
          <DataTable<R> rows={rets.data.items} rowKey={(r) => r.id} columns={[
            { key: "n", header: "Return", cell: (r) => <b>{r.return_number} <SyntheticTag show={r.is_synthetic} /></b> },
            { key: "t", header: "Type", cell: (r) => titleCase(r.return_type) },
            { key: "p", header: "From / to", cell: (r) => r.party },
            { key: "d", header: "Date", cell: (r) => date(r.return_date) },
            { key: "v", header: "Value", align: "right", cell: (r) => money(r.total_value) },
            { key: "re", header: "Reason", cell: (r) => <span className="text-xs">{r.reason}</span> },
            { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
            { key: "x", header: "", cell: (r) => r.status === "pending" && can("inventory.adjust") ? <Button size="sm" onClick={() => receive.mutate(r.id)}>Receive</Button> : r.status === "received" && r.return_type === "customer" ? <Link className="text-xs text-info underline" href="/finance?tab=notes">Issue credit note</Link> : null },
          ]} empty={<p className="p-6 text-center text-sm text-muted">No returns.</p>} />
          <Pagination page={page} pageSize={25} total={rets.data.total} onPage={setPage} />
        </>)}
      </Card>
      <Dialog open={Boolean(deliver)} onClose={() => setDeliver(null)} title={`Delivery for ${deliver?.dispatch_number}`} footer={<><Button onClick={() => setDeliver(null)}>Cancel</Button><Button variant="primary" loading={doDeliver.isPending} onClick={() => doDeliver.mutate(undefined)}>Save</Button></>}>
        <div className="space-y-3">
          <Field label="Outcome">{(id) => <Select id={id} value={outcome} onChange={(e) => setOutcome(e.target.value)}><option value="delivered">Delivered</option><option value="failed">Delivery failed</option></Select>}</Field>
          <Field label="Note">{(id) => <Textarea id={id} rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Received by shop owner" />}</Field>
        </div>
      </Dialog>
      <ReturnDialog open={newReturn} onClose={() => setNewReturn(false)} />
    </>
  );
}

type RL = { product_id: string | null; quantity: string; condition: string };
function ReturnDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { can } = useAuth();
  const whs = useApi<{ id: string; name: string; is_active: boolean }[]>(open ? "/warehouses" : null);
  const [f, setF] = useState({ return_type: "customer", warehouse_id: "", retailer_id: null as string | null, supplier_id: null as string | null, sales_invoice_id: "", reason: "" });
  const [lines, setLines] = useState<RL[]>([{ product_id: null, quantity: "1", condition: "sellable" }]);
  const invs = useApi<{ items: { id: string; invoice_number: string; invoice_date: string }[] }>(open && f.retailer_id ? "/sales-invoices" : null, { retailer_id: f.retailer_id ?? undefined, page_size: 50 });
  const m = useAction(() => api("/returns", { body: { ...f, retailer_id: f.return_type === "customer" ? f.retailer_id : null, supplier_id: f.return_type === "supplier" ? f.supplier_id : null, sales_invoice_id: f.sales_invoice_id || null, reason: f.reason || null, lines: lines.filter((l) => l.product_id && Number(l.quantity) > 0) } }), { invalidate: ["/returns"], success: "Return created", onSuccess: onClose });
  return (
    <Dialog wide open={open} onClose={onClose} title="New return" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.warehouse_id || (f.return_type === "customer" ? !f.retailer_id : !f.supplier_id) || !lines.some((l) => l.product_id)} loading={m.isPending} onClick={() => m.mutate(undefined)}>Create</Button></>}>
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Type">{(id) => <Select id={id} value={f.return_type} onChange={(e) => setF({ ...f, return_type: e.target.value })}><option value="customer">Customer return</option>{can("inventory.adjust") && <option value="supplier">Return to supplier</option>}</Select>}</Field>
        <Field label="Warehouse" required>{(id) => <Select id={id} value={f.warehouse_id} onChange={(e) => setF({ ...f, warehouse_id: e.target.value })}><option value="">Select…</option>{whs.data?.filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</Select>}</Field>
        {f.return_type === "customer" ? <Field label="Retailer" required>{(id) => <RetailerPicker id={id} value={f.retailer_id} onChange={(v) => setF({ ...f, retailer_id: v })} />}</Field> : <Field label="Supplier" required>{(id) => <SupplierPicker id={id} value={f.supplier_id} onChange={(v) => setF({ ...f, supplier_id: v })} />}</Field>}
        {f.return_type === "customer" && <Field label="Original invoice" hint="Used to value the return at the invoiced net price.">{(id) => <Select id={id} value={f.sales_invoice_id} onChange={(e) => setF({ ...f, sales_invoice_id: e.target.value })}><option value="">Not linked</option>{invs.data?.items.map((i) => <option key={i.id} value={i.id}>{i.invoice_number} · {date(i.invoice_date)}</option>)}</Select>}</Field>}
        <Field label="Reason" className="md:col-span-2">{(id) => <Input id={id} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />}</Field>
      </div>
      <div className="mt-3 space-y-2">
        {lines.map((l, i) => (
          <div key={i} className="grid gap-2 md:grid-cols-[1fr_100px_140px_auto]">
            <ProductPicker value={l.product_id} onChange={(v) => setLines(lines.map((x, j) => (j === i ? { ...x, product_id: v } : x)))} />
            <Input aria-label="Quantity (base units)" type="number" min="0" step="any" value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
            <Select aria-label="Condition" value={l.condition} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, condition: e.target.value } : x)))}><option value="sellable">Sellable</option><option value="damaged">Damaged (blocked)</option><option value="expired">Expired (blocked)</option></Select>
            <Button variant="ghost" aria-label="Remove line" onClick={() => setLines(lines.filter((_, j) => j !== i))}><Trash2 className="h-4 w-4" /></Button>
          </div>
        ))}
        <Button size="sm" onClick={() => setLines([...lines, { product_id: null, quantity: "1", condition: "sellable" }])}>Add line</Button>
      </div>
    </Dialog>
  );
}

export default function FulfilmentPage() {
  return <Suspense fallback={<Spinner />}><Inner /></Suspense>;
}
