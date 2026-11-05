"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Badge, Button, Card, DataTable, Dialog, ErrorState, Field, Input, Json, PageHeader, Pagination, Select, Spinner, StatusChip, SyntheticTag, Textarea, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, titleCase } from "@/lib/format";
import { useAction, useApi, usePage } from "@/lib/hooks";

type E = { id: string; exception_type: string; type_label: string; severity: string; status: string; title: string; description: string | null; entity_type: string | null; entity_id: string | null; evidence: Record<string, unknown> | null; suggested_action: string | null; assigned_to_id: string | null; assigned_to: string | null; due_date: string | null; resolution_reason: string | null; created_at: string; age_days: number; is_synthetic: boolean };
type Detail = E & { comments: { id: string; body: string; author: string | null; at: string }[]; history: { action: string; at: string; meta: Record<string, unknown> | null }[] };

const ENTITY_LINK: Record<string, string> = { order: "/orders/", order_source: "/inbox/", purchase_invoice: "/finance/purchase-invoices/", reconciliation_case: "/reconciliation/", scheme: "/schemes/" };

function Inner() {
  const sp = useSearchParams();
  const { can, me } = useAuth();
  const [type, setType] = useState(sp.get("type") ?? "");
  const [severity, setSeverity] = useState("");
  const [status, setStatus] = useState("open,in_progress");
  const [assigned, setAssigned] = useState("");
  const [minAge, setMinAge] = useState("");
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<string | null>(null);
  const list = usePage<E>("/exceptions", { exception_type: type, severity, status, assigned_to: assigned, min_age_days: minAge, page, page_size: 25 });
  const types = (list.data?.types ?? {}) as Record<string, string>;
  const openByType = (list.data?.open_by_type ?? {}) as Record<string, number>;

  return (
    <>
      <PageHeader title="Exceptions" description="One queue for everything that needs a human decision. The system raises items; only people resolve them." actions={can("inventory.read") && <ScanButton />} />
      <div className="mb-3 flex flex-wrap gap-2">
        {Object.entries(openByType).map(([t, n]) => (
          <button key={t} onClick={() => { setType(type === t ? "" : t); setPage(1); }} className={cx("rounded-full border px-3 py-1 text-xs", type === t ? "border-brand bg-brand text-white" : "border-border-strong bg-surface hover:bg-surface-2")}>{types[t] ?? titleCase(t)} · {n}</button>
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
        <Card padded={false}>
          <div className="flex flex-wrap gap-2 border-b border-border p-3">
            <Select aria-label="Type" value={type} onChange={(e) => { setType(e.target.value); setPage(1); }} className="max-w-[220px]"><option value="">All types</option>{Object.entries(types).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</Select>
            <Select aria-label="Severity" value={severity} onChange={(e) => { setSeverity(e.target.value); setPage(1); }} className="max-w-[140px]"><option value="">Any severity</option>{["critical", "high", "medium", "low"].map((s) => <option key={s}>{s}</option>)}</Select>
            <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="max-w-[160px]"><option value="open,in_progress">Open</option><option value="resolved,dismissed">Closed</option><option value="all">All</option></Select>
            <Select aria-label="Owner" value={assigned} onChange={(e) => { setAssigned(e.target.value); setPage(1); }} className="max-w-[150px]"><option value="">Any owner</option><option value="me">Assigned to me</option><option value="unassigned">Unassigned</option></Select>
            <Input aria-label="Minimum age in days" type="number" min="0" placeholder="Min age (days)" value={minAge} onChange={(e) => { setMinAge(e.target.value); setPage(1); }} className="max-w-[130px]" />
          </div>
          {list.isPending ? <Spinner /> : list.error ? <ErrorState error={list.error} /> : <>
            <DataTable<E> rows={list.data.items} rowKey={(r) => r.id} onRowClick={(r) => setSel(r.id)} columns={[
              { key: "s", header: "Severity", cell: (r) => <StatusChip status={r.severity} /> },
              { key: "t", header: "Exception", cell: (r) => <div className="max-w-md"><p className={cx("line-clamp-2", sel === r.id && "font-semibold")}>{r.title} <SyntheticTag show={r.is_synthetic} /></p><p className="text-xs text-muted">{r.type_label}</p></div> },
              { key: "o", header: "Owner", cell: (r) => r.assigned_to ?? <span className="text-muted">—</span> },
              { key: "a", header: "Age", align: "right", cell: (r) => `${r.age_days} d` },
              { key: "st", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
            ]} empty={<p className="p-6 text-center text-sm text-muted">Nothing in the queue.</p>} />
            <Pagination page={page} pageSize={25} total={list.data.total} onPage={setPage} />
          </>}
        </Card>
        {sel ? <ExceptionPanel id={sel} meId={me?.user.id ?? ""} /> : <Card><p className="text-sm text-muted">Select an exception to see its evidence and take action.</p></Card>}
      </div>
    </>
  );
}

function ScanButton() {
  const scan = useAction(() => api<{ batches_flagged: number }>("/exceptions/scan-expiry", { method: "POST" }), { invalidate: ["/exceptions"], success: (r) => `Expiry scan: ${r.batches_flagged} batch(es) flagged` });
  return <Button onClick={() => scan.mutate(undefined)} loading={scan.isPending}>Scan for expiring stock</Button>;
}

function ExceptionPanel({ id, meId }: { id: string; meId: string }) {
  const { can } = useAuth();
  const { data: e, isPending, error } = useApi<Detail>(`/exceptions/${id}`);
  const members = useApi<{ user_id: string; full_name: string; is_active: boolean }[]>("/users");
  const [comment, setComment] = useState("");
  const [dlg, setDlg] = useState<null | "resolve" | "dismiss" | "reopen">(null);
  const [reason, setReason] = useState("");
  const inv = ["/exceptions", "/analytics"];
  const assign = useAction((uid: string) => api(`/exceptions/${id}/assign`, { body: { user_id: uid || null } }), { invalidate: inv, success: "Assigned" });
  const add = useAction(() => api(`/exceptions/${id}/comments`, { body: { body: comment } }), { invalidate: inv, onSuccess: () => setComment("") });
  const act = useAction(() => dlg === "reopen" ? api(`/exceptions/${id}/reopen`, { body: { reason } }) : api(`/exceptions/${id}/resolve`, { body: { reason, dismiss: dlg === "dismiss" } }), { invalidate: inv, success: "Updated", onSuccess: () => { setDlg(null); setReason(""); } });
  if (isPending) return <Card><Spinner /></Card>;
  if (error || !e) return <Card><ErrorState error={error} /></Card>;
  const closed = ["resolved", "dismissed"].includes(e.status);
  const manage = can("exceptions.manage");
  return (
    <Card title={<span className="flex items-center gap-2"><StatusChip status={e.severity} /> {e.type_label}</span>} actions={<StatusChip status={e.status} />}>
      <div className="space-y-3 text-sm">
        <p className="font-medium">{e.title}</p>
        {e.description && <p className="text-ink-2">{e.description}</p>}
        {e.suggested_action && <div className="rounded-md bg-info-bg p-2 text-xs text-info"><b>Suggested next action:</b> {e.suggested_action}</div>}
        {e.entity_type && e.entity_id && ENTITY_LINK[e.entity_type] && <Link className="text-info underline" href={`${ENTITY_LINK[e.entity_type]}${e.entity_id}`}>Open related {titleCase(e.entity_type)}</Link>}
        {e.evidence && <div><p className="mb-1 text-xs font-semibold text-ink-2">Evidence</p><Json value={e.evidence} /></div>}
        {manage && !closed && (
          <Field label="Owner">{(fid) => <Select id={fid} value={e.assigned_to_id ?? ""} onChange={(x) => assign.mutate(x.target.value)}><option value="">Unassigned</option>{members.data?.filter((m) => m.is_active).map((m) => <option key={m.user_id} value={m.user_id}>{m.full_name}{m.user_id === meId ? " (me)" : ""}</option>)}</Select>}</Field>
        )}
        {e.resolution_reason && <p className="text-xs"><b>Resolution:</b> {e.resolution_reason}</p>}
        <div>
          <p className="mb-1 text-xs font-semibold text-ink-2">Comments</p>
          <ul className="mb-2 space-y-1.5">{e.comments.map((c) => <li key={c.id} className="rounded-md bg-surface-2 p-2 text-xs"><b>{c.author}</b> <span className="text-muted">{dateTime(c.at)}</span><p>{c.body}</p></li>)}</ul>
          <div className="flex gap-2"><Input aria-label="Comment" value={comment} onChange={(x) => setComment(x.target.value)} placeholder="Add a comment" /><Button size="sm" disabled={!comment.trim()} loading={add.isPending} onClick={() => add.mutate(undefined)}>Post</Button></div>
        </div>
        {manage && (
          <div className="flex gap-2 border-t border-border pt-3">
            {!closed ? <><Button variant="accent" size="sm" onClick={() => setDlg("resolve")}>Resolve</Button><Button size="sm" onClick={() => setDlg("dismiss")}>Dismiss</Button></> : <Button size="sm" onClick={() => setDlg("reopen")}>Reopen</Button>}
          </div>
        )}
        <details className="text-xs"><summary className="cursor-pointer text-muted">Audit history ({e.history.length})</summary><ul className="mt-1 space-y-0.5">{e.history.map((h, i) => <li key={i}>{dateTime(h.at)} · {h.action}{h.meta?.reason ? ` · ${String(h.meta.reason)}` : ""}</li>)}</ul></details>
      </div>
      <Dialog open={Boolean(dlg)} onClose={() => setDlg(null)} title={titleCase(dlg)} footer={<><Button onClick={() => setDlg(null)}>Cancel</Button><Button variant="primary" disabled={reason.trim().length < 3} loading={act.isPending} onClick={() => act.mutate(undefined)}>Confirm</Button></>}>
        <Field label={dlg === "reopen" ? "Why reopen?" : "Resolution reason"} required>{(fid) => <Textarea id={fid} rows={3} value={reason} onChange={(x) => setReason(x.target.value)} />}</Field>
        <Badge className="mt-2">Recorded in the audit trail</Badge>
      </Dialog>
    </Card>
  );
}

export default function ExceptionsPage() {
  return <Suspense fallback={<Spinner />}><Inner /></Suspense>;
}
