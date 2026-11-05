"use client";

import { CheckCircle2, Circle, Info } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { BarChart, HBarList } from "@/components/charts";
import { Alert, Card, ErrorState, Field, Input, PageHeader, Spinner, Stat } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { daysAgo, isoDay, money, pct, titleCase } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type Overview = {
  period: { from: string; to: string };
  orders: { orders_received: number; orders_approved: number; orders_dispatched: number; avg_processing_hours: number | null; order_correction_rate: number | null; inbox_orders: number };
  sales?: { gross_sales: string; net_sales: string; discounts: string; credit_notes: string; invoice_count: number };
  inventory: { inventory_value?: string; out_of_stock_skus: number; low_stock_skus: number; expiring_batches: number; expired_batches: number; products_without_cost: number };
  receivables?: { total_outstanding: string; buckets: string[]; totals: Record<string, string> };
  reconciliation?: { open_cases: number; potential_discrepancy_value: string; potential_discrepancy_note: string; resolved_discrepancy_value: string };
  schemes?: { scheme_benefits: string };
  exceptions: { open: number; by_severity: Record<string, number>; by_type: Record<string, number>; ageing: Record<string, number> };
};
type Ops = { pending_orders: number; orders_awaiting_approval: number; inbox_awaiting_review: number; blocked_by_stock: number; blocked_by_credit: number; pick_queue: number; ready_to_dispatch: number; returns_awaiting_credit: number };
type Trends = { daily: { date: string; orders: number; net_sales?: string }[] };
type Checklist = { items: { key: string; label: string; done: boolean; href: string }[]; completed: number; total: number };

const DEFS: Record<string, string> = {
  gross: "Sum of invoiced quantity × unit price, before discounts, excluding tax, for invoices dated in the period.",
  net: "Gross sales − invoice discounts − credit notes issued to retailers + debit notes issued, excluding tax. Not profit.",
  processing: "Average hours from order creation to approval, for orders approved in the period.",
  correction: "Share of inbox-created orders in the period where a reviewer changed at least one extracted line.",
  inventory: "Σ on-hand quantity × agreed cost price, now. Products without a cost price are excluded.",
  discrepancy: "Sum of absolute differences on open reconciliation cases. Unverified — not a confirmed loss.",
  resolved: "Sum of recorded resolved values on cases resolved in the period.",
};

function Def({ k }: { k: keyof typeof DEFS }) {
  return (
    <span title={DEFS[k]} className="inline-flex cursor-help items-center text-muted">
      <Info className="ml-1 h-3 w-3" aria-label={DEFS[k]} />
    </span>
  );
}

export default function OverviewPage() {
  const { can, me } = useAuth();
  const [from, setFrom] = useState(daysAgo(29));
  const [to, setTo] = useState(isoDay());
  const canReports = can("reports.read");
  const finance = can("finance.read");
  const ov = useApi<Overview>(canReports ? "/analytics/overview" : null, { date_from: from, date_to: to });
  const ops = useApi<Ops>("/analytics/operations");
  const tr = useApi<Trends>(canReports ? "/analytics/trends" : null, { date_from: from, date_to: to });
  const cl = useApi<Checklist>("/organizations/current/onboarding");

  const shortDay = (d: string) => new Date(`${d}T00:00:00`).toLocaleDateString("en-IN", { day: "2-digit", month: "short" });

  return (
    <div className="space-y-4">
      <PageHeader
        title="Overview"
        description={`${me?.organization?.name} · all figures computed from your records`}
        actions={
          canReports && (
            <div className="flex items-end gap-2">
              <Field label="From">{(id) => <Input id={id} type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)} className="w-[150px]" />}</Field>
              <Field label="To">{(id) => <Input id={id} type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)} className="w-[150px]" />}</Field>
            </div>
          )
        }
      />

      {cl.data && cl.data.completed < cl.data.total && (
        <Card title={`Setup checklist · ${cl.data.completed}/${cl.data.total} complete`}>
          <ul className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-4">
            {cl.data.items.map((i) => (
              <li key={i.key}>
                <Link href={i.href} className="flex items-center gap-2 text-sm hover:underline">
                  {i.done ? <CheckCircle2 className="h-4 w-4 text-success" /> : <Circle className="h-4 w-4 text-muted" />}
                  <span className={i.done ? "text-muted line-through" : ""}>{i.label}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {ops.data && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <Stat label="Inbox to review" value={ops.data.inbox_awaiting_review} href="/inbox" tone={ops.data.inbox_awaiting_review ? "warning" : undefined} />
          <Stat label="Awaiting approval" value={ops.data.orders_awaiting_approval} href="/orders?status=validated" />
          <Stat label="Pending orders" value={ops.data.pending_orders} href="/orders?status=draft,validated" />
          <Stat label="Blocked by stock" value={ops.data.blocked_by_stock} tone={ops.data.blocked_by_stock ? "danger" : undefined} href="/orders?status=draft,validated" />
          <Stat label="Blocked by credit" value={ops.data.blocked_by_credit} tone={ops.data.blocked_by_credit ? "danger" : undefined} href="/orders?status=draft,validated" />
          <Stat label="Pick queue" value={ops.data.pick_queue} href="/fulfilment" />
          <Stat label="Ready to dispatch" value={ops.data.ready_to_dispatch} href="/fulfilment" />
          <Stat label="Returns to credit" value={ops.data.returns_awaiting_credit} href="/fulfilment?tab=returns" />
        </div>
      )}

      {!canReports ? null : ov.isPending ? (
        <Spinner />
      ) : ov.error ? (
        <ErrorState error={ov.error} retry={() => ov.refetch()} />
      ) : (
        ov.data && (
          <>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
              <Stat label="Orders received" value={ov.data.orders.orders_received} sub={`${ov.data.orders.orders_approved} approved · ${ov.data.orders.orders_dispatched} dispatched`} />
              {ov.data.sales && <Stat label="Gross sales" value={money(ov.data.sales.gross_sales, { compact: true })} sub={`${ov.data.sales.invoice_count} invoices`} title={DEFS.gross} />}
              {ov.data.sales && <Stat label="Net sales" value={money(ov.data.sales.net_sales, { compact: true })} sub="excl. tax · not profit" title={DEFS.net} />}
              <Stat label="Avg. processing time" value={ov.data.orders.avg_processing_hours === null ? "—" : `${ov.data.orders.avg_processing_hours} h`} sub="created → approved" title={DEFS.processing} />
              <Stat label="Order correction rate" value={pct(ov.data.orders.order_correction_rate)} sub={`${ov.data.orders.inbox_orders} inbox orders`} title={DEFS.correction} />
              {ov.data.inventory.inventory_value !== undefined && <Stat label="Inventory value" value={money(ov.data.inventory.inventory_value, { compact: true })} sub="at agreed cost" title={DEFS.inventory} />}
            </div>

            {tr.data && (
              <div className="grid gap-4 xl:grid-cols-2">
                <Card>
                  <BarChart title="Orders received per day" valueLabel="orders" data={tr.data.daily.map((d) => ({ label: shortDay(d.date), value: d.orders }))} />
                </Card>
                {finance && (
                  <Card>
                    <BarChart title="Net sales per day (₹, excl. tax)" valueLabel="net sales" formatValue={(v) => money(v)} data={tr.data.daily.map((d) => ({ label: shortDay(d.date), value: Number(d.net_sales ?? 0) }))} />
                  </Card>
                )}
              </div>
            )}

            <div className="grid gap-4 lg:grid-cols-3">
              <Card title="Stock alerts" actions={<Link className="text-xs text-info hover:underline" href="/inventory">Inventory</Link>}>
                <ul className="space-y-1.5 text-sm">
                  <li className="flex justify-between"><span>Out of stock SKUs</span><b className="tabular">{ov.data.inventory.out_of_stock_skus}</b></li>
                  <li className="flex justify-between"><span>Low stock SKUs (≤ 10 available)</span><b className="tabular">{ov.data.inventory.low_stock_skus}</b></li>
                  <li className="flex justify-between"><span>Batches expiring soon</span><b className="tabular">{ov.data.inventory.expiring_batches}</b></li>
                  <li className="flex justify-between"><span>Expired batches with stock</span><b className={`tabular ${ov.data.inventory.expired_batches ? "text-danger" : ""}`}>{ov.data.inventory.expired_batches}</b></li>
                </ul>
              </Card>
              <Card title={`Exceptions requiring attention · ${ov.data.exceptions.open}`} actions={<Link className="text-xs text-info hover:underline" href="/exceptions">Queue</Link>}>
                {ov.data.exceptions.open === 0 ? (
                  <p className="text-sm text-muted">No open exceptions.</p>
                ) : (
                  <ul className="space-y-1.5 text-sm">
                    {Object.entries(ov.data.exceptions.by_type).sort((a, b) => b[1] - a[1]).map(([t, n]) => (
                      <li key={t} className="flex justify-between">
                        <Link className="hover:underline" href={`/exceptions?type=${t}`}>{titleCase(t)}</Link>
                        <b className="tabular">{n}</b>
                      </li>
                    ))}
                  </ul>
                )}
              </Card>
              {ov.data.reconciliation && (
                <Card title="Reconciliation" actions={<Link className="text-xs text-info hover:underline" href="/reconciliation">Cases</Link>}>
                  <ul className="space-y-1.5 text-sm">
                    <li className="flex justify-between"><span>Open cases</span><b className="tabular">{ov.data.reconciliation.open_cases}</b></li>
                    <li className="flex justify-between"><span>Potential discrepancy<Def k="discrepancy" /></span><b className="tabular">{money(ov.data.reconciliation.potential_discrepancy_value)}</b></li>
                    <li className="flex justify-between"><span>Resolved in period<Def k="resolved" /></span><b className="tabular text-success">{money(ov.data.reconciliation.resolved_discrepancy_value)}</b></li>
                    {ov.data.schemes && <li className="flex justify-between"><span>Scheme benefits given</span><b className="tabular">{money(ov.data.schemes.scheme_benefits)}</b></li>}
                  </ul>
                  <p className="mt-2 text-xs text-muted">{ov.data.reconciliation.potential_discrepancy_note}</p>
                </Card>
              )}
            </div>

            {ov.data.receivables && (
              <Card title={`Receivables · ${money(ov.data.receivables.total_outstanding)} outstanding`} actions={<Link className="text-xs text-info hover:underline" href="/finance?tab=receivables">Ageing detail</Link>}>
                <HBarList
                  title="Outstanding by days past due"
                  valueLabel="Outstanding"
                  formatValue={(v) => money(v)}
                  data={ov.data.receivables.buckets.map((b) => ({ label: b === "current" ? "Not yet due" : `${b} days overdue`, value: Number(ov.data!.receivables!.totals[b]) }))}
                />
              </Card>
            )}
            {ov.data.inventory.products_without_cost > 0 && <Alert tone="info">{ov.data.inventory.products_without_cost} product(s) with stock have no cost price and are excluded from inventory value.</Alert>}
          </>
        )
      )}
    </div>
  );
}
