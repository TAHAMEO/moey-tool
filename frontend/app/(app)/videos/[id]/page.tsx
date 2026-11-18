"use client";

import { AlertTriangle, ArrowLeft, ExternalLink, RotateCcw, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { JobCard } from "@/components/chat/JobCard";
import { ACCESS_LABELS, AccessBadge, StatusBadge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, Modal, Skeleton } from "@/components/ui";
import { ClipCard } from "@/components/videos/ClipCard";
import { api, formatDate } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { JobDetail, ProjectDetail } from "@/lib/types";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const toast = useToast();
  const { can } = useAuth();
  const { data: project, reload } = useResource<ProjectDetail>(`/projects/${id}`, { poll: 6000 });
  const [confirmDelete, setConfirmDelete] = useState(false);

  if (!project) {
    return <div className="mx-auto max-w-6xl space-y-4 px-4 py-8 sm:px-6"><Skeleton className="h-10 w-1/2" /><Skeleton className="h-64" /></div>;
  }
  const workflow = project.jobs.find((j) => j.type === "workflow.video");
  const running = workflow && ["queued", "running", "waiting", "paused"].includes(workflow.status);
  const access = project.access;

  async function remove() {
    await api(`/projects/${id}`, { method: "DELETE" });
    toast("success", "Project deleted.");
    router.replace("/videos");
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <Link href="/videos" className="mb-4 inline-flex items-center gap-1 text-[13px] text-muted hover:text-text"><ArrowLeft className="size-3.5" /> My Videos</Link>
      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">{project.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-muted">
            <StatusBadge label={project.status_label} status={project.status} />
            <AccessBadge state={access?.state ?? project.source?.access_status} />
            {project.source?.url && (
              <a href={project.source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-text">
                {project.source.platform} source <ExternalLink className="size-3" />
              </a>
            )}
            <span>· {formatDate(project.created_at)}</span>
          </div>
          {project.instruction && <p className="mt-2 max-w-2xl text-[13px] text-muted">“{project.instruction}”</p>}
        </div>
        {can("editor") && (
          <div className="flex gap-2">
            {workflow?.status === "failed" && (
              <Button variant="secondary" size="sm" icon={<RotateCcw className="size-3.5" />}
                onClick={async () => { await api(`/jobs/${workflow.id}/retry`, { method: "POST" }); toast("info", "Retrying from checkpoint."); reload(); }}>
                Retry
              </Button>
            )}
            <Button variant="ghost" size="sm" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmDelete(true)}>Delete</Button>
          </div>
        )}
      </div>

      {(project.limitations.length > 0 || (access && !access.capabilities.media)) && (
        <Card className="mb-6 border-warning/40 p-4">
          <div className="flex gap-3">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" />
            <div className="text-[13px]">
              <p className="font-semibold">{access ? ACCESS_LABELS[access.state]?.help ?? "Limited access" : "Limited access"}</p>
              <ul className="mt-1.5 space-y-1 text-muted">
                {[...new Set([...(project.limitations ?? []), ...(access?.reasons ?? [])])].map((line) => <li key={line}>{line}</li>)}
              </ul>
              {access && access.alternatives.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {access.alternatives.map((alt) => (
                    <Link key={alt.action} href={alt.href} className="rounded-lg border border-border bg-surface px-2.5 py-1 text-[12px] hover:border-primary hover:text-primary">{alt.label}</Link>
                  ))}
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {project.errors.length > 0 && (
        <Card className="mb-6 border-danger/40 p-4 text-[13px]">
          {project.errors.map((e, i) => <p key={i} className="text-danger">{e.message}</p>)}
        </Card>
      )}

      {running && workflow && <div className="mb-6"><JobCard jobId={workflow.id} projectId={project.id} /></div>}

      <div className="space-y-5">
        {project.clips.map((clip) => <ClipCard key={clip.id} clip={clip} onChanged={reload} />)}
        {!running && project.clips.length === 0 && project.errors.length === 0 && (
          <p className="text-sm text-muted">No clips were produced for this project.</p>
        )}
      </div>

      {(access?.checks.length || workflow) && (
        <details className="mt-8 rounded-xl border border-border bg-surface p-4 text-[12px]">
          <summary className="cursor-pointer font-medium text-muted">Advanced diagnostics</summary>
          {access && access.checks.length > 0 && (
            <>
              <p className="mt-3 font-medium">Access checks</p>
              <ul className="mt-1 space-y-1">
                {access.checks.map((c, i) => <li key={i}><span className="font-mono text-faint">{c.check}</span> — {c.result}</li>)}
              </ul>
              <p className="mt-2 text-faint">Method: {access.method ?? "none"} · capabilities: {Object.entries(access.capabilities).filter(([, v]) => v).map(([k]) => k).join(", ") || "none"}</p>
            </>
          )}
          {workflow && <JobTimeline jobId={workflow.id} />}
        </details>
      )}

      <Modal open={confirmDelete} onClose={() => setConfirmDelete(false)} title="Delete this project?"
        footer={<><Button variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button><Button variant="danger" onClick={remove}>Delete</Button></>}>
        <p className="text-sm text-muted">Rendered clips, captions and thumbnails of this project are deleted. Published videos stay on the platforms.</p>
      </Modal>
    </div>
  );
}

function JobTimeline({ jobId }: { jobId: string }) {
  const { data } = useResource<JobDetail>(`/jobs/${jobId}`);
  if (!data) return null;
  return (
    <>
      <p className="mt-4 font-medium">Job timeline <span className="font-normal text-faint">· {data.status} · attempt {data.attempts} of {data.max_attempts}</span></p>
      <ol className="mt-1 max-h-72 space-y-0.5 overflow-y-auto font-mono text-[11px]">
        {data.events.map((e, i) => (
          <li key={i} className={e.level === "error" ? "text-danger" : e.level === "warning" ? "text-warning" : "text-muted"}>
            <span className="text-faint">{new Date(e.at).toLocaleTimeString()}</span> {e.stage ? `[${e.stage}] ` : ""}{e.message}
          </li>
        ))}
      </ol>
      {data.children.length > 0 && (
        <p className="mt-2 text-faint">Steps: {data.children.map((c) => `${c.type.split(".").pop()} ${c.status}`).join(" · ")}</p>
      )}
    </>
  );
}
