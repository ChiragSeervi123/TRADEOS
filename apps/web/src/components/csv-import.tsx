"use client";

import { Download, Upload } from "lucide-react";
import { useState } from "react";
import { api, download } from "@/lib/api";
import { useInvalidate } from "@/lib/hooks";
import { useToast } from "./toast";
import { Alert, Badge, Button, DataTable, Dialog } from "./ui";

type Preview = { id: string; total_rows: number; valid_rows: number; errors: { row: number; field: string; message: string }[]; column_mapping: Record<string, string>; headers: string[]; preview: Record<string, unknown>[] };

const LABELS: Record<string, string> = { products: "products", retailers: "retailers", suppliers: "suppliers", opening_stock: "opening stock", prices: "prices" };

export function CsvImportButton({ entity, invalidate }: { entity: keyof typeof LABELS; invalidate: string[] }) {
  const [open, setOpen] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [skip, setSkip] = useState(false);
  const toast = useToast();
  const inv = useInvalidate();

  const reset = () => { setPreview(null); setSkip(false); };
  const upload = async (f: File) => {
    setBusy(true);
    const form = new FormData();
    form.append("file", f);
    try {
      setPreview(await api<Preview>(`/imports/${entity}/preview`, { form }));
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const commit = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      const r = await api<{ imported_rows: number; skipped_rows: number[] }>(`/imports/${preview.id}/commit`, { body: { skip_invalid: skip } });
      toast.success(`Imported ${r.imported_rows} row(s)`, r.skipped_rows.length ? `${r.skipped_rows.length} invalid row(s) skipped: ${r.skipped_rows.slice(0, 10).join(", ")}` : undefined);
      inv(...invalidate);
      setOpen(false);
      reset();
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy(false);
    }
  };
  const badRows = new Set(preview?.errors.map((e) => e.row));
  const cols = preview ? Object.keys(preview.preview[0] ?? {}).filter((k) => !k.startsWith("_")) : [];

  return (
    <>
      <Button onClick={() => setOpen(true)}><Upload className="h-4 w-4" /> Import CSV</Button>
      <Dialog wide open={open} onClose={() => { setOpen(false); reset(); }} title={`Import ${LABELS[entity]} from CSV`}
        footer={preview ? (
          <>
            <Button onClick={reset}>Choose another file</Button>
            <Button variant="primary" loading={busy} disabled={preview.valid_rows === 0 || (preview.errors.length > 0 && !skip)} onClick={commit}>
              Import {skip ? preview.valid_rows : preview.total_rows} row(s)
            </Button>
          </>
        ) : undefined}>
        {!preview ? (
          <div className="space-y-3 text-sm">
            <p>Upload a UTF-8 CSV. Columns are matched by name (common aliases accepted). Nothing is imported until you confirm, and imports are all-or-nothing unless you explicitly skip invalid rows.</p>
            <Button variant="ghost" onClick={() => download(`/imports/templates/${entity}`)}><Download className="h-4 w-4" /> Download template</Button>
            <input type="file" accept=".csv,text/csv" aria-label="CSV file" disabled={busy} onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} className="block text-sm" />
            {busy && <p className="text-muted">Checking file…</p>}
          </div>
        ) : (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2">
              <Badge>{preview.total_rows} rows</Badge>
              <Badge tone="success">{preview.valid_rows} valid</Badge>
              {preview.errors.length > 0 && <Badge tone="danger">{badRows.size} with errors</Badge>}
            </div>
            <p className="text-xs text-muted">Column mapping: {Object.entries(preview.column_mapping).map(([k, v]) => `${k} ← “${v}”`).join(", ")}</p>
            {preview.errors.length > 0 && (
              <>
                <Alert tone="danger" title="Row-level errors">
                  <ul className="max-h-40 list-disc overflow-auto pl-5">
                    {preview.errors.slice(0, 100).map((e, i) => <li key={i}>Row {e.row}, {e.field}: {e.message}</li>)}
                  </ul>
                </Alert>
                <label className="flex items-center gap-2"><input type="checkbox" checked={skip} onChange={(e) => setSkip(e.target.checked)} /> Import only the {preview.valid_rows} valid row(s) and skip the rest (reported after import)</label>
              </>
            )}
            <DataTable<Record<string, unknown>>
              dense
              rows={preview.preview.slice(0, 20)}
              rowKey={(r) => String(r._row)}
              columns={[{ key: "_row", header: "Row", cell: (r) => <span className={badRows.has(Number(r._row)) ? "font-semibold text-danger" : ""}>{String(r._row)}</span> }, ...cols.slice(0, 7).map((c) => ({ key: c, header: c, cell: (r: Record<string, unknown>) => String(r[c] ?? "") }))]}
            />
          </div>
        )}
      </Dialog>
    </>
  );
}
