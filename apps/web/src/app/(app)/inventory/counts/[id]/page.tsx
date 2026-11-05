"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { Button, Card, DataTable, ErrorState, Input, PageHeader, Spinner, StatusChip } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { qty, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type Line = { id: string; sku: string; name: string; system_quantity: string; counted_quantity: string | null; variance: string | null };
type Count = { id: string; count_number: string; status: string; lines: Line[] };

export default function StockCountPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data, isPending, error, refetch } = useApi<Count>(`/inventory/stock-counts/${id}`);
  const [counts, setCounts] = useState<Record<string, string>>({});
  const save = useAction(() => api(`/inventory/stock-counts/${id}`, { method: "PATCH", body: { counts } }), { invalidate: ["/inventory"], success: "Counts saved", onSuccess: () => setCounts({}) });
  const post = useAction(() => api(`/inventory/stock-counts/${id}/post`, { body: {} }), { invalidate: ["/inventory"], success: "Stock count posted; variances adjusted" });
  if (isPending) return <Spinner />;
  if (error || !data) return <ErrorState error={error} retry={refetch} />;
  const draft = data.status === "draft" && can("inventory.adjust");
  const incomplete = data.lines.some((l) => l.counted_quantity === null);
  return (
    <>
      <PageHeader back={{ href: "/inventory", label: "Inventory" }} title={<span className="flex items-center gap-2">{data.count_number} <StatusChip status={data.status === "posted" ? "verified" : data.status} label={titleCase(data.status)} /></span>}
        actions={draft && <>
          <Button disabled={!Object.keys(counts).length} loading={save.isPending} onClick={() => save.mutate(undefined)}>Save counts</Button>
          <Button variant="accent" disabled={incomplete || Object.keys(counts).length > 0} title={incomplete ? "Count every line first" : undefined} loading={post.isPending} onClick={() => post.mutate(undefined)}>Post variances</Button>
        </>} />
      <Card padded={false}>
        <DataTable<Line> dense rows={data.lines} rowKey={(l) => l.id} columns={[
          { key: "s", header: "SKU", cell: (l) => <b>{l.sku}</b> }, { key: "n", header: "Product", cell: (l) => l.name },
          { key: "sy", header: "System qty", align: "right", cell: (l) => qty(l.system_quantity) },
          { key: "c", header: "Counted", align: "right", cell: (l) => draft ? <Input aria-label={`Counted quantity for ${l.sku}`} type="number" min="0" step="any" className="ml-auto h-8 w-28 text-right" value={counts[l.id] ?? l.counted_quantity ?? ""} onChange={(e) => setCounts({ ...counts, [l.id]: e.target.value })} /> : qty(l.counted_quantity) },
          { key: "v", header: "Variance", align: "right", cell: (l) => l.variance === null ? "—" : <span className={Number(l.variance) < 0 ? "text-danger" : Number(l.variance) > 0 ? "text-success" : ""}>{qty(l.variance)}</span> },
        ]} />
      </Card>
    </>
  );
}
