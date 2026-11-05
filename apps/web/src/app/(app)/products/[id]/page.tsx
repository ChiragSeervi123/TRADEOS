"use client";

import { useParams } from "next/navigation";
import { useState } from "react";
import { ProductForm, type ProductData } from "@/components/product-form";
import { Button, Card, DataTable, Dialog, ErrorState, Field, Input, KeyValue, PageHeader, Select, Spinner, StatusChip, SyntheticTag } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, money, qty, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type P = ProductData & {
  id: string; is_synthetic: boolean; created_at: string;
  units: { unit: string; factor: string }[];
  stock?: { warehouse_id: string; warehouse: string; on_hand: string; reserved: string; blocked: string; available: string }[];
  prices?: { price_list_id: string; unit_price: string }[];
  activity: { action: string; at: string; after: Record<string, unknown> | null }[];
  recent_movements?: { type: string; bucket: string; quantity: string; at: string }[];
};

export default function ProductDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: p, isPending, error, refetch } = useApi<P>(`/products/${id}`);
  const lists = useApi<{ id: string; name: string }[]>(can("pricing.read") ? "/price-lists" : null);
  const [edit, setEdit] = useState(false);
  const [conv, setConv] = useState(false);
  const [cf, setCf] = useState({ unit_code: "CASE", factor: "" });
  const units = useApi<{ units: { code: string }[] }>(conv ? "/units" : null);
  const addConv = useAction(() => api("/units/conversions", { body: { unit_code: cf.unit_code, factor: cf.factor, product_id: id } }), { invalidate: ["/products", "/units"], success: "Conversion added", onSuccess: () => setConv(false) });

  if (isPending) return <Spinner />;
  if (error || !p) return <ErrorState error={error} retry={refetch} />;
  const plName = (pid: string) => lists.data?.find((l) => l.id === pid)?.name ?? "Price list";

  return (
    <>
      <PageHeader back={{ href: "/products", label: "Products" }} title={<span className="flex items-center gap-2">{p.sku} <StatusChip status={p.is_active ? "active" : "inactive"} /> <SyntheticTag show={p.is_synthetic} /></span>} description={`${p.name} ${p.variant ?? ""}`}
        actions={can("products.write") && <><Button onClick={() => setConv(true)}>Add unit conversion</Button><Button variant="primary" onClick={() => setEdit(true)}>Edit</Button></>} />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Details">
          <KeyValue items={[["Brand", p.brand], ["Category", p.category], ["Base unit", p.base_unit], ["Tax", `${Number(p.tax_rate)}% ${p.tax_code ? `(HSN ${p.tax_code})` : ""}`], ["Agreed cost", money(p.cost_price)], ["MRP", money(p.mrp)], ["Barcode", p.barcode]]} />
        </Card>
        <Card title="Units">
          <ul className="space-y-1 text-sm">{p.units.map((u) => <li key={u.unit} className="flex justify-between"><span>1 {u.unit}</span><span className="tabular">= {Number(u.factor)} {p.base_unit}</span></li>)}</ul>
        </Card>
        {p.prices && (
          <Card title="Prices (per base unit)">
            {p.prices.length ? <ul className="space-y-1 text-sm">{p.prices.map((x) => <li key={x.price_list_id} className="flex justify-between"><span>{plName(x.price_list_id)}</span><span className="tabular">{money(x.unit_price)}</span></li>)}</ul> : <p className="text-sm text-muted">Not priced. Orders for this SKU will be blocked until a price is set.</p>}
          </Card>
        )}
        {p.stock && (
          <Card title="Stock by warehouse" className="lg:col-span-2" padded={false}>
            <DataTable dense rows={p.stock} rowKey={(r) => r.warehouse_id} columns={[
              { key: "w", header: "Warehouse", cell: (r) => r.warehouse },
              { key: "o", header: "On hand", align: "right", cell: (r) => qty(r.on_hand) },
              { key: "r", header: "Reserved", align: "right", cell: (r) => qty(r.reserved) },
              { key: "b", header: "Blocked", align: "right", cell: (r) => qty(r.blocked) },
              { key: "a", header: "Available", align: "right", cell: (r) => <b>{qty(r.available)}</b> },
            ]} empty={<p className="p-4 text-sm text-muted">No stock movements yet.</p>} />
          </Card>
        )}
        <Card title="Activity">
          <ul className="space-y-2 text-sm">
            {p.activity.map((a, i) => <li key={i}><b>{titleCase(a.action.replace("product.", ""))}</b> <span className="text-xs text-muted">{dateTime(a.at)}</span>{a.after && <p className="truncate text-xs text-muted">{Object.entries(a.after).slice(0, 4).map(([k, v]) => `${k}: ${String(v)}`).join(", ")}</p>}</li>)}
            {!p.activity.length && <li className="text-muted">No changes recorded.</li>}
          </ul>
        </Card>
      </div>
      <ProductForm open={edit} onClose={() => setEdit(false)} initial={p} onSaved={() => refetch()} />
      <Dialog open={conv} onClose={() => setConv(false)} title="Add unit conversion" footer={<><Button onClick={() => setConv(false)}>Cancel</Button><Button variant="primary" disabled={!(Number(cf.factor) > 0)} loading={addConv.isPending} onClick={() => addConv.mutate(undefined)}>Add</Button></>}>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Unit">{(fid) => <Select id={fid} value={cf.unit_code} onChange={(e) => setCf({ ...cf, unit_code: e.target.value })}>{units.data?.units.filter((u) => u.code !== p.base_unit).map((u) => <option key={u.code}>{u.code}</option>)}</Select>}</Field>
          <Field label={`${p.base_unit} per unit`}>{(fid) => <Input id={fid} type="number" min="0" step="any" value={cf.factor} onChange={(e) => setCf({ ...cf, factor: e.target.value })} />}</Field>
        </div>
      </Dialog>
    </>
  );
}
