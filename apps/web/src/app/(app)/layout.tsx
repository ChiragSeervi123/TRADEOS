"use client";

import { useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { AppShell } from "@/components/shell";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";

export default function AppLayout({ children }: { children: ReactNode }) {
  const { status, me } = useAuth();
  const router = useRouter();
  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
    else if (status === "authenticated" && !me?.organization) router.replace("/onboarding");
  }, [status, me, router]);
  if (status !== "authenticated" || !me?.organization) return <Spinner label="Loading workspace…" />;
  return <AppShell>{children}</AppShell>;
}
