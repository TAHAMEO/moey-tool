"use client";

import { UserPlus } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Input, Label, Modal, Select } from "@/components/ui";
import { api, timeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import { Row, SettingsSection } from "./shared";

type User = { id: string; email: string; role: string; active: boolean; last_login_at: string | null };
type AuditEntry = { id: string; action: string; target_type: string | null; target_id: string | null; ip: string | null; created_at: string; details: Record<string, unknown> | null };

const ROLE_HELP: Record<string, string> = {
  viewer: "Can see everything, change nothing",
  editor: "Can process videos, edit clips and publish",
  admin: "Also manages connections, settings and spending",
};

export function SecuritySection() {
  const toast = useToast();
  const { me, can } = useAuth();
  const [pw, setPw] = useState({ current_password: "", new_password: "" });
  const [saving, setSaving] = useState(false);

  async function changePassword() {
    setSaving(true);
    try {
      await api("/auth/password", { method: "POST", body: pw });
      setPw({ current_password: "", new_password: "" });
      toast("success", "Password changed. Other sessions were signed out.");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not change password");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection id="security" title="Account & security" description={me ? `Signed in as ${me.email} (${me.role}).` : undefined}>
      <Row className="space-y-3">
        <p className="text-[13px] font-semibold">Change password</p>
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
          <div><Label>Current password</Label><Input type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></div>
          <div><Label hint="10+ characters">New password</Label><Input type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></div>
          <Button loading={saving} disabled={!pw.current_password || pw.new_password.length < 10} onClick={changePassword}>Change</Button>
        </div>
      </Row>
      {can("owner") && <Users />}
      {can("admin") && <AuditLog />}
    </SettingsSection>
  );
}

function Users() {
  const toast = useToast();
  const { data, reload } = useResource<User[]>("/auth/users");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ email: "", password: "", role: "editor" });

  async function add() {
    try {
      await api("/auth/users", { method: "POST", body: form });
      toast("success", `${form.email} can now sign in.`);
      setOpen(false);
      setForm({ email: "", password: "", role: "editor" });
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not add user");
    }
  }

  return (
    <Row className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[13px] font-semibold">People with access</p>
        <Button size="sm" variant="secondary" icon={<UserPlus className="size-3.5" />} onClick={() => setOpen(true)}>Add person</Button>
      </div>
      <ul className="divide-y divide-border rounded-xl border border-border">
        {(data ?? []).map((u) => (
          <li key={u.id} className="flex items-center gap-3 px-3 py-2.5 text-[13px]">
            <span className="min-w-0 flex-1 truncate">{u.email}</span>
            <Badge tone={u.role === "owner" ? "primary" : "neutral"}>{u.role}</Badge>
            <span className="w-28 text-right text-[12px] text-faint">{u.last_login_at ? `active ${timeAgo(u.last_login_at)}` : "never signed in"}</span>
          </li>
        ))}
      </ul>
      <Modal open={open} onClose={() => setOpen(false)} title="Add a person"
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button disabled={!form.email || form.password.length < 10} onClick={add}>Add</Button></>}>
        <div className="space-y-4">
          <div><Label>Email</Label><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
          <div><Label hint="share it with them privately; 10+ characters">Temporary password</Label><Input type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
          <div>
            <Label>Role</Label>
            <Select value={form.role} onChange={(role) => setForm({ ...form, role })} options={Object.entries(ROLE_HELP).map(([value, help]) => ({ value, label: `${value[0].toUpperCase()}${value.slice(1)} — ${help}` }))} />
          </div>
        </div>
      </Modal>
    </Row>
  );
}

function AuditLog() {
  const [open, setOpen] = useState(false);
  const { data } = useResource<AuditEntry[]>(open ? "/audit?limit=100" : null);
  return (
    <Row>
      <button onClick={() => setOpen(!open)} className="text-[13px] font-semibold hover:text-primary">{open ? "Hide" : "Show"} activity log</button>
      {open && (
        <ul className="mt-3 max-h-80 space-y-1 overflow-y-auto font-mono text-[11px] text-muted">
          {(data ?? []).map((a) => (
            <li key={a.id}>
              <span className="text-faint">{new Date(a.created_at).toLocaleString()}</span> {a.action}
              {a.target_type ? ` ${a.target_type}` : ""}{a.ip ? ` from ${a.ip}` : ""}
            </li>
          ))}
        </ul>
      )}
    </Row>
  );
}
