"use client";

import clsx from "clsx";
import { CalendarClock, Clock, Info } from "lucide-react";
import { Input, Label, Select } from "@/components/ui";

/** The browser's IANA time zone ("Europe/London"); UTC if the browser won't say. */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const pad = (n: number) => String(n).padStart(2, "0");

/** Date → value for <input type="datetime-local"> (local wall-clock time). */
export function toLocalInput(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** "Sat 22 Nov, 18:00" in the viewer's own time zone. */
export function formatWhen(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  const sameYear = date.getFullYear() === new Date().getFullYear();
  return date.toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short", year: sameYear ? undefined : "numeric", hour: "2-digit", minute: "2-digit",
  });
}

export type ScheduleChoice = { mode: "now" | "later"; at: string; everyMinutes: number | null };

export function defaultChoice(initial?: string | null, everyMinutes?: number | null): ScheduleChoice {
  if (initial && new Date(initial).getTime() > Date.now()) {
    return { mode: "later", at: toLocalInput(new Date(initial)), everyMinutes: everyMinutes ?? null };
  }
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(18, 0, 0, 0);
  return { mode: initial ? "later" : "now", at: toLocalInput(tomorrow), everyMinutes: everyMinutes ?? null };
}

/** ISO time to send to the API, or null for "now" / an invalid or past time. */
export function choiceIso(choice: ScheduleChoice): string | null {
  if (choice.mode === "now") return null;
  const date = new Date(choice.at); // "YYYY-MM-DDTHH:mm" is read as local time
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function choiceProblem(choice: ScheduleChoice): string | null {
  if (choice.mode === "now") return null;
  const iso = choiceIso(choice);
  if (!iso) return "Pick a date and time.";
  if (new Date(iso).getTime() < Date.now() - 60_000) return "That time has already passed.";
  return null;
}

function quickPicks(): { label: string; date: Date }[] {
  const at = (days: number, hours: number) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    d.setHours(hours, 0, 0, 0);
    return d;
  };
  const inHour = new Date(Date.now() + 60 * 60 * 1000);
  inHour.setSeconds(0, 0);
  const tonight = at(0, 20);
  return [
    { label: "In 1 hour", date: inHour },
    ...(tonight.getTime() > Date.now() + 15 * 60 * 1000 ? [{ label: "Tonight 20:00", date: tonight }] : []),
    { label: "Tomorrow 09:00", date: at(1, 9) },
    { label: "Tomorrow 18:00", date: at(1, 18) },
  ];
}

const SPACING = [
  { value: "", label: "All at the same time" },
  { value: "60", label: "One every hour" },
  { value: "180", label: "One every 3 hours" },
  { value: "1440", label: "One per day" },
  { value: "2880", label: "One every 2 days" },
  { value: "10080", label: "One per week" },
];

/**
 * "Publish now" or an exact date and time, with what will actually happen on each platform.
 * ``platforms`` and ``privacy`` (YouTube visibility) decide the explanation.
 */
export function SchedulePicker({ value, onChange, platforms, privacy, clips = 1, nowLabel = "Publish now" }: {
  value: ScheduleChoice;
  onChange: (value: ScheduleChoice) => void;
  platforms: string[];
  privacy?: string;
  clips?: number;
  nowLabel?: string;
}) {
  const iso = choiceIso(value);
  const lead = iso ? new Date(iso).getTime() - Date.now() : 0;
  const problem = choiceProblem(value);
  const notes: string[] = [];
  if (value.mode === "later") {
    if (platforms.includes("youtube")) {
      notes.push(privacy === "public" && lead >= 15 * 60 * 1000
        ? "YouTube: uploaded now as private — YouTube itself makes it public at this time, even if this computer is off."
        : "YouTube: uploaded about 10 minutes before and switched live by CLIPFORGE at this time — keep CLIPFORGE running."
          + (privacy !== "public" ? " (Choose Public visibility to let YouTube schedule it instead.)" : ""));
    }
    if (platforms.includes("instagram")) {
      notes.push("Instagram: prepared about 10 minutes before and posted by CLIPFORGE at this time (Instagram's API can't schedule) — keep CLIPFORGE running.");
    }
  }
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-1 rounded-xl bg-surface-2 p-1" role="radiogroup" aria-label="When to publish">
        {(["now", "later"] as const).map((mode) => (
          <button key={mode} type="button" role="radio" aria-checked={value.mode === mode}
            onClick={() => onChange({ ...value, mode })}
            className={clsx("flex items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-[13px] font-medium transition-colors",
              value.mode === mode ? "bg-surface text-text shadow-card" : "text-muted hover:text-text")}>
            {mode === "now" ? <Clock className="size-3.5" /> : <CalendarClock className="size-3.5" />}
            {mode === "now" ? nowLabel : "Schedule"}
          </button>
        ))}
      </div>
      {value.mode === "later" && (
        <>
          <div>
            <Label hint={`your time zone: ${browserZone()}`}>Date and time</Label>
            <Input type="datetime-local" value={value.at} min={toLocalInput(new Date())}
              onChange={(e) => onChange({ ...value, at: e.target.value })} aria-invalid={Boolean(problem)} />
            {problem && <p className="mt-1 text-[12px] text-danger">{problem}</p>}
          </div>
          <div className="flex flex-wrap gap-1.5">
            {quickPicks().map((pick) => (
              <button key={pick.label} type="button" onClick={() => onChange({ ...value, at: toLocalInput(pick.date) })}
                className="rounded-lg border border-border bg-surface px-2.5 py-1 text-[12px] hover:border-primary hover:text-primary">
                {pick.label}
              </button>
            ))}
          </div>
          {clips > 1 && (
            <div>
              <Label hint={`${clips} clips`}>Spread them out</Label>
              <Select value={value.everyMinutes ? String(value.everyMinutes) : ""} options={SPACING}
                onChange={(v) => onChange({ ...value, everyMinutes: v ? Number(v) : null })} />
              <p className="mt-1 text-[12px] text-faint">In clip order; a clip going to both platforms goes out on both at the same time.</p>
            </div>
          )}
          {notes.length > 0 && (
            <ul className="space-y-1.5 rounded-xl bg-surface-2 p-3 text-[12px] text-muted">
              {notes.map((note) => (
                <li key={note} className="flex gap-2"><Info className="mt-0.5 size-3.5 shrink-0" />{note}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
