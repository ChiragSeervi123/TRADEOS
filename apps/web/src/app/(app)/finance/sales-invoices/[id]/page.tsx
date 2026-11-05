"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Badge, Card, DataTable, ErrorState, KeyValue, PageHeader, Spinner, StatusChip, SyntheticTag } from "@/components/ui";
import { date, dateTime, money, qty } from "@/lib/format";
import { useApi } from "@/lib/hooks";

type SI = {
  id: string; invoice_number: string; retailer: string; retailer_id: string; order_id: string | null; invoice_date: string; due_date: string; gross_amount: string; discount_amount: string; tax_amount: string; total: string; outstanding: string; status: string; days_overdue: number; is_synthetic: boolean;
  lines: { sku: string; name: string; quantity: string; unit_price: string; gross_amount: string; discount_amount: string; tax_amount: string; line_total: string; is_free_goods: boolean }[];
  allocations: { id: string; payment_id: string; payment_number: string; amount: string; created_at: string; reversed: boolean; reversal_reason: string | null }[];
  credit_notes: { id: string; note_number: string; amount: string; status: string }[];
};

export default function SalesInvoicePage() {
  const { id } = useParams<{ id: string }>();
  const { data: s, isPending, error, refetch } = useApi<SI>(`/sales-invoices/${id}`);
  if (isPending) return <Spinner />;
  if (error || !s) return <ErrorState error={error} retry={refetch} />;
  return (
    <>
      <PageHeader back={{ href: "/finance?tab=sales", label: "Finance" }} title={<span className="flex items-center gap-2">{s.invoice_number} <StatusChip status={s.status} /> <SyntheticTag show={s.is_synthetic} /></span>} description={<span><Link className="hover:underline" href={`/retailers/${s.retailer_id}`}>{s.retailer}</Link>{s.order_id && <> · <Link className="hover:underline" href={`/orders/${s.order_id}`}>order</Link></>}</span>} />
      <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Card padded={false} title="Lines">
          <DataTable dense rows={s.lines} rowKey={(l) => `${l.sku}${l.quantity}${l.is_free_goods}`} columns={[
            { key: "s", header: "SKU", cell: (l) => <span>{l.sku} {l.is_free_goods && <Badge tone="success">free</Badge>}<div className="text-xs text-muted">{l.name}</div></span> },
            { key: "q", header: "Qty", align: "right", cell: (l) => qty(l.quantity) },
            { key: "p", header: "Price", align: "right", cell: (l) => money(l.unit_price) },
            { key: "g", header: "Gross", align: "right", cell: (l) => money(l.gross_amount) },
            { key: "d", header: "Discount", align: "right", cell: (l) => money(l.discount_amount) },
            { key: "t", header: "Tax", align: "right", cell: (l) => money(l.tax_amount) },
            { key: "lt", header: "Total", align: "right", cell: (l) => <b>{money(l.line_total)}</b> },
          ]} />
        </Card>
        <div className="space-y-4">
          <Card title="Summary">
            <KeyValue items={[["Invoice date", date(s.invoice_date)], ["Due date", <span key="d" className={s.days_overdue ? "text-danger" : ""}>{date(s.due_date)}{s.days_overdue ? ` · ${s.days_overdue} days overdue` : ""}</span>], ["Gross", money(s.gross_amount)], ["Discounts", money(s.discount_amount)], ["Tax", money(s.tax_amount)], ["Total", <b key="t">{money(s.total)}</b>], ["Outstanding", <b key="o" className={Number(s.outstanding) > 0 ? "text-accent-strong" : "text-success"}>{money(s.outstanding)}</b>]]} />
          </Card>
          <Card title="Payments & credits">
            <ul className="space-y-1.5 text-sm">
              {s.allocations.map((a) => <li key={a.id} className={a.reversed ? "text-muted line-through" : ""}><Link className="text-info hover:underline" href={`/finance/payments/${a.payment_id}`}>{a.payment_number}</Link> · {money(a.amount)} <span className="text-xs text-muted">{dateTime(a.created_at)}</span>{a.reversed && <span className="text-xs"> (reversed: {a.reversal_reason})</span>}</li>)}
              {s.credit_notes.map((c) => <li key={c.id}>{c.note_number} · {money(c.amount)} <StatusChip status={c.status} /></li>)}
              {!s.allocations.length && !s.credit_notes.length && <li className="text-muted">Nothing allocated yet.</li>}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
