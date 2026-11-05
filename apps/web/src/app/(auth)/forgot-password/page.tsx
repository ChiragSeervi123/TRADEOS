"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Input } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

type Resp = { message: string; email_delivery: string; dev_reset_token?: string };

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [resp, setResp] = useState<Resp | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResp(await api<Resp>("/auth/forgot-password", { body: { email } }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Reset your password">
      {resp ? (
        <div className="space-y-3">
          <Alert tone="info">{resp.message}</Alert>
          {resp.email_delivery === "not_configured" && (
            <Alert tone="warning" title="Email delivery is not configured">
              In a deployment with email configured, the reset link is emailed. In this development environment the link is shown below instead.
            </Alert>
          )}
          {resp.dev_reset_token && (
            <Link className="block text-sm font-medium text-info underline" href={`/reset-password?token=${encodeURIComponent(resp.dev_reset_token)}`}>
              Open the reset link
            </Link>
          )}
          <Link href="/login" className="text-xs text-info hover:underline">
            Back to sign in
          </Link>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-3">
          {error && <Alert tone="danger">{error}</Alert>}
          <Field label="Email" required>
            {(id) => <Input id={id} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />}
          </Field>
          <Button type="submit" variant="primary" className="w-full" loading={busy}>
            Send reset link
          </Button>
          <Link href="/login" className="text-xs text-info hover:underline">
            Back to sign in
          </Link>
        </form>
      )}
    </Card>
  );
}
