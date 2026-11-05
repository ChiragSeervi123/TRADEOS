"use client";

import { CheckCircle2, AlertTriangle, X } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";

type Toast = { id: number; kind: "success" | "error" | "info"; title: string; detail?: string };
type ToastApi = { success: (t: string, d?: string) => void; error: (e: unknown, fallback?: string) => void; info: (t: string, d?: string) => void };

const Ctx = createContext<ToastApi | null>(null);
let seq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = ++seq;
    setItems((xs) => [...xs, { ...t, id }]);
    setTimeout(() => setItems((xs) => xs.filter((x) => x.id !== id)), t.kind === "error" ? 9000 : 4500);
  }, []);
  const api: ToastApi = {
    success: (title, detail) => push({ kind: "success", title, detail }),
    info: (title, detail) => push({ kind: "info", title, detail }),
    error: (e, fallback = "Something went wrong") => {
      if (e instanceof ApiError) {
        const fields = e.fieldErrors();
        const detail = Object.keys(fields).length ? Object.entries(fields).map(([k, v]) => `${k}: ${v}`).join("; ") : e.requestId ? `Ref ${e.requestId.slice(0, 8)}` : undefined;
        push({ kind: "error", title: e.message, detail });
      } else push({ kind: "error", title: fallback, detail: e instanceof Error ? e.message : undefined });
    },
  };
  return (
    <Ctx.Provider value={api}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[360px] max-w-[calc(100vw-2rem)] flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} role={t.kind === "error" ? "alert" : "status"} className={`pointer-events-auto flex gap-2 rounded-md border bg-surface p-3 shadow-lg ${t.kind === "error" ? "border-danger/40" : t.kind === "success" ? "border-success/40" : "border-border"}`}>
            {t.kind === "error" ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" /> : <CheckCircle2 className={`mt-0.5 h-4 w-4 shrink-0 ${t.kind === "success" ? "text-success" : "text-info"}`} />}
            <div className="min-w-0 flex-1">
              <p className="font-medium">{t.title}</p>
              {t.detail && <p className="mt-0.5 text-xs text-muted">{t.detail}</p>}
            </div>
            <button aria-label="Dismiss" className="text-muted hover:text-ink" onClick={() => setItems((xs) => xs.filter((x) => x.id !== t.id))}>
              <X className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast outside provider");
  return v;
}
