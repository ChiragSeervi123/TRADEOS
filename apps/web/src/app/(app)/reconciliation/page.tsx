"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Card, DataTable, Dialog, ErrorState, Field, Input, PageHeader, Pagination, Select, Spinner, Stat, StatusChip, SyntheticTag, Tabs, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, money, titleCase } from "@/lib/format";
import { useAction, useApi, usePage } from "@/lib/hooks";

type C = { id: string; case_number: string; category: string; status: string; title: string; difference: string | null; matching_method: string | null; assigned_to: string | null; created_at: string; age_days: number; is_synthetic: boolean };
type M = { id: string; left_type: string; left_id: string; right_type: string; right_id: string; method: string; score: string | null; status: string; details: Record<string, unknown> | null; created_at: string };
const CATS = ["missing_credit_note", "price_mismatch", "quantity_mismatch", "scheme_benefit_mismatch", "duplicate_document", "unmatched_payment", "return_not_credited", "unknown_difference"];

export default function ReconciliationPage() {
  const router = useRouter();
  const { can } = useAuth();
  const [tab, setTab] = useState<"cases" | "matches">("cases");
  const [status, setStatus] = useState("open,investigating,awaiting_evidence");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [nc, setNc] = useState({ category: "unknown_difference", title: "", description: "", expected_value: "", observed_value: "" });
  const cases = usePage<C>("/reconciliation/cases", { status, category, page, page_size: 25 });
  const matches = useApi<M[]>(tab === "matches" ? "/reconciliation/matches" : null);
  const run = useAction(() => api<Record<string, number>>("/reconciliation/run", { method: "POST" }), { invalidate: ["/reconciliation", "/exceptions"], success: (r) => `Rules ran: ${r.new_cases ?? 0} new case(s), ${r.suggested_matches ?? 0} suggested match(es)` });
  const confirm = useAction((id: string) => api(`/reconciliation/matches/${id}/confirm`, { method: "POST" }), { invalidate: ["/reconciliation"], success: "Match confirmed" });
  const unmatch = useAction((id: string) => api(`/reconciliation/matches/${id}/unmatch`, { body: { reason: "Rejected by reviewer" } }), { invalidate: ["/reconciliation"], success: "Match removed" });
  const create = useAction(() => api<{ id: string }>("/reconciliation/cases", { body: { ...nc, expected_value: nc.expected_value || null, observed_value: nc.observed_value || null } }), { invalidate: ["/reconciliation"], success: "Case created", onSuccess: (c) => router.push(`/reconciliation/${c.id}`) });
  const summary = (cases.data?.summary ?? {}) as Record<string, { count: number; difference: string }>;
  const openCount = ["open", "investigating", "awaiting_evidence"].reduce((s, k) => s + (summary[k]?.count ?? 0), 0);

  return (
    <>
      <PageHeader title="Reconciliation" description="Deterministic matching between invoices, goods receipts, claims, credit notes, returns and payments." actions={can("finance.reconcile") && <>
        <Button onClick={() => setCreating(true)}>New case</Button>
        <Button variant="primary" loading={run.isPending} onClick={() => run.mutate(undefined)}>Run matching rules</Button>
      </>} />
      <Alert tone="info">A mismatch is an exception requiring investigation — not proof of fraud, theft or financial loss.</Alert>
      <div className="my-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Open cases" value={openCount} />
        <Stat label="Awaiting evidence" value={summary.awaiting_evidence?.count ?? 0} />
        <Stat label="Resolved" value={summary.resolved?.count ?? 0} tone="success" />
        <Stat label="Accepted differences" value={summary.accepted_difference?.count ?? 0} />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ key: "cases", label: "Cases" }, { key: "matches", label: "Record matches" }]} />
      {tab === "cases" ? (
        <Card padded={false}>
          <div className="flex flex-wrap gap-2 border-b border-border p-3">
            <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="max-w-[230px]">
              <option value="open,investigating,awaiting_evidence">Open (all active)</option>
              {["open", "investigating", "awaiting_evidence", "resolved", "accepted_difference", "closed"].map((s) => <option key={s} value={s}>{titleCase(s)}</option>)}
              <option value="">All</option>
            </Select>
            <Select aria-label="Category" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }} className="max-w-[230px]">
              <option value="">All categories</option>
              {CATS.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}
            </Select>
          </div>
          {cases.isPending ? <Spinner /> : cases.error ? <ErrorState error={cases.error} /> : <>
            <DataTable<C> rows={cases.data.items} rowKey={(r) => r.id} onRowClick={(r) => router.push(`/reconciliation/${r.id}`)} columns={[
              { key: "n", header: "Case", cell: (r) => <span className="font-medium">{r.case_number} <SyntheticTag show={r.is_synthetic} /></span> },
              { key: "c", header: "Category", cell: (r) => titleCase(r.category) },
              { key: "t", header: "Title", cell: (r) => <span className="line-clamp-2 max-w-md">{r.title}</span> },
              { key: "d", header: "Difference", align: "right", cell: (r) => money(r.difference) },
              { key: "a", header: "Owner", cell: (r) => r.assigned_to ?? <span className="text-muted">Unassigned</span> },
              { key: "ag", header: "Age", align: "right", cell: (r) => `${r.age_days} d` },
              { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
            ]} empty={<p className="p-6 text-center text-sm text-muted">No cases match these filters.</p>} />
            <Pagination page={page} pageSize={25} total={cases.data.total} onPage={setPage} />
          </>}
        </Card>
      ) : (
        <Card padded={false}>
          {matches.isPending ? <Spinner /> : matches.error ? <ErrorState error={matches.error} /> : (
            <DataTable<M> rows={matches.data} rowKey={(m) => m.id} columns={[
              { key: "l", header: "Record", cell: (m) => titleCase(m.left_type) },
              { key: "r", header: "Matched to", cell: (m) => titleCase(m.right_type) },
              { key: "m", header: "Method", cell: (m) => titleCase(m.method) },
              { key: "sc", header: "Score", align: "right", cell: (m) => m.score ?? "—" },
              { key: "d", header: "Detail", cell: (m) => <span className="text-xs text-muted">{m.details ? Object.entries(m.details).slice(0, 3).map(([k, v]) => `${k}: ${String(v)}`).join(" · ") : ""}</span> },
              { key: "c", header: "Created", cell: (m) => date(m.created_at) },
              { key: "s", header: "Status", cell: (m) => <StatusChip status={m.status === "unmatched" ? "unmatched_record" : m.status} label={titleCase(m.status)} /> },
              { key: "x", header: "", cell: (m) => can("finance.reconcile") && m.status !== "unmatched" ? <span className="flex gap-1">{m.status === "suggested" && <Button size="sm" onClick={() => confirm.mutate(m.id)}>Confirm</Button>}<Button size="sm" variant="ghost" onClick={() => unmatch.mutate(m.id)}>Unmatch</Button></span> : null },
            ]} empty={<p className="p-6 text-center text-sm text-muted">No matches yet. Run the matching rules.</p>} />
          )}
        </Card>
      )}
      <Dialog open={creating} onClose={() => setCreating(false)} title="New reconciliation case" footer={<><Button onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" disabled={nc.title.length < 3} loading={create.isPending} onClick={() => create.mutate(undefined)}>Create</Button></>}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Category">{(id) => <Select id={id} value={nc.category} onChange={(e) => setNc({ ...nc, category: e.target.value })}>{CATS.map((c) => <option key={c} value={c}>{titleCase(c)}</option>)}</Select>}</Field>
          <Field label="Title" required>{(id) => <Input id={id} value={nc.title} onChange={(e) => setNc({ ...nc, title: e.target.value })} />}</Field>
          <Field label="Expected value ₹">{(id) => <Input id={id} type="number" step="0.01" value={nc.expected_value} onChange={(e) => setNc({ ...nc, expected_value: e.target.value })} />}</Field>
          <Field label="Observed value ₹">{(id) => <Input id={id} type="number" step="0.01" value={nc.observed_value} onChange={(e) => setNc({ ...nc, observed_value: e.target.value })} />}</Field>
          <Field label="Description" className="sm:col-span-2">{(id) => <Textarea id={id} rows={3} value={nc.description} onChange={(e) => setNc({ ...nc, description: e.target.value })} />}</Field>
        </div>
      </Dialog>
    </>
  );
}
