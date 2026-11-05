"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useApi, useInvalidate } from "@/lib/hooks";
import { SupplierPicker } from "./pickers";
import { useToast } from "./toast";
import { Alert, Button, Dialog, Field, Input, Select } from "./ui";

export type ProductData = { id?: string; sku: string; name: string; brand: string | null; category: string | null; variant: string | null; base_unit: string; tax_code: string | null; tax_rate: string; mrp: string | null; cost_price: string | null; barcode: string | null; supplier_id: string | null; is_active: boolean; version?: number };

const EMPTY: ProductData = { sku: "", name: "", brand: "", category: "", variant: "", base_unit: "PCS", tax_code: "", tax_rate: "18", mrp: "", cost_price: "", barcode: "", supplier_id: null, is_active: true };

type Dup = { id: string; sku: string; name: string; variant: string | null; score: number; reasons: string[] };

export function ProductForm({ open, onClose, initial, onSaved }: { open: boolean; onClose: () => void; initial?: ProductData; onSaved?: (p: ProductData) => void }) {
  const [f, setF] = useState<ProductData>(initial ?? EMPTY);
  const [dups, setDups] = useState<Dup[] | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const units = useApi<{ units: { code: string; name: string }[] }>(open ? "/units" : null);
  const toast = useToast();
  const inv = useInvalidate();
  useEffect(() => {
    if (open) {
      setF(initial ?? EMPTY);
      setDups(null);
      setErrors({});
    }
  }, [open, initial]);

  const save = async (allowDuplicate = false) => {
    setBusy(true);
    setErrors({});
    const clean = (v: string | null) => (v === "" ? null : v);
    const body = { ...f, brand: clean(f.brand), category: clean(f.category), variant: clean(f.variant), tax_code: clean(f.tax_code), mrp: clean(f.mrp), cost_price: clean(f.cost_price), barcode: clean(f.barcode) };
    try {
      const p = f.id
        ? await api<ProductData>(`/products/${f.id}`, { method: "PATCH", body: { ...body, sku: undefined, base_unit: undefined, id: undefined, version: f.version } })
        : await api<ProductData>("/products", { body: { ...body, allow_duplicate: allowDuplicate } });
      toast.success(f.id ? "Product updated" : `Product ${p.sku} created`);
      inv("/products");
      onClose();
      onSaved?.(p);
    } catch (e) {
      if (e instanceof ApiError && e.code === "POSSIBLE_DUPLICATE") setDups((e.details as { candidates: Dup[] }).candidates);
      else if (e instanceof ApiError && e.code === "VALIDATION_ERROR") setErrors(e.fieldErrors());
      else toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const set = (k: keyof ProductData) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  return (
    <Dialog wide open={open} onClose={onClose} title={f.id ? `Edit ${f.sku}` : "New product"} footer={
      <>
        <Button onClick={onClose}>Cancel</Button>
        {dups ? <Button variant="accent" loading={busy} onClick={() => save(true)}>Create anyway</Button> : <Button variant="primary" loading={busy} onClick={() => save(false)}>Save</Button>}
      </>
    }>
      {dups && (
        <div className="mb-3">
          <Alert tone="warning" title="Possible duplicates found">
            <ul className="list-disc pl-5">{dups.map((d) => <li key={d.id}>{d.sku} · {d.name} {d.variant} ({d.reasons.join(", ")})</li>)}</ul>
            Records are never merged automatically. Create anyway only if this is a different product.
          </Alert>
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="SKU code" required error={errors.sku}>{(id) => <Input id={id} value={f.sku} onChange={set("sku")} disabled={Boolean(f.id)} />}</Field>
        <Field label="Product name" required error={errors.name} className="md:col-span-2">{(id) => <Input id={id} value={f.name} onChange={set("name")} />}</Field>
        <Field label="Brand">{(id) => <Input id={id} value={f.brand ?? ""} onChange={set("brand")} />}</Field>
        <Field label="Category">{(id) => <Input id={id} value={f.category ?? ""} onChange={set("category")} />}</Field>
        <Field label="Variant / pack size" hint="e.g. 1 kg, 340 ml">{(id) => <Input id={id} value={f.variant ?? ""} onChange={set("variant")} />}</Field>
        <Field label="Base unit">
          {(id) => (
            <Select id={id} value={f.base_unit} onChange={set("base_unit")} disabled={Boolean(f.id)}>
              {(units.data?.units ?? [{ code: "PCS", name: "Piece" }]).map((u) => <option key={u.code} value={u.code}>{u.code} · {u.name}</option>)}
            </Select>
          )}
        </Field>
        <Field label="Tax rate %" hint="Configured rate; not tax advice." error={errors.tax_rate}>{(id) => <Input id={id} type="number" min="0" max="100" step="0.01" value={f.tax_rate} onChange={set("tax_rate")} />}</Field>
        <Field label="Tax code (HSN)">{(id) => <Input id={id} value={f.tax_code ?? ""} onChange={set("tax_code")} />}</Field>
        <Field label="Agreed cost / base unit" hint="Used for inventory value and supplier price checks." error={errors.cost_price}>{(id) => <Input id={id} type="number" min="0" step="0.01" value={f.cost_price ?? ""} onChange={set("cost_price")} />}</Field>
        <Field label="MRP" error={errors.mrp}>{(id) => <Input id={id} type="number" min="0" step="0.01" value={f.mrp ?? ""} onChange={set("mrp")} />}</Field>
        <Field label="Barcode / GTIN">{(id) => <Input id={id} value={f.barcode ?? ""} onChange={set("barcode")} />}</Field>
        <Field label="Supplier">{(id) => <SupplierPicker id={id} value={f.supplier_id} onChange={(v) => setF({ ...f, supplier_id: v })} />}</Field>
        <Field label="Status">
          {(id) => (
            <Select id={id} value={f.is_active ? "1" : "0"} onChange={(e) => setF({ ...f, is_active: e.target.value === "1" })}>
              <option value="1">Active</option>
              <option value="0">Inactive</option>
            </Select>
          )}
        </Field>
      </div>
    </Dialog>
  );
}
