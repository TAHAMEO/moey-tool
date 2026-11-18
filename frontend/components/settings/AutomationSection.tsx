"use client";

import { OctagonX, Pause, Play } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button, Modal, Toggle } from "@/components/ui";
import { api } from "@/lib/api";
import { useResource } from "@/lib/hooks";
import type { RetentionSettings } from "@/lib/types";
import { NumberField, Row, SettingsSection, type SectionProps } from "./shared";

type Integration = { name: string; label: string; enabled: boolean };

export function AutomationSection({ settings, save, editable, reload }: SectionProps & { reload: () => void }) {
  const toast = useToast();
  const control = settings.control;
  const { data: integrations, reload: reloadIntegrations } = useResource<Integration[]>("/control/integrations");
  const [confirmStop, setConfirmStop] = useState(false);
  const r = settings.retention;
  const setRetention = (patch: Partial<RetentionSettings>) => save("retention", patch);

  async function act(action: string, integration?: string) {
    try {
      const res = await api<{ cancelled_jobs: number }>(`/control/${action}`, { method: "POST", body: integration ? { integration } : undefined });
      if (action === "emergency_stop") toast("info", `Emergency stop on — ${res.cancelled_jobs} job(s) cancelled, external services blocked.`);
      reload();
      reloadIntegrations();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Action failed");
    }
  }

  const groups = [
    { title: "Publishing", prefix: "publish:" },
    { title: "Fetching links", prefix: "source:" },
    { title: "AI services", prefix: "provider:" },
  ];

  return (
    <SettingsSection id="automation" title="Automation & safety" description="Stop everything instantly, or switch individual services off.">
      <Row className="flex flex-wrap items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">
            {control.emergency_stop ? "Emergency stop is on" : control.paused ? "Automation is paused" : "Automation is running"}
          </p>
          <p className="mt-0.5 text-[12px] text-muted">
            {control.emergency_stop ? "All jobs were cancelled and no external service is contacted until you clear it."
              : control.paused ? "Nothing new starts. Work in progress waits where it is." : "New links are processed and scheduled posts go out within your limits."}
          </p>
        </div>
        {editable && (
          <div className="flex gap-2">
            {control.emergency_stop ? (
              <Button size="sm" variant="secondary" onClick={() => act("clear_emergency")}>Clear emergency stop</Button>
            ) : control.paused ? (
              <Button size="sm" icon={<Play className="size-3.5" />} onClick={() => act("resume")}>Resume</Button>
            ) : (
              <Button size="sm" variant="secondary" icon={<Pause className="size-3.5" />} onClick={() => act("pause")}>Pause</Button>
            )}
            {!control.emergency_stop && <Button size="sm" variant="danger" icon={<OctagonX className="size-3.5" />} onClick={() => setConfirmStop(true)}>Emergency stop</Button>}
          </div>
        )}
      </Row>
      <Row className="grid gap-6 md:grid-cols-3">
        {groups.map((g) => (
          <div key={g.prefix} className="space-y-3">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-faint">{g.title}</p>
            {(integrations ?? []).filter((i) => i.name.startsWith(g.prefix)).map((i) => (
              <Toggle key={i.name} checked={i.enabled} disabled={!editable} label={i.label}
                onChange={(on) => act(on ? "enable_integration" : "disable_integration", i.name)} />
            ))}
          </div>
        ))}
      </Row>
      <Row className="space-y-3">
        <p className="text-[13px] font-semibold">Storage clean-up</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <NumberField label="Keep downloaded sources" value={r.source_media_days} min={1} max={3650} suffix="days" disabled={!editable} onCommit={(v) => setRetention({ source_media_days: v })} />
          <NumberField label="Keep work files" value={r.work_files_days} min={1} max={365} suffix="days" disabled={!editable} onCommit={(v) => setRetention({ work_files_days: v })} />
          <NumberField label="Keep finished clips" value={r.keep_renders_days} min={1} max={3650} suffix="days" disabled={!editable} onCommit={(v) => setRetention({ keep_renders_days: v })} />
          <NumberField label="Live stream buffer" value={r.live_buffer_minutes} min={5} max={1440} suffix="min" disabled={!editable} onCommit={(v) => setRetention({ live_buffer_minutes: v })} />
        </div>
      </Row>
      <Modal open={confirmStop} onClose={() => setConfirmStop(false)} title="Emergency stop?"
        footer={<><Button variant="ghost" onClick={() => setConfirmStop(false)}>Cancel</Button><Button variant="danger" onClick={() => { setConfirmStop(false); void act("emergency_stop"); }}>Stop everything</Button></>}>
        <p className="text-sm text-muted">Every running and queued job is cancelled, nothing is published and no external AI or platform is contacted until you clear the stop. Finished clips are kept.</p>
      </Modal>
    </SettingsSection>
  );
}

export function UISection({ settings, save, editable }: SectionProps) {
  return (
    <Row>
      <Toggle checked={settings.ui.show_diagnostics} disabled={!editable} onChange={(v) => save("ui", { show_diagnostics: v })}
        label="Show technical details" description="Adds job logs, access checks and model routing details throughout the app." />
    </Row>
  );
}
