"use client";

import { Plus, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { SupplierPicker } from "@/components/pickers";
import { emptyVersion, SchemeVersionEditor, toApiVersion, TYPE_LABELS, type VersionForm } from "@/components/scheme-editor";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, DataTable, Dialog, ErrorState, Field, Input, PageHeader, Pagination, Select, Spinner, StatusChip, SyntheticTag, Tabs, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, money } from "@/lib/format";
import { useAction, useApi, usePage } from "@/lib/hooks";

type S = { id: string; code: string; name: string; status: string; funded_by: string; supplier: string | null; active_version: number | null; latest_version: number; latest_status: string; scheme_type: string; start_date: string; end_date: string; applications: number; benefit_total: string; is_synthetic: boolean };
type Claim = { id: string; claim_number: string; scheme_id: string; period_start: string; period_end: string; expected_amount: string; status: string; submitted_at: string | null };

export default function SchemesPage() {
  const router = useRouter();
  const { can } = useAuth();
  const toast = useToast();
  const [tab, setTab] = useState<"schemes" | "claims">("schemes");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [head, setHead] = useState({ code: "", name: "", supplier_id: null as string | null, funded_by: "supplier" });
  const [v, setV] = useState<VersionForm>(emptyVersion());
  const [circularText, setCircularText] = useState("");
  const [busy, setBusy] = useState(false);
  const list = usePage<S>("/schemes", { status, page, page_size: 25 });
  const claims = useApi<Claim[]>(tab === "claims" ? "/schemes/claims" : null);
  const create = useAction(() => api<{ id: string }>("/schemes", { body: { ...head, version: toApiVersion(v) } }), { invalidate: ["/schemes"], success: "Draft scheme created", onSuccess: (s) => { setCreating(false); router.push(`/schemes/${s.id}`); } });
  const markSubmitted = useAction((id: string) => api(`/schemes/claims/${id}/mark-submitted`, { body: { notes: "Recorded as submitted to supplier" } }), { invalidate: ["/schemes"], success: "Claim marked as submitted" });

  const extract = async (file?: File) => {
    setBusy(true);
    const form = new FormData();
    if (file) form.append("file", file);
    else form.append("text", circularText);
    try {
      const r = await api<{ job_status: string; result: { status: string; drafts?: { scheme_id: string; code: string; warnings: string[] }[]; error?: string } | null; error: string | null }>("/schemes/extract", { form });
      if (r.result?.drafts?.length) {
        toast.success(`${r.result.drafts.length} draft scheme(s) created for review`, r.result.drafts[0].warnings.slice(0, 2).join(" · "));
        setExtracting(false);
        router.push(`/schemes/${r.result.drafts[0].scheme_id}`);
      } else toast.error(new Error(r.result?.error ?? r.error ?? "Nothing could be extracted"), "Extraction failed");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Trade schemes" description="Only approved, active versions apply to orders. Every application stores its calculation." actions={can("schemes.manage") && <>
        <Button onClick={() => setExtracting(true)}><Upload className="h-4 w-4" /> Draft from circular</Button>
        <Button variant="primary" onClick={() => { setHead({ code: "", name: "", supplier_id: null, funded_by: "supplier" }); setV(emptyVersion()); setCreating(true); }}><Plus className="h-4 w-4" /> New scheme</Button>
      </>} />
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "schemes", label: "Schemes" }, { key: "claims", label: "Supplier claims" }]} />
      {tab === "schemes" ? (
        <Card padded={false}>
          <div className="border-b border-border p-3">
            <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="max-w-[200px]">
              <option value="">All statuses</option>
              {["active", "pending_approval", "draft", "inactive", "rejected"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
            </Select>
          </div>
          {list.isPending ? <Spinner /> : list.error ? <ErrorState error={list.error} /> : <>
            <DataTable<S> rows={list.data.items} rowKey={(r) => r.id} onRowClick={(r) => router.push(`/schemes/${r.id}`)} columns={[
              { key: "c", header: "Code", cell: (r) => <span className="font-medium">{r.code} <SyntheticTag show={r.is_synthetic} /></span> },
              { key: "n", header: "Name", cell: (r) => r.name },
              { key: "t", header: "Type", cell: (r) => TYPE_LABELS[r.scheme_type] ?? r.scheme_type },
              { key: "p", header: "Period", cell: (r) => `${date(r.start_date)} – ${date(r.end_date)}` },
              { key: "f", header: "Funded by", cell: (r) => r.supplier ?? r.funded_by },
              { key: "a", header: "Applied", align: "right", cell: (r) => `${r.applications} · ${money(r.benefit_total)}` },
              { key: "v", header: "Version", cell: (r) => (r.active_version ? `v${r.active_version} active` : `v${r.latest_version} ${r.latest_status.replace("_", " ")}`) },
              { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
            ]} empty={<p className="p-6 text-center text-sm text-muted">No schemes yet.</p>} />
            <Pagination page={page} pageSize={25} total={list.data.total} onPage={setPage} />
          </>}
        </Card>
      ) : (
        <Card padded={false}>
          <div className="border-b border-border p-3 text-xs text-muted">Claims are prepared here from recorded scheme benefits on dispatched orders. TRADEOS never submits claims to suppliers; mark a claim as submitted after you send it yourself. Supplier credit notes linked to a claim are reconciled automatically.</div>
          {claims.isPending ? <Spinner /> : claims.error ? <ErrorState error={claims.error} /> : (
            <DataTable<Claim> rows={claims.data} rowKey={(c) => c.id} columns={[
              { key: "n", header: "Claim", cell: (c) => <b>{c.claim_number}</b> },
              { key: "p", header: "Period", cell: (c) => `${date(c.period_start)} – ${date(c.period_end)}` },
              { key: "a", header: "Expected", align: "right", cell: (c) => money(c.expected_amount) },
              { key: "s", header: "Status", cell: (c) => <StatusChip status={c.status} /> },
              { key: "x", header: "", cell: (c) => c.status === "draft" && can("schemes.manage") ? <Button size="sm" onClick={() => markSubmitted.mutate(c.id)}>Mark submitted</Button> : c.submitted_at ? <span className="text-xs text-muted">submitted {date(c.submitted_at)}</span> : null },
            ]} empty={<p className="p-6 text-center text-sm text-muted">No claims. Generate one from a supplier-funded scheme page.</p>} />
          )}
        </Card>
      )}
      <Dialog wide open={creating} onClose={() => setCreating(false)} title="New scheme (draft)" footer={<><Button onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" disabled={!head.code || !head.name} loading={create.isPending} onClick={() => create.mutate(undefined)}>Save draft</Button></>}>
        <div className="mb-4 grid gap-3 md:grid-cols-4">
          <Field label="Code" required>{(id) => <Input id={id} value={head.code} onChange={(e) => setHead({ ...head, code: e.target.value })} />}</Field>
          <Field label="Name" required className="md:col-span-3">{(id) => <Input id={id} value={head.name} onChange={(e) => setHead({ ...head, name: e.target.value })} />}</Field>
          <Field label="Funded by">{(id) => <Select id={id} value={head.funded_by} onChange={(e) => setHead({ ...head, funded_by: e.target.value })}><option value="supplier">Supplier (claimable)</option><option value="distributor">Distributor</option></Select>}</Field>
          <Field label="Supplier" className="md:col-span-3">{(id) => <SupplierPicker id={id} value={head.supplier_id} onChange={(sid) => setHead({ ...head, supplier_id: sid })} />}</Field>
        </div>
        <SchemeVersionEditor v={v} onChange={setV} />
      </Dialog>
      <Dialog open={extracting} onClose={() => setExtracting(false)} title="Draft a scheme from a circular" footer={<><Button onClick={() => setExtracting(false)}>Cancel</Button><Button variant="primary" disabled={!circularText.trim()} loading={busy} onClick={() => extract()}>Extract from text</Button></>}>
        <div className="space-y-3">
          <Alert tone="info">Extracted rules become <b>draft</b> schemes only. They must be reviewed, submitted and approved by an authorised user before they affect any order.</Alert>
          <Field label="Circular text">{(id) => <Textarea id={id} rows={6} value={circularText} onChange={(e) => setCircularText(e.target.value)} placeholder={"Scheme: Brightwash winter offer\n5% off on Brightwash range\nValid from 01/12/2026 to 31/12/2026"} />}</Field>
          <div>
            <p className="mb-1 text-xs font-medium text-ink-2">…or upload a PDF / image / text file</p>
            <input type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt" aria-label="Circular file" disabled={busy} onChange={(e) => e.target.files?.[0] && extract(e.target.files[0])} className="text-sm" />
          </div>
        </div>
      </Dialog>
    </>
  );
}
