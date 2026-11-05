"use client";

import { Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, ConfidenceChip, DataTable, ErrorState, Field, Input, PageHeader, Pagination, Select, Spinner, StatusChip, SyntheticTag, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateTime, titleCase } from "@/lib/format";
import { useApi, useInvalidate, usePage } from "@/lib/hooks";

type Row = { id: string; channel: string; status: string; sender: string | null; preview: string; received_at: string; provider: string | null; overall_confidence: string | null; summary: { matched: number; ambiguous: number; unmatched: number; total: number } | null; error: string | null; possible_duplicate_of: string | null; is_synthetic: boolean };
type ProviderStatus = { status: string; active_provider: string; message: string; model: string | null };

export default function InboxPage() {
  const router = useRouter();
  const toast = useToast();
  const { can } = useAuth();
  const invalidate = useInvalidate();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [text, setText] = useState("");
  const [sender, setSender] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const list = usePage<Row>("/order-extractions", { status, page, page_size: 20 });
  const provider = useApi<ProviderStatus>("/order-extractions/provider-status");

  const submitText = async () => {
    setBusy(true);
    try {
      const r = await api<{ id: string }>("/order-extractions/text", { body: { text, sender: sender || null } });
      setText("");
      invalidate("/order-extractions", "/exceptions");
      router.push(`/inbox/${r.id}`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File) => {
    setBusy(true);
    const form = new FormData();
    form.append("file", file);
    if (sender) form.append("sender", sender);
    try {
      const r = await api<{ id: string }>("/order-extractions/upload", { form });
      invalidate("/order-extractions", "/exceptions");
      router.push(`/inbox/${r.id}`);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const counts = (list.data?.status_counts ?? {}) as Record<string, number>;

  return (
    <>
      <PageHeader title="Order Inbox" description="Paste a WhatsApp/text order or upload a PDF/photo. Extraction proposes lines; a person reviews and converts them to a draft order." />
      {provider.data && (
        <Alert tone={provider.data.status === "configured" ? "success" : "info"} title={`Extraction provider: ${provider.data.active_provider === "anthropic" ? `Claude (${provider.data.model})` : "local demo parser"}`}>
          {provider.data.message}
        </Alert>
      )}
      <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_1.4fr]">
        {can("orders.create") && (
          <Card title="New inbox item">
            <div className="space-y-3">
              <Field label="Order text" hint="One item per line works best, e.g. “2 case surf excel 1kg”.">
                {(id) => <Textarea id={id} rows={8} value={text} onChange={(e) => setText(e.target.value)} placeholder={"Sharma General Store here\n2 case Brightwash detergent 1 kg\n12 pcs Lotus Glow soap 100 g\ndeliver by tomorrow"} />}
              </Field>
              <Field label="Sender (optional)">{(id) => <Input id={id} value={sender} onChange={(e) => setSender(e.target.value)} placeholder="Name or phone" />}</Field>
              <div className="flex flex-wrap gap-2">
                <Button variant="primary" onClick={submitText} disabled={!text.trim()} loading={busy}>Extract from text</Button>
                <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg,.webp,.txt" className="sr-only" id="inbox-file" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
                <label htmlFor="inbox-file" className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-md border border-border-strong bg-surface px-3.5 text-sm font-medium hover:bg-surface-2">
                  <Upload className="h-4 w-4" /> Upload PDF / image
                </label>
              </div>
              <p className="text-xs text-muted">WhatsApp Business API is not connected; copy/paste or upload messages here. Files up to 10 MB.</p>
            </div>
          </Card>
        )}
        <Card padded={false} title="Received" actions={
          <Select aria-label="Filter inbox by status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }} className="h-8 w-48">
            <option value="">All ({Object.values(counts).reduce((a, b) => a + b, 0)})</option>
            {["needs_review", "failed", "converted", "discarded"].map((s) => <option key={s} value={s}>{titleCase(s)} ({counts[s] ?? 0})</option>)}
          </Select>
        }>
          {list.isPending ? <Spinner /> : list.error ? <ErrorState error={list.error} retry={list.refetch} /> : (
            <>
              <DataTable<Row>
                rows={list.data.items}
                rowKey={(r) => r.id}
                onRowClick={(r) => router.push(`/inbox/${r.id}`)}
                columns={[
                  { key: "p", header: "Message", cell: (r) => <div className="max-w-xs"><p className="truncate">{r.preview || "(file)"}</p><p className="text-xs text-muted">{dateTime(r.received_at)} · {titleCase(r.channel)}{r.sender ? ` · ${r.sender}` : ""} <SyntheticTag show={r.is_synthetic} /></p></div> },
                  { key: "m", header: "Lines", cell: (r) => (r.summary ? <span className="text-xs">{r.summary.matched}/{r.summary.total} matched{r.summary.ambiguous ? `, ${r.summary.ambiguous} ambiguous` : ""}{r.summary.unmatched ? `, ${r.summary.unmatched} unmatched` : ""}</span> : r.error ? <span className="text-xs text-danger">extraction failed</span> : "—") },
                  { key: "c", header: "Confidence", cell: (r) => (r.overall_confidence ? <ConfidenceChip value={r.overall_confidence} /> : "—") },
                  { key: "s", header: "Status", cell: (r) => <span className="flex items-center gap-1"><StatusChip status={r.status} />{r.possible_duplicate_of && <StatusChip status="medium" label="duplicate?" />}</span> },
                ]}
                empty={<p className="p-6 text-center text-sm text-muted">No inbox items yet.</p>}
              />
              <Pagination page={page} pageSize={20} total={list.data.total} onPage={setPage} />
            </>
          )}
        </Card>
      </div>
    </>
  );
}
