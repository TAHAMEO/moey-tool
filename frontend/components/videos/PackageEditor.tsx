"use client";

import { ExternalLink, Send } from "lucide-react";
import { useState } from "react";
import { Badge, StatusBadge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Input, Label, Modal, Select, Textarea } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Package } from "@/lib/types";

export function PackageEditor({ pkg, onChanged }: { pkg: Package; onChanged: () => void }) {
  const toast = useToast();
  const { can } = useAuth();
  const [draft, setDraft] = useState({
    title: pkg.title, description: pkg.description, tags: pkg.tags.join(", "),
    caption: pkg.caption, hashtags: pkg.hashtags.join(" "), privacy: pkg.privacy,
  });
  const [saving, setSaving] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const youtube = pkg.platform === "youtube";
  const published = pkg.publications.find((p) => p.status === "published");

  async function save() {
    setSaving(true);
    try {
      await api(`/packages/${pkg.id}`, {
        method: "PATCH",
        body: youtube
          ? { title: draft.title, description: draft.description, tags: draft.tags.split(",").map((t) => t.trim()).filter(Boolean), privacy: draft.privacy }
          : { caption: draft.caption, hashtags: draft.hashtags.split(/\s+/).filter(Boolean) },
      });
      toast("success", "Saved.");
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function publish() {
    try {
      await save();
      await api(`/packages/${pkg.id}/publish`, { method: "POST" });
      toast("success", "Publishing started — it's marked published only after the platform confirms.");
      setConfirm(false);
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not publish");
    }
  }

  return (
    <div className="rounded-xl border border-border p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{youtube ? "YouTube Shorts" : "Instagram Reels"}</span>
          <Badge tone="neutral">{pkg.metadata_method.replace("_", " ")}</Badge>
        </div>
        <StatusBadge label={pkg.status.replace("_", " ")} />
      </div>
      {youtube ? (
        <div className="space-y-3">
          <div><Label hint={`${draft.title.length}/100`}>Title</Label><Input value={draft.title} maxLength={100} onChange={(e) => setDraft({ ...draft, title: e.target.value })} /></div>
          <div><Label>Description</Label><Textarea rows={4} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div><Label hint="comma separated">Tags</Label><Input value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} /></div>
            <div><Label>Privacy</Label><Select value={draft.privacy} onChange={(v) => setDraft({ ...draft, privacy: v })}
              options={[{ value: "private", label: "Private" }, { value: "unlisted", label: "Unlisted" }, { value: "public", label: "Public" }]} /></div>
          </div>
          <p className="text-[12px] text-muted">A separate caption track (SRT) is uploaded with the video.</p>
        </div>
      ) : (
        <div className="space-y-3">
          <div><Label hint={`${draft.caption.length}/2200`}>Caption</Label><Textarea rows={5} maxLength={2200} value={draft.caption} onChange={(e) => setDraft({ ...draft, caption: e.target.value })} /></div>
          <div><Label hint="space separated">Hashtags</Label><Input value={draft.hashtags} onChange={(e) => setDraft({ ...draft, hashtags: e.target.value })} /></div>
          <p className="text-[12px] text-muted">Reels use the burned-in captions; the post caption above is the text description.</p>
        </div>
      )}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" loading={saving} onClick={save} disabled={!can("editor")}>Save</Button>
        {!published && <Button size="sm" icon={<Send className="size-3.5" />} onClick={() => setConfirm(true)} disabled={!can("editor")}>Publish…</Button>}
        {published?.url && (
          <a href={published.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
            View on {youtube ? "YouTube" : "Instagram"} <ExternalLink className="size-3.5" />
          </a>
        )}
      </div>
      {pkg.publications.map((pub) => (
        <div key={pub.id} className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-[12px]">
          <div className="flex items-center justify-between">
            <StatusBadge label={pub.status} />
            <span className="text-faint">{formatDate(pub.published_at)}</span>
          </div>
          {pub.last_error && <p className="mt-1 text-danger">{pub.last_error}</p>}
          {pub.metrics && (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
              {Object.entries(pub.metrics).slice(0, 8).map(([k, v]) => (
                <span key={k}><span className="text-muted">{k.replace("analytics_", "").replace(/_/g, " ")}:</span> {Number(v).toLocaleString()}</span>
              ))}
            </div>
          )}
          {pub.unavailable_metrics && Object.keys(pub.unavailable_metrics).length > 0 && (
            <p className="mt-1 text-faint">Not available from the platform: {Object.keys(pub.unavailable_metrics).slice(0, 5).join(", ")}</p>
          )}
        </div>
      ))}
      <Modal open={confirm} onClose={() => setConfirm(false)} title={`Publish to ${youtube ? "YouTube" : "Instagram"}?`}
        footer={<><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button><Button onClick={publish}>Publish</Button></>}>
        <p className="text-sm text-muted">
          {youtube ? `The video uploads as ${draft.privacy}. ` : ""}CLIPFORGE marks it as published only after the platform confirms the upload.
          Duplicate uploads of the same file are blocked automatically.
        </p>
      </Modal>
    </div>
  );
}
