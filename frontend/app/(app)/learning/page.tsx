"use client";

import clsx from "clsx";
import { Brain, Check, Download, Upload, FlaskConical, Lightbulb, Pencil, Play, Search, Sparkles, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, EmptyState, Input, PageHeader, SectionHeader, Skeleton, Tabs, Textarea, Toggle } from "@/components/ui";
import { api, formatDate, platformName, timeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { ABTestResult, AnalyticsSummary, CostReport, Experiment, KnowledgeItem, LearningRecord, MemoryItem, PublicationAnalytics, RetentionReport, Strategy } from "@/lib/types";

type Tab = "insights" | "strategy" | "memory" | "knowledge";

export default function LearningPage() {
  const [tab, setTab] = useState<Tab>("insights");
  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <PageHeader title="Learning Center" description="What CLIPFORGE has learned from your published videos, how it's testing improvements, and everything it remembers." />
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: "insights", label: "Insights" }, { value: "strategy", label: "Strategy & tests" },
        { value: "memory", label: "Memory" }, { value: "knowledge", label: "Knowledge" },
      ]} />
      {tab === "insights" && <Insights />}
      {tab === "strategy" && <StrategyTab />}
      {tab === "memory" && <MemoryTab />}
      {tab === "knowledge" && <KnowledgeTab />}
    </div>
  );
}

function confidenceLabel(c: number) {
  return c >= 0.75 ? "High confidence" : c >= 0.45 ? "Medium confidence" : "Low confidence";
}

function Insights() {
  const toast = useToast();
  const { can } = useAuth();
  const { data: summary } = useResource<AnalyticsSummary>("/analytics/summary");
  const { data: records, loading, reload } = useResource<LearningRecord[]>("/learning/records");
  const { data: pubs } = useResource<PublicationAnalytics[]>("/analytics/publications");
  const platforms = Object.entries(summary?.platforms ?? {});

  async function runNow() {
    await api("/learning/run", { method: "POST" });
    toast("info", "Analysis started — new findings appear here in a minute.");
    setTimeout(reload, 8000);
  }

  async function review(id: string, status: "active" | "rejected") {
    await api(`/learning/records/${id}`, { method: "PATCH", body: { status } });
    reload();
  }

  return (
    <div className="space-y-10">
      <section>
        <SectionHeader title="Performance" description="Only numbers the platforms actually report. Nothing is estimated or filled in."
          action={can("editor") && <Button size="sm" variant="secondary" icon={<Play className="size-3.5" />} onClick={runNow}>Analyse now</Button>} />
        {platforms.length === 0 ? (
          <Card className="p-5 text-sm text-muted">{summary?.text ?? "Loading…"}</Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {platforms.map(([platform, p]) => (
              <Card key={platform} className="p-5">
                <p className="text-sm font-semibold">{platform === "youtube" ? "YouTube Shorts" : "Instagram Reels"}</p>
                <div className="mt-3 flex gap-6">
                  <div><p className="text-2xl font-semibold tabular-nums">{Math.round(p.median_views).toLocaleString()}</p><p className="text-[12px] text-muted">median views</p></div>
                  {p.median_engagement_rate !== null && (
                    <div><p className="text-2xl font-semibold tabular-nums">{(p.median_engagement_rate * 100).toFixed(1)}%</p><p className="text-[12px] text-muted">median engagement</p></div>
                  )}
                  <div><p className="text-2xl font-semibold tabular-nums">{p.published_with_data}</p><p className="text-[12px] text-muted">videos with data</p></div>
                </div>
                <p className="mt-4 text-[12px] font-medium text-muted">Best so far</p>
                <ul className="mt-1 space-y-1 text-[13px]">
                  {p.top.map((t) => <li key={t.title} className="flex justify-between gap-3"><span className="truncate">{t.title}</span><span className="shrink-0 tabular-nums text-muted">{t.views.toLocaleString()} · {Math.round(t.age_hours)}h</span></li>)}
                </ul>
              </Card>
            ))}
          </div>
        )}
      </section>

      <RetentionSection />

      <TestsAndCosts />

      <section>
        <SectionHeader title="Findings" description="Patterns found in your results. A pattern is not proof — CLIPFORGE tests promising ones before changing its strategy." />
        {loading && <Skeleton className="h-28" />}
        {!loading && (records ?? []).length === 0 && (
          <EmptyState icon={<Lightbulb className="size-5" />} title="No findings yet"
            description="Findings appear once enough published videos have analytics. CLIPFORGE needs several videos per platform before it compares anything." />
        )}
        <div className="space-y-3">
          {(records ?? []).map((r) => (
            <Card key={r.id} className={clsx("p-4", r.status === "rejected" && "opacity-60")}>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={r.causal_claim ? "success" : "info"}>{r.causal_claim ? "Tested — confirmed" : r.kind === "experiment_result" ? "Test result" : "Pattern (correlation)"}</Badge>
                <Badge>{confidenceLabel(r.confidence)}</Badge>
                {r.platform && <Badge>{platformName(r.platform)}</Badge>}
                {r.status === "rejected" && <Badge tone="danger">Dismissed</Badge>}
                <span className="ml-auto text-[12px] text-faint">{timeAgo(r.created_at)}</span>
              </div>
              <p className="mt-2 text-sm leading-relaxed">{r.finding}</p>
              {r.limitations && <p className="mt-1.5 text-[12px] text-muted">Limitations: {r.limitations}</p>}
              {r.next_experiment && <p className="mt-1 text-[12px] text-muted">Next step: {r.next_experiment}</p>}
              {can("editor") && (
                <div className="mt-3 flex gap-2">
                  {r.status !== "rejected"
                    ? <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} onClick={() => review(r.id, "rejected")}>Dismiss</Button>
                    : <Button size="sm" variant="ghost" icon={<Check className="size-3.5" />} onClick={() => review(r.id, "active")}>Restore</Button>}
                </div>
              )}
            </Card>
          ))}
        </div>
      </section>

      {(pubs ?? []).length > 0 && (
        <section>
          <SectionHeader title="Published videos" description="Latest numbers per video. Metrics a platform doesn't provide are listed as unavailable, not zero." />
          <Card className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-border text-[12px] text-muted">
                <tr><th className="px-4 py-2.5 font-medium">Video</th><th className="px-4 py-2.5 font-medium">Views</th><th className="px-4 py-2.5 font-medium">Likes</th><th className="px-4 py-2.5 font-medium">Comments</th><th className="px-4 py-2.5 font-medium">Updated</th></tr>
              </thead>
              <tbody>
                {(pubs ?? []).map((p) => {
                  const last = p.snapshots[p.snapshots.length - 1];
                  const m = last?.metrics ?? {};
                  const cell = (key: string) => key in m ? Number(m[key]).toLocaleString() : last && key in (last.unavailable ?? {}) ? <span className="text-faint" title={last.unavailable[key]}>n/a</span> : "—";
                  return (
                    <tr key={p.id} className="border-b border-border last:border-0">
                      <td className="max-w-72 px-4 py-2.5">
                        <a href={p.url ?? undefined} target="_blank" rel="noreferrer" className="line-clamp-1 font-medium hover:text-primary">{p.title}</a>
                        <span className="text-[12px] text-faint">{platformName(p.platform)} · {p.status}</span>
                      </td>
                      <td className="px-4 py-2.5 tabular-nums">{cell("views")}</td>
                      <td className="px-4 py-2.5 tabular-nums">{cell("likes")}</td>
                      <td className="px-4 py-2.5 tabular-nums">{cell("comments")}</td>
                      <td className="px-4 py-2.5 text-muted">{last ? timeAgo(last.at) : "not yet"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </Card>
        </section>
      )}
    </div>
  );
}

function StrategyTab() {
  const toast = useToast();
  const { can } = useAuth();
  const { data: strategies, reload } = useResource<Strategy[]>("/learning/strategies");
  const { data: experiments } = useResource<Experiment[]>("/learning/experiments");
  const nameOf = (id: string) => {
    const s = (strategies ?? []).find((x) => x.id === id);
    return s ? `v${s.version} ${s.name}` : "—";
  };

  async function activate(s: Strategy) {
    await api(`/learning/strategies/${s.id}/activate`, { method: "POST" });
    toast("success", `Strategy v${s.version} is now active.`);
    reload();
  }

  return (
    <div className="space-y-10">
      <section>
        <SectionHeader title="Experiments" description="A share of new clips uses a candidate strategy, chosen at random, so results can be compared fairly." />
        {(experiments ?? []).length === 0 && <Card className="p-5 text-sm text-muted">No experiments yet. One starts automatically when a finding is strong enough to test.</Card>}
        <div className="space-y-3">
          {(experiments ?? []).map((e) => (
            <Card key={e.id} className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <FlaskConical className="size-4 text-primary" />
                <p className="font-semibold">{e.name}</p>
                <Badge tone={e.status === "running" ? "info" : e.status === "concluded" ? "success" : "neutral"}>{e.status}</Badge>
                <span className="ml-auto text-[12px] text-faint">since {formatDate(e.started_at)}</span>
              </div>
              <p className="mt-2 text-[13px] text-muted">{e.hypothesis}</p>
              <p className="mt-2 text-[12px] text-faint">
                {Math.round(e.allocation * 100)}% of clips use the candidate · compares {e.metric.replace(/_/g, " ")} · needs {e.min_samples_per_arm} videos per group
              </p>
              <p className="mt-1 text-[12px] text-faint">Control: {nameOf(e.control_strategy_id)} · Candidate: {nameOf(e.variant_strategy_id)}</p>
              {e.result && (
                <p className="mt-2 rounded-lg bg-surface-2 px-3 py-2 text-[12px]">
                  Result: {String(e.result.winner) === "variant" ? "candidate won" : "no improvement"} — {String(e.result.n_variant)} vs {String(e.result.n_control)} videos, p = {String(e.result.p_value)}
                </p>
              )}
            </Card>
          ))}
        </div>
      </section>
      <section>
        <SectionHeader title="Strategy versions" description="Every change is a new version; older versions are kept so any change can be rolled back." />
        <div className="space-y-3">
          {(strategies ?? []).map((s) => {
            const weights = (s.params.weights ?? {}) as Record<string, number>;
            return (
              <Card key={s.id} className="p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[12px]">v{s.version}</span>
                  <p className="font-semibold">{s.name}</p>
                  <Badge tone={s.status === "active" ? "success" : s.status === "experiment" ? "info" : "neutral"}>{s.status}</Badge>
                  {can("admin") && s.status !== "active" && (
                    <Button size="sm" variant="ghost" className="ml-auto" onClick={() => activate(s)}>Make active</Button>
                  )}
                </div>
                <p className="mt-2 text-[13px] text-muted">{s.rationale}</p>
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {Object.entries(weights).map(([k, v]) => <span key={k} className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] text-muted">{k} {Number(v).toFixed(2)}</span>)}
                  {typeof s.params.target_seconds === "number" && <span className="rounded-md bg-primary-soft px-2 py-0.5 text-[11px] text-primary">target {String(s.params.target_seconds)}s</span>}
                </div>
              </Card>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function MemoryTab() {
  const toast = useToast();
  const { can } = useAuth();
  const [scope, setScope] = useState<"user" | "brain">("user");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MemoryItem[] | null>(null);
  const [adding, setAdding] = useState("");
  const { data, loading, reload } = useResource<MemoryItem[]>(`/memory?scope=${scope}`);
  const items = results ?? data ?? [];

  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return setResults(null);
    setResults(await api<MemoryItem[]>(`/memory/search?scope=${scope}&q=${encodeURIComponent(query)}`));
  }

  async function add() {
    if (adding.trim().length < 2) return;
    await api("/memory", { method: "POST", body: { text: adding } });
    setAdding("");
    toast("success", "Remembered.");
    reload();
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex gap-1 rounded-xl bg-surface-2 p-1">
          {(["user", "brain"] as const).map((s) => (
            <button key={s} onClick={() => { setScope(s); setResults(null); }}
              className={clsx("rounded-lg px-3 py-1.5 text-[13px] font-medium", scope === s ? "bg-surface shadow-sm" : "text-muted")}>
              {s === "user" ? "About you" : "Brain's own notes"}
            </button>
          ))}
        </div>
        <a href="/api/memory-export" className="inline-flex items-center gap-1.5 text-[13px] font-medium text-primary hover:underline"><Download className="size-3.5" /> Export all memory</a>
      </div>
      <form onSubmit={search} className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" />
          <Input className="pl-9" placeholder="Search memories…" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Button type="submit" variant="secondary">Search</Button>
      </form>
      {can("editor") && scope === "user" && (
        <div className="flex gap-2">
          <Input placeholder="Teach CLIPFORGE something, e.g. “Never use profanity in captions”" value={adding} onChange={(e) => setAdding(e.target.value)} />
          <Button onClick={add} disabled={adding.trim().length < 2}>Remember</Button>
        </div>
      )}
      {loading && <Skeleton className="h-32" />}
      {!loading && items.length === 0 && (
        <EmptyState icon={<Brain className="size-5" />} title={results ? "No matching memories" : "Nothing remembered yet"}
          description="Preferences you mention in Brain Chat — like tone, clip length or topics to avoid — are saved here permanently." />
      )}
      <div className="space-y-2">
        {items.map((m) => <MemoryRow key={m.id} item={m} editable={can("editor")} onChanged={() => { setResults(null); reload(); }} />)}
      </div>
    </div>
  );
}

function MemoryRow({ item, editable, onChanged }: { item: MemoryItem; editable: boolean; onChanged: () => void }) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(item.memory);

  async function save() {
    await api(`/memory/${item.id}`, { method: "PATCH", body: { text } });
    toast("success", "Memory corrected.");
    setEditing(false);
    onChanged();
  }

  async function remove() {
    await api(`/memory/${item.id}`, { method: "DELETE" });
    toast("success", "Forgotten.");
    onChanged();
  }

  return (
    <Card className="flex items-start gap-3 p-3.5">
      <div className="min-w-0 flex-1">
        {editing ? (
          <div className="space-y-2">
            <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} />
            <div className="flex gap-2"><Button size="sm" onClick={save}>Save</Button><Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button></div>
          </div>
        ) : (
          <>
            <p className="text-sm">{item.memory}</p>
            <p className="mt-1 text-[11px] text-faint">
              {String(item.metadata?.category ?? "fact")}
              {item.updated_at ? ` · updated ${timeAgo(item.updated_at)}` : item.created_at ? ` · ${timeAgo(item.created_at)}` : ""}
              {item.score !== undefined ? ` · match ${Math.round(item.score * 100)}%` : ""}
            </p>
          </>
        )}
      </div>
      {editable && !editing && (
        <div className="flex shrink-0 gap-1">
          <button aria-label="Correct this memory" onClick={() => setEditing(true)} className="rounded-lg p-1.5 text-muted hover:bg-surface-2 hover:text-text"><Pencil className="size-3.5" /></button>
          <button aria-label="Delete this memory" onClick={remove} className="rounded-lg p-1.5 text-muted hover:bg-danger-soft hover:text-danger"><Trash2 className="size-3.5" /></button>
        </div>
      )}
    </Card>
  );
}

const VERIFY: Record<string, { label: string; tone: "success" | "warning" | "danger" | "info" | "neutral" }> = {
  verified: { label: "Verified by you", tone: "success" },
  consensus: { label: "Models agree", tone: "info" },
  single_source: { label: "One model only", tone: "neutral" },
  disputed: { label: "Models disagree", tone: "warning" },
  rejected: { label: "Rejected", tone: "danger" },
};

function KnowledgeTab() {
  const toast = useToast();
  const { can } = useAuth();
  const { data, loading, reload } = useResource<KnowledgeItem[]>("/knowledge");
  const [question, setQuestion] = useState("");
  const [important, setImportant] = useState(false);
  const [asking, setAsking] = useState(false);

  async function ask() {
    setAsking(true);
    try {
      await api("/knowledge/ask", { method: "POST", body: { question, important } });
      setQuestion("");
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "No AI model could answer");
    } finally {
      setAsking(false);
    }
  }

  async function verify(id: string, status: "verified" | "rejected") {
    await api(`/knowledge/${id}`, { method: "PATCH", body: { status } });
    reload();
  }

  return (
    <div className="space-y-5">
      <p className="text-[13px] text-muted">
        Answers CLIPFORGE got from AI models are kept here and reused instead of asking again. Important questions get a second opinion from another model; disagreements are shown, not hidden.
      </p>
      {can("editor") && (
        <Card className="space-y-3 p-4">
          <Textarea rows={2} placeholder="Ask a question worth remembering, e.g. “What hook styles work for educational Shorts?”" value={question} onChange={(e) => setQuestion(e.target.value)} />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <Toggle checked={important} onChange={setImportant} label="Important — get a second opinion" />
            <Button loading={asking} disabled={question.trim().length < 3} icon={<Sparkles className="size-3.5" />} onClick={ask}>Ask</Button>
          </div>
        </Card>
      )}
      {loading && <Skeleton className="h-32" />}
      {!loading && (data ?? []).length === 0 && <EmptyState icon={<Lightbulb className="size-5" />} title="No saved knowledge yet" description="Questions asked in Brain Chat or above are saved here with the models that answered them." />}
      <div className="space-y-3">
        {(data ?? []).map((k) => {
          const v = VERIFY[k.verification_status] ?? { label: k.verification_status, tone: "neutral" as const };
          return (
            <Card key={k.id} className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={v.tone}>{v.label}</Badge>
                <Badge>{Math.round(k.confidence * 100)}% confidence</Badge>
                {k.reuse_count > 0 && <Badge tone="primary">reused {k.reuse_count}×</Badge>}
                <span className="ml-auto text-[12px] text-faint">{timeAgo(k.created_at)}</span>
              </div>
              <p className="mt-2 text-sm font-semibold">{k.question}</p>
              <p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-muted">{k.answer}</p>
              {k.disagreements.length > 0 && (
                <div className="mt-2 rounded-lg bg-warning-soft px-3 py-2 text-[12px] text-warning">
                  Where models disagreed: {k.disagreements.join("; ")}
                </div>
              )}
              <p className="mt-2 text-[11px] text-faint">Answered by {k.providers.join(", ") || "—"}</p>
              {(k.responses ?? []).length > 1 && (
                <details className="mt-2 text-[12px]">
                  <summary className="cursor-pointer font-medium text-muted hover:text-text">Compare the {(k.responses ?? []).length} answers</summary>
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    {(k.responses ?? []).map((r, i) => (
                      <div key={i} className="rounded-lg bg-surface-2 p-3">
                        <p className="font-medium">{r.provider} · <span className="text-faint">{r.model}</span></p>
                        <p className="mt-1 whitespace-pre-line leading-relaxed text-muted">{r.answer}</p>
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {can("editor") && k.verification_status !== "verified" && k.verification_status !== "rejected" && (
                <div className="mt-3 flex gap-2">
                  <Button size="sm" variant="secondary" icon={<Check className="size-3.5" />} onClick={() => verify(k.id, "verified")}>Mark correct</Button>
                  <Button size="sm" variant="ghost" icon={<X className="size-3.5" />} onClick={() => verify(k.id, "rejected")}>Mark wrong</Button>
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

const SIGNAL_LABEL: Record<string, string> = {
  viewed_rate: "viewed", first_3s_retention: "at 3 s", engaged_view_rate: "engaged views", average_percentage_viewed: "watched",
};

/** Early retention (P2): flops to fix, which hook/title styles kept viewers, CSV import. */
function RetentionSection() {
  const toast = useToast();
  const { can } = useAuth();
  const { data, reload } = useResource<RetentionReport>("/analytics/retention");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ imported: number; unmatched: string[]; skipped: string[] } | null>(null);

  async function upload(file: File) {
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api<{ imported: number; unmatched: string[]; skipped: string[] }>("/analytics/import", { form });
      setResult(r);
      toast(r.imported ? "success" : "info", r.imported ? `Imported numbers for ${r.imported} clip(s).` : "No rows matched a published clip.");
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  const ranking = (data?.ranking ?? []).filter((r) => r.enough_data);
  const pct = (v: number, key: string) => key === "average_percentage_viewed" ? `${Math.round(v)}%` : `${Math.round(v * 100)}%`;
  return (
    <section>
      <SectionHeader title="Early retention" description="Did people stay past the first seconds? From YouTube's retention report and engaged views, Instagram insights, and numbers you import. YouTube Studio's “viewed vs. swiped away” isn't in the API — export it from Studio (Analytics → Content → Shorts → Export) and import the CSV here."
        action={can("editor") && (
          <label className={clsx("inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[12px] hover:bg-surface-2", busy && "opacity-50")}>
            <Upload className="size-3.5" /> {busy ? "Importing…" : "Import CSV"}
            <input type="file" accept=".csv,text/csv" className="hidden" disabled={busy}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
          </label>
        )} />
      {result && (result.unmatched.length > 0 || result.skipped.length > 0) && (
        <p className="mb-3 text-[12px] text-muted">
          {result.unmatched.length > 0 && <>Not one of your published clips: {result.unmatched.slice(0, 8).join(", ")}{result.unmatched.length > 8 ? "…" : ""}. </>}
          {result.skipped.length > 0 && <>Skipped: {result.skipped.slice(0, 4).join("; ")}.</>}
        </p>
      )}
      {!data || data.with_retention === 0 ? (
        <EmptyState icon={<Lightbulb className="size-5" />} title="No retention numbers yet"
          description="They arrive 1–3 days after posting from YouTube, or right away when you import a Studio CSV. Switch on “Early-retention feedback” in Settings → Clips & editing to get flop flags." />
      ) : (
        <div className="space-y-5">
          {data.flops.length > 0 && (
            <Card className="p-4">
              <p className="mb-2 text-[13px] font-medium">Clips to look at ({data.flops.length})</p>
              <ul className="space-y-2 text-[12px]">
                {data.flops.map((f) => (
                  <li key={f.publication_id}>
                    <span className="font-medium text-text">{f.title}</span> <span className="text-faint">· {platformName(f.platform)}</span>
                    {f.url && <> · <a className="underline" href={f.url} target="_blank" rel="noreferrer">open</a></>}
                    <div className="text-warning">{f.flags.join("; ")}</div>
                    {f.suggestion && <div className="text-muted">Suggestion: {f.suggestion}. Nothing is changed or deleted for you.</div>}
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card className="p-4">
            <p className="mb-2 text-[13px] font-medium">What kept viewers (relative to your own posts)</p>
            {ranking.length === 0 ? <p className="text-[12px] text-muted">A style needs 3 or more posts with retention numbers before it's ranked.</p> : (
              <ul className="grid gap-1 text-[12px] sm:grid-cols-2">
                {ranking.map((r) => (
                  <li key={`${r.dimension}:${r.style}`} className="flex justify-between gap-2">
                    <span className="text-muted">{r.dimension.replace(/_/g, " ")}: <span className="text-text">{r.style.replace(/_/g, " ")}</span></span>
                    <span className={r.relative_retention >= 0 ? "text-success" : "text-warning"}>{r.relative_retention >= 0 ? "+" : ""}{r.relative_retention.toFixed(2)} · {r.posts} posts</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          {Object.keys(data.segments).length > 0 && (
            <Card className="p-4">
              <p className="mb-2 text-[13px] font-medium">Strongest openings by source video</p>
              <ul className="space-y-1 text-[12px]">
                {Object.entries(data.segments).flatMap(([src, items]) => items.slice(0, 3).map((i) => (
                  <li key={`${src}:${i.clip_id}:${i.platform}`} className="flex justify-between gap-2">
                    <span className="truncate text-muted">{Math.floor(i.start_s / 60)}:{String(Math.floor(i.start_s % 60)).padStart(2, "0")} — “{i.hook}”</span>
                    <span className="shrink-0 text-text">{pct(i.value, i.metric)} {SIGNAL_LABEL[i.metric] ?? i.metric}</span>
                  </li>
                )))}
              </ul>
            </Card>
          )}
        </div>
      )}
    </section>
  );
}

/** P3: A/B tests across clips and what each published Short cost (metered AI spend, processing time). */
function TestsAndCosts() {
  const { data: tests } = useResource<ABTestResult[]>("/analytics/ab-tests");
  const { data: cost } = useResource<CostReport>("/analytics/costs");
  return (
    <section>
      <SectionHeader title="A/B tests and costs" description="Tests alternate new clips between two options (platforms can't show two titles of one Short) and compare how long viewers stayed. Costs are what the AI connections actually metered; local models cost nothing." />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card className="p-4 text-[12px]">
          <p className="mb-2 text-[13px] font-medium">A/B tests</p>
          {(tests ?? []).length === 0 ? <p className="text-muted">No test yet — choose one in Settings → Clips &amp; editing → “A/B test”.</p> : (
            <ul className="space-y-3">
              {(tests ?? []).map((t) => (
                <li key={t.id}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-text">{t.dimension.replace("_", " ")}</span>
                    <Badge tone={t.status === "concluded" ? "success" : t.status === "running" ? "info" : "neutral"}>{t.status}</Badge>
                  </div>
                  <div className="text-muted">A: {t.arms.A} — {t.assigned?.A ?? 0} clips, {t.measured?.A ?? 0} with numbers{t.mean?.A != null ? `, mean ${t.mean.A}` : ""}</div>
                  <div className="text-muted">B: {t.arms.B} — {t.assigned?.B ?? 0} clips, {t.measured?.B ?? 0} with numbers{t.mean?.B != null ? `, mean ${t.mean.B}` : ""}</div>
                  {t.conclusion ? <div className="text-text">{t.conclusion}</div> : <div className="text-faint">Decides after {t.min_per_arm} clips with numbers on each side.</div>}
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card className="p-4 text-[12px]">
          <p className="mb-2 text-[13px] font-medium">Cost</p>
          {!cost ? <Skeleton className="h-16" /> : (
            <>
              <p className="text-muted">AI spend so far <span className="text-text">${cost.ai_usd.toFixed(4)}</span> · processing {(cost.processing_s / 60).toFixed(0)} min · {cost.published} published Shorts
                {cost.avg_ai_usd_per_short != null && <> · <span className="text-text">${cost.avg_ai_usd_per_short.toFixed(4)}</span> per Short</>}</p>
              {cost.shorts.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {cost.shorts.slice(0, 8).map((s) => (
                    <li key={s.publication_id} className="flex justify-between gap-2">
                      <span className="truncate text-muted">{s.title} · {platformName(s.platform)}</span>
                      <span className="shrink-0 text-text">${s.ai_usd.toFixed(4)}{s.estimated ? " est." : ""}{s.usd_per_1k_views != null ? ` · $${s.usd_per_1k_views.toFixed(4)}/1k views` : ""}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </Card>
      </div>
    </section>
  );
}
