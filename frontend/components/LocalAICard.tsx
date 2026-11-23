"use client";

import { Check, Cpu, Download, ExternalLink, RefreshCw, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Badge, StepProgress } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Card, Input } from "@/components/ui";
import { api, formatBytes } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { LocalAIServer, LocalAIStatus } from "@/lib/types";

/**
 * Local AI on this PC: Brain Chat's assistant runs on a model server on this computer (Ollama
 * recommended) — free, private, no API key. Detects it, connects it, downloads a model with progress.
 */
export function LocalAICard({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast();
  const { can } = useAuth();
  const [polling, setPolling] = useState(false);
  const { data, loading, reload } = useResource<LocalAIStatus>("/providers/local", { poll: polling ? 2000 : 0 });
  const [busy, setBusy] = useState<string | null>(null);
  const [custom, setCustom] = useState("");
  const wasPulling = useRef(false);

  const pulling = data?.pulling ?? null;
  useEffect(() => {
    setPolling(Boolean(pulling));
    if (wasPulling.current && !pulling) {
      toast("success", "The AI model is downloaded and connected. Try it in Brain Chat.");
      onChanged?.();
    }
    wasPulling.current = Boolean(pulling);
  }, [pulling, toast, onChanged]);

  const ollama = data?.servers.find((s) => s.kind === "ollama");
  const others = (data?.servers ?? []).filter((s) => s.kind !== "ollama" && s.running);
  const admin = can("admin");

  async function connect(server: LocalAIServer) {
    setBusy(`connect:${server.kind}`);
    try {
      const result = await api<{ test: { ok: boolean; error?: string } | null; models: string[] }>("/providers/local/connect",
        { body: { kind: server.kind } });
      if (result.test?.ok) toast("success", `${server.label} is connected: Brain Chat now uses it.`);
      else toast("error", result.test?.error ?? `${server.label} is connected, but it has no model to chat with yet.`);
      reload();
      onChanged?.();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not connect");
    } finally {
      setBusy(null);
    }
  }

  async function pull(model: string) {
    setBusy(`pull:${model}`);
    try {
      await api("/providers/local/pull", { body: { model } });
      setPolling(true);
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not start the download");
    } finally {
      setBusy(null);
    }
  }

  const installed = new Set((ollama?.models ?? []).map((m) => m.name));
  const ready = data?.chat_ready;

  return (
    <Card className="mb-8 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-primary-soft text-primary"><Cpu className="size-5" /></div>
          <div>
            <h2 className="text-[15px] font-semibold">Local AI on this PC</h2>
            <p className="mt-0.5 max-w-2xl text-[13px] text-muted">
              Lets Brain Chat understand anything you ask, improve your clips (“make clip 2 punchier”, “why is clip 1 weak?”) and
              control CLIPFORGE — free, with no API key, and nothing leaves your computer.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {ready ? <Badge tone="success"><Check className="size-3" /> Brain Chat has an AI model</Badge>
            : <Badge tone="warning">Not set up</Badge>}
          <Button size="sm" variant="ghost" icon={<RefreshCw className="size-3.5" />} onClick={reload} loading={loading && !data}>
            Check again
          </Button>
        </div>
      </div>

      {pulling && (
        <StepProgress className="mt-4" step={{ label: `Downloading ${pulling.model}`, progress: pulling.progress, done: 0, total: 1,
          status: pulling.status, detail: pulling.detail ?? null }} />
      )}

      {data && ollama?.running ? (
        <div className="mt-4 space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-[13px]">
            <Badge tone="success">Ollama {ollama.version ?? ""} is running</Badge>
            {ollama.models.length === 0 && <span className="text-muted">No models yet — download one below.</span>}
            {ollama.models.map((m) => (
              <span key={m.name} className="rounded-full bg-surface-2 px-2.5 py-0.5 text-[12px]">
                {m.name}{m.size ? <span className="text-faint"> · {formatBytes(m.size)}</span> : null}
              </span>
            ))}
            {admin && ollama.models.length > 0 && (!ollama.connection || ollama.connection.status !== "ok") && (
              <Button size="sm" icon={<Sparkles className="size-3.5" />} loading={busy === "connect:ollama"} onClick={() => connect(ollama)}>
                Use for Brain Chat
              </Button>
            )}
            {ollama.connection?.status === "ok" && <span className="text-[12px] text-success">Connected — Brain Chat uses it.</span>}
          </div>
          {admin && (
            <div>
              <p className="mb-2 text-[13px] font-medium">Download a model</p>
              <div className="grid gap-2 sm:grid-cols-3">
                {data.recommended.map((m) => (
                  <div key={m.name} className="flex flex-col rounded-xl border border-border p-3">
                    <p className="text-[13px] font-medium">{m.label}</p>
                    <p className="text-[12px] text-faint">{m.download_gb} GB download · about {m.memory_gb} GB memory</p>
                    <p className="mt-1 flex-1 text-[12px] text-muted">{m.note}</p>
                    <Button size="sm" variant={installed.has(m.name) ? "ghost" : "secondary"} className="mt-2"
                      icon={installed.has(m.name) ? <Check className="size-3.5" /> : <Download className="size-3.5" />}
                      disabled={installed.has(m.name) || Boolean(pulling)} loading={busy === `pull:${m.name}`} onClick={() => pull(m.name)}>
                      {installed.has(m.name) ? "Installed" : "Download"}
                    </Button>
                  </div>
                ))}
              </div>
              <form className="mt-2 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (custom.trim()) pull(custom.trim()); }}>
                <Input value={custom} onChange={(e) => setCustom(e.target.value)} placeholder="Another Ollama model, e.g. gemma3:4b"
                  aria-label="Another Ollama model" className="max-w-xs" />
                <Button size="sm" variant="secondary" type="submit" disabled={!custom.trim() || Boolean(pulling)}>Download</Button>
              </form>
            </div>
          )}
        </div>
      ) : data ? (
        <div className="mt-4 space-y-3">
          <ol className="list-decimal space-y-1.5 pl-5 text-[13px] text-muted">
            <li>
              Install <span className="font-medium text-text">Ollama</span> (free, for Windows, macOS and Linux) and open it — it runs
              quietly in the background (look for the llama icon by the clock).
            </li>
            <li>Click <span className="font-medium text-text">Check again</span> here.</li>
            <li>Download the recommended model (about 2 GB, with progress). Brain Chat then uses it by itself.</li>
          </ol>
          <a href={data.install_url} target="_blank" rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-[13px] font-medium text-primary-fg hover:opacity-90">
            Get Ollama <ExternalLink className="size-3.5" />
          </a>
        </div>
      ) : null}

      {others.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3 text-[13px]">
          {others.map((s) => (
            <span key={s.kind} className="flex items-center gap-2">
              <Badge tone="success">{s.label} is running ({s.models.length} model{s.models.length === 1 ? "" : "s"})</Badge>
              {admin && (!s.connection || s.connection.status !== "ok") && (
                <Button size="sm" variant="secondary" loading={busy === `connect:${s.kind}`} onClick={() => connect(s)}>Use it</Button>
              )}
              {s.connection?.status === "ok" && <span className="text-[12px] text-success">Connected</span>}
            </span>
          ))}
        </div>
      )}
    </Card>
  );
}
