"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { useToast } from "@/components/toast";
import { Alert, Button, Card, Field, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const DEMO_ROLES = [
  ["org_owner", "Owner"],
  ["operations_manager", "Operations manager"],
  ["accountant", "Accountant"],
  ["sales_rep", "Sales rep"],
  ["warehouse_operator", "Warehouse operator"],
] as const;

export default function LoginPage() {
  const { login, demoLogin, status, me } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [demoAvailable, setDemoAvailable] = useState(true);

  useEffect(() => {
    if (status === "authenticated") router.replace(me?.organization ? "/" : "/onboarding");
  }, [status, me, router]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy("login");
    try {
      await login(email, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign in failed.");
    } finally {
      setBusy(null);
    }
  };

  const demo = async (role: string) => {
    setBusy(role);
    try {
      await demoLogin(role);
    } catch (err) {
      if (err instanceof ApiError && (err.code === "DEMO_NOT_SEEDED" || err.code === "NOT_AVAILABLE")) setDemoAvailable(false);
      toast.error(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-4">
      <Card title="Sign in">
        <form onSubmit={submit} className="space-y-3">
          {error && <Alert tone="danger">{error}</Alert>}
          <Field label="Email" required>
            {(id) => <Input id={id} type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />}
          </Field>
          <Field label="Password" required>
            {(id) => <Input id={id} type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />}
          </Field>
          <Button type="submit" variant="primary" className="w-full" loading={busy === "login"}>
            Sign in
          </Button>
          <div className="flex justify-between text-xs">
            <Link href="/forgot-password" className="text-info hover:underline">
              Forgot password?
            </Link>
            <Link href="/register" className="text-info hover:underline">
              Create an account
            </Link>
          </div>
        </form>
      </Card>
      {demoAvailable && (
        <Card title="Demo access (development only)">
          <p className="mb-3 text-xs text-muted">
            Signs in to the seeded <strong>synthetic</strong> demo organisation. Run <code className="rounded bg-surface-2 px-1">python -m app.seed</code> first. Disabled in production.
          </p>
          <div className="grid grid-cols-2 gap-2">
            {DEMO_ROLES.map(([role, label]) => (
              <Button key={role} size="sm" onClick={() => demo(role)} loading={busy === role}>
                {label}
              </Button>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
