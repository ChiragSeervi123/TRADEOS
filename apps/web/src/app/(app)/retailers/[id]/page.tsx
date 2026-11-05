"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { PartyForm } from "@/components/party-form";
import { Button, Card, Dialog, ErrorState, Field, Input, KeyValue, PageHeader, Spinner, StatusChip, SyntheticTag, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, money, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type Ret = Record<string, string | number | boolean | null> & {
  id: string; code: string; name: string; is_active: boolean; is_synthetic: boolean; credit_limit: string | null; payment_terms_days: number;
  recent_orders: { id: string; order_number: string; status: string; net_total: string; order_date: string }[]; order_count: number;
  credit?: { credit_limit: string | null; outstanding: string; open_orders: string; exposure: string; available: string | null; exceeded: boolean };
  activity: { action: string; at: string; after: Record<string, unknown> | null }[];
};

export default function RetailerDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: r, isPending, error, refetch } = useApi<Ret>(`/retailers/${id}`);
  const [edit, setEdit] = useState(false);
  const [credit, setCredit] = useState(false);
  const [ct, setCt] = useState({ credit_limit: "", payment_terms_days: "", reason: "" });
  const saveCredit = useAction(() => api(`/retailers/${id}/credit-terms`, { method: "PUT", body: { credit_limit: ct.credit_limit === "" ? null : ct.credit_limit, payment_terms_days: ct.payment_terms_days ? Number(ct.payment_terms_days) : null, reason: ct.reason } }), { invalidate: ["/retailers"], success: "Credit terms updated", onSuccess: () => setCredit(false) });

  if (isPending) return <Spinner />;
  if (error || !r) return <ErrorState error={error} retry={refetch} />;
  return (
    <>
      <PageHeader back={{ href: "/retailers", label: "Retailers" }} title={<span className="flex items-center gap-2">{r.name} <StatusChip status={r.is_active ? "active" : "inactive"} /> <SyntheticTag show={r.is_synthetic} /></span>} description={`${r.code} · ${[r.territory, r.route].filter(Boolean).join(" · ")}`}
        actions={<>
          {can("finance.approve") && <Button onClick={() => { setCt({ credit_limit: r.credit_limit ?? "", payment_terms_days: String(r.payment_terms_days), reason: "" }); setCredit(true); }}>Credit terms</Button>}
          {can("finance.read") && <Link className="inline-flex h-9 items-center rounded-md border border-border-strong bg-surface px-3.5 text-sm font-medium hover:bg-surface-2" href={`/finance?tab=receivables&retailer=${r.id}`}>Statement</Link>}
          {can("retailers.write") && <Button variant="primary" onClick={() => setEdit(true)}>Edit</Button>}
        </>} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Contact">
          <KeyValue items={[["Contact", r.contact_name], ["Phone", r.phone], ["Email", r.email], ["GSTIN", r.gstin], ["Billing address", r.billing_address], ["Delivery address", r.delivery_address], ["Payment terms", `${r.payment_terms_days} days`]]} />
        </Card>
        {r.credit && (
          <Card title="Credit exposure">
            <KeyValue items={[["Credit limit", r.credit.credit_limit ? money(r.credit.credit_limit) : "No limit"], ["Outstanding invoices", money(r.credit.outstanding)], ["Open (uninvoiced) orders", money(r.credit.open_orders)], ["Total exposure", <b key="e" className={r.credit.exceeded ? "text-danger" : ""}>{money(r.credit.exposure)}</b>], ["Available", r.credit.available ? money(r.credit.available) : "—"]]} />
          </Card>
        )}
        <Card title={`Recent orders · ${r.order_count} total`}>
          <ul className="space-y-1 text-sm">
            {r.recent_orders.map((o) => <li key={o.id} className="flex items-center justify-between gap-2"><Link href={`/orders/${o.id}`} className="text-info hover:underline">{o.order_number}</Link><span className="text-xs text-muted">{date(o.order_date)}</span><span className="tabular">{money(o.net_total)}</span><StatusChip status={o.status} /></li>)}
            {!r.recent_orders.length && <li className="text-muted">No orders yet.</li>}
          </ul>
        </Card>
        <Card title="Activity" className="lg:col-span-3">
          <ul className="space-y-1.5 text-sm">
            {r.activity.map((a, i) => <li key={i}><b>{titleCase(a.action.replace("retailer.", ""))}</b> <span className="text-xs text-muted">{dateTime(a.at)}</span> {a.after && <span className="text-xs text-muted">{Object.entries(a.after).slice(0, 4).map(([k, v]) => `${k}: ${String(v)}`).join(", ")}</span>}</li>)}
          </ul>
        </Card>
      </div>
      <PartyForm kind="retailer" open={edit} onClose={() => setEdit(false)} initial={r} onSaved={() => refetch()} />
      <Dialog open={credit} onClose={() => setCredit(false)} title="Credit terms" footer={<><Button onClick={() => setCredit(false)}>Cancel</Button><Button variant="primary" disabled={ct.reason.trim().length < 3} loading={saveCredit.isPending} onClick={() => saveCredit.mutate(undefined)}>Save</Button></>}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Credit limit (₹)" hint="Empty = no limit">{(fid) => <Input id={fid} type="number" min="0" step="0.01" value={ct.credit_limit} onChange={(e) => setCt({ ...ct, credit_limit: e.target.value })} />}</Field>
          <Field label="Payment terms (days)">{(fid) => <Input id={fid} type="number" min="0" max="365" value={ct.payment_terms_days} onChange={(e) => setCt({ ...ct, payment_terms_days: e.target.value })} />}</Field>
          <Field label="Reason for change" required className="sm:col-span-2">{(fid) => <Textarea id={fid} rows={2} value={ct.reason} onChange={(e) => setCt({ ...ct, reason: e.target.value })} />}</Field>
        </div>
      </Dialog>
    </>
  );
}
