"use client";

import { useMutation, useQuery, useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Page } from "./api";
import { useToast } from "@/components/toast";

type Q = Record<string, string | number | boolean | null | undefined>;

export function useApi<T>(path: string | null, query?: Q, opts: { enabled?: boolean; refetchInterval?: number } = {}) {
  return useQuery<T>({
    queryKey: [path, query ?? {}],
    queryFn: () => api<T>(path as string, { query }),
    enabled: Boolean(path) && opts.enabled !== false,
    refetchInterval: opts.refetchInterval,
  });
}

export function usePage<T>(path: string, query: Q) {
  return useApi<Page<T> & Record<string, unknown>>(path, query);
}

/** Mutation that toasts errors, optionally toasts success, and invalidates queries whose key starts with any prefix. */
export function useAction<TVars, TOut = unknown>(fn: (v: TVars) => Promise<TOut>, opts: { success?: string | ((out: TOut) => string); invalidate?: string[]; onSuccess?: (out: TOut, vars: TVars) => void } = {}) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<TOut, unknown, TVars>({
    mutationFn: fn,
    onSuccess: (out, vars) => {
      if (opts.success) toast.success(typeof opts.success === "function" ? opts.success(out) : opts.success);
      if (opts.invalidate)
        qc.invalidateQueries({ predicate: (q) => opts.invalidate!.some((p) => typeof q.queryKey[0] === "string" && (q.queryKey[0] as string).startsWith(p)) });
      opts.onSuccess?.(out, vars);
    },
    onError: (e) => toast.error(e),
  });
}

export function useInvalidate() {
  const qc = useQueryClient();
  return (...prefixes: string[]) => qc.invalidateQueries({ predicate: (q) => prefixes.some((p) => typeof q.queryKey[0] === "string" && (q.queryKey[0] as string).startsWith(p)) });
}

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export type { QueryKey };
