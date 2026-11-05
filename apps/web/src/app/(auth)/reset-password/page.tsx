"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Input, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

function ResetForm() {
  const token = useSearchParams().get("token") ?? "";
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/reset-password", { body: { token, password } });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Reset failed.");
    } finally {
      setBusy(false);
    }
  };

  if (!token) return <Alert tone="danger">This reset link is missing its token.</Alert>;
  if (done)
    return (
      <div className="space-y-3">
        <Alert tone="success">Your password was changed. All existing sessions were signed out.</Alert>
        <Link href="/login" className="text-sm text-info underline">
          Sign in
        </Link>
      </div>
    );
  return (
    <form onSubmit={submit} className="space-y-3">
      {error && <Alert tone="danger">{error}</Alert>}
      <Field label="New password" required hint="At least 10 characters, with an uppercase letter and a digit.">
        {(id) => <Input id={id} type="password" autoComplete="new-password" required minLength={10} value={password} onChange={(e) => setPassword(e.target.value)} />}
      </Field>
      <Button type="submit" variant="primary" className="w-full" loading={busy}>
        Set new password
      </Button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return (
    <Card title="Choose a new password">
      <Suspense fallback={<Spinner />}>
        <ResetForm />
      </Suspense>
    </Card>
  );
}
