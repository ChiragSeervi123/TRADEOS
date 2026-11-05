"use client";

import { Plus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Card, DataTable, ErrorState, Input, LinkButton, PageHeader, Pagination, Select, Spinner, StatusChip, SyntheticTag } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { date, money, titleCase } from "@/lib/format";
import { useApi, useDebounced, usePage } from "@/lib/hooks";

type Row = { id: string; order_number: string; status: string; channel: string; order_date: string; retailer: string; retailer_code: string; net_total: string; line_count: number; issues: number; is_synthetic: boolean };
const STATUSES = ["draft", "validated", "approved", "picking", "partially_fulfilled", "dispatched", "delivered", "rejected", "cancelled"];

function OrdersList() {
  const router = useRouter();
  const sp = useSearchParams();
  const { can } = useAuth();
  const [status, setStatus] = useState(sp.get("status") ?? "");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const dq = useDebounced(q);
  const { data, isPending, error, refetch } = usePage<Row>("/orders", { status, q: dq, page, page_size: 25 });
  const counts = useApi<Record<string, number>>("/orders/status-counts");

  return (
    <>
      <PageHeader title="Orders" description="Manual and inbox orders, from draft to delivery." actions={can("orders.create") && <LinkButton href="/orders/new" variant="primary"><Plus className="h-4 w-4" /> New order</LinkButton>} />
      <Card padded={false}>
        <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
          <Input aria-label="Search orders" placeholder="Search order number or retailer" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="max-w-xs" />
          <Select aria-label="Filter by status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="max-w-[220px]">
            <option value="">All statuses</option>
            <option value="draft,validated">Awaiting action (draft/validated)</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>{titleCase(s)}{counts.data?.[s] ? ` (${counts.data[s]})` : ""}</option>
            ))}
          </Select>
        </div>
        {isPending ? <Spinner /> : error ? <ErrorState error={error} retry={refetch} /> : (
          <>
            <DataTable<Row>
              rows={data.items}
              rowKey={(r) => r.id}
              onRowClick={(r) => router.push(`/orders/${r.id}`)}
              columns={[
                { key: "n", header: "Order", cell: (r) => <span className="font-medium">{r.order_number} <SyntheticTag show={r.is_synthetic} /></span> },
                { key: "d", header: "Date", cell: (r) => date(r.order_date) },
                { key: "r", header: "Retailer", cell: (r) => <span>{r.retailer} <span className="text-xs text-muted">{r.retailer_code}</span></span> },
                { key: "c", header: "Channel", cell: (r) => titleCase(r.channel) },
                { key: "l", header: "Lines", align: "right", cell: (r) => r.line_count },
                { key: "t", header: "Net total", align: "right", cell: (r) => money(r.net_total) },
                { key: "i", header: "Issues", align: "center", cell: (r) => (r.issues ? <StatusChip status="medium" label={`${r.issues}`} /> : null) },
                { key: "s", header: "Status", cell: (r) => <StatusChip status={r.status} /> },
              ]}
            />
            <Pagination page={page} pageSize={25} total={data.total} onPage={setPage} />
          </>
        )}
      </Card>
    </>
  );
}

export default function OrdersPage() {
  return (
    <Suspense fallback={<Spinner />}>
      <OrdersList />
    </Suspense>
  );
}
