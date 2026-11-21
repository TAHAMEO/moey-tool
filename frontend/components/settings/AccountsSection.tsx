"use client";

import clsx from "clsx";
import { CheckCircle2, Copy, ExternalLink, Film, HardDrive, KeyRound, PlayCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Input, Label, Modal } from "@/components/ui";
import { api, formatDate } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { ConnectionsResponse, SocialAccount } from "@/lib/types";
import { Row, SettingsSection } from "./shared";

const PLATFORMS = [
  { key: "youtube", label: "YouTube", icon: PlayCircle, app: "google", purpose: "youtube",
    blurb: "Publish Shorts, upload caption tracks, read your analytics and fetch your own videos in full quality." },
  { key: "instagram", label: "Instagram", icon: Film, app: "instagram", purpose: null,
    blurb: "Publish Reels and read their insights. Needs a Business or Creator account." },
  { key: "google_drive", label: "Google Drive", icon: HardDrive, app: "google", purpose: "google_drive",
    blurb: "Read-only access to fetch your original video files when a platform only gives a lower-quality copy." },
] as const;
type Platform = (typeof PLATFORMS)[number];

const SOURCE_LABEL = { saved: "set up in CLIPFORGE", built_in: "built into this copy", env: "from the server settings" } as const;

function CopyLine({ value }: { value: string | null | undefined }) {
  const toast = useToast();
  if (!value) return null;
  return (
    <div className="flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2">
      <code className="min-w-0 flex-1 break-all text-[12px]">{value}</code>
      <button aria-label="Copy" className="shrink-0 text-muted hover:text-text"
        onClick={() => { navigator.clipboard?.writeText(value).then(() => toast("success", "Copied.")).catch(() => undefined); }}>
        <Copy className="size-3.5" />
      </button>
    </div>
  );
}

function Step({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-primary-soft text-[11px] font-semibold text-primary">{n}</span>
      <div className="min-w-0 flex-1 space-y-1.5 text-[13px]">{children}</div>
    </li>
  );
}

const ext = (href: string, label: string) => (
  <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 font-medium text-primary hover:underline">
    {label} <ExternalLink className="size-3" />
  </a>
);

/** One-time setup of the Google or Instagram app, saved encrypted in CLIPFORGE (no files, no restart). */
function AppSetup({ provider, data, onClose, onSaved }: {
  provider: "google" | "instagram"; data: ConnectionsResponse; onClose: () => void; onSaved: (popup: Window | null) => void;
}) {
  const toast = useToast();
  const local = typeof window !== "undefined" && ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname);
  const [kind, setKind] = useState<"desktop" | "web">(local ? "desktop" : "web");
  const [id, setId] = useState("");
  const [secret, setSecret] = useState("");
  const [relay, setRelay] = useState(data.apps.instagram.relay_url ?? "");
  const [saving, setSaving] = useState(false);
  const ig = data.apps.instagram;

  async function save() {
    setSaving(true);
    // Opened inside the click so pop-up blockers allow it; it shows the sign-in once the app is saved.
    const popup = window.open("about:blank", "clipforge-signin", "popup,width=520,height=720");
    try {
      const result = provider === "google"
        ? await api<{ detail: string }>("/connections/apps/google", { method: "PUT", body: { client_id: id, client_secret: secret, kind } })
        : await api<{ detail: string }>("/connections/apps/instagram", { method: "PUT", body: { app_id: id, app_secret: secret, relay_url: relay || null } });
      toast("success", result.detail);
      onSaved(popup);
    } catch (err) {
      popup?.close();
      toast("error", err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal open onClose={onClose} wide title={provider === "google" ? "Set up Google sign-in (once)" : "Set up Instagram sign-in (once)"}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button loading={saving} disabled={!id.trim() || !secret.trim()} onClick={save}>Save and connect</Button></>}>
      <div className="space-y-4 text-muted">
        <p className="text-[13px]">
          Google and Instagram only let apps sign in that are registered with them. You register CLIPFORGE once (about 5 minutes);
          after that, connecting is one click. {data.apps[provider].source === null && "Whoever gives you CLIPFORGE can also build this in — then nobody has to do this step."}
        </p>
        {provider === "google" ? (
          <ol className="space-y-3">
            <Step n={1}><p>{ext("https://console.cloud.google.com/projectcreate", "Create a Google Cloud project")} (any name).</p></Step>
            <Step n={2}><p>Enable {ext("https://console.cloud.google.com/apis/library/youtube.googleapis.com", "YouTube Data API v3")}, {ext("https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com", "YouTube Analytics API")} and, if you want Drive, {ext("https://console.cloud.google.com/apis/library/drive.googleapis.com", "Google Drive API")}.</p></Step>
            <Step n={3}><p>{ext("https://console.cloud.google.com/auth/branding", "Configure the consent screen")}: External, your email as support contact. Under <em>Audience</em>, add your own Google account as a test user.</p></Step>
            <Step n={4}>
              <p>{ext("https://console.cloud.google.com/auth/clients/create", "Create an OAuth client")} of type:</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {(["desktop", "web"] as const).map((k) => (
                  <button key={k} type="button" onClick={() => setKind(k)} aria-pressed={kind === k}
                    className={clsx("rounded-xl border p-2.5 text-left", kind === k ? "border-primary bg-primary-soft" : "border-border hover:bg-surface-2")}>
                    <span className={clsx("block text-[13px] font-semibold", kind === k ? "text-primary" : "text-text")}>{k === "desktop" ? "Desktop app" : "Web application"}</span>
                    <span className="mt-0.5 block text-[12px]">{k === "desktop" ? "CLIPFORGE on this computer — nothing else to enter." : "CLIPFORGE on a server with its own address."}</span>
                  </button>
                ))}
              </div>
              {kind === "web" && (<><p>Add this <em>Authorised redirect URI</em>:</p><CopyLine value={data.apps.google.web_redirect_uri} /></>)}
              {kind === "desktop" && !local && <p className="text-warning">Desktop apps only work when CLIPFORGE is opened at http://localhost on the computer it runs on.</p>}
            </Step>
            <Step n={5}><p>Paste the client ID and secret it shows:</p></Step>
          </ol>
        ) : (
          <ol className="space-y-3">
            <Step n={1}><p>{ext("https://developers.facebook.com/apps/creation/", "Create a Meta app")} (use case: <em>Manage messaging & content on Instagram</em>).</p></Step>
            <Step n={2}><p>In the app, open <em>Instagram → API setup with Instagram login</em>. Under <em>Generate access tokens</em>, add your Instagram Business or Creator account (as a tester while the app is in development).</p></Step>
            <Step n={3}>
              <p>Under <em>Set up Instagram business login → Business login settings</em>, add this <em>OAuth redirect URI</em>:</p>
              {ig.https
                ? <CopyLine value={ig.direct_redirect_uri} />
                : <>
                    <p>Instagram only returns to https:// addresses, and this copy runs on http://. Publish the small relay page
                      <span className="font-mono"> deploy/oauth-relay/instagram.html</span> on any HTTPS host (for example GitHub Pages), enter its address
                      below, and register that address as the redirect URI. It only ever forwards the sign-in to CLIPFORGE on this computer.</p>
                    <Label>Relay page address (https://…)</Label>
                    <Input value={relay} onChange={(e) => setRelay(e.target.value)} placeholder="https://you.github.io/clipforge/instagram.html" />
                  </>}
            </Step>
            <Step n={4}><p>Copy the <em>Instagram app ID</em> and <em>Instagram app secret</em> from the same page:</p></Step>
          </ol>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label>{provider === "google" ? "Client ID" : "Instagram app ID"}</Label>
            <Input value={id} onChange={(e) => setId(e.target.value)} autoComplete="off" spellCheck={false}
              placeholder={provider === "google" ? "1234…apps.googleusercontent.com" : "1234567890123456"} />
          </div>
          <div>
            <Label>{provider === "google" ? "Client secret" : "Instagram app secret"}</Label>
            <Input value={secret} onChange={(e) => setSecret(e.target.value)} type="password" autoComplete="off" />
          </div>
        </div>
        <p className="flex items-center gap-1.5 text-[12px]"><KeyRound className="size-3.5" /> Stored encrypted with your master key. Only admins can change it.</p>
      </div>
    </Modal>
  );
}

export function AccountsSection() {
  const toast = useToast();
  const { can } = useAuth();
  const [origin, setOrigin] = useState<string | null>(null);
  useEffect(() => setOrigin(window.location.origin), []);
  const { data, reload } = useResource<ConnectionsResponse>(origin ? `/connections?origin=${encodeURIComponent(origin)}` : null);
  const [busy, setBusy] = useState<string | null>(null);
  const [setup, setSetup] = useState<{ provider: "google" | "instagram"; then: Platform } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [removing, setRemoving] = useState<SocialAccount | null>(null);
  const pending = useRef<string | null>(null);

  const finished = useCallback((message: { type?: string; connected?: string | null; error?: string | null }) => {
    if (message?.type !== "clipforge-oauth" || !pending.current) return;
    pending.current = null;
    setBusy(null);
    if (message.connected) toast("success", `${PLATFORMS.find((p) => p.key === message.connected)?.label ?? "Account"} connected.`);
    else toast("error", message.error === "access_denied" || message.error === "cancelled" ? "Sign-in was cancelled." : `Not connected: ${message.error}`);
    reload();
  }, [reload, toast]);

  useEffect(() => {
    let channel: BroadcastChannel | null = null;
    try {
      channel = new BroadcastChannel("clipforge-oauth");
      channel.onmessage = (event) => finished(event.data);
    } catch {
      /* fall back to window messages */
    }
    const onMessage = (event: MessageEvent) => { if (event.origin === window.location.origin) finished(event.data); };
    window.addEventListener("message", onMessage);
    return () => { channel?.close(); window.removeEventListener("message", onMessage); };
  }, [finished]);

  async function connect(platform: Platform, opened?: Window | null) {
    if (!data) return;
    const app = data.apps[platform.app];
    if (opened === undefined) {  // a fresh click (after a set-up, the app is known to exist)
      if (!app.configured) return setSetup({ provider: platform.app, then: platform });
      if (app.problem) return setProblem(app.problem);
    }
    setBusy(platform.key);
    // Open the window now (inside the click) so pop-up blockers allow it; the address follows.
    const popup = opened !== undefined ? opened : window.open("about:blank", "clipforge-signin", "popup,width=520,height=720");
    try {
      const res = platform.app === "google"
        ? await api<{ auth_url: string }>("/connections/google/start", { body: { purpose: platform.purpose, origin } })
        : await api<{ auth_url: string }>("/connections/instagram/start", { body: { origin } });
      pending.current = platform.key;
      if (popup && !popup.closed) popup.location.href = res.auth_url;
      else window.location.href = res.auth_url; // pop-ups blocked: sign in in this tab
      window.setTimeout(() => { if (pending.current === platform.key) { pending.current = null; setBusy(null); reload(); } }, 5 * 60 * 1000);
    } catch (err) {
      popup?.close();
      if (err instanceof Error && /Desktop app|relay|https:\/\//.test(err.message)) setProblem(err.message);
      else toast("error", err instanceof Error ? err.message : "Could not start sign-in");
      setBusy(null);
    }
  }

  async function disconnect() {
    if (!removing) return;
    await api(`/connections/${removing.id}`, { method: "DELETE" });
    toast("success", `${removing.display_name} disconnected and its tokens deleted.`);
    setRemoving(null);
    reload();
  }

  async function forgetApp(provider: "google" | "instagram") {
    await api(`/connections/apps/${provider}`, { method: "DELETE" });
    toast("success", "Removed. Connected accounts keep working until their access needs renewing.");
    reload();
  }

  return (
    <SettingsSection id="connections" title="Connected accounts"
      description="One click: you sign in on Google's or Instagram's own page — CLIPFORGE never sees your password and stores only encrypted access tokens.">
      {PLATFORMS.map((platform) => {
        const accounts = (data?.accounts ?? []).filter((a) => a.platform === platform.key);
        const Icon = platform.icon;
        return (
          <Row key={platform.key}>
            <div className="flex flex-wrap items-start gap-3">
              <div className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-surface-2"><Icon className="size-5" /></div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">{platform.label}</p>
                <p className="mt-0.5 text-[12px] text-muted">{platform.blurb}</p>
                {accounts.map((a) => (
                  <div key={a.id} className="mt-2 flex flex-wrap items-center gap-2 text-[13px]">
                    <CheckCircle2 className="size-4 text-success" />
                    <span className="font-medium">{a.display_name}</span>
                    {a.status !== "connected" && <Badge tone="warning">{a.status.replace(/_/g, " ")}</Badge>}
                    <span className="text-[12px] text-faint">since {formatDate(a.connected_at)}</span>
                    {can("admin") && <button className="text-[12px] text-muted underline-offset-2 hover:text-danger hover:underline" onClick={() => setRemoving(a)}>Disconnect</button>}
                  </div>
                ))}
              </div>
              {can("admin") && (
                <Button size="sm" variant={accounts.length ? "ghost" : "primary"} loading={busy === platform.key} disabled={!data} onClick={() => connect(platform)}>
                  {accounts.length ? "Reconnect" : "Connect"}
                </Button>
              )}
            </div>
          </Row>
        );
      })}
      {data && can("admin") && (
        <Row className="space-y-1.5 text-[12px] text-muted">
          {(["google", "instagram"] as const).map((provider) => {
            const app = data.apps[provider];
            const name = provider === "google" ? "Google (YouTube, Drive)" : "Instagram";
            return (
              <p key={provider} className="flex flex-wrap items-center gap-x-2">
                <span>{name} sign-in: {app.configured ? SOURCE_LABEL[app.source ?? "env"] : "not set up yet"}</span>
                {app.source === "saved" && <button className="text-muted underline-offset-2 hover:text-danger hover:underline" onClick={() => forgetApp(provider)}>remove</button>}
                {app.source !== "built_in" && (
                  <button className="text-primary underline-offset-2 hover:underline"
                    onClick={() => setSetup({ provider, then: PLATFORMS.find((p) => p.app === provider)! })}>{app.configured ? "change" : "set up"}</button>
                )}
              </p>
            );
          })}
        </Row>
      )}
      {data && (
        <Row className="text-[12px] text-muted">
          YouTube API quota left today: <span className="font-medium tabular-nums text-text">{data.youtube_quota_remaining.toLocaleString()}</span> units
          {" "}(one Short with captions and a thumbnail uses about 2,100). Uploads that would exceed it are rescheduled automatically.
        </Row>
      )}

      {setup && data && (
        <AppSetup provider={setup.provider} data={data} onClose={() => setSetup(null)}
          onSaved={(popup) => { const then = setup.then; setSetup(null); reload(); void connect(then, popup); }} />
      )}
      <Modal open={problem !== null} onClose={() => setProblem(null)} title="Can't connect from here" footer={<Button onClick={() => setProblem(null)}>OK</Button>}>
        <p className="text-sm text-muted">{problem}</p>
      </Modal>
      <Modal open={removing !== null} onClose={() => setRemoving(null)} title={`Disconnect ${removing?.display_name}?`}
        footer={<><Button variant="ghost" onClick={() => setRemoving(null)}>Cancel</Button><Button variant="danger" onClick={disconnect}>Disconnect</Button></>}>
        <p className="text-sm text-muted">Access is revoked and the stored tokens are deleted. Scheduled uploads to this account will stop. Videos already published stay online.</p>
      </Modal>
    </SettingsSection>
  );
}
