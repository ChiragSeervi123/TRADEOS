"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { Page } from "@/lib/api";
import { useApi, useDebounced } from "@/lib/hooks";
import { cx, Input } from "./ui";

export type Option = { id: string; label: string; sub?: string };

/** Accessible combobox that searches a paginated endpoint. */
export function SearchPicker({
  endpoint,
  value,
  onChange,
  toOption,
  placeholder,
  initialLabel,
  extraQuery,
  id: givenId,
  disabled,
}: {
  endpoint: string;
  value: string | null;
  onChange: (id: string | null, option?: Option) => void;
  toOption: (row: Record<string, unknown>) => Option;
  placeholder?: string;
  initialLabel?: string;
  extraQuery?: Record<string, string | boolean>;
  id?: string;
  disabled?: boolean;
}) {
  const autoId = useId();
  const id = givenId ?? autoId;
  const [text, setText] = useState(initialLabel ?? "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const q = useDebounced(text, 250);
  const box = useRef<HTMLDivElement>(null);
  const { data, isFetching } = useApi<Page<Record<string, unknown>>>(open ? endpoint : null, { q, page_size: 12, ...(extraQuery ?? {}) });
  const options = (data?.items ?? []).map(toOption);

  useEffect(() => {
    if (initialLabel !== undefined) setText(initialLabel);
  }, [initialLabel]);
  useEffect(() => {
    const h = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  const choose = (o: Option) => {
    onChange(o.id, o);
    setText(o.label);
    setOpen(false);
  };

  return (
    <div ref={box} className="relative">
      <Input
        id={id}
        role="combobox"
        aria-expanded={open}
        aria-controls={`${id}-list`}
        aria-autocomplete="list"
        placeholder={placeholder ?? "Search…"}
        value={text}
        disabled={disabled}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
          setActive(0);
          if (value) onChange(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, options.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter" && open && options[active]) {
            e.preventDefault();
            choose(options[active]);
          } else if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && (
        <ul id={`${id}-list`} role="listbox" className="absolute z-30 mt-1 max-h-64 w-full overflow-auto rounded-md border border-border bg-surface py-1 shadow-lg">
          {isFetching && !options.length && <li className="px-3 py-2 text-xs text-muted">Searching…</li>}
          {!isFetching && !options.length && <li className="px-3 py-2 text-xs text-muted">No matches</li>}
          {options.map((o, i) => (
            <li
              key={o.id}
              role="option"
              aria-selected={o.id === value}
              onMouseDown={(e) => {
                e.preventDefault();
                choose(o);
              }}
              onMouseEnter={() => setActive(i)}
              className={cx("cursor-pointer px-3 py-1.5 text-sm", i === active && "bg-surface-2", o.id === value && "font-medium")}
            >
              {o.label}
              {o.sub && <span className="ml-2 text-xs text-muted">{o.sub}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export const retailerOption = (r: Record<string, unknown>): Option => ({ id: String(r.id), label: String(r.name), sub: [r.code, r.territory].filter(Boolean).join(" · ") });
export const productOption = (p: Record<string, unknown>): Option => ({ id: String(p.id), label: `${p.name}${p.variant ? ` ${p.variant}` : ""}`, sub: String(p.sku) });
export const supplierOption = (s: Record<string, unknown>): Option => ({ id: String(s.id), label: String(s.name), sub: String(s.code) });

export function RetailerPicker(props: { value: string | null; onChange: (id: string | null, o?: Option) => void; initialLabel?: string; id?: string }) {
  return <SearchPicker endpoint="/retailers" toOption={retailerOption} placeholder="Search retailer by name, code or phone" extraQuery={{ is_active: true }} {...props} />;
}

export function ProductPicker(props: { value: string | null; onChange: (id: string | null, o?: Option) => void; initialLabel?: string; id?: string }) {
  return <SearchPicker endpoint="/products" toOption={productOption} placeholder="Search SKU, name, brand or barcode" extraQuery={{ is_active: true }} {...props} />;
}

export function SupplierPicker(props: { value: string | null; onChange: (id: string | null, o?: Option) => void; initialLabel?: string; id?: string }) {
  return <SearchPicker endpoint="/suppliers" toOption={supplierOption} placeholder="Search supplier" {...props} />;
}
