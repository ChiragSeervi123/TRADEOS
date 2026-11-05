"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Input, Spinner } from "@/components/ui";
import { api, ApiError, type Tokens } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function AcceptForm() {
  const token = useSearchParams().get("token") ?? "";
  const { acceptTokens } = useAuth();
  const router = useRouter();
  const [form, setForm] = useState({ full_name: "", password: "" });
  const [needsAccount, setNeedsAccount] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = needsAccount ? { token, ...form } : { token };
      await acceptTokens(await api<Tokens>("/auth/accept-invitation", { body }));
      router.replace("/");
    } catch (err) {
      if (err instanceof ApiError && err.code === "ACCOUNT_DETAILS_REQUIRED") setNeedsAccount(true);
      else setError(err instanceof ApiError ? err.message : "Could not accept the invitation.");
    } finally {
      setBusy(false);
    }
  };

  if (!token) return <Alert tone="danger">This invitation link is missing its token.</Alert>;
  return (
    <form onSubmit={submit} className="space-y-3">
      {error && <Alert tone="danger">{error}</Alert>}
      {needsAccount ? (
        <>
          <p className="text-sm text-muted">Create your account to join the organisation.</p>
          <Field label="Full name" required>
            {(id) => <Input id={id} required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />}
          </Field>
          <Field label="Password" required hint="At least 10 characters, with an uppercase letter and a digit.">
            {(id) => <Input id={id} type="password" required minLength={10} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />}
          </Field>
        </>
      ) : (
        <p className="text-sm text-muted">You have been invited to join an organisation on TRADEOS.</p>
      )}
      <Button type="submit" variant="primary" className="w-full" loading={busy}>
        Accept invitation
      </Button>
    </form>
  );
}

export default function AcceptInvitePage() {
  return (
    <Card title="Accept invitation">
      <Suspense fallback={<Spinner />}>
        <AcceptForm />
      </Suspense>
    </Card>
  );
}
