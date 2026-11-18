"use client";

import { Copy, Eye, Radio, Square } from "lucide-react";
import { useState } from "react";
import { StatusBadge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Input, Label, Modal } from "@/components/ui";
import { api, timeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { LiveSession } from "@/lib/types";
import { Row, SettingsSection } from "./shared";

const LIVE_LABELS: Record<string, string> = {
  waiting: "Waiting for your encoder", live: "Receiving stream", reconnecting: "Reconnecting", stopping: "Stopping",
  ended: "Ended", failed: "Failed",
};

export function LiveSection() {
  const toast = useToast();
  const { can } = useAuth();
  const { data, reload } = useResource<LiveSession[]>("/live/sessions", { poll: 5000 });
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ source_url: "" });
  const [secret, setSecret] = useState<{ url: string; note?: string } | null>(null);

  async function create() {
    try {
      const res = await api<LiveSession>("/live/sessions", {
        method: "POST", body: { source_url: form.source_url || null },
      });
      setCreating(false);
      setSecret({ url: res.publish_url ?? "", note: res.note });
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not create the session");
    }
  }

  async function reveal(id: string) {
    try {
      const res = await api<{ publish_url: string }>(`/live/sessions/${id}/credentials`);
      setSecret({ url: res.publish_url });
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Not allowed");
    }
  }

  async function stop(id: string) {
    await api(`/live/sessions/${id}/stop`, { method: "POST" });
    toast("info", "Stopping — the replay is assembled from what was received.");
    reload();
  }

  const sessions = data ?? [];
  return (
    <SettingsSection id="live" title="Live streams"
      description="Platforms don't let apps watch someone's livestream. To clip a live show as it happens, send your stream here from OBS or your encoder — at the same time as you stream to YouTube.">
      <Row className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-[13px] text-muted">Each session gets a private SRT address with its own encryption passphrase.</p>
        {can("editor") && <Button size="sm" icon={<Radio className="size-3.5" />} onClick={() => setCreating(true)}>New live session</Button>}
      </Row>
      {sessions.map((s) => (
        <Row key={s.id} className="flex flex-wrap items-center gap-3">
          <StatusBadge label={LIVE_LABELS[s.status] ?? s.status} status={s.status === "live" ? "running" : s.status} />
          <div className="min-w-0 flex-1 text-[12px] text-muted">
            <span className="tabular-nums">{Math.round(s.stream_seconds / 60)} min received</span>
            {" · "}{s.segments} segments · {s.candidates.length} clip candidates · {s.connections} connection{s.connections === 1 ? "" : "s"}
            {" · "}created {timeAgo(s.created_at)}
          </div>
          {can("admin") && ["waiting", "live", "reconnecting"].includes(s.status) && (
            <Button size="sm" variant="ghost" icon={<Eye className="size-3.5" />} onClick={() => reveal(s.id)}>Stream address</Button>
          )}
          {can("editor") && ["waiting", "live", "reconnecting"].includes(s.status) && (
            <Button size="sm" variant="secondary" icon={<Square className="size-3.5" />} onClick={() => stop(s.id)}>Stop</Button>
          )}
        </Row>
      ))}

      <Modal open={creating} onClose={() => setCreating(false)} title="New live session"
        footer={<><Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button><Button onClick={create}>Create</Button></>}>
        <div className="space-y-4">
          <div>
            <Label hint="optional">Your YouTube live link</Label>
            <Input placeholder="https://www.youtube.com/live/…" value={form.source_url} onChange={(e) => setForm({ ...form, source_url: e.target.value })} />
            <p className="mt-1 text-[12px] text-muted">Links the clips to the broadcast. The video itself always comes from your encoder.</p>
          </div>
          <p className="text-[12px] text-muted">The raw stream is kept for clipping as long as set under Automation & safety → Storage clean-up.</p>
        </div>
      </Modal>
      <Modal open={secret !== null} onClose={() => setSecret(null)} title="Stream address"
        footer={<Button onClick={() => setSecret(null)}>Done</Button>}>
        <div className="space-y-3 text-sm">
          <p className="text-muted">In OBS: Settings → Stream → Service “Custom…”, paste this as the Server and leave Stream Key empty. To stream to YouTube at the same time, add this as a second output (e.g. with the Multiple RTMP Outputs plugin, which supports SRT).</p>
          <div className="flex items-start gap-2">
            <code className="flex-1 break-all rounded-lg bg-surface-2 p-3 text-[12px]">{secret?.url}</code>
            <button aria-label="Copy stream address" className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-text"
              onClick={() => { void navigator.clipboard.writeText(secret?.url ?? ""); toast("success", "Copied."); }}>
              <Copy className="size-4" />
            </button>
          </div>
          <p className="text-[12px] text-warning">This address contains a secret passphrase — treat it like a password.</p>
        </div>
      </Modal>
    </SettingsSection>
  );
}
