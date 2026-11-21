"use client";

import { CalendarClock, ExternalLink, Send } from "lucide-react";
import { useState } from "react";
import { choiceIso, choiceProblem, defaultChoice, formatWhen, SchedulePicker, type ScheduleChoice } from "@/components/schedule";
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
  const [when, setWhen] = useState<ScheduleChoice>(() => defaultChoice(pkg.scheduled_at));
  const [busy, setBusy] = useState(false);
  const youtube = pkg.platform === "youtube";
  const published = pkg.publications.find((p) => p.status === "published");
  const scheduled = pkg.status === "scheduled";
  const missed = pkg.status === "missed";
  const waitingOnYouTube = pkg.publications.find((p) => p.status === "scheduled" || p.status === "held");

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

  function openPublish(mode: "now" | "later") {
    setWhen({ ...defaultChoice(pkg.scheduled_at), mode });
    setConfirm(true);
  }

  async function publish() {
    const at = choiceIso(when);
    setBusy(true);
    try {
      await save();
      await api(`/packages/${pkg.id}/publish`, { method: "POST", body: at ? { scheduled_at: at } : {} });
      toast("success", at
        ? `Scheduled for ${formatWhen(at)}. It's marked published only after the platform confirms.`
        : "Publishing started — it's marked published only after the platform confirms.");
      setConfirm(false);
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not publish");
    } finally {
      setBusy(false);
    }
  }

  async function unschedule() {
    setBusy(true);
    try {
      await api(`/packages/${pkg.id}/unschedule`, { method: "POST" });
      toast("success", waitingOnYouTube ? "Schedule cancelled. The upload stays on YouTube as a private video." : "Schedule cancelled.");
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not cancel");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-border p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold">{youtube ? "YouTube Shorts" : "Instagram Reels"}</span>
          <Badge tone="neutral">{pkg.metadata_method.replace("_", " ")}</Badge>
        </div>
        <StatusBadge label={missed ? "missed its time" : pkg.status.replace("_", " ")} />
      </div>
      {(scheduled || missed) && (
        <div className={`mb-3 rounded-xl px-3 py-2.5 text-[13px] ${missed ? "bg-warning-soft text-warning" : "bg-primary-soft/60 text-text"}`}>
          <div className="flex items-start gap-2">
            <CalendarClock className="mt-0.5 size-4 shrink-0" />
            <div className="min-w-0">
              {missed ? (
                <p>Its time ({formatWhen(pkg.scheduled_at)}) passed while CLIPFORGE wasn't running or publishing was on hold. Pick a new time or publish it now.</p>
              ) : (
                <>
                  <p className="font-medium">{pkg.scheduled_at ? `Goes live ${formatWhen(pkg.scheduled_at)}` : "Waiting for the next free slot (daily limit or quota)"}</p>
                  <p className="mt-0.5 text-[12px] text-muted">
                    {pkg.schedule_mode === "youtube"
                      ? (waitingOnYouTube ? "Uploaded as private; YouTube makes it public at that time, even if this computer is off." : "It uploads shortly as private; YouTube then makes it public at that time.")
                      : "CLIPFORGE prepares it about 10 minutes before and posts it at that time — keep CLIPFORGE running."}
                    {" "}Edits you save before then are used.
                  </p>
                </>
              )}
            </div>
          </div>
          {can("editor") && (
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="secondary" onClick={() => openPublish("later")}>{missed ? "Pick a new time" : "Change time"}</Button>
              <Button size="sm" variant="ghost" onClick={() => openPublish("now")}>Publish now</Button>
              <Button size="sm" variant="ghost" loading={busy} onClick={unschedule}>Cancel schedule</Button>
            </div>
          )}
        </div>
      )}
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
        {!published && !scheduled && !missed && (
          <>
            <Button size="sm" icon={<Send className="size-3.5" />} onClick={() => openPublish("now")} disabled={!can("editor")}>Publish…</Button>
            <Button size="sm" variant="secondary" icon={<CalendarClock className="size-3.5" />} onClick={() => openPublish("later")} disabled={!can("editor")}>Schedule…</Button>
          </>
        )}
        {published?.url && (
          <a href={published.url} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-[13px] font-medium text-primary hover:underline">
            View on {youtube ? "YouTube" : "Instagram"} <ExternalLink className="size-3.5" />
          </a>
        )}
      </div>
      {pkg.publications.map((pub) => (
        <div key={pub.id} className="mt-3 rounded-lg bg-surface-2 px-3 py-2 text-[12px]">
          <div className="flex items-center justify-between">
            <StatusBadge label={pub.status === "held" ? "uploaded, private" : pub.status} />
            <span className="text-faint">{pub.published_at ? formatDate(pub.published_at) : pub.scheduled_at ? `goes live ${formatWhen(pub.scheduled_at)}` : ""}</span>
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
      <Modal open={confirm} onClose={() => setConfirm(false)} title={`Publish to ${youtube ? "YouTube" : "Instagram"}`}
        footer={<><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button>
          <Button loading={busy} disabled={Boolean(choiceProblem(when))} onClick={publish}>{when.mode === "later" ? "Schedule" : "Publish now"}</Button></>}>
        <div className="space-y-4">
          <SchedulePicker value={when} onChange={setWhen} platforms={[pkg.platform]} privacy={draft.privacy} />
          <p className="text-sm text-muted">
            {youtube ? `Visibility: ${draft.privacy}. ` : ""}CLIPFORGE marks it as published only after the platform confirms it.
            Duplicate uploads of the same file are blocked automatically.
          </p>
        </div>
      </Modal>
    </div>
  );
}
