"use client";

import { AlertTriangle, ChevronLeft, ChevronRight, Inbox, Loader2, X } from "lucide-react";
import Link from "next/link";
import {
  forwardRef,
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { ApiError } from "@/lib/api";
import { titleCase } from "@/lib/format";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger" | "accent"; size?: "sm" | "md"; loading?: boolean };

export const Button = forwardRef<HTMLButtonElement, BtnProps>(function Button({ variant = "secondary", size = "md", loading, className, children, disabled, ...rest }, ref) {
  const styles = {
    primary: "bg-brand text-white hover:bg-[#162c49] border-brand",
    accent: "bg-accent text-white hover:bg-accent-strong border-accent",
    secondary: "bg-surface text-ink border-border-strong hover:bg-surface-2",
    ghost: "bg-transparent border-transparent text-ink-2 hover:bg-surface-2",
    danger: "bg-surface text-danger border-danger/40 hover:bg-danger-bg",
  }[variant];
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-md border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50", size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-sm", styles, className)}
      {...rest}
    >
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      {children}
    </button>
  );
});

export function LinkButton({ href, children, variant = "secondary", className }: { href: string; children: ReactNode; variant?: "primary" | "secondary" | "accent"; className?: string }) {
  const styles = { primary: "bg-brand text-white border-brand hover:bg-[#162c49]", accent: "bg-accent text-white border-accent hover:bg-accent-strong", secondary: "bg-surface text-ink border-border-strong hover:bg-surface-2" }[variant];
  return (
    <Link href={href} className={cx("inline-flex h-9 items-center gap-1.5 rounded-md border px-3.5 text-sm font-medium", styles, className)}>
      {children}
    </Link>
  );
}

const inputBase = "w-full rounded-md border border-border-strong bg-surface px-2.5 text-sm text-ink placeholder:text-muted/70 disabled:bg-surface-2 disabled:text-muted";

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cx(inputBase, "h-9", className)} {...rest} />;
});

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...rest }, ref) {
  return (
    <select ref={ref} className={cx(inputBase, "h-9 pr-7", className)} {...rest}>
      {children}
    </select>
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea({ className, ...rest }, ref) {
  return <textarea ref={ref} className={cx(inputBase, "py-2", className)} {...rest} />;
});

/** Label + control + hint/error, wired with ids for accessibility. */
export function Field({ label, hint, error, children, required, className }: { label: string; hint?: ReactNode; error?: string; children: (id: string) => ReactNode; required?: boolean; className?: string }) {
  const id = useId();
  return (
    <div className={cx("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs font-medium text-ink-2">
        {label}
        {required && <span className="text-danger"> *</span>}
      </label>
      {children(id)}
      {error ? <p className="text-xs text-danger">{error}</p> : hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function Card({ title, actions, children, className, padded = true }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; padded?: boolean }) {
  return (
    <section className={cx("rounded-lg border border-border bg-surface", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={padded ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function PageHeader({ title, description, actions, back }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; back?: { href: string; label: string } }) {
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {back && (
          <Link href={back.href} className="mb-1 inline-flex items-center gap-1 text-xs text-muted hover:text-ink">
            <ChevronLeft className="h-3 w-3" /> {back.label}
          </Link>
        )}
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const TONES: Record<string, string> = {
  neutral: "bg-surface-2 text-ink-2 border-border",
  success: "bg-success-bg text-success border-success/20",
  warning: "bg-accent-bg text-accent-strong border-accent/25",
  danger: "bg-danger-bg text-danger border-danger/20",
  info: "bg-info-bg text-info border-info/20",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: keyof typeof TONES | string; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium", TONES[tone] ?? TONES.neutral, className)}>
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, string> = {
  draft: "neutral", validated: "info", approved: "info", picking: "warning", picked: "warning", dispatched: "info", delivered: "success",
  partially_fulfilled: "warning", rejected: "danger", cancelled: "neutral", failed: "danger",
  received: "neutral", extracting: "info", needs_review: "warning", converted: "success", discarded: "neutral",
  open: "warning", in_progress: "info", resolved: "success", dismissed: "neutral", investigating: "info", awaiting_evidence: "warning", accepted_difference: "neutral", closed: "neutral",
  verified: "success", paid: "success", partially_paid: "warning", void: "neutral", recorded: "info", issued: "info", applied: "success",
  active: "success", inactive: "neutral", pending_approval: "warning", superseded: "neutral",
  pending: "warning", credited: "success", partially_credited: "warning", submitted: "info",
  configured: "success", available: "success", simulated: "warning", not_configured: "neutral", available_not_enabled: "warning", error: "danger",
  critical: "danger", high: "danger", medium: "warning", low: "neutral",
  in_stock: "success", out: "danger", low_stock: "warning", expired: "danger", expiring: "warning", ok: "success",
  matched: "success", ambiguous: "warning", unmatched: "danger", needs_input: "warning", suggested: "info", unmatched_record: "neutral",
};

export function StatusChip({ status, label }: { status: string | null | undefined; label?: string }) {
  if (!status) return null;
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{label ?? titleCase(status)}</Badge>;
}

export function ConfidenceChip({ value }: { value: string | number | null | undefined }) {
  if (value === null || value === undefined) return <Badge>n/a</Badge>;
  const n = Number(value);
  const tone = n >= 0.85 ? "success" : n >= 0.6 ? "warning" : "danger";
  return (
    <Badge tone={tone} title="Heuristic match confidence (0–1), not a probability">
      {(n * 100).toFixed(0)}%
    </Badge>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 p-6 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

export function EmptyState({ title, description, action }: { title: string; description?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      <Inbox className="h-7 w-7 text-muted/60" />
      <p className="font-medium text-ink">{title}</p>
      {description && <p className="max-w-md text-sm text-muted">{description}</p>}
      {action}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof ApiError ? error.message : "Could not load this data.";
  const forbidden = error instanceof ApiError && error.status === 403;
  return (
    <div role="alert" className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      <AlertTriangle className={cx("h-6 w-6", forbidden ? "text-accent" : "text-danger")} />
      <p className="font-medium">{forbidden ? "You don't have access to this" : msg}</p>
      {forbidden && <p className="text-sm text-muted">{msg}</p>}
      {error instanceof ApiError && error.requestId && <p className="text-xs text-muted">Reference: {error.requestId}</p>}
      {retry && !forbidden && (
        <Button size="sm" onClick={retry}>
          Try again
        </Button>
      )}
    </div>
  );
}

export function Alert({ tone = "warning", title, children }: { tone?: "warning" | "danger" | "info" | "success"; title?: ReactNode; children?: ReactNode }) {
  const s = { warning: "border-accent/30 bg-accent-bg text-accent-strong", danger: "border-danger/30 bg-danger-bg text-danger", info: "border-info/25 bg-info-bg text-info", success: "border-success/25 bg-success-bg text-success" }[tone];
  return (
    <div role={tone === "danger" ? "alert" : "note"} className={cx("rounded-md border px-3 py-2 text-sm", s)}>
      {title && <p className="font-medium">{title}</p>}
      {children && <div className={cx(title ? "mt-0.5" : null, "text-[13px] text-ink-2")}>{children}</div>}
    </div>
  );
}

/** Modal dialog on the native <dialog> element (focus trap + Esc handled by the browser). */
export function Dialog({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose} aria-labelledby="dlg-title" className={cx("m-auto w-[calc(100vw-2rem)] rounded-lg border border-border bg-surface p-0 text-ink shadow-xl", wide ? "max-w-4xl" : "max-w-lg")}>
      {open && (
        <div className="flex max-h-[85vh] flex-col">
          <header className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 id="dlg-title" className="text-base font-semibold">
              {title}
            </h2>
            <button aria-label="Close dialog" onClick={onClose} className="rounded p-1 text-muted hover:bg-surface-2 hover:text-ink">
              <X className="h-4 w-4" />
            </button>
          </header>
          <div className="overflow-y-auto px-4 py-4">{children}</div>
          {footer && <footer className="flex justify-end gap-2 border-t border-border px-4 py-3">{footer}</footer>}
        </div>
      )}
    </dialog>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { key: T; label: ReactNode }[]; value: T; onChange: (v: T) => void }) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 overflow-x-auto border-b border-border">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={value === t.key}
          onClick={() => onChange(t.key)}
          className={cx("-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium", value === t.key ? "border-accent text-ink" : "border-transparent text-muted hover:text-ink")}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export type Column<T> = { key: string; header: ReactNode; cell: (row: T) => ReactNode; align?: "left" | "right" | "center"; className?: string };

export function DataTable<T>({ columns, rows, rowKey, onRowClick, empty, dense }: { columns: Column<T>[]; rows: T[]; rowKey: (r: T) => string; onRowClick?: (r: T) => void; empty?: ReactNode; dense?: boolean }) {
  if (!rows.length) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-surface-2/60 text-left text-xs font-medium text-muted">
            {columns.map((c) => (
              <th key={c.key} scope="col" className={cx("px-3 py-2 font-medium", c.align === "right" && "text-right", c.align === "center" && "text-center", c.className)}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr
              key={rowKey(r)}
              onClick={onRowClick ? () => onRowClick(r) : undefined}
              onKeyDown={onRowClick ? (e) => e.key === "Enter" && onRowClick(r) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              className={cx("border-b border-border last:border-0", onRowClick && "cursor-pointer hover:bg-surface-2/70 focus:bg-surface-2")}
            >
              {columns.map((c) => (
                <td key={c.key} className={cx("px-3", dense ? "py-1.5" : "py-2", c.align === "right" && "text-right tabular", c.align === "center" && "text-center", c.className)}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (p: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return total ? <p className="px-3 py-2 text-xs text-muted">{total} record{total === 1 ? "" : "s"}</p> : null;
  return (
    <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs text-muted">
      <span>
        {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}
      </span>
      <div className="flex items-center gap-1">
        <Button size="sm" variant="ghost" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft className="h-3.5 w-3.5" />
        </Button>
        <span>
          Page {page} / {pages}
        </span>
        <Button size="sm" variant="ghost" aria-label="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

export function Stat({ label, value, sub, tone, href, title }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "danger" | "warning" | "success"; href?: string; title?: string }) {
  const body = (
    <div title={title} className="rounded-lg border border-border bg-surface p-3.5">
      <p className="text-xs font-medium text-muted">{label}</p>
      <p className={cx("mt-1 text-xl font-semibold tabular", tone === "danger" && "text-danger", tone === "warning" && "text-accent-strong", tone === "success" && "text-success")}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
    </div>
  );
  return href ? (
    <Link href={href} className="block rounded-lg hover:ring-1 hover:ring-border-strong">
      {body}
    </Link>
  ) : (
    body
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,auto)_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SyntheticTag({ show }: { show?: boolean }) {
  return show ? (
    <Badge tone="warning" title="Synthetic demo record">
      demo
    </Badge>
  ) : null;
}

export function Json({ value }: { value: unknown }) {
  return <pre className="max-h-80 overflow-auto rounded-md bg-surface-2 p-3 text-xs">{JSON.stringify(value, null, 2)}</pre>;
}
