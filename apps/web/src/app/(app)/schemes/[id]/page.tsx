"use client";

import { Plus, Trash2 } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { ProductPicker, RetailerPicker } from "@/components/pickers";
import { emptyVersion, SchemeVersionEditor, toApiVersion, TYPE_LABELS, type VersionForm } from "@/components/scheme-editor";
import { Alert, Badge, Button, Card, Dialog, ErrorState, Field, Input, Json, KeyValue, PageHeader, Spinner, StatusChip, SyntheticTag, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, money, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type Version = { id: string; version: number; status: string; scheme_type: string; config: Record<string, unknown>; scope: VersionForm["scope"]; eligibility: VersionForm["eligibility"]; start_date: string; end_date: string; stacking: string; priority: number; max_benefit: string | null; source_reference: string | null; source_document_id: string | null; extraction_id: string | null; change_note: string | null; created_by: string | null; submitted_by: string | null; approved_by: string | null; approved_at: string | null; created_at: string };
type Scheme = { id: string; code: string; name: string; status: string; funded_by: string; supplier: string | null; active_version: number | null; applications: number; benefit_total: string; is_synthetic: boolean; versions: Version[]; claims: { id: string; claim_number: string; expected_amount: string; status: string; period_start: string; period_end: string }[] };
type Preview = { lines: { line: number; sku: string; base_quantity: string; gross: string; scheme_discount: string }[]; applied: Record<string, string>[]; skipped: { code: string; reason: string }[]; total_discount: string; issues: string[] };

const toForm = (v: Version): VersionForm => ({ ...emptyVersion(), ...v, max_benefit: v.max_benefit ?? "", source_reference: v.source_reference ?? "", change_note: "" });

export default function SchemeDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: s, isPending, error, refetch } = useApi<Scheme>(`/schemes/${id}`);
  const [editing, setEditing] = useState<{ mode: "edit" | "new"; vid?: string; v: VersionForm } | null>(null);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [deact, setDeact] = useState(false);
  const [reason, setReason] = useState("");
  const [claimDlg, setClaimDlg] = useState(false);
  const [period, setPeriod] = useState({ period_start: "", period_end: "" });
  const [pv, setPv] = useState<{ retailer: string | null; lines: { product_id: string | null; quantity: string; unit: string }[]; versionId: string | null }>({ retailer: null, lines: [{ product_id: null, quantity: "24", unit: "PCS" }], versionId: null });
  const [preview, setPreview] = useState<Preview | null>(null);
  const inv = ["/schemes", "/orders"];
  const wf = useAction((v: { path: string; body?: unknown }) => api(`/schemes/${id}${v.path}`, { body: v.body ?? {} }), { invalidate: inv, onSuccess: () => { setRejecting(null); setDeact(false); setReason(""); } });
  const saveVersion = useAction(() => editing!.mode === "edit" ? api(`/schemes/${id}/versions/${editing!.vid}`, { method: "PUT", body: toApiVersion(editing!.v) }) : api(`/schemes/${id}/versions`, { body: toApiVersion(editing!.v) }), { invalidate: inv, success: "Version saved", onSuccess: () => setEditing(null) });
  const runPreview = useAction(() => api<Preview>("/schemes/preview", { body: { retailer_id: pv.retailer, lines: pv.lines.filter((l) => l.product_id), include_version_id: pv.versionId } }), { onSuccess: setPreview });
  const claim = useAction(() => api(`/schemes/claims`, { body: { scheme_id: id, ...period } }), { invalidate: inv, success: "Claim prepared", onSuccess: () => setClaimDlg(false) });

  if (isPending) return <Spinner />;
  if (error || !s) return <ErrorState error={error} retry={refetch} />;
  const open = s.versions.find((v) => v.status === "draft" || v.status === "pending_approval");
  const latest = s.versions[s.versions.length - 1];

  return (
    <>
      <PageHeader back={{ href: "/schemes", label: "Schemes" }} title={<span className="flex items-center gap-2">{s.name} <StatusChip status={s.status} /> <SyntheticTag show={s.is_synthetic} /></span>} description={`${s.code} · funded by ${s.supplier ?? s.funded_by} · applied ${s.applications}× · ${money(s.benefit_total)} benefit`}
        actions={<>
          {s.funded_by === "supplier" && can("schemes.manage") && <Button onClick={() => setClaimDlg(true)}>Prepare claim</Button>}
          {s.active_version && can("schemes.approve") && <Button variant="danger" onClick={() => setDeact(true)}>Deactivate</Button>}
          {!open && can("schemes.manage") && <Button variant="primary" onClick={() => setEditing({ mode: "new", v: toForm(latest) })}>New version</Button>}
        </>} />
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-3">
          {[...s.versions].reverse().map((v) => (
            <Card key={v.id} title={<span className="flex items-center gap-2">Version {v.version} <StatusChip status={v.status} /> <Badge>{TYPE_LABELS[v.scheme_type]}</Badge></span>} actions={<>
              {v.status === "draft" && can("schemes.manage") && <><Button size="sm" onClick={() => setEditing({ mode: "edit", vid: v.id, v: toForm(v) })}>Edit</Button><Button size="sm" variant="accent" onClick={() => wf.mutate({ path: `/versions/${v.id}/submit` })}>Submit for approval</Button></>}
              {v.status === "pending_approval" && can("schemes.approve") && <><Button size="sm" variant="danger" onClick={() => setRejecting(v.id)}>Reject</Button><Button size="sm" variant="accent" onClick={() => wf.mutate({ path: `/versions/${v.id}/approve` })}>Approve & activate</Button></>}
              {["draft", "pending_approval"].includes(v.status) && <Button size="sm" onClick={() => { setPv({ ...pv, versionId: v.id }); }}>Use in preview</Button>}
            </>}>
              {v.extraction_id && v.status === "draft" && <div className="mb-3"><Alert tone="warning" title="Drafted from document extraction">{v.change_note}</Alert></div>}
              <div className="grid gap-4 md:grid-cols-2">
                <KeyValue items={[["Effective", `${date(v.start_date)} – ${date(v.end_date)}`], ["Stacking", `${titleCase(v.stacking)} · priority ${v.priority}`], ["Cap per order", v.max_benefit ? money(v.max_benefit) : "None"], ["Source", v.source_reference], ["Created", `${v.created_by ?? "system"} · ${dateTime(v.created_at)}`], ["Approved", v.approved_at ? `${v.approved_by} · ${dateTime(v.approved_at)}` : "—"]]} />
                <div className="space-y-2 text-sm">
                  <p className="text-xs font-semibold text-ink-2">Benefit</p><Json value={v.config} />
                  <p className="text-xs text-muted">Scope: {[...v.scope.brands, ...v.scope.categories].join(", ") || (v.scope.product_ids.length ? `${v.scope.product_ids.length} product(s)` : "all products")} · Eligibility: {v.eligibility.territories.join(", ") || (v.eligibility.retailer_ids.length ? `${v.eligibility.retailer_ids.length} retailer(s)` : "all retailers")}</p>
                </div>
              </div>
            </Card>
          ))}
        </div>
        <div className="space-y-4">
          <Card title="Eligibility & calculation preview">
            <div className="space-y-2">
              <p className="text-xs text-muted">Runs the deterministic engine with active schemes{pv.versionId ? " plus the selected draft version" : ""}. Nothing is saved.</p>
              <Field label="Retailer">{(fid) => <RetailerPicker id={fid} value={pv.retailer} onChange={(r) => setPv({ ...pv, retailer: r })} />}</Field>
              {pv.lines.map((l, i) => (
                <div key={i} className="grid grid-cols-[1fr_70px_auto] gap-1.5">
                  <ProductPicker value={l.product_id} onChange={(p) => setPv({ ...pv, lines: pv.lines.map((x, j) => (j === i ? { ...x, product_id: p } : x)) })} />
                  <Input aria-label="Quantity" type="number" min="0" value={l.quantity} onChange={(e) => setPv({ ...pv, lines: pv.lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)) })} />
                  <Button variant="ghost" aria-label="Remove" onClick={() => setPv({ ...pv, lines: pv.lines.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></Button>
                </div>
              ))}
              <div className="flex gap-2">
                <Button size="sm" onClick={() => setPv({ ...pv, lines: [...pv.lines, { product_id: null, quantity: "1", unit: "PCS" }] })}><Plus className="h-3.5 w-3.5" /> Line</Button>
                <Button size="sm" variant="primary" disabled={!pv.retailer || !pv.lines.some((l) => l.product_id)} loading={runPreview.isPending} onClick={() => runPreview.mutate(undefined)}>Calculate</Button>
              </div>
              {preview && (
                <div className="space-y-2 border-t border-border pt-2 text-sm">
                  {preview.issues.map((x) => <p key={x} className="text-xs text-danger">{x}</p>)}
                  {preview.applied.map((a, i) => <div key={i} className="rounded-md bg-success-bg p-2 text-xs"><b>{a.scheme_name}</b> → {money(a.benefit)} · {a.calculation}</div>)}
                  {preview.skipped.map((x, i) => <p key={i} className="text-xs text-muted">{x.code}: {x.reason}</p>)}
                  <p className="font-semibold">Total discount {money(preview.total_discount)}</p>
                </div>
              )}
            </div>
          </Card>
          {s.claims.length > 0 && (
            <Card title="Claims">
              <ul className="space-y-1 text-sm">{s.claims.map((c) => <li key={c.id} className="flex justify-between"><span>{c.claim_number}</span><span className="tabular">{money(c.expected_amount)}</span><StatusChip status={c.status} /></li>)}</ul>
            </Card>
          )}
        </div>
      </div>
      <Dialog wide open={Boolean(editing)} onClose={() => setEditing(null)} title={editing?.mode === "edit" ? "Edit draft version" : "New version (draft)"} footer={<><Button onClick={() => setEditing(null)}>Cancel</Button><Button variant="primary" loading={saveVersion.isPending} onClick={() => saveVersion.mutate(undefined)}>Save draft</Button></>}>
        {editing && <><SchemeVersionEditor v={editing.v} onChange={(v) => setEditing({ ...editing, v })} /><Field label="Change note" className="mt-3">{(fid) => <Textarea id={fid} rows={2} value={editing.v.change_note} onChange={(e) => setEditing({ ...editing, v: { ...editing.v, change_note: e.target.value } })} />}</Field></>}
      </Dialog>
      <Dialog open={Boolean(rejecting) || deact} onClose={() => { setRejecting(null); setDeact(false); }} title={deact ? "Deactivate scheme" : "Reject version"} footer={<><Button onClick={() => { setRejecting(null); setDeact(false); }}>Back</Button><Button variant="danger" disabled={reason.trim().length < 3} loading={wf.isPending} onClick={() => wf.mutate(deact ? { path: "/deactivate", body: { reason } } : { path: `/versions/${rejecting}/reject`, body: { reason } })}>Confirm</Button></>}>
        <Field label="Reason" required>{(fid) => <Textarea id={fid} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      </Dialog>
      <Dialog open={claimDlg} onClose={() => setClaimDlg(false)} title="Prepare supplier claim" footer={<><Button onClick={() => setClaimDlg(false)}>Cancel</Button><Button variant="primary" disabled={!period.period_start || !period.period_end} loading={claim.isPending} onClick={() => claim.mutate(undefined)}>Prepare claim</Button></>}>
        <p className="mb-3 text-sm text-muted">Totals unclaimed benefits on dispatched orders with an order date in the period. Each benefit can be claimed once.</p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="From">{(fid) => <Input id={fid} type="date" value={period.period_start} onChange={(e) => setPeriod({ ...period, period_start: e.target.value })} />}</Field>
          <Field label="To">{(fid) => <Input id={fid} type="date" value={period.period_end} onChange={(e) => setPeriod({ ...period, period_end: e.target.value })} />}</Field>
        </div>
      </Dialog>
    </>
  );
}
