"use client";

import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ProductPicker, SupplierPicker } from "@/components/pickers";
import { Alert, Badge, Button, Card, ConfidenceChip, DataTable, Dialog, ErrorState, Field, Input, Json, KeyValue, PageHeader, Spinner, StatusChip, SyntheticTag, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { date, money, qty, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type Line = { line_no: number; product_id: string | null; sku: string | null; agreed_price: string | null; description: string; quantity: string; unit_price: string; tax_rate: string; tax_amount: string; line_total: string; match_confidence: string | null };
type PI = {
  id: string; supplier_id: string | null; supplier: string | null; invoice_number: string; invoice_date: string | null; due_date: string | null; subtotal: string; tax_total: string; other_charges: string; total: string; status: string; is_synthetic: boolean;
  lines: Line[]; validation: { valid: boolean; issues: { code: string; severity: string; message: string }[]; extraction_notes?: string[]; supplier_match_confidence?: number; rejected_reason?: string } | null;
  document: { id: string; filename: string; content_type: string; url: string; text_content: string | null } | null;
  extraction: { provider: string; model: string | null; status: string; error: string | null; raw_output: unknown; latency_ms: number | null } | null;
  cases: { id: string; case_number: string; category: string; status: string; difference: string | null }[];
};
type EL = { key: number; product_id: string | null; product_label?: string; description: string; quantity: string; unit_price: string; tax_rate: string; line_total: string };

export default function PurchaseInvoicePage() {
  const { id } = useParams<{ id: string }>();
  const { can } = useAuth();
  const { data: pi, isPending, error, refetch } = useApi<PI>(`/purchase-invoices/${id}`);
  const [head, setHead] = useState({ supplier_id: null as string | null, invoice_number: "", invoice_date: "", due_date: "", subtotal: "", tax_total: "", other_charges: "", total: "" });
  const [lines, setLines] = useState<EL[]>([]);
  const [dirty, setDirty] = useState(false);
  const [verifyDlg, setVerifyDlg] = useState(false);
  const [rejectDlg, setRejectDlg] = useState(false);
  const [reason, setReason] = useState("");
  const [raw, setRaw] = useState(false);
  useEffect(() => {
    if (!pi) return;
    setHead({ supplier_id: pi.supplier_id, invoice_number: pi.invoice_number.startsWith("PENDING-") ? "" : pi.invoice_number, invoice_date: pi.invoice_date ?? "", due_date: pi.due_date ?? "", subtotal: pi.subtotal, tax_total: pi.tax_total, other_charges: pi.other_charges, total: pi.total });
    setLines(pi.lines.map((l, i) => ({ key: i, product_id: l.product_id, product_label: l.sku ?? undefined, description: l.description, quantity: l.quantity, unit_price: l.unit_price, tax_rate: l.tax_rate, line_total: l.line_total })));
    setDirty(false);
  }, [pi]);
  const inv = ["/purchase-invoices", "/reconciliation", "/exceptions"];
  const save = useAction(() => api(`/purchase-invoices/${id}`, { method: "PATCH", body: { ...head, invoice_number: head.invoice_number || undefined, invoice_date: head.invoice_date || null, due_date: head.due_date || null, lines: lines.map((l) => ({ product_id: l.product_id, description: l.description || "(no description)", quantity: l.quantity || "0", unit_price: l.unit_price || "0", tax_rate: l.tax_rate || "0", line_total: l.line_total || null })) } }), { invalidate: inv, success: "Corrections saved and re-checked", onSuccess: () => setDirty(false) });
  const verify = useAction(() => api(`/purchase-invoices/${id}/verify`, { body: reason ? { accept_differences_reason: reason } : {} }), { invalidate: inv, success: "Invoice verified; reconciliation rules ran", onSuccess: () => { setVerifyDlg(false); setReason(""); } });
  const reject = useAction(() => api(`/purchase-invoices/${id}/reject`, { body: { reason } }), { invalidate: inv, success: "Invoice rejected", onSuccess: () => { setRejectDlg(false); setReason(""); } });

  if (isPending) return <Spinner />;
  if (error || !pi) return <ErrorState error={error} retry={refetch} />;
  const editable = pi.status === "needs_review" && can("finance.write");
  const v = pi.validation;
  const errors = v?.issues.filter((i) => i.severity === "error") ?? [];
  const setL = (k: number, p: Partial<EL>) => { setLines(lines.map((l) => (l.key === k ? { ...l, ...p } : l))); setDirty(true); };
  const hset = (k: keyof typeof head) => (e: { target: { value: string } }) => { setHead({ ...head, [k]: e.target.value }); setDirty(true); };

  return (
    <>
      <PageHeader back={{ href: "/finance?tab=purchase", label: "Finance" }} title={<span className="flex items-center gap-2">Supplier invoice {pi.invoice_number.startsWith("PENDING-") ? "(number not yet confirmed)" : pi.invoice_number} <StatusChip status={pi.status} /> <SyntheticTag show={pi.is_synthetic} /></span>}
        description={`${pi.supplier ?? "Supplier not identified"} · ${date(pi.invoice_date)} · total ${money(pi.total)}`}
        actions={<>
          {editable && dirty && <Button variant="primary" loading={save.isPending} onClick={() => save.mutate(undefined)}>Save corrections</Button>}
          {pi.status === "needs_review" && can("finance.approve") && <Button variant="danger" onClick={() => setRejectDlg(true)}>Reject</Button>}
          {pi.status === "needs_review" && can("finance.approve") && <Button variant="accent" disabled={dirty} title={dirty ? "Save corrections first" : undefined} onClick={() => setVerifyDlg(true)}>Verify</Button>}
        </>} />
      {pi.status === "needs_review" && <Alert tone="info">Extracted values are not authoritative until a person verifies them. Compare with the document, correct anything wrong, then verify.</Alert>}
      {v?.rejected_reason && <Alert tone="danger" title="Rejected">{v.rejected_reason}</Alert>}
      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(300px,0.9fr)_1.5fr]">
        <div className="space-y-4">
          <Card title="Source document">
            {pi.document ? (
              <div className="space-y-2">
                {pi.document.content_type.startsWith("image/") ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={pi.document.url} alt={pi.document.filename} className="max-h-[480px] rounded-md border border-border" />
                ) : pi.document.content_type === "application/pdf" ? (
                  <iframe title={pi.document.filename} src={pi.document.url} className="h-[480px] w-full rounded-md border border-border" sandbox="" />
                ) : null}
                <a className="text-xs text-info underline" href={pi.document.url} target="_blank" rel="noreferrer">Open {pi.document.filename}</a>
                {pi.document.text_content && <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 p-2 text-xs">{pi.document.text_content}</pre>}
              </div>
            ) : <p className="text-sm text-muted">Entered manually; no document attached.</p>}
          </Card>
          {pi.extraction && (
            <Card title="Extraction">
              <KeyValue items={[["Provider", pi.extraction.provider === "anthropic" ? `Claude · ${pi.extraction.model}` : "Local demo parser"], ["Status", titleCase(pi.extraction.status)], ["Supplier match", v?.supplier_match_confidence !== undefined ? <ConfidenceChip key="c" value={v.supplier_match_confidence} /> : "—"]]} />
              {pi.extraction.error && <div className="mt-2"><Alert tone="danger">{pi.extraction.error}</Alert></div>}
              {v?.extraction_notes?.length ? <ul className="mt-2 list-disc pl-5 text-xs text-accent-strong">{v.extraction_notes.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
              <button className="mt-2 text-xs text-info hover:underline" onClick={() => setRaw(!raw)}>{raw ? "Hide" : "Show"} raw output</button>
              {raw && <Json value={pi.extraction.raw_output} />}
            </Card>
          )}
        </div>
        <div className="space-y-4">
          {(v?.issues.length ?? 0) > 0 && (
            <Card title={`Checks · ${errors.length} error(s)`}>
              <ul className="space-y-1">{v!.issues.map((i, n) => <li key={n} className="flex gap-2 text-sm"><Badge tone={i.severity === "error" ? "danger" : "warning"}>{i.severity}</Badge>{i.message}</li>)}</ul>
            </Card>
          )}
          <Card title="Header">
            <div className="grid gap-3 md:grid-cols-4">
              <Field label="Supplier" className="md:col-span-2">{(fid) => editable ? <SupplierPicker id={fid} value={head.supplier_id} initialLabel={pi.supplier ?? ""} onChange={(s) => { setHead({ ...head, supplier_id: s }); setDirty(true); }} /> : <p className="text-sm">{pi.supplier}</p>}</Field>
              <Field label="Invoice number">{(fid) => <Input id={fid} value={head.invoice_number} onChange={hset("invoice_number")} disabled={!editable} />}</Field>
              <Field label="Invoice date">{(fid) => <Input id={fid} type="date" value={head.invoice_date} onChange={hset("invoice_date")} disabled={!editable} />}</Field>
              {(["subtotal", "tax_total", "other_charges", "total"] as const).map((k) => <Field key={k} label={titleCase(k)}>{(fid) => <Input id={fid} type="number" step="0.01" value={head[k]} onChange={hset(k)} disabled={!editable} />}</Field>)}
            </div>
          </Card>
          <Card title="Line items" padded={false}>
            {editable ? (
              <div className="space-y-2 p-3">
                {lines.map((l) => (
                  <div key={l.key} className="grid gap-2 rounded-md border border-border p-2 md:grid-cols-[1.2fr_1fr_80px_100px_70px_110px_auto]">
                    <ProductPicker value={l.product_id} initialLabel={l.product_label} onChange={(p, o) => setL(l.key, { product_id: p, product_label: o?.label })} />
                    <Input aria-label="Description" value={l.description} onChange={(e) => setL(l.key, { description: e.target.value })} />
                    <Input aria-label="Quantity" type="number" step="any" value={l.quantity} onChange={(e) => setL(l.key, { quantity: e.target.value })} />
                    <Input aria-label="Unit price" type="number" step="0.01" value={l.unit_price} onChange={(e) => setL(l.key, { unit_price: e.target.value })} />
                    <Input aria-label="Tax %" type="number" step="0.01" value={l.tax_rate} onChange={(e) => setL(l.key, { tax_rate: e.target.value })} />
                    <Input aria-label="Line total" type="number" step="0.01" value={l.line_total} onChange={(e) => setL(l.key, { line_total: e.target.value })} />
                    <Button variant="ghost" aria-label="Remove line" onClick={() => { setLines(lines.filter((x) => x.key !== l.key)); setDirty(true); }}><Trash2 className="h-4 w-4" /></Button>
                  </div>
                ))}
                <Button size="sm" onClick={() => { setLines([...lines, { key: Date.now(), product_id: null, description: "", quantity: "1", unit_price: "0", tax_rate: "0", line_total: "" }]); setDirty(true); }}><Plus className="h-3.5 w-3.5" /> Add line</Button>
              </div>
            ) : (
              <DataTable<Line> dense rows={pi.lines} rowKey={(l) => String(l.line_no)} columns={[
                { key: "p", header: "Product", cell: (l) => <span>{l.sku ?? <span className="text-muted">unlinked</span>}<div className="text-xs text-muted">{l.description}</div></span> },
                { key: "q", header: "Qty", align: "right", cell: (l) => qty(l.quantity) },
                { key: "u", header: "Unit price", align: "right", cell: (l) => <span>{money(l.unit_price)}{l.agreed_price && l.agreed_price !== l.unit_price && <div className="text-xs text-accent-strong">agreed {money(l.agreed_price)}</div>}</span> },
                { key: "t", header: "Line total", align: "right", cell: (l) => money(l.line_total) },
              ]} />
            )}
          </Card>
          {pi.cases.length > 0 && (
            <Card title="Reconciliation cases">
              <ul className="space-y-1 text-sm">{pi.cases.map((c) => <li key={c.id} className="flex justify-between gap-2"><Link className="text-info hover:underline" href={`/reconciliation/${c.id}`}>{c.case_number} · {titleCase(c.category)}</Link><span className="tabular">{money(c.difference)}</span><StatusChip status={c.status} /></li>)}</ul>
            </Card>
          )}
        </div>
      </div>
      <Dialog open={verifyDlg} onClose={() => setVerifyDlg(false)} title="Verify supplier invoice" footer={<><Button onClick={() => setVerifyDlg(false)}>Cancel</Button><Button variant="accent" loading={verify.isPending} disabled={errors.length > 0 && reason.trim().length < 5} onClick={() => verify.mutate(undefined)}>Verify</Button></>}>
        <p className="mb-3 text-sm">Verifying makes this invoice authoritative and runs goods-receipt and price matching.</p>
        {errors.length > 0 && <Field label="Reason for verifying with arithmetic differences" required hint="The difference stays visible and can be reconciled.">{(fid) => <Textarea id={fid} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>}
      </Dialog>
      <Dialog open={rejectDlg} onClose={() => setRejectDlg(false)} title="Reject invoice" footer={<><Button onClick={() => setRejectDlg(false)}>Back</Button><Button variant="danger" disabled={reason.trim().length < 3} loading={reject.isPending} onClick={() => reject.mutate(undefined)}>Reject</Button></>}>
        <Field label="Reason" required>{(fid) => <Textarea id={fid} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />}</Field>
      </Dialog>
    </>
  );
}
