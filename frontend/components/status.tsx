import clsx from "clsx";

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
  if (/(queued|checking|fetching|transcrib|analyz|finding|editing|render|publishing|running|processing|receiving)/.test(text)) return "primary";
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
