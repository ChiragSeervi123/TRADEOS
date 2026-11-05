"use client";

import { useState } from "react";
import { CsvImportButton } from "@/components/csv-import";
import { useToast } from "@/components/toast";
import { Button, Card, DataTable, Dialog, EmptyState, ErrorState, Field, Input, Pagination, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { money } from "@/lib/format";
import { useAction, useApi, useDebounced, usePage } from "@/lib/hooks";

type PL = { id: string; name: string; currency: string; is_default: boolean; valid_from: string | null; valid_to: string | null; is_active: boolean; item_count: number };
type Item = { id: string; product_id: string; sku: string; name: string; variant: string | null; base_unit: string; unit_price: string };

export function PricingPanel() {
  const { can } = useAuth();
  const toast = useToast();
  const lists = useApi<PL[]>("/price-lists");
  const [sel, setSel] = useState<string>("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [creating, setCreating] = useState(false);
  const [newPl, setNewPl] = useState({ name: "", valid_from: "", valid_to: "" });
  const plId = sel || lists.data?.find((l) => l.is_default)?.id || lists.data?.[0]?.id || "";
  const dq = useDebounced(q);
  const items = usePage<Item>(plId ? `/price-lists/${plId}/items` : "/price-lists/none/items", { q: dq, page, page_size: 25 });
  const save = useAction(() => api(`/price-lists/${plId}/items`, { method: "PUT", body: { items: Object.entries(edits).map(([product_id, unit_price]) => ({ product_id, unit_price })) } }), {
    invalidate: ["/price-lists"],
    success: (r) => `${(r as { updated: number }).updated} price(s) updated`,
    onSuccess: () => setEdits({}),
  });
  const create = useAction(() => api<PL>("/price-lists", { body: { name: newPl.name, valid_from: newPl.valid_from || null, valid_to: newPl.valid_to || null } }), { invalidate: ["/price-lists"], success: "Price list created", onSuccess: (p) => { setCreating(false); setSel(p.id); } });

  if (lists.isPending) return <Spinner />;
  if (lists.error) return <ErrorState error={lists.error} />;
  const pl = lists.data.find((l) => l.id === plId);

  return (
    <Card padded={false} title="Price lists" actions={
      <>
        {can("pricing.write") && <CsvImportButton entity="prices" invalidate={["/price-lists"]} />}
        {can("pricing.write") && <Button onClick={() => setCreating(true)}>New price list</Button>}
      </>
    }>
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <Select aria-label="Price list" value={plId} onChange={(e) => { setSel(e.target.value); setPage(1); setEdits({}); }} className="max-w-xs">
          {lists.data.map((l) => <option key={l.id} value={l.id}>{l.name}{l.is_default ? " (default)" : ""} · {l.item_count} items</option>)}
        </Select>
        <Input aria-label="Search items" placeholder="Search SKU or name" value={q} onChange={(e) => setQ(e.target.value)} className="max-w-xs" />
        {pl && <span className="text-xs text-muted">Prices are per base unit, excluding tax. {pl.valid_from || pl.valid_to ? `Valid ${pl.valid_from ?? "…"} → ${pl.valid_to ?? "…"}` : "No date limits."}</span>}
        {Object.keys(edits).length > 0 && <Button variant="primary" size="sm" className="ml-auto" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save {Object.keys(edits).length} change(s)</Button>}
      </div>
      {items.isPending ? <Spinner /> : items.error ? <ErrorState error={items.error} /> : items.data.items.length === 0 ? <EmptyState title="No prices in this list" description="Import a prices CSV or set prices from a product page." /> : (
        <>
          <DataTable<Item>
            dense
            rows={items.data.items}
            rowKey={(r) => r.id}
            columns={[
              { key: "s", header: "SKU", cell: (r) => <span className="font-medium">{r.sku}</span> },
              { key: "n", header: "Product", cell: (r) => `${r.name} ${r.variant ?? ""}` },
              { key: "u", header: "Unit", cell: (r) => r.base_unit },
              { key: "p", header: "Price", align: "right", cell: (r) => can("pricing.write") ? (
                <Input aria-label={`Price for ${r.sku}`} type="number" min="0" step="0.01" className="ml-auto h-8 w-28 text-right" value={edits[r.product_id] ?? r.unit_price} onChange={(e) => setEdits({ ...edits, [r.product_id]: e.target.value })} />
              ) : money(r.unit_price) },
            ]}
          />
          <Pagination page={page} pageSize={25} total={items.data.total} onPage={setPage} />
        </>
      )}
      <Dialog open={creating} onClose={() => setCreating(false)} title="New price list" footer={<><Button onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" disabled={!newPl.name} loading={create.isPending} onClick={() => create.mutate(undefined)}>Create</Button></>}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Name" required className="sm:col-span-3">{(id) => <Input id={id} value={newPl.name} onChange={(e) => setNewPl({ ...newPl, name: e.target.value })} />}</Field>
          <Field label="Valid from">{(id) => <Input id={id} type="date" value={newPl.valid_from} onChange={(e) => setNewPl({ ...newPl, valid_from: e.target.value })} />}</Field>
          <Field label="Valid to">{(id) => <Input id={id} type="date" value={newPl.valid_to} onChange={(e) => setNewPl({ ...newPl, valid_to: e.target.value })} />}</Field>
        </div>
        <p className="mt-2 text-xs text-muted">Assign a price list to retailers on the retailer page; otherwise the default list applies.</p>
      </Dialog>
      {toast && null}
    </Card>
  );
}
