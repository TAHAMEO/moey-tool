import clsx from "clsx";
import type { JobLiveDetail, JobStep } from "@/lib/types";

type Tone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted",
  primary: "bg-primary-soft text-primary",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: React.ReactNode; className?: string }) {
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[12px] font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function toneForLabel(label: string, status?: string): Tone {
  const text = `${label} ${status ?? ""}`.toLowerCase();
  if (/(failed|unavailable|denied|error|cancel)/.test(text)) return "danger";
  if (/(access required|waiting|approval|scheduled|paused|transcript only|reconnecting)/.test(text)) return "warning";
  if (/(completed|ready|published|done|ok|connected)/.test(text)) return "success";
  if (/(queued|checking|fetching|download|preparing|transcrib|analyz|finding|editing|render|publishing|running|processing|receiving)/.test(text)) return "primary";
  return "neutral";
}

export function StatusBadge({ label, status }: { label: string; status?: string }) {
  const tone = toneForLabel(label, status);
  const active = tone === "primary";
  return (
    <Badge tone={tone}>
      {active && <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />}
      {label}
    </Badge>
  );
}

export const ACCESS_LABELS: Record<string, { label: string; tone: Tone; help: string }> = {
  FULL_MEDIA_ACCESS: { label: "Full media", tone: "success", help: "Audio and video frames were available — full analysis and rendering." },
  REPLAY_ACCESSIBLE: { label: "Replay available", tone: "success", help: "The recording of the stream is available for full processing." },
  AUTHORIZED_LIVE_FEED: { label: "Live feed connected", tone: "success", help: "Your encoder is sending an authenticated live feed." },
  TRANSCRIPT_ONLY: { label: "Transcript only", tone: "warning", help: "Only the words were available: no visual analysis and no rendering." },
  METADATA_ONLY: { label: "Details only", tone: "warning", help: "Only the public details (title, author) were available — not the media." },
  LOGIN_REQUIRED: { label: "Sign-in needed", tone: "warning", help: "Connect the account that owns this content." },
  PERMISSION_REQUIRED: { label: "Permission needed", tone: "warning", help: "The platform only allows access to your own content." },
  PLATFORM_APPROVAL_REQUIRED: { label: "Platform approval needed", tone: "warning", help: "This feature needs the platform to approve the app." },
  SOURCE_PRIVATE: { label: "Private", tone: "danger", help: "The source is private." },
  SOURCE_UNAVAILABLE: { label: "Unavailable", tone: "danger", help: "The source could not be reached or no longer exists." },
  UNSUPPORTED_SOURCE: { label: "Unsupported", tone: "danger", help: "This kind of link isn't supported." },
  ACCESS_DENIED: { label: "Access denied", tone: "danger", help: "Access was refused." },
};

export function AccessBadge({ state }: { state?: string | null }) {
  if (!state) return null;
  const info = ACCESS_LABELS[state] ?? { label: state, tone: "neutral" as Tone };
  return <Badge tone={info.tone}>{info.label}</Badge>;
}

export const PIPELINE_STEPS = [
  { key: "Checking access", match: /(checking link|checking access)/i },
  { key: "Fetching source", match: /(fetching|preparing media|waiting for live|live)/i },
  { key: "Transcribing", match: /(transcrib)/i },
  { key: "Finding highlights", match: /(analyz|finding highlights)/i },
  { key: "Rendering", match: /(editing|render)/i },
  { key: "Ready", match: /(completed|clips ready|ready|publish|approval|scheduled)/i },
];

export function stepIndex(label: string): number {
  let index = 0;
  PIPELINE_STEPS.forEach((step, i) => {
    if (step.match.test(label)) index = i;
  });
  return index;
}

function bytes(value?: number | null): string {
  if (!value) return "0 MB";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function duration(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${Math.round(seconds % 60)} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** "120 MB of 260 MB · 5.2 MB/s · 35 s left · file 1 of 2" for a running download. */
export function liveDetailText(detail?: JobLiveDetail | null): string | null {
  if (!detail || detail.downloaded === undefined) return null;
  const parts = [detail.total ? `${bytes(detail.downloaded)} of ${bytes(detail.total)}` : bytes(detail.downloaded)];
  if (detail.speed) parts.push(`${bytes(detail.speed)}/s`);
  if (detail.eta !== null && detail.eta !== undefined) parts.push(`${duration(detail.eta)} left`);
  if (detail.parts && detail.parts > 1) parts.push(`${detail.part === 1 ? "video" : "audio"} (file ${detail.part} of ${detail.parts})`);
  return parts.join(" · ");
}

/** The step a workflow is working on right now: its own bar, percent and live figures. */
export function StepProgress({ step, className }: { step: JobStep; className?: string }) {
  // A download with a known size: the bar follows the bytes; otherwise the step's own progress.
  const bytesShare = step.total === 1 && step.detail?.total ? (step.detail.downloaded ?? 0) / step.detail.total : null;
  const percent = Math.floor(Math.min(1, bytesShare ?? step.progress) * 100);
  const live = liveDetailText(step.detail);
  return (
    <div className={clsx("rounded-xl bg-surface-2 px-3 py-2.5", className)} aria-live="polite">
      <div className="mb-1.5 flex items-center justify-between gap-3 text-[12px]">
        <span className="truncate font-medium text-text">
          {step.label}
          {step.total > 1 && <span className="font-normal text-muted"> · {step.done} of {step.total} done</span>}
        </span>
        <span className="shrink-0 tabular-nums text-muted">{step.status === "queued" ? "waiting for a free worker" : `${percent}%`}</span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-border/60" role="progressbar" aria-valuenow={percent}
        aria-valuemin={0} aria-valuemax={100} aria-label={step.label}>
        <div className="h-full rounded-full bg-primary transition-all duration-500" style={{ width: `${Math.max(2, Math.min(100, percent))}%` }} />
      </div>
      {live && <p className="mt-1.5 text-[11px] tabular-nums text-muted">{live}</p>}
    </div>
  );
}
