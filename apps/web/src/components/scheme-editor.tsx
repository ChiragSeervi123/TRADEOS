"use client";

import { Plus, Trash2 } from "lucide-react";
import { useApi } from "@/lib/hooks";
import { ProductPicker } from "./pickers";
import { Button, Field, Input, Select } from "./ui";

export type Slab = { min_quantity?: string; min_value?: string; percent?: string; amount?: string; amount_per_unit?: string };
export type VersionForm = {
  scheme_type: string;
  config: Record<string, unknown>;
  scope: { product_ids: string[]; brands: string[]; categories: string[]; exclude_product_ids: string[] };
  eligibility: { retailer_ids: string[]; territories: string[]; exclude_retailer_ids: string[] };
  start_date: string;
  end_date: string;
  stacking: string;
  priority: number;
  max_benefit: string;
  source_reference: string;
  change_note: string;
};

export const emptyVersion = (): VersionForm => ({
  scheme_type: "percentage",
  config: { percent: "5" },
  scope: { product_ids: [], brands: [], categories: [], exclude_product_ids: [] },
  eligibility: { retailer_ids: [], territories: [], exclude_retailer_ids: [] },
  start_date: new Date().toISOString().slice(0, 10),
  end_date: new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10),
  stacking: "exclusive",
  priority: 100,
  max_benefit: "",
  source_reference: "",
  change_note: "",
});

export const TYPE_LABELS: Record<string, string> = {
  percentage: "Percentage discount",
  fixed_amount: "Fixed amount discount",
  buy_x_get_y: "Buy X get Y (free goods)",
  quantity_slab: "Quantity slab",
  value_slab: "Order-value slab",
  combination: "Product combination",
};

const DEFAULT_CFG: Record<string, Record<string, unknown>> = {
  percentage: { percent: "5" },
  fixed_amount: { amount: "100", per: "order" },
  buy_x_get_y: { buy_quantity: "10", free_quantity: "1" },
  quantity_slab: { slabs: [{ min_quantity: "24", percent: "2" }, { min_quantity: "72", percent: "4" }], basis: "scope_total" },
  value_slab: { slabs: [{ min_value: "5000", percent: "1" }] },
  combination: { required: [], percent: "5" },
};

function MultiChoice({ label, options, value, onChange }: { label: string; options: string[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <fieldset>
      <legend className="mb-1 text-xs font-medium text-ink-2">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => (
          <label key={o} className={`cursor-pointer rounded-full border px-2.5 py-0.5 text-xs ${value.includes(o) ? "border-brand bg-brand text-white" : "border-border-strong"}`}>
            <input type="checkbox" className="sr-only" checked={value.includes(o)} onChange={(e) => onChange(e.target.checked ? [...value, o] : value.filter((x) => x !== o))} />
            {o}
          </label>
        ))}
        {!options.length && <span className="text-xs text-muted">None in catalogue</span>}
      </div>
    </fieldset>
  );
}

export function SchemeVersionEditor({ v, onChange }: { v: VersionForm; onChange: (v: VersionForm) => void }) {
  const facets = useApi<{ brands: string[]; categories: string[] }>("/products/facets");
  const rf = useApi<{ territories: string[] }>("/retailers/facets");
  const cfg = v.config as Record<string, string> & { slabs?: Slab[]; required?: { product_id: string; min_quantity: string; label?: string }[] };
  const setCfg = (patch: Record<string, unknown>) => onChange({ ...v, config: { ...v.config, ...patch } });
  const num = (k: string, label: string, hint?: string) => (
    <Field label={label} hint={hint}>{(id) => <Input id={id} type="number" min="0" step="any" value={String(cfg[k] ?? "")} onChange={(e) => setCfg({ [k]: e.target.value || undefined })} />}</Field>
  );
  const slabs = (cfg.slabs ?? []) as Slab[];
  const slabKey = v.scheme_type === "quantity_slab" ? "min_quantity" : "min_value";
  const setSlab = (i: number, p: Slab) => setCfg({ slabs: slabs.map((s, j) => (j === i ? { ...s, ...p } : s)) });

  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Scheme type">{(id) => <Select id={id} value={v.scheme_type} onChange={(e) => onChange({ ...v, scheme_type: e.target.value, config: DEFAULT_CFG[e.target.value] })}>{Object.entries(TYPE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>}</Field>
        <Field label="Effective from" required>{(id) => <Input id={id} type="date" value={v.start_date} onChange={(e) => onChange({ ...v, start_date: e.target.value })} />}</Field>
        <Field label="Effective to" required>{(id) => <Input id={id} type="date" value={v.end_date} onChange={(e) => onChange({ ...v, end_date: e.target.value })} />}</Field>
      </div>
      <div className="rounded-md border border-border p-3">
        <p className="mb-2 text-xs font-semibold text-ink-2">Benefit formula</p>
        <div className="grid gap-3 md:grid-cols-3">
          {v.scheme_type === "percentage" && <>{num("percent", "Discount %")}{num("min_quantity", "Min. qualifying qty (base units)", "Optional")}{num("min_value", "Min. qualifying value ₹", "Optional")}</>}
          {v.scheme_type === "fixed_amount" && <>{num("amount", "Amount ₹")}<Field label="Per">{(id) => <Select id={id} value={String(cfg.per ?? "order")} onChange={(e) => setCfg({ per: e.target.value })}><option value="order">Order (allocated pro rata)</option><option value="unit">Unit</option></Select>}</Field>{num("min_quantity", "Min. qty", "Optional")}</>}
          {v.scheme_type === "buy_x_get_y" && <>
            {num("buy_quantity", "Buy quantity (base units)")}{num("free_quantity", "Free quantity")}
            <Field label="Free product" hint="Empty = same product as purchased">{(id) => <ProductPicker id={id} value={(cfg.free_product_id as string) ?? null} onChange={(pid) => setCfg({ free_product_id: pid ?? undefined })} />}</Field>
          </>}
          {(v.scheme_type === "quantity_slab" || v.scheme_type === "value_slab") && (
            <div className="space-y-2 md:col-span-3">
              {slabs.map((s, i) => (
                <div key={i} className="grid grid-cols-[1fr_1fr_auto] gap-2">
                  <Input aria-label={slabKey === "min_quantity" ? "Minimum quantity" : "Minimum value"} placeholder={slabKey === "min_quantity" ? "Min qty" : "Min value ₹"} type="number" min="0" value={s[slabKey] ?? ""} onChange={(e) => setSlab(i, { [slabKey]: e.target.value })} />
                  <Input aria-label="Discount percent" placeholder="% discount" type="number" min="0" max="100" step="any" value={s.percent ?? ""} onChange={(e) => setSlab(i, { percent: e.target.value || undefined })} />
                  <Button variant="ghost" aria-label="Remove slab" onClick={() => setCfg({ slabs: slabs.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></Button>
                </div>
              ))}
              <Button size="sm" onClick={() => setCfg({ slabs: [...slabs, {}] })}><Plus className="h-3.5 w-3.5" /> Add slab</Button>
              <p className="text-xs text-muted">The highest slab reached applies.</p>
            </div>
          )}
          {v.scheme_type === "combination" && (
            <div className="space-y-2 md:col-span-3">
              {(cfg.required ?? []).map((r, i) => (
                <div key={i} className="grid grid-cols-[1fr_120px_auto] gap-2">
                  <ProductPicker value={r.product_id} initialLabel={r.label} onChange={(pid, o) => setCfg({ required: (cfg.required ?? []).map((x, j) => (j === i ? { ...x, product_id: pid ?? "", label: o?.label } : x)) })} />
                  <Input aria-label="Minimum quantity" type="number" min="0" value={r.min_quantity} onChange={(e) => setCfg({ required: (cfg.required ?? []).map((x, j) => (j === i ? { ...x, min_quantity: e.target.value } : x)) })} />
                  <Button variant="ghost" aria-label="Remove product" onClick={() => setCfg({ required: (cfg.required ?? []).filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></Button>
                </div>
              ))}
              <Button size="sm" onClick={() => setCfg({ required: [...(cfg.required ?? []), { product_id: "", min_quantity: "1" }] })}><Plus className="h-3.5 w-3.5" /> Add required product</Button>
              <div className="grid gap-3 md:grid-cols-3">{num("percent", "Discount % on combination lines")}</div>
            </div>
          )}
        </div>
      </div>
      <div className="rounded-md border border-border p-3 space-y-3">
        <p className="text-xs font-semibold text-ink-2">Product scope (empty = all products)</p>
        <MultiChoice label="Brands" options={facets.data?.brands ?? []} value={v.scope.brands} onChange={(b) => onChange({ ...v, scope: { ...v.scope, brands: b } })} />
        <MultiChoice label="Categories" options={facets.data?.categories ?? []} value={v.scope.categories} onChange={(c) => onChange({ ...v, scope: { ...v.scope, categories: c } })} />
      </div>
      <div className="rounded-md border border-border p-3 space-y-3">
        <p className="text-xs font-semibold text-ink-2">Retailer eligibility (empty = all retailers)</p>
        <MultiChoice label="Territories" options={rf.data?.territories ?? []} value={v.eligibility.territories} onChange={(t) => onChange({ ...v, eligibility: { ...v.eligibility, territories: t } })} />
      </div>
      <div className="grid gap-3 md:grid-cols-4">
        <Field label="Stacking" hint="Exclusive schemes never combine with others on the same line.">{(id) => <Select id={id} value={v.stacking} onChange={(e) => onChange({ ...v, stacking: e.target.value })}><option value="exclusive">Exclusive</option><option value="stackable">Stackable</option></Select>}</Field>
        <Field label="Priority" hint="Lower applies first">{(id) => <Input id={id} type="number" min="0" value={v.priority} onChange={(e) => onChange({ ...v, priority: Number(e.target.value) })} />}</Field>
        <Field label="Max benefit per order ₹" hint="Optional cap">{(id) => <Input id={id} type="number" min="0" step="0.01" value={v.max_benefit} onChange={(e) => onChange({ ...v, max_benefit: e.target.value })} />}</Field>
        <Field label="Source reference" hint="Circular number / page">{(id) => <Input id={id} value={v.source_reference} onChange={(e) => onChange({ ...v, source_reference: e.target.value })} />}</Field>
      </div>
    </div>
  );
}

export function toApiVersion(v: VersionForm) {
  const clean = (o: Record<string, unknown>): Record<string, unknown> => Object.fromEntries(Object.entries(o).filter(([, x]) => x !== "" && x !== undefined && x !== null));
  const cfg = clean(v.config);
  if (Array.isArray(cfg.slabs)) cfg.slabs = (cfg.slabs as Record<string, unknown>[]).map(clean);
  if (Array.isArray(cfg.required)) cfg.required = (cfg.required as { product_id: string; min_quantity: string }[]).map((r) => ({ product_id: r.product_id, min_quantity: r.min_quantity }));
  return { ...v, config: cfg, max_benefit: v.max_benefit || null, source_reference: v.source_reference || null, change_note: v.change_note || null };
}
