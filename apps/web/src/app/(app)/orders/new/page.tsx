"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { LineEditor, newLine, toApiLines, type EditLine } from "@/components/line-editor";
import { RetailerPicker } from "@/components/pickers";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Field, Input, PageHeader, Select, Textarea } from "@/components/ui";
import { api, newIdempotencyKey } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/hooks";

type Wh = { id: string; code: string; name: string; is_default: boolean; is_active: boolean };

export default function NewOrderPage() {
  const router = useRouter();
  const toast = useToast();
  const { can } = useAuth();
  const whs = useApi<Wh[]>("/warehouses");
  const [retailer, setRetailer] = useState<string | null>(null);
  const [warehouse, setWarehouse] = useState("");
  const [deliveryDate, setDeliveryDate] = useState("");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<EditLine[]>([newLine()]);
  const [busy, setBusy] = useState(false);
  const idem = useRef(newIdempotencyKey()); // one key per form: retries never create duplicates
  const dirty = Boolean(retailer || lines.some((l) => l.product_id));

  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if (dirty && !busy) e.preventDefault();
    };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty, busy]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!retailer) return toast.error(new Error("Select a retailer."), "Select a retailer");
    const apiLines = toApiLines(lines);
    if (!apiLines.length) return toast.error(new Error("Add at least one product."), "Add at least one product");
    if (lines.some((l) => l.unit_price_override && !l.price_override_reason)) return toast.error(new Error("Give a reason for every price override."), "Missing override reason");
    setBusy(true);
    try {
      const o = await api<{ id: string; order_number: string }>("/orders", {
        body: { retailer_id: retailer, warehouse_id: warehouse || null, requested_delivery_date: deliveryDate || null, notes: notes || null, lines: apiLines },
        headers: { "Idempotency-Key": idem.current },
      });
      toast.success(`Order ${o.order_number} created`, "Review validation results before approval.");
      router.push(`/orders/${o.id}`);
    } catch (err) {
      toast.error(err);
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="New order" back={{ href: "/orders", label: "Orders" }} description="Prices, schemes, stock and credit are checked when you save. Approval is a separate step." />
      <form onSubmit={submit} className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <Card title="Lines">
          <LineEditor lines={lines} onChange={setLines} allowPriceOverride={can("orders.approve")} />
        </Card>
        <div className="space-y-4">
          <Card title="Order details">
            <div className="space-y-3">
              <Field label="Retailer" required>{(id) => <RetailerPicker id={id} value={retailer} onChange={(v) => setRetailer(v)} />}</Field>
              <Field label="Warehouse" hint="Defaults to your default warehouse.">
                {(id) => (
                  <Select id={id} value={warehouse} onChange={(e) => setWarehouse(e.target.value)}>
                    <option value="">Default warehouse</option>
                    {whs.data?.filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
                  </Select>
                )}
              </Field>
              <Field label="Requested delivery date">{(id) => <Input id={id} type="date" value={deliveryDate} onChange={(e) => setDeliveryDate(e.target.value)} />}</Field>
              <Field label="Notes">{(id) => <Textarea id={id} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />}</Field>
            </div>
          </Card>
          {whs.data && whs.data.length === 0 && <Alert tone="warning" title="No warehouse">Create a warehouse in Settings before taking orders.</Alert>}
          <Button type="submit" variant="primary" className="w-full" loading={busy}>Save order</Button>
        </div>
      </form>
    </>
  );
}
