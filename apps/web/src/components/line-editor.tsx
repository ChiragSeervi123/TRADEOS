"use client";

import { Plus, Trash2 } from "lucide-react";
import { useApi } from "@/lib/hooks";
import { ProductPicker } from "./pickers";
import { Button, Input, Select } from "./ui";

export type EditLine = { key: string; product_id: string | null; product_label?: string; quantity: string; unit: string; unit_price_override?: string; price_override_reason?: string };

type ProductDetail = { units: { unit: string; factor: string }[]; base_unit: string };

function UnitSelect({ productId, value, onChange }: { productId: string | null; value: string; onChange: (u: string) => void }) {
  const { data } = useApi<ProductDetail>(productId ? `/products/${productId}` : null);
  const units = data?.units ?? [{ unit: value || "PCS", factor: "1" }];
  return (
    <Select aria-label="Unit" value={value} onChange={(e) => onChange(e.target.value)} className="w-[120px]">
      {units.map((u) => (
        <option key={u.unit} value={u.unit}>
          {u.unit}{u.factor !== "1" && u.factor !== "1.0000" ? ` (×${Number(u.factor)})` : ""}
        </option>
      ))}
      {!units.some((u) => u.unit === value) && value && <option value={value}>{value}</option>}
    </Select>
  );
}

let k = 0;
export const newLine = (): EditLine => ({ key: `l${++k}`, product_id: null, quantity: "1", unit: "PCS" });

export function LineEditor({ lines, onChange, allowPriceOverride }: { lines: EditLine[]; onChange: (l: EditLine[]) => void; allowPriceOverride?: boolean }) {
  const set = (i: number, patch: Partial<EditLine>) => onChange(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <div className="space-y-2">
      {lines.map((l, i) => (
        <div key={l.key} className="grid grid-cols-1 gap-2 rounded-md border border-border p-2 md:grid-cols-[1fr_110px_120px_auto] md:items-center">
          <ProductPicker value={l.product_id} initialLabel={l.product_label} onChange={(id, o) => set(i, { product_id: id, product_label: o?.label, unit: id ? l.unit : l.unit })} />
          <Input aria-label="Quantity" type="number" min="0.001" step="any" value={l.quantity} onChange={(e) => set(i, { quantity: e.target.value })} />
          <UnitSelect productId={l.product_id} value={l.unit} onChange={(u) => set(i, { unit: u })} />
          <Button type="button" variant="ghost" aria-label="Remove line" onClick={() => onChange(lines.filter((_, j) => j !== i))} disabled={lines.length === 1}>
            <Trash2 className="h-4 w-4" />
          </Button>
          {allowPriceOverride && (
            <div className="grid grid-cols-1 gap-2 md:col-span-4 md:grid-cols-[160px_1fr]">
              <Input aria-label="Price override per base unit (optional)" placeholder="Override price / base unit" type="number" min="0" step="0.01" value={l.unit_price_override ?? ""} onChange={(e) => set(i, { unit_price_override: e.target.value })} />
              {l.unit_price_override && <Input aria-label="Reason for price override" placeholder="Reason for price override (required)" value={l.price_override_reason ?? ""} onChange={(e) => set(i, { price_override_reason: e.target.value })} />}
            </div>
          )}
        </div>
      ))}
      <Button type="button" size="sm" onClick={() => onChange([...lines, newLine()])}>
        <Plus className="h-3.5 w-3.5" /> Add line
      </Button>
    </div>
  );
}

export function toApiLines(lines: EditLine[]) {
  return lines
    .filter((l) => l.product_id)
    .map((l) => ({
      product_id: l.product_id,
      quantity: l.quantity,
      unit: l.unit,
      ...(l.unit_price_override ? { unit_price_override: l.unit_price_override, price_override_reason: l.price_override_reason } : {}),
    }));
}
