"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Alert, Button, Card, DataTable, Dialog, ErrorState, Field, Input, KeyValue, PageHeader, Spinner, StatusChip } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, qty } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type Line = { id: string; line_no: number; sku: string; name: string; variant: string | null; base_unit: string; batch_number: string | null; expiry_date: string | null; requested_quantity: string; picked_quantity: string | null };
type PL = { id: string; pick_number: string; status: string; order_id: string; order_number: string; order_status: string; retailer: string; delivery_address: string | null; route: string | null; warehouse: string; requested_delivery_date: string | null; lines: Line[]; dispatch: { id: string; dispatch_number: string; status: string } | null };

export default function PickListPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: p, isPending, error, refetch } = useApi<PL>(`/fulfilment/pick-lists/${id}`);
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [dispatching, setDispatching] = useState(false);
  const [vehicle, setVehicle] = useState("");
  const inv = ["/fulfilment", "/orders", "/inventory", "/sales-invoices"];
  const start = useAction(() => api(`/fulfilment/pick-lists/${id}/start`, { method: "POST" }), { invalidate: inv, success: "Picking started" });
  const save = useAction(() => api(`/fulfilment/pick-lists/${id}/picks`, { body: { picks } }), { invalidate: inv, success: "Picked quantities saved", onSuccess: () => setPicks({}) });
  const dispatch = useAction(() => api<{ dispatch_number: string; already_dispatched: boolean; order_status: string }>(`/fulfilment/pick-lists/${id}/dispatch`, { body: { vehicle: vehicle || null } }), { invalidate: inv, success: (r) => (r.already_dispatched ? `Already dispatched as ${r.dispatch_number} (no change)` : `Dispatched ${r.dispatch_number}; invoice raised`), onSuccess: () => setDispatching(false) });

  if (isPending) return <Spinner />;
  if (error || !p) return <ErrorState error={error} retry={refetch} />;
  const active = ["open", "picking", "picked"].includes(p.status) && can("fulfilment.execute");
  const partial = p.lines.some((l) => {
    const v = picks[l.id] ?? l.picked_quantity;
    return v !== null && v !== undefined && Number(v) < Number(l.requested_quantity);
  });

  return (
    <>
      <PageHeader back={{ href: "/fulfilment", label: "Fulfilment" }} title={<span className="flex items-center gap-2">{p.pick_number} <StatusChip status={p.status} /></span>} description={<span>Order <Link className="hover:underline" href={`/orders/${p.order_id}`}>{p.order_number}</Link> · {p.retailer} · {p.warehouse}</span>}
        actions={active && <>
          {p.status === "open" && <Button onClick={() => start.mutate(undefined)} loading={start.isPending}>Start picking</Button>}
          {Object.keys(picks).length > 0 && <Button onClick={() => save.mutate(undefined)} loading={save.isPending}>Save picked quantities</Button>}
          <Button variant="accent" disabled={Object.keys(picks).length > 0} title={Object.keys(picks).length ? "Save picked quantities first" : undefined} onClick={() => setDispatching(true)}>Dispatch</Button>
        </>} />
      {p.dispatch && <Alert tone="success">Dispatched as {p.dispatch.dispatch_number} ({p.dispatch.status}).</Alert>}
      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_300px]">
        <Card padded={false} title="Pick lines (batches suggested earliest-expiry first)">
          <DataTable<Line> rows={p.lines} rowKey={(l) => l.id} columns={[
            { key: "n", header: "#", cell: (l) => l.line_no },
            { key: "s", header: "SKU", cell: (l) => <span><b>{l.sku}</b><div className="text-xs text-muted">{l.name} {l.variant}</div></span> },
            { key: "b", header: "Batch", cell: (l) => l.batch_number ?? <span className="text-muted">any</span> },
            { key: "e", header: "Expiry", cell: (l) => date(l.expiry_date) },
            { key: "r", header: "Requested", align: "right", cell: (l) => qty(l.requested_quantity, l.base_unit) },
            { key: "p", header: "Picked", align: "right", cell: (l) => active ? <Input aria-label={`Picked quantity for line ${l.line_no}`} type="number" min="0" max={l.requested_quantity} step="any" className="ml-auto h-8 w-24 text-right" placeholder={String(Number(l.requested_quantity))} value={picks[l.id] ?? l.picked_quantity ?? ""} onChange={(e) => setPicks({ ...picks, [l.id]: e.target.value })} /> : qty(l.picked_quantity) },
          ]} />
          {active && <p className="border-t border-border p-3 text-xs text-muted">Leave “Picked” empty to dispatch the full requested quantity. Enter less for a partial dispatch; the remainder stays reserved on the order.</p>}
        </Card>
        <Card title="Delivery">
          <KeyValue items={[["Retailer", p.retailer], ["Route", p.route], ["Address", p.delivery_address], ["Deliver by", date(p.requested_delivery_date)], ["Order status", p.order_status]]} />
        </Card>
      </div>
      <Dialog open={dispatching} onClose={() => setDispatching(false)} title={`Dispatch ${p.pick_number}`} footer={<><Button onClick={() => setDispatching(false)}>Cancel</Button><Button variant="accent" loading={dispatch.isPending} onClick={() => dispatch.mutate(undefined)}>Confirm dispatch</Button></>}>
        <div className="space-y-3 text-sm">
          <p>Stock leaves {p.warehouse}, the reservation is consumed and a sales invoice is raised for the dispatched quantities. A repeated dispatch request never moves stock twice.</p>
          {partial && <Alert tone="warning">Some lines are picked short. The order will become partially fulfilled.</Alert>}
          <Field label="Vehicle (optional)">{(fid) => <Input id={fid} value={vehicle} onChange={(e) => setVehicle(e.target.value)} placeholder="e.g. MP09 AB 1234" />}</Field>
        </div>
      </Dialog>
    </>
  );
}
