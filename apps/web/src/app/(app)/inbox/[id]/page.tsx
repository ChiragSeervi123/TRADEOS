"use client";

import { Plus } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ProductPicker, RetailerPicker } from "@/components/pickers";
import { useToast } from "@/components/toast";
import { Alert, Badge, Button, Card, ConfidenceChip, Dialog, ErrorState, Field, Input, Json, KeyValue, PageHeader, Select, Spinner, StatusChip, Textarea, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, titleCase } from "@/lib/format";
import { useAction, useApi } from "@/lib/hooks";

type Cand = { product_id: string; sku: string; name: string; variant: string | null; base_unit: string; confidence: number; reasons: string[] };
type ExtLine = { index: number; source_description: string; product_text: string; quantity: string | null; unit: string | null; base_quantity: string | null; status: string; selected_product_id: string | null; sku_candidates: Cand[]; confidence: { product: number; quantity: number }; warnings: string[] };
type Result = {
  retailer: { extracted: { name: string | null; phone: string | null }; selected_retailer_id: string | null; candidates: { retailer_id: string; code: string; name: string; confidence: number; reasons: string[] }[]; confidence: number };
  lines: ExtLine[]; requested_delivery_date: string | null; notes: string[]; warnings: string[]; overall_confidence: number;
};
type Source = {
  id: string; status: string; channel: string; sender: string | null; received_at: string; raw_text: string | null; possible_duplicate_of: string | null;
  document: { id: string; filename: string; content_type: string; url: string; text_content: string | null } | null;
  extraction: { id: string; provider: string; model: string | null; status: string; error: string | null; latency_ms: number | null; input_tokens: number | null; output_tokens: number | null; result: Result | null; raw_output: unknown; warnings: string[] | null } | null;
  order: { id: string; order_number: string; status: string } | null; history: { id: string; provider: string; status: string; at: string }[]; created_by: string | null;
};
type Draft = { key: string; index: number | null; include: boolean; product_id: string | null; product_label?: string; quantity: string; unit: string; confirm: boolean; status: string | null };

let seq = 0;

export default function InboxReviewPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useAuth();
  const { data: s, isPending, error, refetch } = useApi<Source>(`/order-extractions/${id}`);
  const [retailer, setRetailer] = useState<string | null>(null);
  const [retailerLabel, setRetailerLabel] = useState<string | undefined>();
  const [lines, setLines] = useState<Draft[]>([]);
  const [deliveryDate, setDeliveryDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [reason, setReason] = useState("");
  const [showRaw, setShowRaw] = useState(false);
  const reextract = useAction(() => api(`/order-extractions/${id}/reextract`, { method: "POST" }), { invalidate: ["/order-extractions"], success: "Extraction re-run" });
  const doDiscard = useAction(() => api(`/order-extractions/${id}/discard`, { body: { reason } }), { invalidate: ["/order-extractions"], success: "Inbox item discarded", onSuccess: () => setDiscard(false) });

  const res = s?.extraction?.result ?? null;
  useEffect(() => {
    if (!res) {
      setLines([]);
      return;
    }
    const sel = res.retailer.candidates.find((c) => c.retailer_id === res.retailer.selected_retailer_id);
    setRetailer(res.retailer.selected_retailer_id);
    setRetailerLabel(sel ? sel.name : "");
    setDeliveryDate(res.requested_delivery_date ?? "");
    setLines(
      res.lines.map((l) => {
        const c = l.sku_candidates.find((x) => x.product_id === l.selected_product_id);
        return { key: `x${l.index}`, index: l.index, include: true, product_id: l.selected_product_id, product_label: c ? `${c.name} ${c.variant ?? ""}`.trim() : "", quantity: l.quantity ?? "", unit: l.unit ?? c?.base_unit ?? "PCS", confirm: false, status: l.status };
      }),
    );
  }, [res]);

  if (isPending) return <Spinner />;
  if (error || !s) return <ErrorState error={error} retry={refetch} />;

  const editable = !["converted", "discarded"].includes(s.status) && can("orders.create");
  const set = (k: string, patch: Partial<Draft>) => setLines((ls) => ls.map((l) => (l.key === k ? { ...l, ...patch } : l)));
  const included = lines.filter((l) => l.include);
  const needsConfirm = included.filter((l) => (l.status === "ambiguous" || l.status === "unmatched") && !l.confirm);
  const missing = included.filter((l) => !l.product_id || !(Number(l.quantity) > 0));
  const canConvert = editable && retailer && included.length > 0 && !needsConfirm.length && !missing.length;

  const convert = async () => {
    setBusy(true);
    try {
      const o = await api<{ id: string; order_number: string }>(`/order-extractions/${id}/convert`, {
        body: { retailer_id: retailer, requested_delivery_date: deliveryDate || null, lines: lines.map((l) => ({ index: l.index, product_id: l.product_id, quantity: l.quantity || "0", unit: l.unit, include: l.include, confirm: l.confirm })).filter((l) => l.include) },
      });
      toast.success(`Draft order ${o.order_number} created`, "It still needs validation and approval.");
      router.push(`/orders/${o.id}`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  const ext = s.extraction;
  return (
    <>
      <PageHeader
        back={{ href: "/inbox", label: "Order Inbox" }}
        title={<span className="flex items-center gap-2">Review inbox item <StatusChip status={s.status} /></span>}
        description={`${titleCase(s.channel)} · received ${dateTime(s.received_at)}${s.sender ? ` · ${s.sender}` : ""}${s.created_by ? ` · by ${s.created_by}` : ""}`}
        actions={editable && (
          <>
            <Button onClick={() => reextract.mutate(undefined)} loading={reextract.isPending}>Re-extract</Button>
            <Button variant="danger" onClick={() => setDiscard(true)}>Discard</Button>
          </>
        )}
      />
      {s.order && <Alert tone="success" title="Converted">This item became draft order <Link className="underline" href={`/orders/${s.order.id}`}>{s.order.order_number}</Link> ({titleCase(s.order.status)}).</Alert>}
      {s.possible_duplicate_of && <Alert tone="warning" title="Possible duplicate">The same content was received recently (<Link className="underline" href={`/inbox/${s.possible_duplicate_of}`}>view earlier item</Link>). Check before converting.</Alert>}

      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(280px,0.8fr)_1.6fr]">
        <div className="space-y-4">
          <Card title="Original source">
            {s.raw_text && <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 p-3 text-sm">{s.raw_text}</pre>}
            {s.document && (
              <div className="space-y-2">
                <p className="text-sm">{s.document.filename}</p>
                {s.document.content_type.startsWith("image/") ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={s.document.url} alt={`Uploaded order ${s.document.filename}`} className="max-h-96 rounded-md border border-border" />
                ) : (
                  <a className="text-sm text-info underline" href={s.document.url} target="_blank" rel="noreferrer">Open document (link expires in 5 minutes)</a>
                )}
                {s.document.text_content && <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-surface-2 p-3 text-xs">{s.document.text_content}</pre>}
              </div>
            )}
          </Card>
          {ext && (
            <Card title="Extraction">
              <KeyValue items={[
                ["Provider", ext.provider === "anthropic" ? `Claude · ${ext.model}` : `Local demo parser (${ext.model})`],
                ["Status", <StatusChip key="s" status={ext.status === "succeeded" ? "verified" : "failed"} label={ext.status} />],
                ["Overall confidence", res ? <ConfidenceChip key="c" value={res.overall_confidence} /> : "—"],
                ["Latency", ext.latency_ms !== null ? `${ext.latency_ms} ms` : "—"],
                ...(ext.input_tokens ? [["Tokens", `${ext.input_tokens} in / ${ext.output_tokens} out`] as [string, string]] : []),
              ]} />
              {ext.error && <div className="mt-3"><Alert tone="danger" title="Extraction failed">{ext.error} You can still add lines manually below.</Alert></div>}
              {(res?.warnings.length ?? 0) > 0 && <ul className="mt-3 list-disc space-y-0.5 pl-5 text-xs text-accent-strong">{res!.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              <button className="mt-3 text-xs text-info hover:underline" onClick={() => setShowRaw((v) => !v)}>{showRaw ? "Hide" : "Show"} raw provider output</button>
              {showRaw && <div className="mt-2"><Json value={ext.raw_output} /></div>}
            </Card>
          )}
        </div>

        <Card title="Review & convert to draft order">
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-[1fr_180px]">
              <Field label="Retailer" required hint={res ? (res.retailer.selected_retailer_id ? `Matched from “${res.retailer.extracted.name ?? res.retailer.extracted.phone}”` : res.retailer.candidates.length ? "Ambiguous - choose a candidate or search" : "Not found - search the directory") : undefined}>
                {(fid) => <RetailerPicker id={fid} value={retailer} initialLabel={retailerLabel} onChange={(v, o) => { setRetailer(v); setRetailerLabel(o?.label); }} />}
              </Field>
              <Field label="Requested delivery">{(fid) => <Input id={fid} type="date" value={deliveryDate} onChange={(e) => setDeliveryDate(e.target.value)} disabled={!editable} />}</Field>
            </div>
            {res && !res.retailer.selected_retailer_id && res.retailer.candidates.length > 0 && editable && (
              <div className="flex flex-wrap gap-2">
                {res.retailer.candidates.map((c) => (
                  <Button key={c.retailer_id} size="sm" onClick={() => { setRetailer(c.retailer_id); setRetailerLabel(c.name); }} title={c.reasons.join(", ")}>
                    {c.name} <span className="text-muted">{c.code}</span> <ConfidenceChip value={c.confidence} />
                  </Button>
                ))}
              </div>
            )}

            <div className="space-y-2">
              {lines.map((l) => {
                const el = l.index !== null ? res?.lines.find((x) => x.index === l.index) : undefined;
                const risky = l.status === "ambiguous" || l.status === "unmatched";
                return (
                  <div key={l.key} className={cx("rounded-md border p-3", !l.include ? "border-border opacity-60" : risky ? "border-accent/50 bg-accent-bg/30" : "border-border")}>
                    {el && (
                      <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-medium">“{el.source_description}”</span>
                        <StatusChip status={el.status} />
                        <span className="text-xs text-muted">product</span> <ConfidenceChip value={el.confidence.product} />
                        <span className="text-xs text-muted">qty</span> <ConfidenceChip value={el.confidence.quantity} />
                      </div>
                    )}
                    {!el && <p className="mb-2 text-xs font-medium text-muted">Manually added line</p>}
                    <div className="grid gap-2 md:grid-cols-[1fr_100px_110px_auto] md:items-center">
                      <ProductPicker value={l.product_id} initialLabel={l.product_label} onChange={(v, o) => set(l.key, { product_id: v, product_label: o?.label, confirm: l.confirm })} />
                      <Input aria-label="Quantity" type="number" min="0" step="any" value={l.quantity} onChange={(e) => set(l.key, { quantity: e.target.value })} disabled={!editable} />
                      <Select aria-label="Unit" value={l.unit} onChange={(e) => set(l.key, { unit: e.target.value })} disabled={!editable}>
                        {["PCS", "CASE", "DOZEN", "BOX"].map((u) => <option key={u} value={u}>{u}</option>)}
                        {!["PCS", "CASE", "DOZEN", "BOX"].includes(l.unit) && <option value={l.unit}>{l.unit}</option>}
                      </Select>
                      <label className="flex items-center gap-1.5 text-xs"><input type="checkbox" checked={l.include} onChange={(e) => set(l.key, { include: e.target.checked })} disabled={!editable} /> Include</label>
                    </div>
                    {el && el.sku_candidates.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {el.sku_candidates.map((c) => (
                          <button key={c.product_id} type="button" disabled={!editable} onClick={() => set(l.key, { product_id: c.product_id, product_label: `${c.name} ${c.variant ?? ""}`.trim() })} title={c.reasons.join(" · ")}
                            className={cx("rounded-md border px-2 py-1 text-xs", l.product_id === c.product_id ? "border-brand bg-brand text-white" : "border-border-strong bg-surface hover:bg-surface-2")}>
                            {c.sku} · {c.name} {c.variant} · {(c.confidence * 100).toFixed(0)}%
                          </button>
                        ))}
                      </div>
                    )}
                    {el?.warnings.length ? <ul className="mt-2 list-disc pl-5 text-xs text-accent-strong">{el.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
                    {risky && l.include && editable && (
                      <label className="mt-2 flex items-center gap-2 text-sm font-medium text-accent-strong">
                        <input type="checkbox" checked={l.confirm} onChange={(e) => set(l.key, { confirm: e.target.checked })} />
                        I have checked this line and confirm the selected product
                      </label>
                    )}
                  </div>
                );
              })}
              {editable && <Button size="sm" onClick={() => setLines((ls) => [...ls, { key: `m${++seq}`, index: null, include: true, product_id: null, quantity: "1", unit: "PCS", confirm: false, status: null }])}><Plus className="h-3.5 w-3.5" /> Add line manually</Button>}
              {lines.length === 0 && <p className="text-sm text-muted">No lines extracted. Add lines manually to create the order.</p>}
            </div>

            {editable && (
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
                <div className="text-xs text-muted">
                  {needsConfirm.length > 0 && <Badge tone="warning">{needsConfirm.length} line(s) need confirmation</Badge>} {missing.length > 0 && <Badge tone="danger">{missing.length} line(s) missing product or quantity</Badge>} {!retailer && <Badge tone="danger">retailer required</Badge>}
                </div>
                <Button variant="accent" disabled={!canConvert} loading={busy} onClick={convert}>Create draft order</Button>
              </div>
            )}
          </div>
        </Card>
      </div>

      <Dialog open={discard} onClose={() => setDiscard(false)} title="Discard inbox item" footer={<><Button onClick={() => setDiscard(false)}>Back</Button><Button variant="danger" disabled={reason.trim().length < 3} loading={doDiscard.isPending} onClick={() => doDiscard.mutate(undefined)}>Discard</Button></>}>
        <Field label="Reason" required>{(fid) => <Textarea id={fid} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Not an order / duplicate message" />}</Field>
      </Dialog>
    </>
  );
}
