"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowRight, Check, CheckCircle2, ExternalLink, RotateCcw, XCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AccessBadge, ACCESS_LABELS, PIPELINE_STEPS, StatusBadge, stepIndex } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, ProgressBar } from "@/components/ui";
import { api, platformName } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { JobDetail, ProjectDetail } from "@/lib/types";

const ACTIVE = ["queued", "running", "waiting", "paused"];

export function JobCard({ jobId, projectId }: { jobId: string; projectId: string | null }) {
  const { data: job, reload } = useResource<JobDetail>(`/jobs/${jobId}`, { poll: 2000 });
  const finished = job && !ACTIVE.includes(job.status);
  const awaitingApproval = job?.status === "waiting" && job.waiting?.type === "approval";
  const { data: project } = useResource<ProjectDetail>(projectId && (finished || awaitingApproval) ? `/projects/${projectId}` : null);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Record<string, boolean>>({});

  if (!job) {
    return <div className="skeleton mt-3 h-24 w-full max-w-xl" />;
  }

  const current = stepIndex(job.label);
  const failed = job.status === "failed" || job.status === "cancelled";
  const access = project?.access;
  const accessLimited = access && !access.capabilities.media && finished;

  async function retry() {
    setBusy(true);
    try {
      await api(`/jobs/${jobId}/retry`, { method: "POST" });
      toast("info", "Retrying from the last checkpoint.");
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Retry failed");
    } finally {
      setBusy(false);
    }
  }

  async function approve(publish: boolean) {
    const packages = job?.waiting?.packages ?? [];
    const approved = publish ? packages.filter((p) => selected[p.package_id] ?? (p.qc_passed && !p.review_words)).map((p) => p.package_id) : [];
    setBusy(true);
    try {
      await api(`/jobs/${jobId}/approve`, { body: { approved } });
      toast("success", approved.length ? `Publishing ${approved.length} package(s).` : "Finished without publishing.");
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Approval failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="animate-fade-up mt-3 w-full max-w-xl rounded-2xl border border-border bg-surface p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold">{project?.title ?? "Video project"}</p>
          <p className="mt-0.5 text-[12px] text-faint">{job.type === "workflow.video" ? "Production" : job.type}</p>
        </div>
        <StatusBadge label={job.label} status={job.status} />
      </div>

      {job.type === "workflow.video" && !failed && !accessLimited && (
        <div className="mt-4">
          <ProgressBar value={finished ? 1 : job.progress} tone={finished ? "success" : "primary"} />
          <ol className="mt-3 grid grid-cols-3 gap-x-2 gap-y-1.5 sm:grid-cols-6">
            {PIPELINE_STEPS.map((step, i) => {
              const done = finished ? true : i < current;
              const now = !finished && i === current;
              return (
                <li key={step.key} className={clsx("flex items-center gap-1 text-[11px]", done ? "text-success" : now ? "font-medium text-primary" : "text-faint")}>
                  {done ? <Check className="size-3" /> : <span className={clsx("size-1.5 rounded-full", now ? "bg-primary" : "bg-border")} />}
                  {step.key}
                </li>
              );
            })}
          </ol>
        </div>
      )}

      {failed && (
        <div className="mt-3 rounded-xl bg-danger-soft px-3 py-2.5 text-[13px] text-danger">
          <div className="flex items-start gap-2">
            <XCircle className="mt-0.5 size-4 shrink-0" />
            <span>{job.error?.message ?? "This job did not finish."}</span>
          </div>
          {job.status === "failed" && (
            <Button size="sm" variant="secondary" className="mt-2" icon={<RotateCcw className="size-3.5" />} loading={busy} onClick={retry}>
              Retry from checkpoint
            </Button>
          )}
        </div>
      )}

      {access && (accessLimited || (finished && access.state !== "FULL_MEDIA_ACCESS")) && (
        <div className="mt-3 rounded-xl border border-border bg-surface-2 p-3">
          <div className="flex items-center gap-2">
            <AccessBadge state={access.state} />
            <span className="text-[12px] text-muted">{ACCESS_LABELS[access.state]?.help}</span>
          </div>
          <ul className="mt-2 space-y-1 text-[13px] text-text">
            {access.reasons.map((reason) => (
              <li key={reason} className="flex gap-2">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
                {reason}
              </li>
            ))}
          </ul>
          {access.alternatives.length > 0 && (
            <div className="mt-3">
              <p className="text-[12px] font-medium text-muted">Simplest authorized options</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {access.alternatives.map((alt) => (
                  <Link key={alt.action} href={alt.href} className="rounded-lg border border-border bg-surface px-2.5 py-1 text-[12px] hover:border-primary hover:text-primary">
                    {alt.label}
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {awaitingApproval && (
        <div className="mt-3 rounded-xl border border-primary/30 bg-primary-soft/50 p-3">
          <p className="text-[13px] font-medium">Ready to publish — choose what goes live</p>
          <ul className="mt-2 space-y-1.5">
            {(job.waiting?.packages ?? []).map((pkg) => {
              const flagged = !pkg.qc_passed || pkg.review_words > 0;
              const checked = selected[pkg.package_id] ?? !flagged;
              return (
                <li key={pkg.package_id} className="flex items-center gap-2 text-[13px]">
                  <input type="checkbox" checked={checked} onChange={(e) => setSelected({ ...selected, [pkg.package_id]: e.target.checked })}
                    className="size-4 accent-[var(--primary)]" aria-label={`Publish clip ${pkg.rank} to ${platformName(pkg.platform)}`} />
                  <span className="font-medium">{platformName(pkg.platform)}</span>
                  <span className="truncate text-muted">#{pkg.rank} {pkg.title}</span>
                  {pkg.review_words > 0 && <span className="shrink-0 text-[11px] text-warning">check captions</span>}
                </li>
              );
            })}
          </ul>
          <div className="mt-3 flex gap-2">
            <Button size="sm" loading={busy} onClick={() => approve(true)}>Approve & publish</Button>
            <Button size="sm" variant="ghost" onClick={() => approve(false)}>Not now</Button>
          </div>
        </div>
      )}

      {finished && !failed && project && project.clips.length > 0 && (
        <div className="mt-3 flex items-center justify-between rounded-xl bg-success-soft px-3 py-2.5 text-[13px] text-success">
          <span className="flex items-center gap-2">
            <CheckCircle2 className="size-4" />
            {project.clips.length} clip{project.clips.length === 1 ? "" : "s"} {project.analysis_level === "transcript_only" ? "found (transcript only)" : "ready"}
          </span>
          <Link href={`/videos/${project.id}`} className="flex items-center gap-1 font-medium hover:underline">
            Review <ArrowRight className="size-3.5" />
          </Link>
        </div>
      )}
      {finished && project?.clips.some((c) => c.packages.some((p) => p.publications.some((pub) => pub.url))) && (
        <div className="mt-2 flex flex-wrap gap-2">
          {project.clips.flatMap((c) => c.packages.flatMap((p) => p.publications.filter((pub) => pub.url).map((pub) => (
            <a key={pub.id} href={pub.url!} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-[12px] text-primary hover:underline">
              {platformName(p.platform)} #{c.rank} <ExternalLink className="size-3" />
            </a>
          ))))}
        </div>
      )}
    </div>
  );
}
