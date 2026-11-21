"use client";

import { Clapperboard, FolderInput } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { AccessBadge, StatusBadge } from "@/components/status";
import { Button, EmptyState, PageHeader, Skeleton, Tabs } from "@/components/ui";
import { BatchesPanel, FolderBatchDialog } from "@/components/videos/FolderBatch";
import { timeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { ProjectSummary } from "@/lib/types";

type Filter = "all" | "active" | "ready" | "published" | "attention";

const FILTERS: Record<Filter, (p: ProjectSummary) => boolean> = {
  all: () => true,
  active: (p) => ["queued", "running", "publishing", "waiting"].includes(p.status),
  ready: (p) => ["ready", "completed", "awaiting_approval"].includes(p.status),
  published: (p) => ["published", "partially_published", "scheduled"].includes(p.status),
  attention: (p) => ["failed", "access_required", "unavailable", "awaiting_approval"].includes(p.status),
};

export default function VideosPage() {
  const { can } = useAuth();
  const [batch, setBatch] = useState<string | null>(null);
  const [folderDialog, setFolderDialog] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const { data, loading } = useResource<ProjectSummary[]>(`/projects?limit=200${batch ? `&batch_id=${batch}` : ""}`, { poll: 8000 });
  const [filter, setFilter] = useState<Filter>("all");
  const items = (data ?? []).filter(FILTERS[filter]);
  const count = (f: Filter) => (data ?? []).filter(FILTERS[f]).length;

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <PageHeader title="My Videos" description="Every source you've sent, the clips made from it, and where they were published."
        action={can("editor") ? <Button icon={<FolderInput className="size-4" />} onClick={() => setFolderDialog(true)}>Process a folder</Button> : undefined} />
      <BatchesPanel refreshKey={refreshKey} onFilter={setBatch} activeFilter={batch} />
      <FolderBatchDialog open={folderDialog} onClose={() => setFolderDialog(false)} onStarted={() => setRefreshKey((k) => k + 1)} />
      <Tabs value={filter} onChange={setFilter} tabs={[
        { value: "all", label: "All", count: count("all") }, { value: "active", label: "In progress", count: count("active") },
        { value: "ready", label: "Ready", count: count("ready") }, { value: "published", label: "Published", count: count("published") },
        { value: "attention", label: "Needs attention", count: count("attention") },
      ]} />
      {loading && (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="aspect-[3/4]" />)}
        </div>
      )}
      {!loading && items.length === 0 && (
        <EmptyState icon={<Clapperboard className="size-5" />} title="Nothing here yet"
          description="Paste a YouTube or Instagram link in Brain Chat and your clips will appear here." action={<Link href="/" className="text-sm font-medium text-primary">Open Brain Chat →</Link>} />
      )}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {items.map((p) => (
          <Link key={p.id} href={`/videos/${p.id}`} className="group animate-fade-up overflow-hidden rounded-2xl border border-border bg-surface shadow-card transition-colors hover:border-primary/40">
            <div className="relative aspect-[3/4] bg-surface-2">
              {p.thumbnail_url
                ? <img src={p.thumbnail_url} alt="" className="size-full object-cover" />
                : <div className="flex size-full items-center justify-center text-faint"><Clapperboard className="size-7" /></div>}
              <div className="absolute left-2 top-2"><StatusBadge label={p.status_label} status={p.status} /></div>
            </div>
            <div className="p-3">
              <p className="line-clamp-2 text-[13px] font-medium leading-snug group-hover:text-primary">{p.title}</p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <AccessBadge state={p.source?.access_status} />
                <span className="text-[11px] text-faint">{p.clips} clip{p.clips === 1 ? "" : "s"} · {timeAgo(p.created_at)}</span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
