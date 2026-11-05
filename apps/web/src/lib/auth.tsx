"use client";

import { useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, refreshSession, setAccessToken, type Tokens } from "./api";

export type Me = {
  user: { id: string; email: string; full_name: string; is_platform_admin: boolean };
  organization: { id: string; name: string; org_type: string; currency: string; timezone: string; is_demo: boolean } | null;
  role: string | null;
  permissions: string[];
  memberships: { organization_id: string; organization_name: string; org_type: string; role: string; is_demo: boolean }[];
};

type AuthState = {
  status: "loading" | "anonymous" | "authenticated";
  me: Me | null;
  can: (perm: string) => boolean;
  acceptTokens: (t: Tokens) => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  demoLogin: (role: string) => Promise<void>;
  logout: () => Promise<void>;
  switchOrg: (orgId: string) => Promise<void>;
  reload: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [me, setMe] = useState<Me | null>(null);

  const reload = useCallback(async () => {
    try {
      const m = await api<Me>("/auth/me");
      setMe(m);
      setStatus("authenticated");
    } catch {
      setMe(null);
      setStatus("anonymous");
    }
  }, []);

  useEffect(() => {
    (async () => {
      if (await refreshSession()) await reload();
      else setStatus("anonymous");
    })();
  }, [reload]);

  const acceptTokens = useCallback(
    async (t: Tokens) => {
      setAccessToken(t.access_token);
      qc.clear();
      await reload();
    },
    [qc, reload],
  );

  const value = useMemo<AuthState>(
    () => ({
      status,
      me,
      can: (p) => Boolean(me?.permissions.includes(p)),
      acceptTokens,
      login: async (email, password) => acceptTokens(await api<Tokens>("/auth/login", { body: { email, password } })),
      demoLogin: async (role) => acceptTokens(await api<Tokens>("/auth/demo-login", { body: { role } })),
      logout: async () => {
        try {
          await api("/auth/logout", { method: "POST" });
        } finally {
          setAccessToken(null);
          qc.clear();
          setMe(null);
          setStatus("anonymous");
        }
      },
      switchOrg: async (orgId) => acceptTokens(await api<Tokens>("/auth/switch-organization", { body: { organization_id: orgId } })),
      reload,
    }),
    [status, me, acceptTokens, reload, qc],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
