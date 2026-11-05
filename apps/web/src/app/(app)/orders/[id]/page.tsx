"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { LineEditor, toApiLines, type EditLine } from "@/components/line-editor";
import { Alert, Badge, Button, Card, DataTable, Dialog, ErrorState, Field, KeyValue, PageHeader, Spinner, StatusChip, SyntheticTag, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, dateTime, money, qty, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type Issue = { code: string; severity: "error" | "warning" | "info"; message: string; line_no?: number; overridable?: boolean; details?: Record<string, unknown> };
type Line = {
  id: string; line_no: number; product_id: string | null; sku: string | null; name: string | null; variant: string | null; source_description: string | null;
  quantity: string; unit: string; base_quantity: string; base_unit: string | null; list_price: string | null; unit_price: string; price_override_reason: string | null;
  gross_amount: string; scheme_discount: string; tax_rate: string; tax_amount: string; line_total: string; is_free_goods: boolean; dispatched_quantity: string;
  provenance: string; match_confidence: string | null; extracted_values: Record<string, unknown> | null;
};
type Order = {
  id: string; order_number: string; status: string; channel: string; order_date: string; retailer: string; retailer_id: string; retailer_code: string; warehouse: string;
  requested_delivery_date: string | null; notes: string | null; source_id: string | null; totals: Record<string, string>; validation: { valid: boolean; issues: Issue[]; validated_at: string } | null;
  stock_override_reason: string | null; credit_override_reason: string | null; created_by: string | null; approved_by: string | null; approved_at: string | null; revision: number; version: number;
  lines: Line[]; schemes: { scheme_id: string; version: number; benefit_type: string; benefit_amount: string; free_quantity: string; explanation: Record<string, unknown> }[];
  timeline: { from: string | null; to: string; action: string; reason: string | null; actor: string | null; at: string }[];
  revisions: { revision: number; reason: string; at: string }[]; pick_lists: { id: string; pick_number: string; status: string }[];
  dispatches: { id: string; dispatch_number: string; status: string; dispatched_at: string }[];
  invoices?: { id: string; invoice_number: string; total: string; outstanding: string; status: string }[];
  credit?: { credit_limit: string | null; outstanding: string; open_orders: string; exposure: string; available: string | null; exceeded: boolean };
  is_synthetic: boolean; fulfilment_closed_at: string | null;
};

const SEV_TONE = { error: "danger", warning: "warning", info: "info" } as const;

export default function OrderDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: o, isPending, error, refetch } = useApi<Order>(`/orders/${id}`);
  const [dialog, setDialog] = useState<null | "approve" | "reject" | "cancel" | "amend" | "edit" | "close">(null);
  const [reason, setReason] = useState("");
  const [stockReason, setStockReason] = useState("");
  const [creditReason, setCreditReason] = useState("");
  const [editLines, setEditLines] = useState<EditLine[]>([]);
  const close = () => { setDialog(null); setReason(""); setStockReason(""); setCreditReason(""); };
  const inv = ["/orders", "/inventory", "/analytics", "/fulfilment", "/exceptions"];
  const act = useAction((v: { path: string; body?: unknown; method?: string }) => api(`/orders/${id}${v.path}`, { body: v.body ?? {}, method: v.method }), { invalidate: inv, onSuccess: close });
  const closeShort = useAction(() => api(`/orders/${id}/close-short`, { body: { reason } }), { invalidate: inv, success: "Remaining quantity closed and reservation released", onSuccess: close });
  const backorder = useAction(() => api(`/orders/${id}/backorder-pick-list`, { method: "POST" }), { invalidate: inv, success: "Pick list created for remaining quantity" });

  if (isPending) return <Spinner />;
  if (error || !o) return <ErrorState error={error} retry={refetch} />;

  const issues = o.validation?.issues ?? [];
  const blocking = issues.filter((i) => i.severity === "error" && !i.overridable);
  const stockIssue = issues.find((i) => i.code === "INSUFFICIENT_AVAILABLE_STOCK");
  const creditIssue = issues.find((i) => i.code === "CREDIT_LIMIT_EXCEEDED");
  const editable = ["draft", "validated"].includes(o.status);

  const openEdit = () => {
    setEditLines(o.lines.filter((l) => !l.is_free_goods && l.product_id).map((l) => ({ key: l.id, product_id: l.product_id, product_label: `${l.name} ${l.variant ?? ""}`.trim(), quantity: String(Number(l.quantity)), unit: l.unit, ...(l.price_override_reason ? { unit_price_override: l.unit_price, price_override_reason: l.price_override_reason } : {}) })));
    setDialog("edit");
  };

  return (
    <>
      <PageHeader
        back={{ href: "/orders", label: "Orders" }}
        title={<span className="flex items-center gap-2">{o.order_number} <StatusChip status={o.status} /> <SyntheticTag show={o.is_synthetic} /></span>}
        description={`${o.retailer} (${o.retailer_code}) · ${date(o.order_date)} · ${titleCase(o.channel)}${o.revision > 1 ? ` · revision ${o.revision}` : ""}`}
        actions={
          <>
            {editable && can("orders.create") && <Button onClick={openEdit}>Edit lines</Button>}
            {editable && can("orders.create") && <Button onClick={() => act.mutate({ path: "/validate" })} loading={act.isPending && !dialog}>Re-validate</Button>}
            {editable && can("orders.approve") && <Button variant="danger" onClick={() => setDialog("reject")}>Reject</Button>}
            {["approved", "picking"].includes(o.status) && can("orders.approve") && <Button onClick={() => setDialog("amend")}>Amend</Button>}
            {["draft", "validated", "approved", "picking"].includes(o.status) && can("orders.cancel") && <Button variant="danger" onClick={() => setDialog("cancel")}>Cancel order</Button>}
            {o.status === "partially_fulfilled" && !o.fulfilment_closed_at && can("fulfilment.execute") && <Button onClick={() => backorder.mutate(undefined)} loading={backorder.isPending}>Pick remaining</Button>}
            {o.status === "partially_fulfilled" && !o.fulfilment_closed_at && can("orders.cancel") && <Button onClick={() => setDialog("close")}>Close short</Button>}
            {editable && can("orders.approve") && <Button variant="accent" onClick={() => setDialog("approve")} disabled={blocking.length > 0} title={blocking.length ? "Fix blocking errors first" : undefined}>Approve</Button>}
          </>
        }
      />

      {o.source_id && <Alert tone="info">Created from an <Link className="underline" href={`/inbox/${o.source_id}`}>inbox item</Link>. Extracted and corrected values are kept on each line.</Alert>}

      <div className="mt-4 grid gap-4 xl:grid-cols-[1fr_340px]">
        <div className="space-y-4">
          {issues.length > 0 && (
            <Card title={`Validation · ${o.validation?.valid ? "ready to approve" : blocking.length ? `${blocking.length} blocking` : "needs override"}`}>
              <ul className="space-y-1.5">
                {issues.map((i, n) => (
                  <li key={n} className="flex items-start gap-2 text-sm">
                    <Badge tone={SEV_TONE[i.severity]}>{i.severity}</Badge>
                    <span>{i.message}{i.overridable && <span className="text-xs text-muted"> (can be overridden at approval with permission and a reason)</span>}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card title="Lines" padded={false}>
            <DataTable<Line>
              rows={o.lines}
              rowKey={(l) => l.id}
              dense
              columns={[
                { key: "n", header: "#", cell: (l) => l.line_no },
                { key: "p", header: "Product", cell: (l) => (
                  <div>
                    <div className="font-medium">{l.sku ?? <span className="text-danger">Unresolved</span>} {l.is_free_goods && <Badge tone="success">free goods</Badge>}</div>
                    <div className="text-xs text-muted">{l.name} {l.variant}</div>
                    {l.source_description && l.provenance !== "manual" && <div className="text-xs text-muted">“{l.source_description}”</div>}
                  </div>
                ) },
                { key: "q", header: "Qty", align: "right", cell: (l) => <span title={`${qty(l.base_quantity, l.base_unit)} base units`}>{qty(l.quantity, l.unit)}<div className="text-xs text-muted">{qty(l.base_quantity, l.base_unit)}</div></span> },
                { key: "pr", header: "Price / base unit", align: "right", cell: (l) => <span>{money(l.unit_price)}{l.price_override_reason && <div className="text-xs text-accent-strong" title={l.price_override_reason}>override (list {money(l.list_price)})</div>}</span> },
                { key: "g", header: "Gross", align: "right", cell: (l) => money(l.gross_amount) },
                { key: "s", header: "Scheme", align: "right", cell: (l) => (Number(l.scheme_discount) ? <span className="text-success">−{money(l.scheme_discount)}</span> : "—") },
                { key: "t", header: "Tax", align: "right", cell: (l) => <span>{money(l.tax_amount)}<div className="text-xs text-muted">{Number(l.tax_rate)}%</div></span> },
                { key: "lt", header: "Total", align: "right", cell: (l) => <b>{money(l.line_total)}</b> },
                { key: "d", header: "Dispatched", align: "right", cell: (l) => qty(l.dispatched_quantity) },
                { key: "v", header: "Source", cell: (l) => (
                  <span title={l.extracted_values ? `Extracted: ${String(l.extracted_values.quantity ?? "?")} ${String(l.extracted_values.unit ?? "")} (${String(l.extracted_values.status ?? "")})` : undefined}>
                    <Badge tone={l.provenance === "corrected" ? "warning" : l.provenance === "extracted" ? "info" : l.provenance === "scheme" ? "success" : "neutral"}>{titleCase(l.provenance)}</Badge>
                  </span>
                ) },
              ]}
            />
            <div className="flex justify-end border-t border-border p-3">
              <KeyValue items={[
                ["Gross", money(o.totals.gross)],
                ["Scheme discount", <span key="s" className="text-success">−{money(o.totals.scheme_discount)}</span>],
                ["Taxable", money(o.totals.taxable)],
                ["Tax (configured rates)", money(o.totals.tax)],
                ["Net total", <b key="n" className="text-base">{money(o.totals.net)}</b>],
              ]} />
            </div>
          </Card>
          {o.schemes.length > 0 && (
            <Card title="Scheme calculations">
              <ul className="space-y-3">
                {o.schemes.map((s, i) => {
                  const e = s.explanation as Record<string, string | string[] | boolean>;
                  return (
                    <li key={i} className="rounded-md border border-border p-3 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <b>{String(e.scheme_name)}</b> <Badge>v{s.version}</Badge> <Badge tone="info">{titleCase(String(e.scheme_type))}</Badge>
                        <span className="ml-auto font-semibold text-success">{s.benefit_type === "free_goods" ? `${qty(s.free_quantity)} free (value ${money(s.benefit_amount)})` : `−${money(s.benefit_amount)}`}</span>
                      </div>
                      <p className="mt-1 text-muted">Qualifying: {String(e.qualifying_quantity)} units / {money(String(e.qualifying_value))} · Threshold: {String(e.threshold)} · Rate: {String(e.rate)}</p>
                      <p className="text-muted">Calculation: {String(e.calculation)}{e.capped ? ` (capped at ${money(String(e.cap))})` : ""}</p>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
        </div>

        <div className="space-y-4">
          <Card title="Details">
            <KeyValue items={[
              ["Warehouse", o.warehouse],
              ["Requested delivery", date(o.requested_delivery_date)],
              ["Created by", o.created_by],
              ["Approved", o.approved_at ? `${o.approved_by} · ${dateTime(o.approved_at)}` : "—"],
              ["Notes", o.notes],
              ...(o.stock_override_reason ? [["Stock override", o.stock_override_reason] as [string, string]] : []),
              ...(o.credit_override_reason ? [["Credit override", o.credit_override_reason] as [string, string]] : []),
            ]} />
          </Card>
          {o.credit && (
            <Card title="Credit exposure">
              <KeyValue items={[["Credit limit", o.credit.credit_limit ? money(o.credit.credit_limit) : "No limit set"], ["Outstanding", money(o.credit.outstanding)], ["Other open orders", money(o.credit.open_orders)], ["Exposure", <b key="e" className={o.credit.exceeded ? "text-danger" : ""}>{money(o.credit.exposure)}</b>], ["Available", o.credit.available ? money(o.credit.available) : "—"]]} />
            </Card>
          )}
          {(o.pick_lists.length > 0 || o.dispatches.length > 0 || (o.invoices?.length ?? 0) > 0) && (
            <Card title="Fulfilment & billing">
              <ul className="space-y-1 text-sm">
                {o.pick_lists.map((p) => <li key={p.id} className="flex justify-between"><Link className="text-info hover:underline" href={`/fulfilment/pick-lists/${p.id}`}>{p.pick_number}</Link><StatusChip status={p.status} /></li>)}
                {o.dispatches.map((d) => <li key={d.id} className="flex justify-between"><span>{d.dispatch_number}</span><StatusChip status={d.status} /></li>)}
                {o.invoices?.map((i) => <li key={i.id} className="flex justify-between"><Link className="text-info hover:underline" href={`/finance/sales-invoices/${i.id}`}>{i.invoice_number} · {money(i.total)}</Link><StatusChip status={i.status} /></li>)}
              </ul>
            </Card>
          )}
          <Card title="Timeline">
            <ol className="space-y-2 text-sm">
              {o.timeline.map((t, i) => (
                <li key={i} className="border-l-2 border-border pl-3">
                  <p><b>{titleCase(t.action)}</b> → <StatusChip status={t.to} /></p>
                  <p className="text-xs text-muted">{dateTime(t.at)}{t.actor ? ` · ${t.actor}` : ""}</p>
                  {t.reason && <p className="text-xs text-ink-2">{t.reason}</p>}
                </li>
              ))}
            </ol>
            {o.revisions.length > 0 && <p className="mt-3 text-xs text-muted">{o.revisions.length} earlier revision(s) kept: {o.revisions.map((r) => `r${r.revision} (${r.reason})`).join(", ")}</p>}
          </Card>
        </div>
      </div>

      <Dialog open={dialog === "approve"} onClose={close} title={`Approve ${o.order_number}`} footer={<><Button onClick={close}>Cancel</Button><Button variant="accent" loading={act.isPending} onClick={() => act.mutate({ path: "/approve", body: { stock_override_reason: stockReason || null, credit_override_reason: creditReason || null } })}>Approve and reserve stock</Button></>}>
        <div className="space-y-3 text-sm">
          <p>Approval reserves stock in {o.warehouse} and creates a pick list. Net total {money(o.totals.net)}.</p>
          {stockIssue && (
            <Field label="Stock override reason" hint="Only allowed if your organisation's negative-stock policy permits overrides. Audited.">
              {(fid) => <Textarea id={fid} rows={2} value={stockReason} onChange={(e) => setStockReason(e.target.value)} placeholder={stockIssue.message} />}
            </Field>
          )}
          {creditIssue && (
            <Field label="Credit override reason" hint="Requires override permission. Recorded in the audit trail.">
              {(fid) => <Textarea id={fid} rows={2} value={creditReason} onChange={(e) => setCreditReason(e.target.value)} placeholder={creditIssue.message} />}
            </Field>
          )}
        </div>
      </Dialog>

      {(["reject", "cancel", "amend", "close"] as const).map((k) => (
        <Dialog key={k} open={dialog === k} onClose={close} title={{ reject: "Reject order", cancel: "Cancel order", amend: "Amend approved order", close: "Close remaining quantity" }[k]}
          footer={<><Button onClick={close}>Back</Button><Button variant={k === "amend" ? "accent" : "danger"} disabled={reason.trim().length < 3} loading={act.isPending || closeShort.isPending} onClick={() => (k === "close" ? closeShort.mutate(undefined) : act.mutate({ path: `/${k}`, body: { reason } }))}>Confirm</Button></>}>
          <div className="space-y-3 text-sm">
            <p>{{ reject: "The order will not be fulfilled.", cancel: "Reserved stock will be released and open pick lists cancelled.", amend: "Reservations are released and the order returns to draft for editing and re-approval. The current version is kept as a revision.", close: "Remaining undispatched quantities will not be supplied; their reservation is released." }[k]}</p>
            <Field label="Reason" required>{(fid) => <Textarea id={fid} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
          </div>
        </Dialog>
      ))}

      <Dialog wide open={dialog === "edit"} onClose={close} title="Edit order lines" footer={<><Button onClick={close}>Cancel</Button><Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ path: "", method: "PATCH", body: { lines: toApiLines(editLines), version: o.version } })}>Save and re-validate</Button></>}>
        <p className="mb-3 text-sm text-muted">Free-goods lines are recalculated automatically. Saving creates a revision.</p>
        <LineEditor lines={editLines} onChange={setEditLines} allowPriceOverride={can("orders.approve")} />
      </Dialog>
    </>
  );
}
