"use client";

import { Download, Plus, Upload } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { HBarList } from "@/components/charts";
import { RetailerPicker, SupplierPicker } from "@/components/pickers";
import { useToast } from "@/components/toast";
import { Button, Card, DataTable, Dialog, ErrorState, Field, Input, PageHeader, Pagination, Select, Spinner, Stat, StatusChip, SyntheticTag, Tabs, Textarea } from "@/components/ui";
import { api, download, newIdempotencyKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, isoDay, money, titleCase } from "@/lib/format";
import { useAction, useApi, useDebounced, useInvalidate, usePage } from "@/lib/hooks";

type Tab = "purchase" | "sales" | "payments" | "notes" | "receivables";
type PI = { id: string; supplier: string | null; invoice_number: string; invoice_date: string | null; total: string; status: string; issue_count: number; has_document: boolean; is_synthetic: boolean };
type SI = { id: string; invoice_number: string; retailer: string; invoice_date: string; due_date: string; total: string; outstanding: string; status: string; days_overdue: number; is_synthetic: boolean };
type Pay = { id: string; payment_number: string; retailer: string; amount: string; unallocated: string; received_on: string; method: string; reference: string | null; status: string; is_synthetic: boolean };
type Note = { id: string; note_type: string; direction: string; note_number: string; note_date: string; amount: string; reason: string; status: string; is_synthetic: boolean };
type Ageing = { buckets: string[]; totals: Record<string, string>; total_outstanding: string; retailers: { retailer_id: string; retailer: string; code: string; credit_limit: string | null; buckets: Record<string, string>; total: string }[] };

function FinanceInner() {
  const sp = useSearchParams();
  const router = useRouter();
  const { can } = useAuth();
  const toast = useToast();
  const inv = useInvalidate();
  const [tab, setTab] = useState<Tab>((sp.get("tab") as Tab) ?? "purchase");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [dlg, setDlg] = useState<null | "payment" | "note" | "pi">(null);
  const dq = useDebounced(q);
  const pis = usePage<PI>(tab === "purchase" ? "/purchase-invoices" : "", { status, q: dq, page, page_size: 25 });
  const sis = usePage<SI>(tab === "sales" ? "/sales-invoices" : "", { status, q: dq, page, page_size: 25 });
  const pays = usePage<Pay>(tab === "payments" ? "/payments" : "", { q: dq, page, page_size: 25, unallocated: status === "unallocated" ? true : undefined });
  const notes = usePage<Note>(tab === "notes" ? "/credit-notes" : "", { status, page, page_size: 25 });
  const ageing = useApi<Ageing>(tab === "receivables" ? "/receivables/ageing" : null);
  const [uploading, setUploading] = useState(false);

  const upload = async (f: File) => {
    setUploading(true);
    const form = new FormData();
    form.append("file", f);
    try {
      const r = await api<{ id: string; status: string }>("/purchase-invoices/upload", { form });
      inv("/purchase-invoices", "/exceptions");
      router.push(`/finance/purchase-invoices/${r.id}`);
    } catch (e) {
      toast.error(e);
    } finally {
      setUploading(false);
    }
  };
  const change = (t: Tab) => { setTab(t); setStatus(""); setPage(1); };

  return (
    <>
      <PageHeader title="Finance" description="Operational records for reconciliation. TRADEOS is not a statutory ledger and does not file returns." actions={<>
        {tab === "purchase" && can("finance.write") && <>
          <input id="pi-file" type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt" className="sr-only" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          <label htmlFor="pi-file" className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-md border border-border-strong bg-surface px-3.5 text-sm font-medium hover:bg-surface-2">{uploading ? "Extracting…" : <><Upload className="h-4 w-4" /> Upload supplier invoice</>}</label>
          <Button onClick={() => setDlg("pi")}><Plus className="h-4 w-4" /> Enter manually</Button>
        </>}
        {tab === "sales" && can("reports.export") && <Button onClick={() => download("/exports/sales_invoices")}><Download className="h-4 w-4" /> Export</Button>}
        {tab === "payments" && can("payments.allocate") && <Button variant="primary" onClick={() => setDlg("payment")}><Plus className="h-4 w-4" /> Record payment</Button>}
        {tab === "notes" && can("finance.write") && <Button variant="primary" onClick={() => setDlg("note")}><Plus className="h-4 w-4" /> New credit/debit note</Button>}
      </>} />
      <Tabs value={tab} onChange={change} tabs={[{ key: "purchase", label: "Purchase invoices" }, { key: "sales", label: "Sales invoices" }, ...(can("payments.read") ? [{ key: "payments" as Tab, label: "Payments" }] : []), { key: "notes", label: "Credit & debit notes" }, { key: "receivables", label: "Receivables ageing" }]} />
      {tab !== "receivables" && (
        <div className="mb-3 flex flex-wrap gap-2">
          {tab !== "notes" && <Input aria-label="Search" placeholder="Search number / reference" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="max-w-xs" />}
          <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="max-w-[220px]">
            <option value="">All</option>
            {(tab === "purchase" ? ["needs_review", "verified", "rejected"] : tab === "sales" ? ["open", "partially_paid", "paid"] : tab === "payments" ? ["unallocated"] : ["draft", "issued", "applied", "recorded", "void"]).map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
          </Select>
        </div>
      )}
      {tab === "purchase" && <Card padded={false}>{pis.isPending ? <Spinner /> : pis.error ? <ErrorState error={pis.error} /> : <>
        <DataTable<PI> rows={pis.data.items} rowKey={(r) => r.id} onRowClick={(r) => router.push(`/finance/purchase-invoices/${r.id}`)} columns={[
          { key: "n", header: "Invoice", cell: (r) => <span className="font-medium">{r.invoice_number} <SyntheticTag show={r.is_synthetic} /></span> },
          { key: "s", header: "Supplier", cell: (r) => r.supplier ?? <span className="text-danger">Not identified</span> },
          { key: "d", header: "Date", cell: (r) => date(r.invoice_date) },
          { key: "t", header: "Total", align: "right", cell: (r) => money(r.total) },
          { key: "i", header: "Checks", align: "center", cell: (r) => (r.issue_count ? <StatusChip status="medium" label={`${r.issue_count} issue(s)`} /> : <StatusChip status="ok" label="OK" />) },
          { key: "st", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
        ]} empty={<p className="p-6 text-center text-sm text-muted">No purchase invoices. Upload one to start.</p>} />
        <Pagination page={page} pageSize={25} total={pis.data.total} onPage={setPage} /></>}</Card>}
      {tab === "sales" && <Card padded={false}>{sis.isPending ? <Spinner /> : sis.error ? <ErrorState error={sis.error} /> : <>
        <DataTable<SI> rows={sis.data.items} rowKey={(r) => r.id} onRowClick={(r) => router.push(`/finance/sales-invoices/${r.id}`)} columns={[
          { key: "n", header: "Invoice", cell: (r) => <span className="font-medium">{r.invoice_number} <SyntheticTag show={r.is_synthetic} /></span> },
          { key: "r", header: "Retailer", cell: (r) => r.retailer },
          { key: "d", header: "Date", cell: (r) => date(r.invoice_date) },
          { key: "due", header: "Due", cell: (r) => <span className={r.days_overdue ? "text-danger" : ""}>{date(r.due_date)}{r.days_overdue ? ` (${r.days_overdue}d overdue)` : ""}</span> },
          { key: "t", header: "Total", align: "right", cell: (r) => money(r.total) },
          { key: "o", header: "Outstanding", align: "right", cell: (r) => money(r.outstanding) },
          { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
        ]} />
        <Pagination page={page} pageSize={25} total={sis.data.total} onPage={setPage} /></>}</Card>}
      {tab === "payments" && <Card padded={false}>{pays.isPending ? <Spinner /> : pays.error ? <ErrorState error={pays.error} /> : <>
        <DataTable<Pay> rows={pays.data.items} rowKey={(r) => r.id} onRowClick={(r) => router.push(`/finance/payments/${r.id}`)} columns={[
          { key: "n", header: "Payment", cell: (r) => <span className="font-medium">{r.payment_number} <SyntheticTag show={r.is_synthetic} /></span> },
          { key: "r", header: "Retailer", cell: (r) => r.retailer },
          { key: "d", header: "Received", cell: (r) => date(r.received_on) },
          { key: "m", header: "Method / ref", cell: (r) => `${titleCase(r.method)}${r.reference ? ` · ${r.reference}` : ""}` },
          { key: "a", header: "Amount", align: "right", cell: (r) => money(r.amount) },
          { key: "u", header: "Unallocated", align: "right", cell: (r) => <span className={Number(r.unallocated) > 0 ? "font-medium text-accent-strong" : ""}>{money(r.unallocated)}</span> },
          { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
        ]} />
        <Pagination page={page} pageSize={25} total={pays.data.total} onPage={setPage} /></>}</Card>}
      {tab === "notes" && <NotesTable notes={notes} page={page} setPage={setPage} />}
      {tab === "receivables" && (ageing.isPending ? <Spinner /> : ageing.error ? <ErrorState error={ageing.error} /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
            <Stat label="Total outstanding" value={money(ageing.data.total_outstanding, { compact: true })} />
            {ageing.data.buckets.map((b) => <Stat key={b} label={b === "current" ? "Not yet due" : `${b} days overdue`} value={money(ageing.data.totals[b], { compact: true })} tone={b !== "current" && Number(ageing.data.totals[b]) > 0 ? "warning" : undefined} />)}
          </div>
          <Card><HBarList title="Outstanding by ageing bucket" valueLabel="Outstanding" formatValue={(v) => money(v)} data={ageing.data.buckets.map((b) => ({ label: b === "current" ? "Not yet due" : `${b} days`, value: Number(ageing.data.totals[b]) }))} /></Card>
          <Card padded={false} title="By retailer">
            <DataTable dense rows={ageing.data.retailers} rowKey={(r) => r.retailer_id} onRowClick={(r) => router.push(`/retailers/${r.retailer_id}`)} columns={[
              { key: "r", header: "Retailer", cell: (r) => <span>{r.retailer} <span className="text-xs text-muted">{r.code}</span></span> },
              ...ageing.data.buckets.map((b) => ({ key: b, header: b === "current" ? "Current" : b, align: "right" as const, cell: (r: Ageing["retailers"][number]) => (Number(r.buckets[b]) ? money(r.buckets[b]) : "—") })),
              { key: "t", header: "Total", align: "right", cell: (r) => <b>{money(r.total)}</b> },
              { key: "l", header: "Limit", align: "right", cell: (r) => (r.credit_limit ? money(r.credit_limit) : "—") },
            ]} />
          </Card>
          <p className="text-xs text-muted">Buckets are configurable in Settings. TRADEOS never sends collection messages automatically.</p>
        </div>
      ))}
      <PaymentDialog open={dlg === "payment"} onClose={() => setDlg(null)} onSaved={(id) => router.push(`/finance/payments/${id}`)} />
      <NoteDialog open={dlg === "note"} onClose={() => setDlg(null)} />
      <ManualPIDialog open={dlg === "pi"} onClose={() => setDlg(null)} onSaved={(id) => router.push(`/finance/purchase-invoices/${id}`)} />
    </>
  );
}

function NotesTable({ notes, page, setPage }: { notes: ReturnType<typeof usePage<Note>>; page: number; setPage: (p: number) => void }) {
  const { can } = useAuth();
  const [applying, setApplying] = useState<Note | null>(null);
  const [invoiceId, setInvoiceId] = useState("");
  const issue = useAction((id: string) => api(`/credit-notes/${id}/issue`, { method: "POST" }), { invalidate: ["/credit-notes", "/returns"], success: "Note issued" });
  const apply = useAction(() => api(`/credit-notes/${applying!.id}/apply`, { body: { sales_invoice_id: invoiceId } }), { invalidate: ["/credit-notes", "/sales-invoices", "/receivables"], success: "Credit applied to invoice", onSuccess: () => setApplying(null) });
  const invs = useApi<{ items: { id: string; invoice_number: string; outstanding: string }[] }>(applying ? "/sales-invoices" : null, { status: "open,partially_paid", page_size: 100 });
  return (
    <Card padded={false}>{notes.isPending ? <Spinner /> : notes.error ? <ErrorState error={notes.error} /> : <>
      <DataTable<Note> rows={notes.data.items} rowKey={(r) => r.id} columns={[
        { key: "n", header: "Number", cell: (r) => <span className="font-medium">{r.note_number} <SyntheticTag show={r.is_synthetic} /></span> },
        { key: "t", header: "Type", cell: (r) => `${titleCase(r.note_type)} · ${r.direction === "issued" ? "issued to retailer" : "received from supplier"}` },
        { key: "d", header: "Date", cell: (r) => date(r.note_date) },
        { key: "r", header: "Reason", cell: (r) => titleCase(r.reason) },
        { key: "a", header: "Amount", align: "right", cell: (r) => money(r.amount) },
        { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
        { key: "x", header: "", cell: (r) => <span className="flex gap-1">
          {r.status === "draft" && r.direction === "issued" && can("finance.approve") && <Button size="sm" onClick={() => issue.mutate(r.id)}>Issue</Button>}
          {r.status === "issued" && r.note_type === "credit" && can("payments.allocate") && <Button size="sm" onClick={() => setApplying(r)}>Apply to invoice</Button>}
        </span> },
      ]} />
      <Pagination page={page} pageSize={25} total={notes.data.total} onPage={setPage} /></>}
      <Dialog open={Boolean(applying)} onClose={() => setApplying(null)} title={`Apply ${applying?.note_number}`} footer={<><Button onClick={() => setApplying(null)}>Cancel</Button><Button variant="primary" disabled={!invoiceId} loading={apply.isPending} onClick={() => apply.mutate(undefined)}>Apply</Button></>}>
        <Field label="Open invoice of this retailer" hint="The note amount cannot exceed the invoice's outstanding amount.">{(id) => <Select id={id} value={invoiceId} onChange={(e) => setInvoiceId(e.target.value)}><option value="">Select…</option>{invs.data?.items.map((i) => <option key={i.id} value={i.id}>{i.invoice_number} · {money(i.outstanding)} outstanding</option>)}</Select>}</Field>
      </Dialog>
    </Card>
  );
}

function PaymentDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: (id: string) => void }) {
  const [f, setF] = useState({ retailer_id: null as string | null, amount: "", received_on: isoDay(), method: "upi", reference: "", notes: "" });
  const [key] = useState(newIdempotencyKey);
  const m = useAction(() => api<{ id: string }>("/payments", { body: { ...f, reference: f.reference || null, notes: f.notes || null }, headers: { "Idempotency-Key": key } }), { invalidate: ["/payments", "/receivables"], success: "Payment recorded", onSuccess: (p) => { onClose(); onSaved(p.id); } });
  return (
    <Dialog open={open} onClose={onClose} title="Record payment received" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.retailer_id || !(Number(f.amount) > 0)} loading={m.isPending} onClick={() => m.mutate(undefined)}>Record</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Retailer" required className="sm:col-span-2">{(id) => <RetailerPicker id={id} value={f.retailer_id} onChange={(v) => setF({ ...f, retailer_id: v })} />}</Field>
        <Field label="Amount ₹" required>{(id) => <Input id={id} type="number" min="0" step="0.01" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />}</Field>
        <Field label="Received on" required>{(id) => <Input id={id} type="date" value={f.received_on} onChange={(e) => setF({ ...f, received_on: e.target.value })} />}</Field>
        <Field label="Method">{(id) => <Select id={id} value={f.method} onChange={(e) => setF({ ...f, method: e.target.value })}>{["upi", "bank_transfer", "cheque", "cash", "other"].map((m) => <option key={m} value={m}>{titleCase(m)}</option>)}</Select>}</Field>
        <Field label="Reference">{(id) => <Input id={id} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} />}</Field>
      </div>
      <p className="mt-3 text-xs text-muted">Recording a payment does not initiate any transfer. Allocate it to invoices on the next screen.</p>
    </Dialog>
  );
}

function NoteDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [f, setF] = useState({ direction: "issued", note_type: "credit", retailer_id: null as string | null, supplier_id: null as string | null, note_number: "", amount: "", reason: "other", description: "", scheme_claim_id: "" });
  const claims = useApi<{ id: string; claim_number: string; expected_amount: string; status: string }[]>(open && f.direction === "received" ? "/schemes/claims" : null);
  const m = useAction(() => api("/credit-notes", { body: { ...f, retailer_id: f.direction === "issued" ? f.retailer_id : null, supplier_id: f.direction === "received" ? f.supplier_id : null, note_number: f.note_number || null, description: f.description || null, scheme_claim_id: f.scheme_claim_id || null } }), { invalidate: ["/credit-notes", "/schemes"], success: "Note saved", onSuccess: onClose });
  return (
    <Dialog open={open} onClose={onClose} title="New credit / debit note" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!(Number(f.amount) > 0) || (f.direction === "issued" ? !f.retailer_id : !f.supplier_id || !f.note_number)} loading={m.isPending} onClick={() => m.mutate(undefined)}>Save</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Direction">{(id) => <Select id={id} value={f.direction} onChange={(e) => setF({ ...f, direction: e.target.value })}><option value="issued">Issued to a retailer</option><option value="received">Received from a supplier</option></Select>}</Field>
        <Field label="Type">{(id) => <Select id={id} value={f.note_type} onChange={(e) => setF({ ...f, note_type: e.target.value })}><option value="credit">Credit note</option><option value="debit">Debit note</option></Select>}</Field>
        {f.direction === "issued" ? <Field label="Retailer" required className="sm:col-span-2">{(id) => <RetailerPicker id={id} value={f.retailer_id} onChange={(v) => setF({ ...f, retailer_id: v })} />}</Field> : <>
          <Field label="Supplier" required>{(id) => <SupplierPicker id={id} value={f.supplier_id} onChange={(v) => setF({ ...f, supplier_id: v })} />}</Field>
          <Field label="Supplier's note number" required>{(id) => <Input id={id} value={f.note_number} onChange={(e) => setF({ ...f, note_number: e.target.value })} />}</Field>
          <Field label="Scheme claim (optional)" className="sm:col-span-2">{(id) => <Select id={id} value={f.scheme_claim_id} onChange={(e) => setF({ ...f, scheme_claim_id: e.target.value, reason: e.target.value ? "scheme" : f.reason })}><option value="">Not linked</option>{claims.data?.map((c) => <option key={c.id} value={c.id}>{c.claim_number} · expected {money(c.expected_amount)} · {c.status}</option>)}</Select>}</Field>
        </>}
        <Field label="Amount ₹" required>{(id) => <Input id={id} type="number" min="0" step="0.01" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />}</Field>
        <Field label="Reason">{(id) => <Select id={id} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })}>{["return", "scheme", "price_adjustment", "quantity_adjustment", "other"].map((r) => <option key={r} value={r}>{titleCase(r)}</option>)}</Select>}</Field>
        <Field label="Description" className="sm:col-span-2">{(id) => <Textarea id={id} rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />}</Field>
      </div>
    </Dialog>
  );
}

function ManualPIDialog({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: (id: string) => void }) {
  const [f, setF] = useState({ supplier_id: null as string | null, invoice_number: "", invoice_date: isoDay(), total: "" });
  const m = useAction(() => api<{ id: string }>("/purchase-invoices", { body: { supplier_id: f.supplier_id, invoice_number: f.invoice_number, invoice_date: f.invoice_date, total: f.total || null, lines: [] } }), { invalidate: ["/purchase-invoices"], success: "Invoice created - add lines and verify", onSuccess: (p) => { onClose(); onSaved(p.id); } });
  return (
    <Dialog open={open} onClose={onClose} title="Enter supplier invoice" footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!f.supplier_id || !f.invoice_number} loading={m.isPending} onClick={() => m.mutate(undefined)}>Create</Button></>}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Supplier" required className="sm:col-span-2">{(id) => <SupplierPicker id={id} value={f.supplier_id} onChange={(v) => setF({ ...f, supplier_id: v })} />}</Field>
        <Field label="Invoice number" required>{(id) => <Input id={id} value={f.invoice_number} onChange={(e) => setF({ ...f, invoice_number: e.target.value })} />}</Field>
        <Field label="Invoice date">{(id) => <Input id={id} type="date" value={f.invoice_date} onChange={(e) => setF({ ...f, invoice_date: e.target.value })} />}</Field>
      </div>
    </Dialog>
  );
}

export default function FinancePage() {
  return <Suspense fallback={<Spinner />}><FinanceInner /></Suspense>;
}
