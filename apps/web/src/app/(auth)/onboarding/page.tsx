"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Alert, Button, Card, Field, Input, Select, Spinner } from "@/components/ui";
import { api, ApiError, type Tokens } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function OnboardingPage() {
  const { status, me, acceptTokens } = useAuth();
  const router = useRouter();
  const [form, setForm] = useState({ name: "", org_type: "distributor", gstin: "", address: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated") return <Spinner />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ tokens: Tokens }>("/organizations", { body: { name: form.name, org_type: form.org_type, gstin: form.gstin || null, address: form.address || null } });
      await acceptTokens(r.tokens);
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the organisation.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="Set up your organisation">
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm text-muted">
          Signed in as {me?.user.email}. {me?.memberships.length ? "You can also create an additional organisation." : "Create your organisation to get started."}
        </p>
        {error && <Alert tone="danger">{error}</Alert>}
        <Field label="Organisation name" required>
          {(id) => <Input id={id} required minLength={2} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />}
        </Field>
        <Field label="Organisation type" hint="Manufacturers only see data that distributors explicitly share with them.">
          {(id) => (
            <Select id={id} value={form.org_type} onChange={(e) => setForm({ ...form, org_type: e.target.value })}>
              <option value="distributor">Distributor</option>
              <option value="manufacturer">Manufacturer</option>
            </Select>
          )}
        </Field>
        <Field label="GSTIN (optional)" hint="Stored as reference data only; TRADEOS does not file returns.">
          {(id) => <Input id={id} value={form.gstin} maxLength={20} onChange={(e) => setForm({ ...form, gstin: e.target.value })} />}
        </Field>
        <Field label="Address (optional)">
          {(id) => <Input id={id} value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />}
        </Field>
        <Button type="submit" variant="primary" className="w-full" loading={busy}>
          Create organisation
        </Button>
        {me?.memberships.length ? (
          <Button type="button" variant="ghost" className="w-full" onClick={() => router.replace("/")}>
            Back to my organisation
          </Button>
        ) : null}
      </form>
    </Card>
  );
}
