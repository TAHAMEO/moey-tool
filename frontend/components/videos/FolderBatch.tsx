"use client";

import clsx from "clsx";
import { Eye, FolderOpen, FolderSync, Pause, Play, Plus, Square, Upload, X } from "lucide-react";
import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { formatWhen } from "@/components/schedule";
import { Choice, Chips } from "@/components/settings/shared";
import { Badge, StatusBadge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, Input, Label, Modal, ProgressBar, Select, Textarea, Toggle } from "@/components/ui";
import { api, formatBytes, timeAgo, uploadMedia } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { AllSettings, BatchRecipe, BatchSummary, FolderListing } from "@/lib/types";

const VIDEO_EXT = [".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts", ".3gp", ".wmv", ".flv"];
const isVideo = (name: string) => VIDEO_EXT.some((ext) => name.toLowerCase().endsWith(ext));
const sizeText = (bytes: number) => bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1e6))} MB`;

type Source = "device" | "watch";

function defaultRecipe(settings?: AllSettings | null): BatchRecipe {
  return {
    instruction: "", count: null, platforms: settings?.publishing.platforms ?? ["youtube", "instagram"],
    publish: "approve", privacy: settings?.publishing.default_privacy ?? "private",
    schedule: { mode: "slots", times: ["18:00"], start_date: null },
  };
}

/** My Videos → "Process a folder": every video in a folder, one recipe. */
export function FolderBatchDialog({ open, onClose, onStarted }: { open: boolean; onClose: () => void; onStarted: () => void }) {
  const toast = useToast();
  const { data: settings } = useResource<AllSettings>(open ? "/settings" : null);
  const { data: listing, reload: reloadListing } = useResource<FolderListing>(open ? "/folders" : null);
  const [source, setSource] = useState<Source>("device");
  const [files, setFiles] = useState<File[]>([]);
  const [folderName, setFolderName] = useState("");
  const [watchPath, setWatchPath] = useState<string | null>(null);
  const [includeExisting, setIncludeExisting] = useState(true);
  const [moveDone, setMoveDone] = useState(false);
  const [recipe, setRecipe] = useState<BatchRecipe | null>(null);
  const [newTime, setNewTime] = useState("12:00");
  const [progress, setProgress] = useState<{ done: number; total: number; current: string; fraction: number; size?: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const r = recipe ?? defaultRecipe(settings);
  const set = (patch: Partial<BatchRecipe>) => setRecipe({ ...r, ...patch });
  const watched = new Set((listing?.watched ?? []).map((w) => w.path));
  const total = useMemo(() => files.reduce((sum, f) => sum + f.size, 0), [files]);

  function pick(list: FileList | null) {
    const all = Array.from(list ?? []);
    const videos = all.filter((f) => isVideo(f.name));
    setFiles(videos);
    const first = all[0] as (File & { webkitRelativePath?: string }) | undefined;
    setFolderName(first?.webkitRelativePath?.split("/")[0] ?? "Folder");
  }

  async function start() {
    setBusy(true);
    try {
      if (source === "watch") {
        await api("/folders", {
          body: { path: watchPath ?? "", recipe: r, include_existing: includeExisting, after_processing: moveDone ? "move" : "keep" },
        });
        toast("success", "Folder is being watched. Videos already in it start within a minute; new ones as soon as they're copied in.");
        onStarted();
        onClose();
        return;
      }
      const batch = await api<BatchSummary>("/batches", { body: { name: folderName || "Folder upload", recipe: r } });
      let failed = 0;
      for (let i = 0; i < files.length; i++) {
        const file = files[i] as File & { webkitRelativePath?: string };
        setProgress({ done: i, total: files.length, current: file.name, fraction: 0, size: file.size });
        try {
          // In pieces of a few MB: one big request is cut off at 10 MB by the Windows app's web server.
          const uploaded = await uploadMedia(file,
            (fraction) => setProgress({ done: i, total: files.length, current: file.name, fraction, size: file.size }));
          await api(`/batches/${batch.id}/items`, { body: { asset_id: uploaded.asset_id, filename: file.webkitRelativePath || file.name } });
        } catch (err) {
          failed += 1;
          toast("error", `${file.name}: ${err instanceof Error ? err.message : "upload failed"}`);
        }
      }
      await api(`/batches/${batch.id}/close`, { method: "POST" });
      toast(failed ? "info" : "success", `${files.length - failed} video(s) added to “${batch.name}”${failed ? `, ${failed} failed` : ""}.`);
      onStarted();
      onClose();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not start");
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  const ready = source === "device" ? files.length > 0 : watchPath !== null && !watched.has(watchPath);
  const canStart = ready && r.platforms.length > 0 && !(r.publish !== "drafts" && r.schedule.mode === "slots" && r.schedule.times.length === 0);

  return (
    <Modal open={open} onClose={busy ? () => undefined : onClose} title="Process a folder" wide
      footer={<>
        <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button loading={busy} disabled={!canStart} onClick={start}>
          {source === "watch" ? "Start watching" : `Process ${files.length || ""} video${files.length === 1 ? "" : "s"}`}
        </Button>
      </>}>
      {progress ? (
        <div className="space-y-3 py-2">
          <p className="text-sm font-medium">Uploading {progress.done + 1} of {progress.total}: {progress.current}</p>
          <ProgressBar value={(progress.done + progress.fraction) / progress.total} />
          <p className="text-[12px] tabular-nums text-muted" aria-live="polite">
            This video: {Math.floor(progress.fraction * 100)}% ({formatBytes((progress.size ?? 0) * progress.fraction)} of {formatBytes(progress.size)})
          </p>
          <p className="text-[12px] text-muted">Keep this tab open until the upload finishes. Each video starts processing as soon as it has arrived.</p>
        </div>
      ) : (
        <div className="space-y-5">
          <div className="grid gap-2 sm:grid-cols-2" role="radiogroup" aria-label="Where are the videos?">
            {([["device", "A folder on this device", "Pick a folder; its videos are uploaded and processed.", Upload],
              ["watch", "Watch a CLIPFORGE folder", "Videos put in this folder now or later are processed automatically.", FolderSync]] as const)
              .map(([value, label, hint, Icon]) => (
                <button key={value} type="button" role="radio" aria-checked={source === value} onClick={() => setSource(value)}
                  className={clsx("flex gap-3 rounded-xl border p-3 text-left transition-colors",
                    source === value ? "border-primary bg-primary-soft" : "border-border hover:bg-surface-2")}>
                  <Icon className={clsx("mt-0.5 size-4 shrink-0", source === value ? "text-primary" : "text-muted")} />
                  <span>
                    <span className={clsx("block text-[13px] font-semibold", source === value && "text-primary")}>{label}</span>
                    <span className="mt-0.5 block text-[12px] text-muted">{hint}</span>
                  </span>
                </button>
              ))}
          </div>

          {source === "device" ? (
            <div>
              <input ref={input} type="file" multiple className="hidden" onChange={(e) => pick(e.target.files)}
                {...({ webkitdirectory: "", directory: "" } as Record<string, string>)} />
              <Button variant="secondary" icon={<FolderOpen className="size-4" />} onClick={() => input.current?.click()}>
                {files.length ? "Choose another folder" : "Choose folder…"}
              </Button>
              {files.length > 0 && (
                <p className="mt-2 text-[13px] text-muted">
                  <span className="font-medium text-text">{folderName}</span>: {files.length} video{files.length === 1 ? "" : "s"} ({sizeText(total)}).
                  Other files are ignored.
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-[12px] text-muted">
                CLIPFORGE's inbox is <span className="break-all font-mono text-text">{listing?.root ?? "…"}</span>. Make a folder in it for each kind of video,
                then choose it here.
              </p>
              <div className="max-h-48 space-y-1 overflow-y-auto rounded-xl border border-border p-1.5">
                {listing && [{ name: "The whole inbox", path: "", videos: listing.videos }, ...listing.folders].map((folder) => {
                  const taken = watched.has(folder.path);
                  return (
                    <button key={folder.path || "(root)"} type="button" disabled={taken} onClick={() => setWatchPath(folder.path)}
                      className={clsx("flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[13px] disabled:opacity-50",
                        watchPath === folder.path ? "bg-primary-soft text-primary" : "hover:bg-surface-2")}>
                      <span className="flex items-center gap-2"><FolderOpen className="size-3.5" />{folder.name}</span>
                      <span className="text-[12px] text-faint">{taken ? "already watched" : `${folder.videos} video${folder.videos === 1 ? "" : "s"}`}</span>
                    </button>
                  );
                })}
                {listing && listing.folders.length === 0 && (
                  <p className="px-2.5 py-1.5 text-[12px] text-faint">No sub-folders yet — create one in the inbox, then <button className="text-primary" onClick={reloadListing}>refresh</button>.</p>
                )}
              </div>
              <Toggle checked={includeExisting} onChange={setIncludeExisting} label="Also process the videos already in it" />
              <Toggle checked={moveDone} onChange={setMoveDone} disabled={!listing?.writable}
                label="Move finished videos into a “Processed” sub-folder" description="Off: your files are never touched." />
            </div>
          )}

          <div className="space-y-4 border-t border-border pt-4">
            <div>
              <Label hint="optional — like a chat message">What should I make from each video?</Label>
              <Textarea rows={2} value={r.instruction} onChange={(e) => set({ instruction: e.target.value })}
                placeholder="e.g. 3 Shorts under 45 seconds with the most useful tips" />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label hint="leave empty to use the instruction or your default">Clips per video</Label>
                <Input type="number" min={1} max={30} value={r.count ?? ""} placeholder={`${settings?.clips.default_count ?? 5} (default)`}
                  onChange={(e) => set({ count: e.target.value ? Math.max(1, Math.min(30, Number(e.target.value))) : null })} />
              </div>
              <div>
                <Label>Platforms</Label>
                <Chips value={r.platforms} onChange={(platforms) => set({ platforms })}
                  options={[{ value: "youtube", label: "YouTube Shorts" }, { value: "instagram", label: "Instagram Reels" }]} />
              </div>
            </div>
            <div>
              <Label>When clips are ready</Label>
              <Choice value={r.publish} onChange={(publish) => set({ publish })} options={[
                { value: "drafts", label: "Keep as drafts", description: "Nothing is posted." },
                { value: "approve", label: "Ask me first", description: "Each video's clips wait for your OK." },
                { value: "auto", label: "Post automatically", description: "Clips that pass every check are posted." },
              ]} />
            </div>
            {r.publish !== "drafts" && (
              <div className="space-y-3">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <Label>Posting times</Label>
                    <Select value={r.schedule.mode} onChange={(mode) => set({ schedule: { ...r.schedule, mode: mode as "now" | "slots" } })}
                      options={[{ value: "slots", label: "One clip per time slot, day by day" }, { value: "now", label: "As soon as each clip is ready" }]} />
                  </div>
                  {r.platforms.includes("youtube") && (
                    <div>
                      <Label>YouTube visibility</Label>
                      <Select value={r.privacy ?? "private"} onChange={(v) => set({ privacy: v as BatchRecipe["privacy"] })}
                        options={[{ value: "public", label: "Public" }, { value: "unlisted", label: "Unlisted" }, { value: "private", label: "Private" }]} />
                    </div>
                  )}
                </div>
                {r.schedule.mode === "slots" && (
                  <div>
                    <div className="flex flex-wrap items-center gap-1.5">
                      {r.schedule.times.map((t) => (
                        <span key={t} className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2.5 py-1 text-[13px]">
                          {t}
                          <button aria-label={`Remove ${t}`} onClick={() => set({ schedule: { ...r.schedule, times: r.schedule.times.filter((x) => x !== t) } })}>
                            <X className="size-3 text-muted hover:text-text" />
                          </button>
                        </span>
                      ))}
                      <input type="time" value={newTime} onChange={(e) => setNewTime(e.target.value)} aria-label="Add a posting time"
                        className="h-8 rounded-lg border border-border bg-surface px-2 text-[13px]" />
                      <Button size="sm" variant="ghost" icon={<Plus className="size-3.5" />}
                        onClick={() => newTime && !r.schedule.times.includes(newTime) && set({ schedule: { ...r.schedule, times: [...r.schedule.times, newTime].sort() } })}>
                        Add time
                      </Button>
                    </div>
                    <p className="mt-1.5 text-[12px] text-muted">
                      Clips are lined up across all videos of this batch: one per time slot ({settings?.publishing.timezone ?? "your time zone"}),
                      skipping days that already reach your daily limit. A clip going to both platforms goes out on both at once.
                    </p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Batches and watched folders, with progress and controls. */
export function BatchesPanel({ refreshKey, onFilter, activeFilter }: {
  refreshKey: number; onFilter: (batchId: string | null) => void; activeFilter: string | null;
}) {
  const { data, reload } = useResource<BatchSummary[]>(`/batches?k=${refreshKey}`, { poll: 5000 });
  const { can } = useAuth();
  const toast = useToast();
  const [stopping, setStopping] = useState<BatchSummary | null>(null);
  if (!data || data.length === 0) return null;

  async function act(batch: BatchSummary, action: string) {
    try {
      await api(`/batches/${batch.id}/${action}`, { method: "POST" });
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Action failed");
    }
  }

  async function toggleWatch(batch: BatchSummary) {
    if (!batch.folder) return;
    try {
      await api(`/folders/${batch.folder.id}`, { method: "PATCH", body: { enabled: !batch.folder.enabled } });
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not change the folder");
    }
  }

  return (
    <div className="mb-6 grid gap-3 lg:grid-cols-2">
      {data.map((batch) => {
        const c = batch.counts;
        const finished = c.ready + c.scheduled + c.published + c.attention + c.skipped;
        return (
          <Card key={batch.id} className={clsx("p-4", activeFilter === batch.id && "ring-2 ring-primary/40")}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex items-center gap-1.5 truncate text-sm font-semibold">
                  {batch.origin === "folder" ? <FolderSync className="size-4 shrink-0 text-primary" /> : <Upload className="size-4 shrink-0 text-primary" />}
                  {batch.name}
                </p>
                <p className="mt-0.5 truncate text-[12px] text-muted">{batch.recipe_summary}</p>
              </div>
              <StatusBadge label={batch.status === "running" && batch.origin === "folder" ? (batch.folder?.enabled ? "watching" : "not watching") : batch.status}
                status={batch.status === "paused" ? "paused" : undefined} />
            </div>
            <div className="mt-3">
              <ProgressBar value={c.total ? finished / c.total : 0} tone={c.attention ? "warning" : "primary"} />
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-muted">
                <span>{c.total} video{c.total === 1 ? "" : "s"}</span>
                {c.processing + c.waiting > 0 && <span>{c.processing + c.waiting} in progress</span>}
                {c.ready > 0 && <span>{c.ready} ready</span>}
                {c.scheduled > 0 && <span>{c.scheduled} scheduled</span>}
                {c.published > 0 && <span>{c.published} published</span>}
                {c.attention > 0 && <span className="text-warning">{c.attention} need attention</span>}
                {c.skipped > 0 && <span>{c.skipped} skipped (duplicates)</span>}
              </div>
              {batch.folder && (
                <p className="mt-1 text-[12px] text-faint">
                  {batch.folder.last_error ? <span className="text-danger">{batch.folder.last_error}</span>
                    : batch.folder.last_scan_at ? `Checked ${timeAgo(batch.folder.last_scan_at)}` : "Waiting for the first check"}
                  {batch.folder.waiting_files > 0 && ` · ${batch.folder.waiting_files} file(s) still being copied`}
                </p>
              )}
              {batch.next_slot && batch.recipe.schedule.mode === "slots" && batch.recipe.publish !== "drafts" && (
                <p className="mt-1 text-[12px] text-faint">Next free posting slot after {formatWhen(batch.next_slot)}</p>
              )}
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" variant={activeFilter === batch.id ? "secondary" : "ghost"} icon={<Eye className="size-3.5" />}
                onClick={() => onFilter(activeFilter === batch.id ? null : batch.id)}>
                {activeFilter === batch.id ? "Show all videos" : "Show its videos"}
              </Button>
              {can("editor") && batch.status !== "completed" && (
                <>
                  {batch.origin === "folder" && batch.folder && (
                    <Button size="sm" variant="ghost" icon={batch.folder.enabled ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                      onClick={() => toggleWatch(batch)}>{batch.folder.enabled ? "Stop watching" : "Watch again"}</Button>
                  )}
                  {batch.status === "paused"
                    ? <Button size="sm" variant="ghost" icon={<Play className="size-3.5" />} onClick={() => act(batch, "resume")}>Resume</Button>
                    : <Button size="sm" variant="ghost" icon={<Pause className="size-3.5" />} onClick={() => act(batch, "pause")}>Pause</Button>}
                  <Button size="sm" variant="ghost" icon={<Square className="size-3.5" />} onClick={() => setStopping(batch)}>Cancel</Button>
                </>
              )}
              {batch.counts.attention > 0 && <Badge tone="warning">Check the videos marked “needs attention”</Badge>}
            </div>
          </Card>
        );
      })}
      <Modal open={stopping !== null} onClose={() => setStopping(null)} title="Cancel this batch?"
        footer={<><Button variant="ghost" onClick={() => setStopping(null)}>Keep it</Button>
          <Button variant="danger" onClick={async () => { if (stopping) await act(stopping, "cancel"); setStopping(null); }}>Cancel batch</Button></>}>
        <p className="text-sm text-muted">Videos still waiting or being processed are stopped{stopping?.origin === "folder" ? " and the folder is no longer watched" : ""}.
          Clips already made, scheduled or published are kept. <Link href="/dashboard" className="text-primary">Scheduled posts</Link> can be cancelled from each video.</p>
      </Modal>
    </div>
  );
}
