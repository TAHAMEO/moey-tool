"use client";

import { Activity, CalendarClock, CheckCircle2, Circle, Clapperboard, DollarSign, Gauge, OctagonPause, Pause, Play, Send, ShieldAlert, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { formatWhen } from "@/components/schedule";
import { Badge, StatusBadge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, EmptyState, Modal, PageHeader, ProgressBar, SectionHeader, Skeleton, Stat } from "@/components/ui";
import { api, money, platformName, timeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { JobSummary, ProjectSummary, ScheduleItem } from "@/lib/types";

const JOB_NAMES: Record<string, string> = {
  "workflow.video": "Video production", "workflow.publish": "Publishing", "batch.ingest_file": "Taking in a video from a watched folder",
  "media.render": "Re-rendering a clip", "live.process": "Live stream",
};

type Usage = { today_usd: number; month_usd: number; budgets: { daily: number; monthly: number }; cache_hits_month: number };
type Connections = { accounts: { platform: string; status: string }[]; youtube_quota_remaining: number };
type Diagnostics = Record<string, { ok?: boolean; [k: string]: unknown }>;

/** First-run guidance; disappears when everything is done (or when dismissed on this device). */
function SetupChecklist({ connections, projects, diagnostics }: {
  connections: Connections | null; projects: ProjectSummary[] | null; diagnostics: Diagnostics | null;
}) {
  const { can } = useAuth();
  const { data: providers } = useResource<{ connections: unknown[] }>("/providers");
  const [dismissed, setDismissed] = useState(true);
  useEffect(() => {
    try { setDismissed(localStorage.getItem("cf-setup-dismissed") === "1"); } catch { setDismissed(false); }
  }, []);
  if (!connections || !projects || !providers || dismissed) return null;
  // Transcription runs in the media workers, which report their engines in their heartbeats.
  const engines = (diagnostics?.workers as { media_transcription_engines?: string[] } | undefined)?.media_transcription_engines;
  const has = (platform: string) => connections.accounts.some((a) => a.platform === platform);
  const steps = [
    ...(can("admin") && engines ? [{ done: engines.length > 0, label: "Install the speech-recognition model",
      hint: "On the server: docker compose exec worker clipforge download-models --vosk", href: null }] : []),
    { done: providers.connections.length > 0, label: "Connect an AI service (optional)",
      hint: "Smarter titles and answers. Everything else already runs locally.", href: "/connections" },
    { done: has("youtube"), label: "Connect your YouTube channel", hint: "To publish Shorts and read analytics.", href: "/settings#connections" },
    { done: has("instagram"), label: "Connect Instagram", hint: "A Business or Creator account, to publish Reels.", href: "/settings#connections" },
    { done: projects.length > 0, label: "Make your first clips", hint: "Paste a link in Brain Chat.", href: "/" },
  ];
  if (steps.every((step) => step.done)) return null;
  const doneCount = steps.filter((step) => step.done).length;
  return (
    <Card className="mt-6 p-5">
      <div className="flex items-start justify-between gap-3">
        <SectionHeader title="Get set up" description={`${doneCount} of ${steps.length} done`} />
        <button aria-label="Hide setup steps" className="rounded-lg p-1 text-faint hover:bg-surface-2 hover:text-text"
          onClick={() => { try { localStorage.setItem("cf-setup-dismissed", "1"); } catch { /* private mode */ } setDismissed(true); }}>
          <X className="size-4" />
        </button>
      </div>
      <ul className="space-y-2.5">
        {steps.map((step) => (
          <li key={step.label} className="flex items-start gap-2.5">
            {step.done ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <Circle className="mt-0.5 size-4 shrink-0 text-faint" />}
            <div className="min-w-0 text-[13px]">
              {step.href && !step.done
                ? <Link href={step.href} className="font-medium hover:text-primary">{step.label} →</Link>
                : <span className={step.done ? "text-muted line-through" : "font-medium"}>{step.label}</span>}
              {!step.done && <p className="mt-0.5 break-words text-[12px] text-muted">{step.hint}</p>}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}

/** Scheduled (and missed) posts, soonest first, with how each one will go out. */
function UpcomingPosts() {
  const { data } = useResource<{ timezone: string; items: ScheduleItem[] }>("/schedule", { poll: 30000 });
  if (!data || data.items.length === 0) return null;
  return (
    <Card className="p-5">
      <SectionHeader title="Upcoming posts" description={`Times shown in your time zone. Schedules use ${data.timezone} for “tomorrow at 6pm” and daily limits.`} />
      <ul className="divide-y divide-border">
        {data.items.slice(0, 12).map((item) => (
          <li key={item.package_id} className="flex items-center gap-3 py-2.5">
            <CalendarClock className={`size-4 shrink-0 ${item.status === "missed" ? "text-warning" : "text-primary"}`} />
            <div className="min-w-0 flex-1">
              <Link href={`/videos/${item.project_id}`} className="block truncate text-[13px] font-medium hover:text-primary">{item.title}</Link>
              <p className="truncate text-[12px] text-muted">
                {platformName(item.platform)} · {item.status === "missed" ? `missed (${formatWhen(item.scheduled_at)})` : formatWhen(item.scheduled_at)}
                {!item.owner_time && item.status !== "missed" ? " · next free slot" : ""}
              </p>
            </div>
            {item.status === "missed" ? <Badge tone="warning">Pick a new time</Badge>
              : item.on_hold ? <Badge tone="warning">On hold</Badge>
              : item.mode === "youtube" ? <Badge tone="success">{item.uploaded ? "With YouTube" : "YouTube schedules it"}</Badge>
              : <Badge tone="info">CLIPFORGE posts it</Badge>}
          </li>
        ))}
      </ul>
      {data.items.some((i) => i.mode === "held" && i.status === "scheduled") && (
        <p className="mt-3 text-[12px] text-faint">“CLIPFORGE posts it” needs CLIPFORGE running at that time (on Windows: the PC on and Docker Desktop started).</p>
      )}
    </Card>
  );
}

export default function DashboardPage() {
  const { can } = useAuth();
  const toast = useToast();
  const { data: jobs } = useResource<JobSummary[]>("/jobs?active=true", { poll: 3000 });
  const { data: projects } = useResource<ProjectSummary[]>("/projects?limit=8", { poll: 10000 });
  const { data: usage } = useResource<Usage>("/usage/summary", { poll: 30000 });
  const { data: connections } = useResource<Connections>("/connections");
  const { data: settings, reload: reloadSettings } = useResource<{ control: { paused: boolean; emergency_stop: boolean } }>("/settings");
  const { data: diagnostics } = useResource<Diagnostics>(can("admin") ? "/diagnostics" : null, { poll: 30000 });
  const [confirmStop, setConfirmStop] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  async function control(action: string) {
    setBusy(action);
    try {
      const result = await api<{ cancelled_jobs?: number; unscheduled_posts?: number }>(`/control/${action}`, { method: "POST" });
      toast("success", action === "emergency_stop"
        ? `Emergency stop on. ${result.cancelled_jobs ?? 0} job(s) and ${result.unscheduled_posts ?? 0} scheduled post(s) cancelled.` : "Updated.");
      reloadSettings();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy(null);
      setConfirmStop(false);
    }
  }

  const ready = projects?.filter((p) => ["ready", "completed", "published", "partially_published"].includes(p.status)).length ?? 0;
  const published = projects?.filter((p) => p.status === "published").length ?? 0;
  const control_ = settings?.control;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <PageHeader title="Dashboard" description="Everything CLIPFORGE is doing, at a glance." />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Active jobs" value={jobs ? jobs.length : "—"} icon={<Activity className="size-4" />}
          hint={jobs?.some((j) => j.status === "waiting") ? "some are waiting for you" : "running now"} />
        <Stat label="Projects ready" value={projects ? ready : "—"} icon={<Clapperboard className="size-4" />} hint={`${published} published`} />
        <Stat label="AI spend this month" value={usage ? money(usage.month_usd) : "—"} icon={<DollarSign className="size-4" />}
          hint={usage ? `budget ${money(usage.budgets.monthly)} · ${usage.cache_hits_month} cached answers` : undefined} />
        <Stat label="YouTube quota left" value={connections ? connections.youtube_quota_remaining.toLocaleString() : "—"}
          icon={<Gauge className="size-4" />} hint="units today (resets midnight PT)" />
      </div>

      <SetupChecklist connections={connections} projects={projects} diagnostics={diagnostics} />

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card className="p-5">
            <SectionHeader title="Working on now" description="Live progress of every running workflow." />
            {!jobs && <Skeleton className="h-16" />}
            {jobs && jobs.length === 0 && <p className="text-sm text-muted">Nothing running. Paste a link in Brain Chat to start.</p>}
            <ul className="divide-y divide-border">
              {jobs?.map((job) => (
                <li key={job.id} className="py-3">
                  <div className="flex items-center justify-between gap-3">
                    <Link href={job.project_id ? `/videos/${job.project_id}` : "#"} className="truncate text-sm font-medium hover:text-primary">
                      {JOB_NAMES[job.type] ?? "Background task"}
                    </Link>
                    <StatusBadge label={job.label} status={job.status} />
                  </div>
                  <div className="mt-2 flex items-center gap-3">
                    <ProgressBar value={job.progress} />
                    <span className="w-16 shrink-0 text-right text-[12px] text-faint">{timeAgo(job.created_at)}</span>
                  </div>
                </li>
              ))}
            </ul>
          </Card>

          <UpcomingPosts />

          <Card className="p-5">
            <SectionHeader title="Recent projects" action={<Link href="/videos" className="text-[13px] font-medium text-primary">All videos →</Link>} />
            {projects && projects.length === 0 && (
              <EmptyState icon={<Clapperboard className="size-5" />} title="No projects yet" description="Send a video link in Brain Chat." />
            )}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {projects?.map((p) => (
                <Link key={p.id} href={`/videos/${p.id}`} className="group">
                  <div className="aspect-[9/12] overflow-hidden rounded-xl border border-border bg-surface-2">
                    {p.thumbnail_url
                      ? <img src={p.thumbnail_url} alt="" className="size-full object-cover transition-transform group-hover:scale-[1.02]" />
                      : <div className="flex size-full items-center justify-center text-faint"><Clapperboard className="size-6" /></div>}
                  </div>
                  <p className="mt-1.5 truncate text-[13px] font-medium">{p.title}</p>
                  <StatusBadge label={p.status_label} status={p.status} />
                </Link>
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card className="p-5">
            <SectionHeader title="Controls" description="Stop everything instantly if you need to." />
            {control_ && (
              <div className="space-y-2">
                {control_.paused
                  ? <Button className="w-full" variant="secondary" icon={<Play className="size-4" />} loading={busy === "resume"} onClick={() => control("resume")} disabled={!can("admin")}>Resume automation</Button>
                  : <Button className="w-full" variant="secondary" icon={<Pause className="size-4" />} loading={busy === "pause"} onClick={() => control("pause")} disabled={!can("admin")}>Pause automation</Button>}
                {control_.emergency_stop
                  ? <Button className="w-full" variant="secondary" icon={<ShieldAlert className="size-4" />} loading={busy === "clear_emergency"} onClick={() => control("clear_emergency")} disabled={!can("admin")}>Clear emergency stop</Button>
                  : <Button className="w-full" variant="danger" icon={<OctagonPause className="size-4" />} onClick={() => setConfirmStop(true)} disabled={!can("admin")}>Emergency stop</Button>}
              </div>
            )}
            {usage && (
              <div className="mt-5">
                <div className="flex justify-between text-[13px]"><span className="text-muted">Today</span><span>{money(usage.today_usd)} / {money(usage.budgets.daily)}</span></div>
                <div className="mt-1.5"><ProgressBar value={usage.budgets.daily ? usage.today_usd / usage.budgets.daily : 0} tone={usage.today_usd > usage.budgets.daily * 0.8 ? "warning" : "primary"} /></div>
              </div>
            )}
          </Card>

          <Card className="p-5">
            <SectionHeader title="Accounts" />
            <ul className="space-y-2 text-[13px]">
              {["youtube", "instagram", "google_drive"].map((platform) => {
                const account = connections?.accounts.find((a) => a.platform === platform);
                return (
                  <li key={platform} className="flex items-center justify-between">
                    <span>{platformName(platform)}</span>
                    {account ? <StatusBadge label={account.status === "connected" ? "Connected" : "Reconnect"} /> : <Link href="/settings#connections" className="text-primary">Connect</Link>}
                  </li>
                );
              })}
            </ul>
            <Link href="/settings#connections" className="mt-4 flex items-center gap-1.5 text-[13px] font-medium text-primary"><Send className="size-3.5" /> Manage publishing</Link>
          </Card>

          {diagnostics && (
            <Card className="p-5">
              <SectionHeader title="System health" />
              <ul className="space-y-1.5 text-[13px]">
                {["database", "workers", "ffmpeg", "redis", "disk"].map((key) => (
                  <li key={key} className="flex items-center justify-between">
                    <span className="capitalize text-muted">{key}</span>
                    <StatusBadge label={diagnostics[key]?.ok ? "OK" : key === "redis" ? "Degraded (optional)" : "Check"} />
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>

      <Modal open={confirmStop} onClose={() => setConfirmStop(false)} title="Emergency stop?"
        footer={<><Button variant="ghost" onClick={() => setConfirmStop(false)}>Cancel</Button>
          <Button variant="danger" loading={busy === "emergency_stop"} onClick={() => control("emergency_stop")}>Stop everything</Button></>}>
        <p className="text-sm text-muted">This cancels every running and queued job and every scheduled post, blocks publishing and stops all calls to external AI providers until you clear it. Videos already waiting on YouTube are switched to private.</p>
      </Modal>
    </div>
  );
}
