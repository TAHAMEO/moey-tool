"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * Where Google/Instagram sign-in ends (the sign-in popup lands here). It tells the Settings page
 * the result — over a BroadcastChannel, which still works when the provider's pages cut the
 * popup's link to its opener — and closes itself; opened as a normal page, it goes to Settings.
 */
export default function ConnectedPage() {
  const [result, setResult] = useState<{ connected: string | null; error: string | null } | null>(null);

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const payload = { type: "clipforge-oauth", connected: query.get("connected"), error: query.get("error") };
    setResult(payload);
    try {
      const channel = new BroadcastChannel("clipforge-oauth");
      channel.postMessage(payload);
      channel.close();
    } catch {
      /* very old browser: the Settings page below shows the result instead */
    }
    try {
      window.opener?.postMessage(payload, window.location.origin);
    } catch {
      /* no opener (or a different one) */
    }
    const inPopup = window.name === "clipforge-signin";
    if (inPopup) window.close();
    const target = payload.connected
      ? `/settings?connected=${encodeURIComponent(payload.connected)}#connections`
      : `/settings?connection_error=${encodeURIComponent(payload.error ?? "cancelled")}#connections`;
    const timer = window.setTimeout(() => window.location.replace(target), inPopup ? 1500 : 200);
    return () => window.clearTimeout(timer);
  }, []);

  const ok = result?.connected;
  return (
    <main className="flex min-h-dvh items-center justify-center bg-bg p-4">
      <div className="max-w-sm text-center">
        {result && (ok ? <CheckCircle2 className="mx-auto mb-3 size-8 text-success" /> : <XCircle className="mx-auto mb-3 size-8 text-danger" />)}
        <h1 className="text-lg font-semibold">{!result ? "Finishing sign-in…" : ok ? "Connected" : "Not connected"}</h1>
        <p className="mt-1 text-sm text-muted">{result?.error ?? "You can close this window."}</p>
      </div>
    </main>
  );
}
