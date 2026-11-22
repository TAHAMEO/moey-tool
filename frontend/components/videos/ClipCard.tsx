"use client";

import clsx from "clsx";
import { AlertTriangle, CheckCircle2, ChevronDown, Download, Scissors, XCircle } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, Input, Label, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Clip } from "@/lib/types";
import { PackageEditor } from "./PackageEditor";
import { ScoreBars } from "./ScoreBars";

const fmt = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

export function ClipCard({ clip, onChanged, technical = false }: { clip: Clip; onChanged: () => void; technical?: boolean }) {
  const toast = useToast();
  const { can } = useAuth();
  const renders = clip.renders.filter((r) => r.status === "done");
  const expired = renders.length === 0 && clip.renders.some((r) => r.status === "expired");
  const [aspect, setAspect] = useState(renders[0]?.aspect);
  const [details, setDetails] = useState(false);
  const [editing, setEditing] = useState(false);
  const [edit, setEdit] = useState({ start: clip.start_s.toFixed(1), end: clip.end_s.toFixed(1), style: "" });
  const render = renders.find((r) => r.aspect === aspect) ?? renders[0];
  const reviewWords = render?.subtitles.review_words ?? [];

  async function rerender() {
    try {
      await api(`/clips/${clip.id}`, { method: "PATCH", body: { start_s: Number(edit.start), end_s: Number(edit.end), caption_style: edit.style || undefined } });
      toast("success", "Re-rendering with your changes.");
      setEditing(false);
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not update clip");
    }
  }

  return (
    <Card className="animate-fade-up overflow-hidden">
      <div className="grid gap-0 md:grid-cols-[minmax(0,280px)_1fr]">
        <div className="border-b border-border bg-black/90 md:border-b-0 md:border-r">
          {render?.video_url ? (
            <video key={render.video_url} src={render.video_url} controls preload="metadata" playsInline poster={render.thumbnail_url ?? undefined}
              className={clsx("mx-auto max-h-[520px] w-full", render.aspect === "16:9" ? "aspect-video" : render.aspect === "1:1" ? "aspect-square" : "aspect-[9/16]")}>
              {render.vtt_url && <track kind="captions" src={render.vtt_url} srcLang="en" label="Captions" />}
            </video>
          ) : (
            <div className="flex aspect-[9/16] items-center justify-center p-6 text-center text-[13px] text-white/60">
              {clip.status === "candidate" ? "Moment identified from the transcript — no media was available to render it."
                : expired ? "The files of this clip were removed by storage clean-up. Use Adjust → Re-render to recreate them."
                : "Rendering…"}
            </div>
          )}
          {renders.length > 1 && (
            <div className="flex justify-center gap-1 bg-surface p-2">
              {renders.map((r) => (
                <button key={r.aspect} onClick={() => setAspect(r.aspect)}
                  className={clsx("rounded-md px-2 py-1 text-[12px] font-medium", r.aspect === render?.aspect ? "bg-primary-soft text-primary" : "text-muted")}>
                  {r.aspect}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="p-5">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="text-[12px] font-medium text-faint">Clip {clip.rank} · {fmt(clip.start_s)}–{fmt(clip.end_s)} · {Math.round(clip.end_s - clip.start_s)}s</p>
              <h3 className="mt-0.5 text-[15px] font-semibold leading-snug">{clip.title || clip.hook}</h3>
            </div>
            <div className="text-right">
              <p className="text-[11px] text-faint">Potential score</p>
              <p className="text-xl font-semibold tabular-nums">{Math.round(clip.total_score * 100)}</p>
            </div>
          </div>
          <p className="mt-1 text-[12px] text-faint">Scores estimate potential from content signals — they are not a prediction of views.</p>

          {clip.reasons.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-1.5">{clip.reasons.map((r) => <Badge key={r} tone="primary">{r}</Badge>)}</div>
          )}

          <ShortsStatus clip={clip} />
          <Variants clip={clip} onChanged={onChanged} />

          {render && (
            <div className="mt-4 flex flex-wrap gap-2 text-[12px]">
              <Badge tone={render.quality.passed ? "success" : "danger"}>
                {render.quality.passed ? <CheckCircle2 className="size-3" /> : <XCircle className="size-3" />} Quality checks
              </Badge>
              <Badge tone={reviewWords.length ? "warning" : "success"}>
                {reviewWords.length ? <AlertTriangle className="size-3" /> : <CheckCircle2 className="size-3" />}
                {reviewWords.length ? `${reviewWords.length} caption word(s) to review` : "Captions validated"}
              </Badge>
              {render.edits.removed_silence_s ? <Badge>{render.edits.removed_silence_s.toFixed(1)}s dead air removed</Badge> : null}
              {render.edits.crop_strategies?.includes("track") && <Badge>Face-tracked framing</Badge>}
            </div>
          )}

          {reviewWords.length > 0 && (
            <div className="mt-3 rounded-xl bg-warning-soft px-3 py-2 text-[13px] text-warning">
              Please check: {reviewWords.map((w) => `“${w.word}” at ${w.time.toFixed(1)}s (${w.reason})`).join("; ")}
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {render?.video_url && <a href={`${render.video_url}?download=true`} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[12px] hover:bg-surface-2"><Download className="size-3.5" /> MP4</a>}
            {render?.srt_url && <a href={`${render.srt_url}?download=true`} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[12px] hover:bg-surface-2"><Download className="size-3.5" /> SRT</a>}
            {render?.vtt_url && <a href={`${render.vtt_url}?download=true`} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[12px] hover:bg-surface-2"><Download className="size-3.5" /> VTT</a>}
            {clip.transcript_url && <a href={`${clip.transcript_url}?download=true`} className="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[12px] hover:bg-surface-2"><Download className="size-3.5" /> Transcript</a>}
            {can("editor") && clip.status !== "candidate" && (
              <Button size="sm" variant="ghost" icon={<Scissors className="size-3.5" />} onClick={() => setEditing(!editing)}>Adjust</Button>
            )}
          </div>

          {editing && (
            <div className="mt-3 grid gap-3 rounded-xl border border-border p-3 sm:grid-cols-4">
              <div><Label>Start (s)</Label><Input inputMode="decimal" value={edit.start} onChange={(e) => setEdit({ ...edit, start: e.target.value })} /></div>
              <div><Label>End (s)</Label><Input inputMode="decimal" value={edit.end} onChange={(e) => setEdit({ ...edit, end: e.target.value })} /></div>
              <div><Label>Captions</Label><Select value={edit.style} onChange={(v) => setEdit({ ...edit, style: v })} options={[
                { value: "", label: "Keep style" }, { value: "bold_pop", label: "Bold pop" }, { value: "karaoke_fill", label: "Karaoke" },
                { value: "clean_lower", label: "Clean" }, { value: "boxed", label: "Boxed" }, { value: "kinetic", label: "Kinetic" }]} /></div>
              <div className="flex items-end"><Button size="sm" className="w-full" onClick={rerender}>Re-render</Button></div>
            </div>
          )}

          <button onClick={() => setDetails(!details)} className="mt-4 flex items-center gap-1 text-[12px] font-medium text-muted hover:text-text">
            Score breakdown & transcript <ChevronDown className={clsx("size-3.5 transition-transform", details && "rotate-180")} />
          </button>
          {details && (
            <div className="mt-3 space-y-4">
              <ScoreBars scores={clip.scores} />
              <p className="rounded-xl bg-surface-2 p-3 text-[13px] leading-relaxed text-muted">{clip.transcript}</p>
              {render && (
                <ul className="grid gap-1 text-[12px] sm:grid-cols-2">
                  {render.quality.checks.map((c) => (
                    <li key={c.name} className="flex items-center gap-1.5">
                      {c.ok ? <CheckCircle2 className="size-3 text-success" /> : <XCircle className={clsx("size-3", c.blocking ? "text-danger" : "text-warning")} />}
                      <span className="text-muted">{c.detail}</span>
                    </li>
                  ))}
                </ul>
              )}
              {technical && <p className="text-[11px] text-faint">Scored by: {clip.scoring_method.replace(/_/g, " ")}</p>}
            </div>
          )}
        </div>
      </div>
      {clip.packages.length > 0 && (
        <div className="grid gap-4 border-t border-border p-5 lg:grid-cols-2">
          {clip.packages.map((pkg) => <PackageEditor key={pkg.id} pkg={pkg} onChanged={onChanged} />)}
        </div>
      )}
    </Card>
  );
}

const GATE_LABEL: Record<string, string> = {
  approved: "Published / scheduled", awaiting_approval: "Waiting for your approval", held: "Held for review",
  draft: "Draft only", not_approved: "Not approved",
};

/** First-3-seconds hook, loop seam and the publish decision, with the reasons. */
function ShortsStatus({ clip }: { clip: Clip }) {
  const hook = clip.shorts?.hook;
  const loop = clip.shorts?.loop;
  const gate = clip.shorts?.publish_gate_result;
  const rehook = clip.shorts?.retention_plan;
  if (!hook && !loop && !gate && !rehook) return null;
  const pct = (v?: number | null) => (v == null ? "–" : Math.round(v * 100));
  return (
    <div className="mt-4 space-y-2 rounded-xl border border-border p-3 text-[12px]">
      <div className="flex flex-wrap gap-2">
        {hook && (
          <Badge tone={hook.status === "held" ? "warning" : "success"}>
            First 3 s: {pct(clip.hook_score)}{hook.status === "improved" ? ` (was ${pct(hook.before.score)})` : ""}
          </Badge>
        )}
        {loop && <Badge tone={loop.assessment.loopable ? "success" : "neutral"}>Loop: {pct(clip.loop_score)}{loop.bridged ? " · blended" : ""}</Badge>}
        {gate && <Badge tone={gate.result === "held" ? "warning" : gate.result === "approved" ? "success" : "neutral"}>{GATE_LABEL[gate.result] ?? gate.result}</Badge>}
        {rehook && <Badge tone={rehook.at != null ? "success" : "neutral"}>{rehook.at != null ? `Re-hook at ${rehook.at.toFixed(1)} s` : "No re-hook"}</Badge>}
      </div>
      {(clip.shorts?.first_frame_text ?? hook?.text_hook) && <p className="text-muted">Text on frame one: <span className="font-semibold text-text">{clip.shorts?.first_frame_text ?? hook?.text_hook}</span></p>}
      {[...(hook?.changes ?? []), ...(loop?.changes ?? []), ...(rehook?.changes ?? [])].length > 0 && (
        <ul className="list-disc space-y-0.5 pl-4 text-muted">
          {[...(hook?.changes ?? []), ...(loop?.changes ?? []), ...(rehook?.changes ?? [])].map((c) => <li key={c}>{c}</li>)}
        </ul>
      )}
      {rehook?.skipped && <p className="text-faint">Re-hook skipped: {rehook.skipped}</p>}
      {(gate?.result === "held" || hook?.status === "held") && (
        <ul className="list-disc space-y-0.5 pl-4 text-warning">
          {[...new Set([...(gate?.reasons ?? []), ...(hook?.after.reasons ?? [])])].map((r) => <li key={r}>{r}</li>)}
        </ul>
      )}
    </div>
  );
}

/** Ranked title / first-frame options; "Use" puts that title, description and hashtags on the
 *  clip's posts (anything not yet published). */
function Variants({ clip, onChanged }: { clip: Clip; onChanged: () => void }) {
  const toast = useToast();
  const { can } = useAuth();
  const variants = clip.shorts?.packaging_variants ?? [];
  if (!variants.length) return null;
  const yt = clip.packages.find((p) => p.platform === "youtube");
  const current = yt ? yt.title.replace(/\s*#shorts$/i, "") : null;
  async function use(v: (typeof variants)[number]) {
    try {
      for (const pkg of clip.packages) {
        if (pkg.status === "published") continue;
        const body = pkg.platform === "youtube"
          ? { title: `${v.title} #shorts`.slice(0, 100), description: v.youtube_description }
          : { caption: v.instagram_caption, hashtags: v.hashtags.instagram };
        await api(`/packages/${pkg.id}`, { method: "PATCH", body });
      }
      toast("success", "Title and description updated.");
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not update the posts");
    }
  }
  return (
    <div className="mt-3 rounded-xl border border-border p-3 text-[12px]">
      <p className="mb-2 font-medium text-text">Title and first-frame options</p>
      <ol className="space-y-2">
        {variants.map((v) => (
          <li key={v.rank} className="flex flex-wrap items-center gap-2">
            <span className="text-faint">#{v.rank}</span>
            <span className="font-semibold text-text">{v.title}</span>
            <span className="text-faint">({v.title.length} chars · {Math.round(v.score * 100)})</span>
            {v.first_frame_text && <Badge tone={v.burned ? "success" : "neutral"}>{v.burned ? "On frame one: " : "Frame text: "}{v.first_frame_text}</Badge>}
            {current === v.title ? <Badge tone="success">In use</Badge>
              : can("editor") && <Button size="sm" variant="ghost" onClick={() => use(v)}>Use</Button>}
            {v.reasons.length > 0 && <span className="w-full pl-6 text-faint">{v.reasons.join("; ")}</span>}
          </li>
        ))}
      </ol>
    </div>
  );
}
