"use client";

import clsx from "clsx";
import { Bot, ChevronDown, Cpu, KeyRound, Plus, RefreshCw, Trash2, Wallet } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, EmptyState, Input, Label, Modal, PageHeader, ProgressBar, SectionHeader, Select, Skeleton, Stat, Toggle } from "@/components/ui";
import { api, money, timeAgo } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { AISettings, AllSettings, ConnectionTest, ProviderConnection, ProvidersResponse, UsageSummary } from "@/lib/types";

const STATUS: Record<string, { label: string; tone: "success" | "warning" | "danger" | "neutral" }> = {
  ok: { label: "Connected", tone: "success" },
  untested: { label: "Not tested", tone: "neutral" },
  degraded: { label: "Temporarily failing", tone: "warning" },
  quota_exhausted: { label: "Quota or credit exhausted", tone: "warning" },
  invalid_credentials: { label: "Key rejected", tone: "danger" },
  error: { label: "Error", tone: "danger" },
};

const TIERS = [
  { value: "3", label: "Best quality" },
  { value: "2", label: "Balanced" },
  { value: "1", label: "Fast & cheap" },
];

function priceSource(source: string): string {
  if (source === "owner") return "set by you";
  if (source === "local") return "free";
  if (source.startsWith("reference:")) return `list price ${source.slice(10)}`;
  return "unknown — estimated";
}

export default function ConnectionsPage() {
  const { can } = useAuth();
  const { data, loading, reload } = useResource<ProvidersResponse>("/providers");
  const { data: usage } = useResource<UsageSummary>("/usage/summary", { poll: 30000 });
  const [adding, setAdding] = useState(false);
  const connections = data?.connections ?? [];

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <PageHeader
        title="AI Connections"
        description="Connect the AI services you want CLIPFORGE to use. Each task is routed automatically to the best available model — local models first, paid ones only when they add value and stay within budget."
        action={can("admin") && <Button icon={<Plus className="size-4" />} onClick={() => setAdding(true)}>Add connection</Button>}
      />

      {usage && (
        <div className="mb-8 grid gap-4 sm:grid-cols-3">
          <Stat label="Spent today" icon={<Wallet className="size-4" />} value={money(usage.today_usd)}
            hint={<BudgetBar spent={usage.today_usd} budget={usage.budgets.daily} />} />
          <Stat label="Spent this month" icon={<Wallet className="size-4" />} value={money(usage.month_usd)}
            hint={<BudgetBar spent={usage.month_usd} budget={usage.budgets.monthly} />} />
          <Stat label="Saved by cache" icon={<RefreshCw className="size-4" />} value={usage.cache_hits_month.toLocaleString()}
            hint={`answers reused this month${usage.failed_calls_month ? ` · ${usage.failed_calls_month} failed calls` : ""}`} />
        </div>
      )}

      <SectionHeader title="Your connections" description="Keys are encrypted on the server and never shown again — only the last four characters." />
      {loading && <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-24" /></div>}
      {!loading && connections.length === 0 && (
        <EmptyState icon={<Bot className="size-5" />} title="No AI connected yet"
          description="CLIPFORGE still works without one — transcription, scene detection, scoring and editing all run locally. Connect a model to get smarter titles, chat answers and clip re-ranking."
          action={can("admin") && <Button onClick={() => setAdding(true)}>Add your first connection</Button>} />
      )}
      <div className="space-y-3">
        {connections.map((c) => <ConnectionCard key={c.id} conn={c} onChanged={reload} />)}
      </div>

      {can("admin") && <RoutingCard />}

      {usage && usage.by_model.length > 0 && (
        <div className="mt-10">
          <SectionHeader title="Usage this month" description={usage.calls_with_estimated_prices ? `${usage.calls_with_estimated_prices} call(s) were priced with estimated rates — set exact prices under a model's advanced settings.` : undefined} />
          <Card className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-border text-[12px] text-muted">
                <tr><th className="px-4 py-2.5 font-medium">Model</th><th className="px-4 py-2.5 font-medium">Calls</th><th className="px-4 py-2.5 font-medium">Tokens in / out</th><th className="px-4 py-2.5 text-right font-medium">Cost</th></tr>
              </thead>
              <tbody>
                {usage.by_model.map((row) => (
                  <tr key={`${row.provider_type}:${row.model}`} className="border-b border-border last:border-0">
                    <td className="px-4 py-2.5"><span className="font-medium">{row.model}</span> <span className="text-faint">· {row.provider_type}</span></td>
                    <td className="px-4 py-2.5 tabular-nums">{row.calls}</td>
                    <td className="px-4 py-2.5 tabular-nums text-muted">{row.input_tokens.toLocaleString()} / {row.output_tokens.toLocaleString()}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">{money(row.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}

      <AddConnection open={adding} onClose={() => setAdding(false)} types={data?.available_types ?? []} onAdded={reload} />
    </div>
  );
}

function BudgetBar({ spent, budget }: { spent: number; budget: number }) {
  if (!budget) return <span>No limit set</span>;
  const ratio = Math.min(1, spent / budget);
  return (
    <div className="space-y-1">
      <ProgressBar value={ratio} tone={ratio > 0.9 ? "danger" : ratio > 0.7 ? "warning" : "primary"} />
      <span>of {money(budget)} limit</span>
    </div>
  );
}

function ConnectionCard({ conn, onChanged }: { conn: ProviderConnection; onChanged: () => void }) {
  const toast = useToast();
  const { can } = useAuth();
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [newKey, setNewKey] = useState("");
  const status = STATUS[conn.status] ?? { label: conn.status, tone: "neutral" as const };
  const enabledModels = conn.models.filter((m) => m.enabled).length;

  async function test() {
    setTesting(true);
    try {
      const res = await api<{ test: ConnectionTest }>(`/providers/${conn.id}/test`, { method: "POST" });
      if (res.test.ok) toast("success", `Working — ${res.test.probe_model} replied.`);
      else toast("error", res.test.error ?? "The test failed.");
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Test failed");
    } finally {
      setTesting(false);
    }
  }

  async function patch(body: Record<string, unknown>, message?: string) {
    try {
      await api(`/providers/${conn.id}`, { method: "PATCH", body });
      if (message) toast("success", message);
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Update failed");
    }
  }

  async function remove() {
    await api(`/providers/${conn.id}`, { method: "DELETE" });
    toast("success", "Connection removed and its key deleted.");
    setConfirmRemove(false);
    onChanged();
  }

  return (
    <Card className="animate-fade-up">
      <div className="flex flex-wrap items-center gap-4 p-4">
        <div className={clsx("flex size-10 shrink-0 items-center justify-center rounded-xl", conn.is_local ? "bg-success-soft text-success" : "bg-primary-soft text-primary")}>
          {conn.is_local ? <Cpu className="size-5" /> : <Bot className="size-5" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold">{conn.name}</p>
            <Badge tone={status.tone}>{status.label}</Badge>
            {conn.is_local && <Badge tone="success">Local · free</Badge>}
            {!conn.enabled && <Badge>Disabled</Badge>}
          </div>
          <p className="mt-0.5 text-[12px] text-muted">
            {conn.provider_label}
            {conn.key_hint && <> · key ••••{conn.key_hint}</>}
            {conn.base_url && <> · {conn.base_url}</>}
            {" · "}{enabledModels} model{enabledModels === 1 ? "" : "s"} in use
            {conn.last_tested_at && <> · tested {timeAgo(conn.last_tested_at)}</>}
          </p>
          {conn.last_error && conn.status !== "ok" && <p className="mt-1 text-[12px] text-danger">{conn.last_error}</p>}
        </div>
        <div className="text-right text-[12px] text-muted">
          <p className="tabular-nums">{conn.usage_30d.calls.toLocaleString()} calls</p>
          <p className="tabular-nums">{money(conn.usage_30d.cost_usd)} · 30 days</p>
        </div>
        {can("admin") && (
          <div className="flex items-center gap-2">
            <Button size="sm" variant="secondary" loading={testing} onClick={test}>Test</Button>
            <Toggle checked={conn.enabled} onChange={(v) => patch({ enabled: v }, v ? "Enabled." : "Disabled — it won't be used for any task.")} label={conn.enabled ? "On" : "Off"} />
          </div>
        )}
      </div>
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-1 border-t border-border px-4 py-2 text-[12px] font-medium text-muted hover:text-text">
        Advanced settings <ChevronDown className={clsx("size-3.5 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="space-y-5 border-t border-border p-4">
          {can("admin") && conn.provider_type !== "ollama" && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-60 flex-1">
                <Label hint="replaces the stored key">New API key</Label>
                <Input type="password" autoComplete="off" value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="Paste a new key" />
              </div>
              <Button size="sm" variant="secondary" icon={<KeyRound className="size-3.5" />} disabled={!newKey.trim()}
                onClick={async () => { await patch({ api_key: newKey }, "Key replaced — run a test to verify it."); setNewKey(""); }}>
                Replace key
              </Button>
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[12px]">
              <thead className="text-muted">
                <tr>
                  <th className="py-1.5 pr-3 font-medium">Model</th><th className="py-1.5 pr-3 font-medium">Use</th><th className="py-1.5 pr-3 font-medium">Role</th>
                  <th className="py-1.5 pr-3 font-medium">$ / M tokens (in · out)</th><th className="py-1.5 pr-3 font-medium">Speed</th><th className="py-1.5 font-medium">Reliability</th>
                </tr>
              </thead>
              <tbody>
                {conn.models.map((m) => (
                  <ModelRow key={m.id} connId={conn.id} model={m} editable={can("admin")} onChanged={onChanged} />
                ))}
              </tbody>
            </table>
            {conn.models.length === 0 && <p className="py-2 text-[12px] text-muted">No models discovered yet — run a test.</p>}
          </div>
          {can("admin") && (
            <div className="flex justify-end">
              <Button size="sm" variant="ghost" icon={<Trash2 className="size-3.5" />} onClick={() => setConfirmRemove(true)}>Remove connection</Button>
            </div>
          )}
        </div>
      )}
      <Modal open={confirmRemove} onClose={() => setConfirmRemove(false)} title={`Remove ${conn.name}?`}
        footer={<><Button variant="ghost" onClick={() => setConfirmRemove(false)}>Cancel</Button><Button variant="danger" onClick={remove}>Remove</Button></>}>
        <p className="text-sm text-muted">The stored key is deleted. Tasks will route to your other connections or to local processing.</p>
      </Modal>
    </Card>
  );
}

function ModelRow({ connId, model, editable, onChanged }: { connId: string; model: ProviderConnection["models"][number]; editable: boolean; onChanged: () => void }) {
  const toast = useToast();
  const [prices, setPrices] = useState({ in: model.price_in_per_mtok?.toString() ?? "", out: model.price_out_per_mtok?.toString() ?? "" });

  async function patch(body: Record<string, unknown>) {
    try {
      await api(`/providers/${connId}/models/${model.id}`, { method: "PATCH", body });
      onChanged();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Update failed");
    }
  }

  function savePrices() {
    const pin = Number(prices.in), pout = Number(prices.out);
    if (prices.in === "" || prices.out === "" || Number.isNaN(pin) || Number.isNaN(pout)) return;
    if (pin === model.price_in_per_mtok && pout === model.price_out_per_mtok) return;
    void patch({ price_in_per_mtok: pin, price_out_per_mtok: pout });
  }

  return (
    <tr className="border-t border-border align-middle">
      <td className="py-2 pr-3"><span className="font-medium">{model.model_id}</span>{model.capabilities.vision ? <span className="ml-1.5 text-faint">· vision</span> : null}</td>
      <td className="py-2 pr-3">
        <input type="checkbox" aria-label={`Use ${model.model_id}`} className="size-4 accent-[var(--primary)]" checked={model.enabled} disabled={!editable}
          onChange={(e) => patch({ enabled: e.target.checked })} />
      </td>
      <td className="py-2 pr-3">
        {editable ? <Select className="h-8 text-[12px]" value={String(model.quality_tier)} onChange={(v) => patch({ quality_tier: Number(v) })} options={TIERS} />
          : TIERS.find((t) => t.value === String(model.quality_tier))?.label}
      </td>
      <td className="py-2 pr-3">
        <div className="flex items-center gap-1">
          <input className="h-8 w-16 rounded-md border border-border bg-surface px-2 tabular-nums" inputMode="decimal" value={prices.in} disabled={!editable}
            onChange={(e) => setPrices({ ...prices, in: e.target.value })} onBlur={savePrices} aria-label="Input price per million tokens" />
          <input className="h-8 w-16 rounded-md border border-border bg-surface px-2 tabular-nums" inputMode="decimal" value={prices.out} disabled={!editable}
            onChange={(e) => setPrices({ ...prices, out: e.target.value })} onBlur={savePrices} aria-label="Output price per million tokens" />
          <span className="whitespace-nowrap text-faint" title="Where this price came from">{priceSource(model.price_source)}</span>
        </div>
      </td>
      <td className="py-2 pr-3 tabular-nums text-muted">{model.latency_ms ? `${(model.latency_ms / 1000).toFixed(1)}s` : "—"}</td>
      <td className="py-2 tabular-nums text-muted">{model.calls ? `${Math.round(model.success_rate * 100)}% of ${model.calls}` : "—"}</td>
    </tr>
  );
}

function AddConnection({ open, onClose, types, onAdded }: { open: boolean; onClose: () => void; types: ProvidersResponse["available_types"]; onAdded: () => void }) {
  const toast = useToast();
  const [type, setType] = useState("openai");
  const [form, setForm] = useState({ name: "", api_key: "", base_url: "" });
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ConnectionTest | null>(null);
  const meta = types.find((t) => t.type === type);

  function close() {
    setForm({ name: "", api_key: "", base_url: "" });
    setResult(null);
    onClose();
  }

  async function submit() {
    setBusy(true);
    setResult(null);
    try {
      const res = await api<{ test: ConnectionTest | null }>("/providers", {
        method: "POST",
        body: { provider_type: type, name: form.name || undefined, api_key: form.api_key || undefined, base_url: form.base_url || undefined, test: true },
      });
      onAdded();
      if (res.test?.ok) {
        toast("success", `Connected — ${res.test.models_discovered} model(s) found.`);
        close();
      } else {
        setResult(res.test);
      }
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not add the connection");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} onClose={close} title="Add an AI connection"
      footer={<><Button variant="ghost" onClick={close}>{result ? "Close" : "Cancel"}</Button>{!result && <Button loading={busy} onClick={submit}>Connect & test</Button>}</>}>
      {result ? (
        <div className="space-y-2 text-sm">
          <p className="font-medium text-warning">Saved, but the test didn&apos;t pass.</p>
          <p className="text-muted">{result.error ?? result.discovery_error ?? "No reply from the model."}</p>
          <p className="text-[12px] text-faint">Check the key or endpoint, then press Test on its card. Until it works, tasks fall back to your other connections or to local processing.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2">
            {types.map((t) => (
              <button key={t.type} onClick={() => setType(t.type)}
                className={clsx("rounded-xl border px-3 py-2.5 text-left text-[13px] font-medium transition-colors",
                  t.type === type ? "border-primary bg-primary-soft text-primary" : "border-border hover:bg-surface-2")}>
                {t.label}
              </button>
            ))}
          </div>
          <div><Label hint="optional">Name</Label><Input value={form.name} placeholder={meta?.label} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          {meta?.needs_base_url && (
            <div>
              <Label>Endpoint URL</Label>
              <Input value={form.base_url} placeholder={type === "ollama" ? "http://localhost:11434/v1" : "https://your-server/v1"} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </div>
          )}
          {type !== "ollama" && (
            <div>
              <Label hint={meta?.needs_key ? undefined : "if your endpoint needs one"}>API key</Label>
              <Input type="password" autoComplete="off" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            </div>
          )}
          <p className="text-[12px] text-muted">CLIPFORGE lists the available models and sends a one-word test message to confirm the key works.</p>
        </div>
      )}
    </Modal>
  );
}

function RoutingCard() {
  const toast = useToast();
  const { data, reload } = useResource<AllSettings>("/settings");
  const ai = data?.ai;
  const [draft, setDraft] = useState<Partial<AISettings>>({});
  if (!ai) return null;
  const value = { ...ai, ...draft };
  const dirty = Object.keys(draft).length > 0;

  async function save(patch: Partial<AISettings>) {
    try {
      await api("/settings/ai", { method: "PATCH", body: patch });
      setDraft({});
      reload();
      toast("success", "Saved.");
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Save failed");
    }
  }

  return (
    <div className="mt-10">
      <SectionHeader title="Routing & spending" description="How CLIPFORGE chooses between your connections." />
      <Card className="divide-y divide-border">
        <div className="space-y-4 p-4">
          <p className="text-[12px] text-muted">Audio and video frames are always analysed on this server. Only text — your messages, relevant memories, transcript excerpts and video titles — is sent to the AI services you connect.</p>
          <Toggle checked={value.local_first} onChange={(v) => save({ local_first: v })} label="Prefer local models"
            description="Use free local processing whenever it can do the job well; paid models handle what local ones can't." />
          <Toggle checked={value.allow_paid_apis} onChange={(v) => save({ allow_paid_apis: v })} label="Allow paid AI services"
            description="Turn off to run everything locally — CLIPFORGE falls back to rule-based results where no model is available." />
          <Toggle checked={value.llm_rerank_clips} onChange={(v) => save({ llm_rerank_clips: v })} label="AI second look at clip choices"
            description="An AI model reviews the top candidate clips for context and completeness before rendering." />
        </div>
        <div className="grid gap-4 p-4 sm:grid-cols-3">
          <div><Label hint="USD">Daily limit</Label><Input inputMode="decimal" value={value.daily_budget_usd} onChange={(e) => setDraft({ ...draft, daily_budget_usd: Number(e.target.value) || 0 })} /></div>
          <div><Label hint="USD">Monthly limit</Label><Input inputMode="decimal" value={value.monthly_budget_usd} onChange={(e) => setDraft({ ...draft, monthly_budget_usd: Number(e.target.value) || 0 })} /></div>
          <div><Label hint="USD">Per video limit</Label><Input inputMode="decimal" value={value.per_job_budget_usd} onChange={(e) => setDraft({ ...draft, per_job_budget_usd: Number(e.target.value) || 0 })} /></div>
          <div className="sm:col-span-3">
            <Label hint="for important questions">Second opinions</Label>
            <Select value={String(value.consensus_max_providers)} onChange={(v) => save({ consensus_max_providers: Number(v) })}
              options={[1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: n === 1 ? "One model only" : `Up to ${n} models` }))} />
          </div>
        </div>
        {dirty && (
          <div className="flex justify-end gap-2 p-3">
            <Button size="sm" variant="ghost" onClick={() => setDraft({})}>Discard</Button>
            <Button size="sm" onClick={() => save(draft)}>Save limits</Button>
          </div>
        )}
      </Card>
    </div>
  );
}
