"use client";

import { Archive, Download, Trash2 } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Input, Modal } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import { Row, SettingsSection } from "./shared";

type Backup = { name: string; created_at: string; label: string; size: number; memory_export?: string; memory_export_error?: string };

export function DataSection() {
  const toast = useToast();
  const { can } = useAuth();
  const { data: backups, reload } = useResource<Backup[]>(can("admin") ? "/admin/backups" : null);
  const [backingUp, setBackingUp] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [typed, setTyped] = useState("");

  async function backup() {
    setBackingUp(true);
    try {
      await api("/admin/backup", { method: "POST" });
      toast("success", "Backup created.");
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Backup failed");
    } finally {
      setBackingUp(false);
    }
  }

  async function deleteAll() {
    try {
      await api("/admin/delete-all-data", { method: "POST", body: { confirm: typed } });
      toast("success", "All content data deleted.");
      setConfirm(false);
      setTyped("");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not delete");
    }
  }

  return (
    <SettingsSection id="data" title="Your data" description="Everything CLIPFORGE stores belongs to you and can be exported or deleted.">
      <Row className="flex flex-wrap gap-2">
        {can("admin") && <a href="/api/admin/export" className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium hover:bg-surface-2"><Download className="size-3.5" /> Export all data (JSON)</a>}
        <a href="/api/memory-export" className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-[13px] font-medium hover:bg-surface-2"><Download className="size-3.5" /> Export memory</a>
        {can("admin") && <Button size="sm" variant="secondary" loading={backingUp} icon={<Archive className="size-3.5" />} onClick={backup}>Back up now</Button>}
      </Row>
      {can("admin") && (
        <Row>
          <p className="text-[13px] font-semibold">Backups</p>
          <p className="mt-0.5 text-[12px] text-muted">A database and memory backup runs every day; the last 14 are kept on the server. Copy them off the server regularly.</p>
          <ul className="mt-2 space-y-1 text-[12px] text-muted">
            {(backups ?? []).slice(0, 6).map((b) => (
              <li key={b.name} className="flex justify-between gap-3">
                <span className="font-mono">{b.name}</span>
                <span>{formatDate(b.created_at)} · {b.label} · {(b.size / 1024 / 1024).toFixed(1)} MB{b.memory_export_error ? " · memory export failed" : ""}</span>
              </li>
            ))}
            {(backups ?? []).length === 0 && <li>No backups yet.</li>}
          </ul>
        </Row>
      )}
      {can("owner") && (
        <Row className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-[13px] font-semibold text-danger">Delete all content</p>
            <p className="mt-0.5 text-[12px] text-muted">Removes projects, media, clips, analytics, knowledge and memory. Accounts and settings stay. Videos already published stay on the platforms.</p>
          </div>
          <Button size="sm" variant="danger" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirm(true)}>Delete…</Button>
        </Row>
      )}
      <Modal open={confirm} onClose={() => setConfirm(false)} title="Delete all content?"
        footer={<><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button><Button variant="danger" disabled={typed !== "DELETE ALL DATA"} onClick={deleteAll}>Delete everything</Button></>}>
        <div className="space-y-3 text-sm text-muted">
          <p>This can&apos;t be undone. Type <span className="font-mono font-semibold text-text">DELETE ALL DATA</span> to confirm.</p>
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        </div>
      </Modal>
    </SettingsSection>
  );
}
