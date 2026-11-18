"use client";

import type { ReactNode } from "react";
import { useToast } from "@/components/toast";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import type { AllSettings } from "@/lib/types";

export type SectionProps = {
  settings: AllSettings;
  save: <K extends keyof AllSettings>(section: K, patch: Partial<AllSettings[K]>) => Promise<boolean>;
  editable: boolean;
};

/** Returns a saver that PATCHes one settings section and reports the outcome. */
export function useSettingsSaver(reload: () => void) {
  const toast = useToast();
  return async function save<K extends keyof AllSettings>(section: K, patch: Partial<AllSettings[K]>): Promise<boolean> {
    try {
      await api(`/settings/${section}`, { method: "PATCH", body: patch });
      reload();
      toast("success", "Saved.");
      return true;
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Could not save");
      return false;
    }
  };
}

export function SettingsSection({ id, title, description, children }: { id: string; title: string; description?: string; children: ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24">
      <div className="mb-3">
        <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-0.5 text-[13px] text-muted">{description}</p>}
      </div>
      <Card className="divide-y divide-border">{children}</Card>
    </section>
  );
}

export function Row({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`p-4 ${className}`}>{children}</div>;
}

/** Radio-card group for choices that deserve an explanation. */
export function Choice<T extends string>({ value, onChange, options, disabled }: {
  value: T; onChange: (v: T) => void; disabled?: boolean;
  options: { value: T; label: string; description: string }[];
}) {
  return (
    <div className="grid gap-2 sm:grid-cols-3" role="radiogroup">
      {options.map((o) => (
        <button key={o.value} role="radio" aria-checked={o.value === value} disabled={disabled} onClick={() => onChange(o.value)}
          className={`rounded-xl border p-3 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${o.value === value ? "border-primary bg-primary-soft" : "border-border hover:bg-surface-2"}`}>
          <p className={`text-[13px] font-semibold ${o.value === value ? "text-primary" : ""}`}>{o.label}</p>
          <p className="mt-0.5 text-[12px] text-muted">{o.description}</p>
        </button>
      ))}
    </div>
  );
}

/** Multi-select chips. */
export function Chips<T extends string>({ value, onChange, options, disabled }: {
  value: T[]; onChange: (v: T[]) => void; options: { value: T; label: string }[]; disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((o) => {
        const on = value.includes(o.value);
        return (
          <button key={o.value} aria-pressed={on} disabled={disabled}
            onClick={() => onChange(on ? value.filter((v) => v !== o.value) : [...value, o.value])}
            className={`rounded-full border px-3 py-1 text-[13px] font-medium transition-colors disabled:opacity-60 ${on ? "border-primary bg-primary-soft text-primary" : "border-border text-muted hover:text-text"}`}>
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Number input that commits on blur/enter, so typing doesn't spam saves. */
export function NumberField({ value, onCommit, min, max, step = 1, suffix, disabled, label }: {
  value: number; onCommit: (v: number) => void; min?: number; max?: number; step?: number; suffix?: string; disabled?: boolean; label: string;
}) {
  return (
    <label className="flex items-center justify-between gap-3 text-[13px]">
      <span className="text-text">{label}</span>
      <span className="flex items-center gap-1.5">
        <input key={value} type="number" defaultValue={value} min={min} max={max} step={step} disabled={disabled} aria-label={label}
          className="h-9 w-24 rounded-lg border border-border bg-surface px-2.5 text-right tabular-nums outline-none focus:border-primary disabled:opacity-60"
          onBlur={(e) => { const n = Number(e.target.value); if (!Number.isNaN(n) && n !== value) onCommit(n); }}
          onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
        {suffix && <span className="w-10 text-muted">{suffix}</span>}
      </span>
    </label>
  );
}
