/**
 * API client. Access token lives in memory only; the refresh token is an httpOnly
 * cookie scoped to /api/v1/auth. On 401 we try one refresh, then give up.
 */

export type ApiErrorBody = {
  code: string;
  message: string;
  details?: unknown;
  request_id?: string | null;
};

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  requestId?: string | null;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.details = body.details;
    this.requestId = body.request_id;
  }

  /** Field-level messages from a VALIDATION_ERROR response. */
  fieldErrors(): Record<string, string> {
    if (this.code !== "VALIDATION_ERROR" || !Array.isArray(this.details)) return {};
    const out: Record<string, string> = {};
    for (const d of this.details as { field: string; message: string }[]) out[d.field] = d.message;
    return out;
  }
}

export type Tokens = { access_token: string; refresh_token: string; organization_id: string | null };

let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;
const listeners = new Set<(authed: boolean) => void>();

export function setAccessToken(token: string | null) {
  accessToken = token;
  listeners.forEach((l) => l(Boolean(token)));
}

export function onAuthChange(fn: (authed: boolean) => void) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export async function refreshSession(): Promise<boolean> {
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch("/api/v1/auth/refresh", { method: "POST", credentials: "same-origin" });
        if (!res.ok) {
          setAccessToken(null);
          return false;
        }
        const t = (await res.json()) as Tokens;
        setAccessToken(t.access_token);
        return true;
      } catch {
        setAccessToken(null);
        return false;
      } finally {
        setTimeout(() => (refreshing = null), 0);
      }
    })();
  }
  return refreshing;
}

type Options = {
  method?: string;
  body?: unknown;
  form?: FormData;
  query?: Record<string, string | number | boolean | null | undefined>;
  headers?: Record<string, string>;
  raw?: boolean;
};

function buildUrl(path: string, query?: Options["query"]) {
  const url = new URL(`/api/v1${path}`, window.location.origin);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

export async function api<T = unknown>(path: string, opts: Options = {}, retry = true): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers ?? {}) };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(buildUrl(path, opts.query), {
    method: opts.method ?? (body ? "POST" : "GET"),
    headers,
    body,
    credentials: "same-origin",
  });
  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refreshSession()) return api<T>(path, opts, false);
  }
  if (!res.ok) {
    let err: ApiErrorBody = { code: "HTTP_ERROR", message: `Request failed (${res.status})` };
    try {
      const j = await res.json();
      if (j?.error) err = j.error;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, err);
  }
  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Download a file from an authenticated endpoint (CSV/XML exports). */
export async function download(path: string, query?: Options["query"]) {
  const res = await api<Response>(path, { query, raw: true });
  const blob = await res.blob();
  const cd = res.headers.get("Content-Disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] ?? "download";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
}

export function newIdempotencyKey() {
  return typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
}

export type Page<T> = { items: T[]; total: number; page: number; page_size: number };
