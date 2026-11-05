"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Dialog, ErrorState, Field, Input, Json, KeyValue, PageHeader, Select, Spinner, StatusChip, SyntheticTag, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, money, titleCase } from "@/lib/format";
import { useAction, useApi, useInvalidate } from "@/lib/hooks";

type Case = {
  id: string; case_number: string; category: string; status: string; title: string; description: string | null; expected_value: string | null; observed_value: string | null; difference: string | null; resolved_value: string | null;
  matching_method: string | null; rule: string | null; confidence: string | null; assigned_to_id: string | null; assigned_to: string | null; explanation: Record<string, unknown> | null; records: { type: string; id: string; label: string }[];
  documents: { id: string; filename: string; url: string }[]; resolution_note: string | null; resolved_by: string | null; resolved_at: string | null; allowed_transitions: string[];
  history: { event: string; from: string | null; to: string | null; note: string | null; actor: string | null; at: string }[]; notice: string; is_synthetic: boolean;
};
type Member = { user_id: string; full_name: string; is_active: boolean };

const LINKS: Record<string, string> = { purchase_invoice: "/finance/purchase-invoices/", payment: "/finance/payments/", sales_invoice: "/finance/sales-invoices/" };

export default function CasePage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const toast = useToast();
  const inv = useInvalidate();
  const { data: c, isPending, error, refetch } = useApi<Case>(`/reconciliation/cases/${id}`);
  const members = useApi<Member[]>("/users");
  const [to, setTo] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [resolved, setResolved] = useState("");
  const [evNote, setEvNote] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const transition = useAction(() => api(`/reconciliation/cases/${id}/transition`, { body: { status: to, note: note || null, resolved_value: resolved || null } }), { invalidate: ["/reconciliation", "/exceptions", "/analytics"], success: "Case updated", onSuccess: () => { setTo(null); setNote(""); setResolved(""); } });
  const assign = useAction((uid: string) => api(`/reconciliation/cases/${id}/assign`, { body: { user_id: uid || null } }), { invalidate: ["/reconciliation"], success: "Assignment updated" });

  const addEvidence = async () => {
    setBusy(true);
    const form = new FormData();
    form.append("note", evNote);
    if (file) form.append("file", file);
    try {
      await api(`/reconciliation/cases/${id}/notes`, { form });
      setEvNote("");
      setFile(null);
      inv("/reconciliation");
      toast.success(file ? "Evidence attached" : "Note added");
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  if (isPending) return <Spinner />;
  if (error || !c) return <ErrorState error={error} retry={refetch} />;
  const exp = c.explanation ?? {};
  const lines = (exp.lines ?? []) as Record<string, string>[];
  const canAct = can("finance.reconcile");

  return (
    <>
      <PageHeader back={{ href: "/reconciliation", label: "Reconciliation" }} title={<span className="flex items-center gap-2">{c.case_number} <StatusChip status={c.status} /> <SyntheticTag show={c.is_synthetic} /></span>} description={`${titleCase(c.category)} · ${c.title}`}
        actions={canAct && c.allowed_transitions.map((t) => <Button key={t} variant={t === "resolved" ? "accent" : "secondary"} onClick={() => setTo(t)}>{t === "investigating" && ["resolved", "accepted_difference"].includes(c.status) ? "Reopen" : titleCase(t)}</Button>)} />
      <Alert tone="info">{c.notice}</Alert>
      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_360px]">
        <div className="space-y-4">
          <div className="grid grid-cols-3 gap-3">
            <Card><p className="text-xs text-muted">Expected</p><p className="text-lg font-semibold tabular">{money(c.expected_value)}</p></Card>
            <Card><p className="text-xs text-muted">Observed</p><p className="text-lg font-semibold tabular">{money(c.observed_value)}</p></Card>
            <Card><p className="text-xs text-muted">Difference (observed − expected)</p><p className="text-lg font-semibold tabular text-accent-strong">{money(c.difference)}</p></Card>
          </div>
          <Card title="Explanation">
            {c.description && <p className="mb-3 text-sm">{c.description}</p>}
            <KeyValue items={[["Matching method", c.matching_method], ["Rule", c.rule], ["Confidence", c.confidence ?? "—"], ["Calculation", exp.calculation ? String(exp.calculation) : "—"]]} />
            {lines.length > 0 && (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr className="border-b border-border text-left text-xs text-muted">{Object.keys(lines[0]).map((k) => <th key={k} className="px-2 py-1 font-medium">{titleCase(k)}</th>)}</tr></thead>
                  <tbody>{lines.map((l, i) => <tr key={i} className="border-b border-border last:border-0">{Object.values(l).map((v, j) => <td key={j} className="px-2 py-1 tabular">{String(v)}</td>)}</tr>)}</tbody>
                </table>
              </div>
            )}
            {!lines.length && Object.keys(exp).length > 1 && <div className="mt-3"><Json value={exp} /></div>}
          </Card>
          <Card title="Records compared">
            <ul className="space-y-1 text-sm">{c.records.map((r) => <li key={r.id}>{titleCase(r.type)}: {LINKS[r.type] ? <Link className="text-info hover:underline" href={`${LINKS[r.type]}${r.id}`}>{r.label}</Link> : r.label}</li>)}</ul>
          </Card>
          <Card title="History">
            <ol className="space-y-2 text-sm">
              {c.history.map((h, i) => (
                <li key={i} className="border-l-2 border-border pl-3">
                  <p><b>{titleCase(h.event)}</b>{h.to && <> → <StatusChip status={h.to} /></>}</p>
                  <p className="text-xs text-muted">{dateTime(h.at)}{h.actor ? ` · ${h.actor}` : " · system"}</p>
                  {h.note && <p className="text-xs">{h.note}</p>}
                </li>
              ))}
            </ol>
          </Card>
        </div>
        <div className="space-y-4">
          <Card title="Owner">
            {canAct ? (
              <Select aria-label="Assign to" value={c.assigned_to_id ?? ""} onChange={(e) => assign.mutate(e.target.value)}>
                <option value="">Unassigned</option>
                {members.data?.filter((m) => m.is_active).map((m) => <option key={m.user_id} value={m.user_id}>{m.full_name}</option>)}
              </Select>
            ) : <p className="text-sm">{c.assigned_to ?? "Unassigned"}</p>}
          </Card>
          {c.resolved_at && <Card title="Resolution"><KeyValue items={[["By", c.resolved_by], ["When", dateTime(c.resolved_at)], ["Resolved value", money(c.resolved_value)], ["Note", c.resolution_note]]} /></Card>}
          <Card title="Evidence">
            <ul className="mb-3 space-y-1 text-sm">{c.documents.map((d) => <li key={d.id}><a className="text-info underline" href={d.url} target="_blank" rel="noreferrer">{d.filename}</a></li>)}{!c.documents.length && <li className="text-muted">No documents.</li>}</ul>
            {canAct && c.status !== "closed" && (
              <div className="space-y-2">
                <Textarea aria-label="Note" rows={2} placeholder="Add a note or describe the evidence" value={evNote} onChange={(e) => setEvNote(e.target.value)} />
                <input type="file" aria-label="Evidence file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-xs" />
                <Button size="sm" disabled={!evNote.trim()} loading={busy} onClick={addEvidence}>Add</Button>
              </div>
            )}
          </Card>
        </div>
      </div>
      <Dialog open={Boolean(to)} onClose={() => setTo(null)} title={`Move to ${titleCase(to)}`} footer={<><Button onClick={() => setTo(null)}>Cancel</Button><Button variant="primary" loading={transition.isPending} disabled={["resolved", "accepted_difference"].includes(to ?? "") && note.trim().length < 3} onClick={() => transition.mutate(undefined)}>Confirm</Button></>}>
        <div className="space-y-3">
          <Field label={["resolved", "accepted_difference"].includes(to ?? "") ? "Resolution note (required)" : "Note"}>{(fid) => <Textarea id={fid} rows={3} value={note} onChange={(e) => setNote(e.target.value)} />}</Field>
          {to === "resolved" && <Field label="Resolved value ₹" hint="Amount recovered or corrected; defaults to the difference.">{(fid) => <Input id={fid} type="number" step="0.01" value={resolved} onChange={(e) => setResolved(e.target.value)} />}</Field>}
        </div>
      </Dialog>
    </>
  );
}
