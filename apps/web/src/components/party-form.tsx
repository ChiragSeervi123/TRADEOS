"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi, useInvalidate } from "@/lib/hooks";
import { useToast } from "./toast";
import { Alert, Button, Dialog, Field, Input, Select } from "./ui";

type Party = Record<string, string | number | boolean | null | undefined> & { id?: string };
type Dup = { id: string; code: string; name: string; reasons?: string[] };

const RETAILER_FIELDS: [string, string, string?][] = [["code", "Code"], ["name", "Business name"], ["contact_name", "Contact name"], ["phone", "Phone"], ["email", "Email", "email"], ["gstin", "GSTIN"], ["territory", "Territory"], ["route", "Route"], ["billing_address", "Billing address"], ["delivery_address", "Delivery address"], ["payment_terms_days", "Payment terms (days)", "number"]];
const SUPPLIER_FIELDS: [string, string, string?][] = [["code", "Code"], ["name", "Name"], ["contact_name", "Contact name"], ["phone", "Phone"], ["email", "Email", "email"], ["gstin", "GSTIN"], ["address", "Address"], ["external_id", "External system ID"]];

export function PartyForm({ kind, open, onClose, initial, onSaved }: { kind: "retailer" | "supplier"; open: boolean; onClose: () => void; initial?: Party; onSaved?: (p: Party) => void }) {
  const { can } = useAuth();
  const [f, setF] = useState<Party>({});
  const [dups, setDups] = useState<Dup[] | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const inv = useInvalidate();
  const pls = useApi<{ id: string; name: string }[]>(open && kind === "retailer" && can("pricing.read") ? "/price-lists" : null);
  useEffect(() => {
    if (open) {
      setF(initial ?? { is_active: true, payment_terms_days: 30 });
      setDups(null);
      setErrors({});
    }
  }, [open, initial]);
  const fields = kind === "retailer" ? RETAILER_FIELDS : SUPPLIER_FIELDS;
  const base = kind === "retailer" ? "/retailers" : "/suppliers";

  const save = async (allow = false) => {
    setBusy(true);
    setErrors({});
    const body: Party = {};
    for (const [k] of fields) if (f[k] !== undefined) body[k] = f[k] === "" ? null : f[k];
    body.is_active = f.is_active;
    if (kind === "retailer") body.price_list_id = f.price_list_id || null;
    if (!f.id && kind === "retailer" && can("finance.approve") && f.credit_limit) body.credit_limit = f.credit_limit;
    try {
      const out = f.id ? await api<Party>(`${base}/${f.id}`, { method: "PATCH", body: { ...body, code: undefined, version: f.version } }) : await api<Party>(base, { body: { ...body, allow_duplicate: allow } });
      toast.success(f.id ? "Saved" : `${kind === "retailer" ? "Retailer" : "Supplier"} created`);
      inv(base);
      onClose();
      onSaved?.(out);
    } catch (e) {
      if (e instanceof ApiError && e.code === "POSSIBLE_DUPLICATE") setDups((e.details as { candidates: Dup[] }).candidates);
      else if (e instanceof ApiError && e.code === "VALIDATION_ERROR") setErrors(e.fieldErrors());
      else toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog wide open={open} onClose={onClose} title={f.id ? `Edit ${String(f.code)}` : `New ${kind}`} footer={<><Button onClick={onClose}>Cancel</Button>{dups ? <Button variant="accent" loading={busy} onClick={() => save(true)}>Create anyway</Button> : <Button variant="primary" loading={busy} onClick={() => save(false)}>Save</Button>}</>}>
      {dups && <div className="mb-3"><Alert tone="warning" title="Possible duplicates">{dups.map((d) => `${d.code} · ${d.name}${d.reasons ? ` (${d.reasons.join(", ")})` : ""}`).join("; ")}. Records are never merged automatically.</Alert></div>}
      <div className="grid gap-3 md:grid-cols-2">
        {fields.map(([k, label, type]) => (
          <Field key={k} label={label} required={k === "code" || k === "name"} error={errors[k]}>
            {(id) => <Input id={id} type={type ?? "text"} value={String(f[k] ?? "")} disabled={k === "code" && Boolean(f.id)} onChange={(e) => setF({ ...f, [k]: type === "number" ? Number(e.target.value) : e.target.value })} />}
          </Field>
        ))}
        {kind === "retailer" && !f.id && can("finance.approve") && <Field label="Credit limit (₹)" hint="Leave empty for no limit.">{(id) => <Input id={id} type="number" min="0" step="0.01" value={String(f.credit_limit ?? "")} onChange={(e) => setF({ ...f, credit_limit: e.target.value })} />}</Field>}
        {kind === "retailer" && pls.data && (
          <Field label="Price list" hint="Default list applies when empty.">{(id) => <Select id={id} value={String(f.price_list_id ?? "")} onChange={(e) => setF({ ...f, price_list_id: e.target.value })}><option value="">Default</option>{pls.data!.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</Select>}</Field>
        )}
        <Field label="Status">{(id) => <Select id={id} value={f.is_active === false ? "0" : "1"} onChange={(e) => setF({ ...f, is_active: e.target.value === "1" })}><option value="1">Active</option><option value="0">Inactive</option></Select>}</Field>
      </div>
    </Dialog>
  );
}
