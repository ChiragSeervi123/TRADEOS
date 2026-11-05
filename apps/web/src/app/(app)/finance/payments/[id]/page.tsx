"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Card, DataTable, Dialog, ErrorState, Field, Input, KeyValue, PageHeader, Spinner, StatusChip, SyntheticTag, Textarea } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, money, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type P = {
  id: string; payment_number: string; retailer: string; retailer_id: string; amount: string; unallocated: string; received_on: string; method: string; reference: string | null; status: string; notes: string | null; is_synthetic: boolean;
  allocations: { id: string; sales_invoice_id: string; invoice_number: string; amount: string; created_at: string; reversed: boolean; reversal_reason: string | null }[];
  suggestions: { sales_invoice_id: string; invoice_number: string; due_date: string; outstanding: string; amount: string }[];
};

export default function PaymentPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: p, isPending, error, refetch } = useApi<P>(`/payments/${id}`);
  const [amounts, setAmounts] = useState<Record<string, string>>({});
  const [reverse, setReverse] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(newIdempotencyKey);
  const inv = ["/payments", "/sales-invoices", "/receivables", "/analytics"];
  const allocate = useAction(() => api(`/payments/${id}/allocations`, { body: { allocations: Object.entries(amounts).filter(([, a]) => Number(a) > 0).map(([sales_invoice_id, amount]) => ({ sales_invoice_id, amount })) }, headers: { "Idempotency-Key": key } }), { invalidate: inv, success: "Allocated", onSuccess: () => { setAmounts({}); setKey(newIdempotencyKey()); } });
  const rev = useAction(() => api(`/payments/${id}/allocations/${reverse}/reverse`, { body: { reason } }), { invalidate: inv, success: "Allocation reversed", onSuccess: () => { setReverse(null); setReason(""); } });

  if (isPending) return <Spinner />;
  if (error || !p) return <ErrorState error={error} retry={refetch} />;
  const total = Object.values(amounts).reduce((s, a) => s + (Number(a) || 0), 0);
  const over = total > Number(p.unallocated) + 0.0001;
  const canAlloc = can("payments.allocate") && p.status === "recorded" && Number(p.unallocated) > 0;

  return (
    <>
      <PageHeader back={{ href: "/finance?tab=payments", label: "Finance" }} title={<span className="flex items-center gap-2">{p.payment_number} <StatusChip status={p.status} /> <SyntheticTag show={p.is_synthetic} /></span>} description={<span><Link className="hover:underline" href={`/retailers/${p.retailer_id}`}>{p.retailer}</Link> · {date(p.received_on)} · {titleCase(p.method)}{p.reference ? ` · ${p.reference}` : ""}</span>} />
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="space-y-4">
          {canAlloc && (
            <Card title="Allocate to invoices" actions={p.suggestions.length > 0 && <Button size="sm" onClick={() => setAmounts(Object.fromEntries(p.suggestions.map((s) => [s.sales_invoice_id, s.amount])))}>Fill oldest-due first</Button>}>
              {p.suggestions.length === 0 ? <p className="text-sm text-muted">This retailer has no open invoices.</p> : (
                <>
                  <DataTable dense rows={p.suggestions} rowKey={(s) => s.sales_invoice_id} columns={[
                    { key: "n", header: "Invoice", cell: (s) => <Link className="text-info hover:underline" href={`/finance/sales-invoices/${s.sales_invoice_id}`}>{s.invoice_number}</Link> },
                    { key: "d", header: "Due", cell: (s) => date(s.due_date) },
                    { key: "o", header: "Outstanding", align: "right", cell: (s) => money(s.outstanding) },
                    { key: "a", header: "Allocate", align: "right", cell: (s) => <Input aria-label={`Allocate to ${s.invoice_number}`} type="number" min="0" max={s.outstanding} step="0.01" className="ml-auto h-8 w-32 text-right" value={amounts[s.sales_invoice_id] ?? ""} onChange={(e) => setAmounts({ ...amounts, [s.sales_invoice_id]: e.target.value })} /> },
                  ]} />
                  <div className="mt-3 flex items-center justify-between">
                    <span className={`text-sm ${over ? "text-danger" : "text-muted"}`}>Allocating {money(total)} of {money(p.unallocated)} unallocated</span>
                    <Button variant="primary" disabled={!total || over} loading={allocate.isPending} onClick={() => allocate.mutate(undefined)}>Allocate</Button>
                  </div>
                  <p className="mt-2 text-xs text-muted">Suggestions only list open invoices of this retailer. Over-allocation is rejected by the server.</p>
                </>
              )}
            </Card>
          )}
          <Card title="Allocations" padded={false}>
            <DataTable dense rows={p.allocations} rowKey={(a) => a.id} columns={[
              { key: "n", header: "Invoice", cell: (a) => <Link className="text-info hover:underline" href={`/finance/sales-invoices/${a.sales_invoice_id}`}>{a.invoice_number}</Link> },
              { key: "a", header: "Amount", align: "right", cell: (a) => <span className={a.reversed ? "text-muted line-through" : ""}>{money(a.amount)}</span> },
              { key: "d", header: "When", cell: (a) => dateTime(a.created_at) },
              { key: "s", header: "", cell: (a) => a.reversed ? <span className="text-xs text-muted">reversed: {a.reversal_reason}</span> : can("payments.allocate") ? <Button size="sm" variant="ghost" onClick={() => setReverse(a.id)}>Reverse</Button> : null },
            ]} empty={<p className="p-4 text-sm text-muted">Not allocated yet.</p>} />
          </Card>
        </div>
        <Card title="Payment">
          <KeyValue items={[["Amount", <b key="a">{money(p.amount)}</b>], ["Unallocated", <b key="u" className={Number(p.unallocated) > 0 ? "text-accent-strong" : "text-success"}>{money(p.unallocated)}</b>], ["Notes", p.notes]]} />
          <div className="mt-3"><Alert tone="info">TRADEOS records payments received; it never initiates payments or collection messages.</Alert></div>
        </Card>
      </div>
      <Dialog open={Boolean(reverse)} onClose={() => setReverse(null)} title="Reverse allocation" footer={<><Button onClick={() => setReverse(null)}>Back</Button><Button variant="danger" disabled={reason.trim().length < 3} loading={rev.isPending} onClick={() => rev.mutate(undefined)}>Reverse</Button></>}>
        <Field label="Reason" required>{(fid) => <Textarea id={fid} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      </Dialog>
    </>
  );
}
