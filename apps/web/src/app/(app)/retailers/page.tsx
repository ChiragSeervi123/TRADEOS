"use client";

import { Download, Plus } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CsvImportButton } from "@/components/csv-import";
import { PartyForm } from "@/components/party-form";
import { Button, Card, DataTable, ErrorState, Input, PageHeader, Pagination, Select, Spinner, StatusChip, SyntheticTag, Tabs } from "@/components/ui";
import { download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { money } from "@/lib/format";
import { useApi, useDebounced, usePage } from "@/lib/hooks";

type R = { id: string; code: string; name: string; phone: string | null; territory: string | null; route: string | null; credit_limit: string | null; payment_terms_days: number; is_active: boolean; is_synthetic: boolean };
type S = { id: string; code: string; name: string; phone: string | null; email: string | null; is_active: boolean; is_synthetic: boolean };

function Inner() {
  const router = useRouter();
  const sp = useSearchParams();
  const { can } = useAuth();
  const [tab, setTab] = useState<"retailers" | "suppliers">(sp.get("tab") === "suppliers" ? "suppliers" : "retailers");
  const [q, setQ] = useState("");
  const [territory, setTerritory] = useState("");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const dq = useDebounced(q);
  const retailers = usePage<R>("/retailers", { q: dq, territory, page, page_size: 25, sort: "code" });
  const suppliers = usePage<S>("/suppliers", { q: dq, page, page_size: 25 });
  const facets = useApi<{ territories: string[] }>("/retailers/facets");
  const canWrite = tab === "retailers" ? can("retailers.write") : can("suppliers.write");

  return (
    <>
      <PageHeader title="Retailers & suppliers" description="Customer and supplier directory." actions={
        <>
          {can("reports.export") && <Button onClick={() => download(`/exports/${tab}`)}><Download className="h-4 w-4" /> Export</Button>}
          {canWrite && <CsvImportButton entity={tab} invalidate={[`/${tab}`]} />}
          {canWrite && <Button variant="primary" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New {tab === "retailers" ? "retailer" : "supplier"}</Button>}
        </>
      } />
      <Tabs value={tab} onChange={(t) => { setTab(t); setPage(1); }} tabs={[{ key: "retailers", label: "Retailers" }, ...(can("suppliers.read") ? [{ key: "suppliers" as const, label: "Suppliers" }] : [])]} />
      <Card padded={false}>
        <div className="flex flex-wrap gap-2 border-b border-border p-3">
          <Input aria-label="Search" placeholder={tab === "retailers" ? "Search name, code or phone" : "Search name or code"} value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="max-w-xs" />
          {tab === "retailers" && (
            <Select aria-label="Territory" value={territory} onChange={(e) => { setTerritory(e.target.value); setPage(1); }} className="max-w-[180px]">
              <option value="">All territories</option>
              {facets.data?.territories.map((t) => <option key={t}>{t}</option>)}
            </Select>
          )}
        </div>
        {tab === "retailers" ? (
          retailers.isPending ? <Spinner /> : retailers.error ? <ErrorState error={retailers.error} /> : (
            <>
              <DataTable<R> rows={retailers.data.items} rowKey={(r) => r.id} onRowClick={(r) => router.push(`/retailers/${r.id}`)} columns={[
                { key: "c", header: "Code", cell: (r) => <span className="font-medium">{r.code} <SyntheticTag show={r.is_synthetic} /></span> },
                { key: "n", header: "Name", cell: (r) => r.name },
                { key: "p", header: "Phone", cell: (r) => r.phone ?? "—" },
                { key: "t", header: "Territory / route", cell: (r) => [r.territory, r.route].filter(Boolean).join(" · ") || "—" },
                ...(can("finance.read") ? [{ key: "cl", header: "Credit limit", align: "right" as const, cell: (r: R) => (r.credit_limit ? money(r.credit_limit) : "No limit") }] : []),
                { key: "pt", header: "Terms", align: "right", cell: (r) => `${r.payment_terms_days} d` },
                { key: "s", header: "Status", cell: (r) => <StatusChip status={r.is_active ? "active" : "inactive"} /> },
              ]} />
              <Pagination page={page} pageSize={25} total={retailers.data.total} onPage={setPage} />
            </>
          )
        ) : suppliers.isPending ? <Spinner /> : suppliers.error ? <ErrorState error={suppliers.error} /> : (
          <>
            <DataTable<S> rows={suppliers.data.items} rowKey={(r) => r.id} columns={[
              { key: "c", header: "Code", cell: (r) => <span className="font-medium">{r.code} <SyntheticTag show={r.is_synthetic} /></span> },
              { key: "n", header: "Name", cell: (r) => r.name },
              { key: "p", header: "Phone", cell: (r) => r.phone ?? "—" },
              { key: "e", header: "Email", cell: (r) => r.email ?? "—" },
              { key: "s", header: "Status", cell: (r) => <StatusChip status={r.is_active ? "active" : "inactive"} /> },
            ]} />
            <Pagination page={page} pageSize={25} total={suppliers.data.total} onPage={setPage} />
          </>
        )}
      </Card>
      <PartyForm kind={tab === "retailers" ? "retailer" : "supplier"} open={creating} onClose={() => setCreating(false)} onSaved={(r) => tab === "retailers" && router.push(`/retailers/${r.id}`)} />
    </>
  );
}

export default function RetailersPage() {
  return <Suspense fallback={<Spinner />}><Inner /></Suspense>;
}
