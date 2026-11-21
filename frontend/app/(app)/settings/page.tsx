"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";
import { AccountsSection } from "@/components/settings/AccountsSection";
import { AutomationSection, UISection } from "@/components/settings/AutomationSection";
import { BrandSection, ClipsSection } from "@/components/settings/ClipsSection";
import { DataSection } from "@/components/settings/DataSection";
import { LibrarySection } from "@/components/settings/LibrarySection";
import { LiveSection } from "@/components/settings/LiveSection";
import { PublishingSection } from "@/components/settings/PublishingSection";
import { SecuritySection } from "@/components/settings/SecuritySection";
import { SourcesSection } from "@/components/settings/SourcesSection";
import { SettingsSection, useSettingsSaver } from "@/components/settings/shared";
import { useToast } from "@/components/toast";
import { PageHeader, Skeleton } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { AllSettings } from "@/lib/types";

const SECTIONS = [
  { id: "publishing", label: "Publishing" },
  { id: "connections", label: "Accounts" },
  { id: "sources", label: "Sources" },
  { id: "clips", label: "Clips & editing" },
  { id: "brand", label: "Brand" },
  { id: "live", label: "Live streams" },
  { id: "library", label: "Media library" },
  { id: "automation", label: "Automation & safety" },
  { id: "security", label: "Account & security" },
  { id: "data", label: "Your data" },
  { id: "advanced", label: "Advanced" },
];

const PLATFORM_NAMES: Record<string, string> = { youtube: "YouTube", instagram: "Instagram", google_drive: "Google Drive" };

export default function SettingsPage() {
  const toast = useToast();
  const { can } = useAuth();
  const { data: settings, reload } = useResource<AllSettings>("/settings");
  const save = useSettingsSaver(reload);
  const [active, setActive] = useState("publishing");
  const editable = can("admin");

  // Result of an OAuth round-trip (the backend redirects here with ?connected= or ?connection_error=).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    const failed = params.get("connection_error");
    if (connected) toast("success", `${PLATFORM_NAMES[connected] ?? connected} connected.`);
    if (failed) toast("error", failed === "access_denied" || failed === "cancelled" ? "Sign-in was cancelled." : `Could not connect: ${failed}`);
    if (connected || failed) window.history.replaceState(null, "", `/settings${window.location.hash}`);
  }, [toast]);

  // Highlight the section in view.
  useEffect(() => {
    if (!settings) return;
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) setActive(visible[0].target.id);
    }, { rootMargin: "-20% 0px -70% 0px" });
    SECTIONS.forEach((s) => { const el = document.getElementById(s.id); if (el) observer.observe(el); });
    if (window.location.hash) document.getElementById(window.location.hash.slice(1))?.scrollIntoView();
    return () => observer.disconnect();
  }, [settings]);

  if (!settings) {
    return <div className="mx-auto max-w-5xl space-y-4 px-4 py-8 sm:px-6"><Skeleton className="h-10 w-48" /><Skeleton className="h-64" /><Skeleton className="h-64" /></div>;
  }
  const props = { settings, save, editable };

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
      <PageHeader title="Settings" description={editable ? "Changes save automatically." : "You can view settings; an admin can change them."} />
      <div className="lg:grid lg:grid-cols-[200px_1fr] lg:gap-10">
        <nav aria-label="Settings sections" className="sticky top-0 z-10 -mx-4 mb-6 flex gap-1 overflow-x-auto bg-bg px-4 py-2 lg:top-8 lg:mx-0 lg:mb-0 lg:block lg:self-start lg:space-y-0.5 lg:px-0 lg:py-0">
          {SECTIONS.map((s) => (
            <a key={s.id} href={`#${s.id}`} onClick={() => setActive(s.id)}
              className={clsx("block whitespace-nowrap rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
                active === s.id ? "bg-primary-soft text-primary" : "text-muted hover:bg-surface-2 hover:text-text")}>
              {s.label}
            </a>
          ))}
        </nav>
        <div className="min-w-0 space-y-10">
          <PublishingSection {...props} />
          <AccountsSection />
          <SourcesSection {...props} />
          <ClipsSection {...props} />
          <BrandSection {...props} />
          <LiveSection />
          <LibrarySection />
          <AutomationSection {...props} reload={reload} />
          <SecuritySection />
          <DataSection />
          <SettingsSection id="advanced" title="Advanced">
            <UISection {...props} />
            {can("admin") && <Diagnostics />}
          </SettingsSection>
        </div>
      </div>
    </div>
  );
}

type Diag = Record<string, { ok?: boolean; [key: string]: unknown }>;

function Diagnostics() {
  const [open, setOpen] = useState(false);
  const { data } = useResource<Diag>(open ? "/diagnostics" : null);
  return (
    <div className="p-4">
      <button onClick={() => setOpen(!open)} className="text-[13px] font-semibold hover:text-primary">{open ? "Hide" : "Show"} system diagnostics</button>
      {open && (
        <div className="mt-3 space-y-2">
          {data ? Object.entries(data).map(([key, value]) => (
            <details key={key} className="rounded-lg border border-border px-3 py-2">
              <summary className="cursor-pointer text-[13px]">
                <span className={clsx("mr-2 inline-block size-2 rounded-full", value?.ok === false ? "bg-danger" : value?.ok ? "bg-success" : "bg-faint")} />
                {key.replace(/_/g, " ")}
              </summary>
              <pre className="mt-2 overflow-x-auto text-[11px] text-muted">{JSON.stringify(value, null, 2)}</pre>
            </details>
          )) : <Skeleton className="h-24" />}
        </div>
      )}
    </div>
  );
}
