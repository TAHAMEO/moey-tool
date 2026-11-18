"use client";

import { CheckCircle2, CloudCog, Film, HardDrive, PlayCircle } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Modal } from "@/components/ui";
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

export function AccountsSection() {
  const toast = useToast();
  const { can } = useAuth();
  const { data, reload } = useResource<ConnectionsResponse>("/connections");
  const [busy, setBusy] = useState<string | null>(null);
  const [setupHelp, setSetupHelp] = useState<null | "google" | "instagram">(null);
  const [removing, setRemoving] = useState<SocialAccount | null>(null);

  async function connect(platform: (typeof PLATFORMS)[number]) {
    if (data && !data.apps[platform.app].configured) return setSetupHelp(platform.app);
    setBusy(platform.key);
    try {
      const res = platform.app === "google"
        ? await api<{ auth_url: string }>("/connections/google/start", { method: "POST", body: { purpose: platform.purpose } })
        : await api<{ auth_url: string }>("/connections/instagram/start", { method: "POST" });
      window.location.href = res.auth_url;
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not start sign-in");
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

  return (
    <SettingsSection id="connections" title="Connected accounts"
      description="You sign in on Google's or Instagram's own page — CLIPFORGE never sees your password and stores only encrypted access tokens.">
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
                <Button size="sm" variant={accounts.length ? "ghost" : "secondary"} loading={busy === platform.key} onClick={() => connect(platform)}>
                  {accounts.length ? "Reconnect" : "Connect"}
                </Button>
              )}
            </div>
          </Row>
        );
      })}
      {data && (
        <Row className="text-[12px] text-muted">
          YouTube API quota left today: <span className="font-medium tabular-nums text-text">{data.youtube_quota_remaining.toLocaleString()}</span> units
          {" "}(one Short with captions and a thumbnail uses about 2,100). Uploads that would exceed it are rescheduled automatically.
        </Row>
      )}

      <Modal open={setupHelp !== null} onClose={() => setSetupHelp(null)} title="One-time setup needed"
        footer={<Button onClick={() => setSetupHelp(null)}>Got it</Button>}>
        <div className="space-y-3 text-sm text-muted">
          <p className="flex items-center gap-2 font-medium text-text"><CloudCog className="size-4" /> {setupHelp === "google" ? "Google" : "Instagram"} sign-in isn&apos;t configured on this server yet.</p>
          {setupHelp === "google" ? (
            <>
              <p>Create an OAuth client (type “Web application”) in Google Cloud Console with the YouTube Data API v3, YouTube Analytics API and Google Drive API enabled, then set these on the server and restart:</p>
              <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-[12px]">CLIPFORGE_GOOGLE_CLIENT_ID=…{"\n"}CLIPFORGE_GOOGLE_CLIENT_SECRET=…</pre>
              <p>Authorised redirect URI:</p>
              <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-[12px]">{data?.apps.google.redirect_uri}</pre>
            </>
          ) : (
            <>
              <p>Create a Meta app with the “Instagram API with Instagram Login” product, then set these on the server and restart:</p>
              <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-[12px]">CLIPFORGE_META_APP_ID=…{"\n"}CLIPFORGE_META_APP_SECRET=…</pre>
              <p>Valid OAuth redirect URI:</p>
              <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-[12px]">{data?.apps.instagram.redirect_uri}</pre>
            </>
          )}
          <p className="text-[12px]">Step-by-step instructions are in <span className="font-mono">docs/API_SETUP.md</span>.</p>
        </div>
      </Modal>
      <Modal open={removing !== null} onClose={() => setRemoving(null)} title={`Disconnect ${removing?.display_name}?`}
        footer={<><Button variant="ghost" onClick={() => setRemoving(null)}>Cancel</Button><Button variant="danger" onClick={disconnect}>Disconnect</Button></>}>
        <p className="text-sm text-muted">Access is revoked and the stored tokens are deleted. Scheduled uploads to this account will stop. Videos already published stay online.</p>
      </Modal>
    </SettingsSection>
  );
}
