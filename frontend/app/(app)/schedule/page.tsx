"use client";

import { CalendarClock, CalendarPlus, CheckCircle2, Clapperboard, Link2, PlaySquare, Send, Trash2 } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { choiceIso, choiceProblem, defaultChoice, formatWhen, SchedulePicker, type ScheduleChoice } from "@/components/schedule";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, EmptyState, Modal, PageHeader, SectionHeader, Skeleton } from "@/components/ui";
import { api, platformName } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { ConnectionsResponse, ReadyPost, ScheduleItem, ScheduleOverview } from "@/lib/types";

/** "Today", "Tomorrow", or "Monday 24 November" in the viewer's time zone. */
function dayLabel(value: string | null): string {
  if (!value) return "Next free slot";
  const date = new Date(value);
  const start = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((start(date) - start(new Date())) / 86_400_000);
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return date.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

const time = (value: string | null) =>
  value ? new Date(value).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "—";

function Thumb({ url }: { url: string | null }) {
  return url
    // eslint-disable-next-line @next/next/no-img-element
    ? <img src={url} alt="" className="h-14 w-10 shrink-0 rounded-md bg-surface-2 object-cover" />
    : <div className="flex h-14 w-10 shrink-0 items-center justify-center rounded-md bg-surface-2"><Clapperboard className="size-4 text-faint" /></div>;
}

type Target = { posts: { package_id: string; platform: string; privacy: string; title: string }[]; current?: string | null };

export default function SchedulePage() {
  const { can } = useAuth();
  const toast = useToast();
  const { data, reload } = useResource<ScheduleOverview>("/schedule", { poll: 20000 });
  const { data: connections } = useResource<ConnectionsResponse>("/connections");
  const [target, setTarget] = useState<Target | null>(null);
  const [when, setWhen] = useState<ScheduleChoice>(defaultChoice(null));
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);

  const groups = useMemo(() => {
    const out: { label: string; items: ScheduleItem[] }[] = [];
    for (const item of data?.items ?? []) {
      const label = item.status === "missed" ? "Missed — pick a new time" : dayLabel(item.scheduled_at);
      const last = out[out.length - 1];
      if (last && last.label === label) last.items.push(item);
      else out.push({ label, items: [item] });
    }
    return out;
  }, [data]);

  const connected = (platform: string) =>
    !connections || connections.accounts.some((a) => a.platform === platform && a.status !== "revoked");

  function open(posts: Target["posts"], current?: string | null) {
    setWhen({ ...defaultChoice(current ?? null), mode: "later" });
    setTarget({ posts, current });
  }

  async function submit() {
    if (!target) return;
    const start = choiceIso(when);
    setBusy(true);
    let done = 0;
    try {
      for (const [index, post] of target.posts.entries()) {
        // Several clips: the first at the chosen time, the next ones spaced out as chosen.
        const at = start && when.everyMinutes ? new Date(new Date(start).getTime() + index * when.everyMinutes * 60_000).toISOString() : start;
        await api(`/packages/${post.package_id}/publish`, { method: "POST", body: at ? { scheduled_at: at } : {} });
        done += 1;
      }
      toast("success", start
        ? `${done === 1 ? "Scheduled" : `${done} posts scheduled`} — first at ${formatWhen(start)}.`
        : `${done === 1 ? "Publishing started" : `Publishing ${done} posts`}. Each is marked published once the platform confirms.`);
      setTarget(null);
      setSelected([]);
      reload();
    } catch (err) {
      toast("error", `${done ? `${done} done, then: ` : ""}${err instanceof Error ? err.message : "Could not schedule"}`);
      reload();
    } finally {
      setBusy(false);
    }
  }

  async function cancel(item: ScheduleItem) {
    try {
      await api(`/packages/${item.package_id}/unschedule`, { method: "POST" });
      toast("success", item.uploaded ? "Schedule cancelled. The upload stays on YouTube as a private video." : "Schedule cancelled.");
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not cancel");
    }
  }

  if (!data) {
    return <div className="mx-auto max-w-5xl space-y-4 px-4 py-8 sm:px-6"><Skeleton className="h-10 w-48" /><Skeleton className="h-64" /></div>;
  }

  const editor = can("editor");
  const readySelected = data.ready.filter((p) => selected.includes(p.package_id));
  const missingAccounts = ["youtube", "instagram"].filter((p) => !connected(p));

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <PageHeader title="Schedule"
        description={`Pick the exact day and time each clip goes out on YouTube or Instagram. Times are in your time zone (${data.timezone}).`} />

      {missingAccounts.length > 0 && (
        <Card className="mb-6 flex flex-wrap items-center gap-3 p-4">
          <Link2 className="size-4 text-primary" />
          <p className="flex-1 text-[13px]">
            Connect {missingAccounts.map(platformName).join(" and ")} to post there.
          </p>
          <Link href="/settings#connections"><Button size="sm" variant="secondary">Connect accounts</Button></Link>
        </Card>
      )}

      <Card className="mb-6 p-5">
        <SectionHeader title="Upcoming posts" description="Soonest first. Change a time or cancel it here." />
        {groups.length === 0 ? (
          <EmptyState icon={<CalendarClock className="size-5" />} title="Nothing scheduled yet"
            description={data.ready.length ? "Pick clips below and give them a date and time." : "Once CLIPFORGE has made clips from a video, they appear below, ready to schedule."} />
        ) : groups.map((group) => (
          <div key={group.label} className="mt-4 first:mt-1">
            <p className={`mb-1 text-[12px] font-semibold uppercase tracking-wide ${group.label.startsWith("Missed") ? "text-warning" : "text-muted"}`}>{group.label}</p>
            <ul className="divide-y divide-border">
              {group.items.map((item) => (
                <li key={item.package_id} className="flex items-center gap-3 py-2.5">
                  <span className="w-12 shrink-0 text-[13px] font-semibold tabular-nums">{item.status === "missed" ? "—" : time(item.scheduled_at)}</span>
                  <Thumb url={item.thumbnail_url} />
                  <div className="min-w-0 flex-1">
                    <Link href={`/videos/${item.project_id}`} className="block truncate text-[13px] font-medium hover:text-primary">{item.title}</Link>
                    <p className="truncate text-[12px] text-muted">
                      {platformName(item.platform)} · {item.project_title}
                      {!item.owner_time && item.status !== "missed" ? " · next free slot" : ""}
                    </p>
                    {item.last_error && <p className="truncate text-[12px] text-danger">{item.last_error}</p>}
                  </div>
                  {item.status === "missed" ? <Badge tone="warning">Missed</Badge>
                    : item.on_hold ? <Badge tone="warning">On hold</Badge>
                    : item.mode === "youtube" ? <Badge tone="success">{item.uploaded ? "With YouTube" : "YouTube posts it"}</Badge>
                    : <Badge tone="info">CLIPFORGE posts it</Badge>}
                  {editor && (
                    <div className="flex shrink-0 gap-1">
                      <Button size="sm" variant="secondary" onClick={() => open([{ package_id: item.package_id, platform: item.platform, privacy: item.privacy, title: item.title }], item.scheduled_at)}>
                        {item.status === "missed" ? "New time" : "Change"}
                      </Button>
                      <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => cancel(item)} aria-label="Cancel this post" />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {data.items.some((i) => i.mode === "held" && i.status === "scheduled") && (
          <p className="mt-3 text-[12px] text-faint">“CLIPFORGE posts it” needs this PC on and CLIPFORGE running at that time. Public YouTube videos are posted by YouTube itself, even with the PC off.</p>
        )}
      </Card>

      <Card className="mb-6 p-5">
        <SectionHeader title="Ready to schedule"
          description="Clips CLIPFORGE has finished. Tick several to schedule them in one go, spaced out."
          action={editor && data.ready.length > 0 ? (
            <Button size="sm" icon={<CalendarPlus className="size-3.5" />} disabled={readySelected.length === 0}
              onClick={() => open(readySelected)}>
              Schedule {readySelected.length > 0 ? readySelected.length : ""} selected
            </Button>
          ) : undefined} />
        {data.ready.length === 0 ? (
          <EmptyState icon={<Clapperboard className="size-5" />} title="No finished clips waiting"
            description="Add a video in My Videos (or process a folder); its clips land here when they're done."
            action={<Link href="/videos"><Button size="sm" variant="secondary">Go to My Videos</Button></Link>} />
        ) : (
          <ul className="divide-y divide-border">
            {data.ready.map((post: ReadyPost) => (
              <li key={post.package_id} className="flex items-center gap-3 py-2.5">
                {editor && (
                  <input type="checkbox" className="size-4 accent-[var(--color-primary)]" aria-label={`Select ${post.title}`}
                    checked={selected.includes(post.package_id)}
                    onChange={(e) => setSelected((s) => e.target.checked ? [...s, post.package_id] : s.filter((id) => id !== post.package_id))} />
                )}
                <Thumb url={post.thumbnail_url} />
                <div className="min-w-0 flex-1">
                  <Link href={`/videos/${post.project_id}`} className="block truncate text-[13px] font-medium hover:text-primary">{post.title}</Link>
                  <p className="truncate text-[12px] text-muted">
                    {platformName(post.platform)} · {post.duration_s ? `${Math.round(post.duration_s)} s · ` : ""}{post.project_title}
                  </p>
                </div>
                {post.status === "failed" && <Badge tone="danger">Last attempt failed</Badge>}
                {editor && (
                  <Button size="sm" variant="secondary" icon={<CalendarClock className="size-3.5" />}
                    onClick={() => open([{ package_id: post.package_id, platform: post.platform, privacy: post.privacy, title: post.title }])}>
                    Schedule…
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {data.recent.length > 0 && (
        <Card className="p-5">
          <SectionHeader title="Recently posted" description="The last two weeks." />
          <ul className="divide-y divide-border">
            {data.recent.map((post) => (
              <li key={post.package_id} className="flex items-center gap-3 py-2">
                {post.platform === "youtube" ? <PlaySquare className="size-4 text-faint" /> : <Send className="size-4 text-faint" />}
                <Link href={`/videos/${post.project_id}`} className="min-w-0 flex-1 truncate text-[13px] hover:text-primary">{post.title}</Link>
                <span className="text-[12px] text-muted">{formatWhen(post.published_at)}</span>
                <CheckCircle2 className="size-4 text-success" />
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Modal open={Boolean(target)} onClose={() => setTarget(null)}
        title={target && target.posts.length > 1 ? `Schedule ${target.posts.length} clips` : "When should it go out?"}
        footer={(
          <>
            <Button variant="ghost" onClick={() => setTarget(null)}>Close</Button>
            <Button loading={busy} disabled={Boolean(choiceProblem(when))} onClick={submit}>
              {when.mode === "now" ? "Publish now" : "Schedule"}
            </Button>
          </>
        )}>
        {target && (
          <div className="space-y-3">
            <p className="truncate text-[13px] text-muted">{target.posts.length === 1 ? target.posts[0].title : `${target.posts.length} clips, starting at the time you pick.`}</p>
            <SchedulePicker value={when} onChange={setWhen}
              platforms={[...new Set(target.posts.map((p) => p.platform))]}
              privacy={target.posts.every((p) => p.privacy === "public") ? "public" : target.posts[0]?.privacy}
              clips={target.posts.length} />
          </div>
        )}
      </Modal>
    </div>
  );
}
