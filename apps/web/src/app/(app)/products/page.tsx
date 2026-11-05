"use client";

import { Download, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { CsvImportButton } from "@/components/csv-import";
import { ProductForm } from "@/components/product-form";
import { Button, Card, DataTable, ErrorState, Input, PageHeader, Pagination, Select, Spinner, StatusChip, SyntheticTag, Tabs } from "@/components/ui";
import { download } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { money } from "@/lib/format";
import { useApi, useDebounced, usePage } from "@/lib/hooks";
import { PricingPanel } from "./pricing";

type Row = { id: string; sku: string; name: string; brand: string | null; category: string | null; variant: string | null; base_unit: string; tax_rate: string; cost_price: string | null; mrp: string | null; is_active: boolean; is_synthetic: boolean };

export default function ProductsPage() {
  const router = useRouter();
  const { can } = useAuth();
  const [tab, setTab] = useState<"catalogue" | "pricing">("catalogue");
  const [q, setQ] = useState("");
  const [brand, setBrand] = useState("");
  const [category, setCategory] = useState("");
  const [active, setActive] = useState("true");
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const dq = useDebounced(q);
  const list = usePage<Row>("/products", { q: dq, brand, category, is_active: active === "" ? undefined : active, page, page_size: 25, sort: "sku" });
  const facets = useApi<{ brands: string[]; categories: string[] }>("/products/facets");

  return (
    <>
      <PageHeader title="Products" description="SKU catalogue, units and prices." actions={
        <>
          {can("reports.export") && <Button onClick={() => download("/exports/products")}><Download className="h-4 w-4" /> Export</Button>}
          {can("products.write") && <CsvImportButton entity="products" invalidate={["/products"]} />}
          {can("products.write") && <Button variant="primary" onClick={() => setCreating(true)}><Plus className="h-4 w-4" /> New product</Button>}
        </>
      } />
      {can("pricing.read") && <Tabs value={tab} onChange={setTab} tabs={[{ key: "catalogue", label: "Catalogue" }, { key: "pricing", label: "Price lists" }]} />}
      {tab === "pricing" ? <PricingPanel /> : (
        <Card padded={false}>
          <div className="flex flex-wrap gap-2 border-b border-border p-3">
            <Input aria-label="Search products" placeholder="Search SKU, name, brand, barcode" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} className="max-w-xs" />
            <Select aria-label="Brand" value={brand} onChange={(e) => { setBrand(e.target.value); setPage(1); }} className="max-w-[180px]">
              <option value="">All brands</option>
              {facets.data?.brands.map((b) => <option key={b}>{b}</option>)}
            </Select>
            <Select aria-label="Category" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }} className="max-w-[180px]">
              <option value="">All categories</option>
              {facets.data?.categories.map((c) => <option key={c}>{c}</option>)}
            </Select>
            <Select aria-label="Status" value={active} onChange={(e) => { setActive(e.target.value); setPage(1); }} className="max-w-[140px]">
              <option value="true">Active</option>
              <option value="false">Inactive</option>
              <option value="">All</option>
            </Select>
          </div>
          {list.isPending ? <Spinner /> : list.error ? <ErrorState error={list.error} retry={list.refetch} /> : (
            <>
              <DataTable<Row>
                rows={list.data.items}
                rowKey={(r) => r.id}
                onRowClick={(r) => router.push(`/products/${r.id}`)}
                columns={[
                  { key: "sku", header: "SKU", cell: (r) => <span className="font-medium">{r.sku} <SyntheticTag show={r.is_synthetic} /></span> },
                  { key: "n", header: "Product", cell: (r) => <span>{r.name} <span className="text-muted">{r.variant}</span></span> },
                  { key: "b", header: "Brand", cell: (r) => r.brand ?? "—" },
                  { key: "c", header: "Category", cell: (r) => r.category ?? "—" },
                  { key: "u", header: "Base unit", cell: (r) => r.base_unit },
                  { key: "t", header: "Tax %", align: "right", cell: (r) => Number(r.tax_rate) },
                  ...(can("pricing.read") ? [{ key: "cp", header: "Agreed cost", align: "right" as const, cell: (r: Row) => money(r.cost_price) }] : []),
                  { key: "s", header: "Status", cell: (r) => <StatusChip status={r.is_active ? "active" : "inactive"} /> },
                ]}
                empty={<p className="p-6 text-center text-sm text-muted">No products match. Add one or import a CSV.</p>}
              />
              <Pagination page={page} pageSize={25} total={list.data.total} onPage={setPage} />
            </>
          )}
        </Card>
      )}
      <ProductForm open={creating} onClose={() => setCreating(false)} onSaved={(p) => router.push(`/products/${p.id}`)} />
    </>
  );
}
