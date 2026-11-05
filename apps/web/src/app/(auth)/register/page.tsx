"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Input } from "@/components/ui";
import { api, ApiError, type Tokens } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function RegisterPage() {
  const { acceptTokens } = useAuth();
  const router = useRouter();
  const [form, setForm] = useState({ full_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setFieldErrors({});
    try {
      await acceptTokens(await api<Tokens>("/auth/register", { body: form }));
      router.replace("/onboarding");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        setFieldErrors(err.fieldErrors());
      } else setError("Registration failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Create your account">
      <form onSubmit={submit} className="space-y-3">
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Full name" required error={fieldErrors.full_name}>
          {(id) => <Input id={id} required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} />}
        </Field>
        <Field label="Work email" required error={fieldErrors.email}>
          {(id) => <Input id={id} type="email" autoComplete="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />}
        </Field>
        <Field label="Password" required hint="At least 10 characters, with an uppercase letter and a digit." error={fieldErrors.password}>
          {(id) => <Input id={id} type="password" autoComplete="new-password" required minLength={10} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />}
        </Field>
        <Button type="submit" variant="primary" className="w-full" loading={busy}>
          Create account
        </Button>
        <p className="text-center text-xs">
          Already have an account?{" "}
          <Link href="/login" className="text-info hover:underline">
            Sign in
          </Link>
        </p>
      </form>
    </Card>
  );
}
