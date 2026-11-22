"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AuthCard } from "@/components/AuthCard";
import { Button, Input, Label } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Me } from "@/lib/types";

export default function SetupPage() {
  const [form, setForm] = useState({ display_name: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { signIn } = useAuth();
  const router = useRouter();

  useEffect(() => {
    // The single-person Windows app has no sign-in: go straight in.
    api<{ local?: boolean }>("/auth/status").then((status) => { if (status.local) router.replace("/"); }).catch(() => undefined);
  }, [router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const me = await api<Me>("/auth/setup", { body: form });
      signIn(me);
      router.replace("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Setup failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard title="Set up CLIPFORGE" subtitle="Create the owner account. You can invite teammates later.">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <Label>Your name</Label>
          <Input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        </div>
        <div>
          <Label>Email</Label>
          <Input type="email" required autoComplete="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </div>
        <div>
          <Label hint="at least 10 characters">Password</Label>
          <Input type="password" required autoComplete="new-password" minLength={10} value={form.password}
            onChange={(e) => setForm({ ...form, password: e.target.value })} />
        </div>
        {error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{error}</p>}
        <Button type="submit" className="w-full" loading={busy}>
          Create owner account
        </Button>
      </form>
    </AuthCard>
  );
}
