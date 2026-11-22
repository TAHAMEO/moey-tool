"use client";

import { Input, Label, Select, Toggle } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import type { BrandSettings, ClipSettings, LibraryItem, ShortsSettings } from "@/lib/types";
import { Chips, NumberField, Row, SettingsSection, type SectionProps } from "./shared";

const CAPTION_STYLES: { value: ClipSettings["caption_style"]; label: string }[] = [
  { value: "bold_pop", label: "Bold pop — big words, active word highlighted" },
  { value: "karaoke_fill", label: "Karaoke — words fill in as they're spoken" },
  { value: "clean_lower", label: "Clean — understated lower captions" },
  { value: "boxed", label: "Boxed — text on a solid band" },
];

export function ClipsSection({ settings, save, editable }: SectionProps) {
  const c = settings.clips;
  const set = (patch: Partial<ClipSettings>) => save("clips", patch);
  return (
    <SettingsSection id="clips" title="Clips & editing" description="Defaults for every new video. You can override them in Brain Chat, e.g. “make 3 clips under 30 seconds”.">
      <Row className="grid gap-4 sm:grid-cols-3">
        <NumberField label="Clips per video" value={c.default_count} min={1} max={30} disabled={!editable} onCommit={(v) => set({ default_count: v })} />
        <NumberField label="Shortest" value={c.min_seconds} min={3} max={180} suffix="sec" disabled={!editable} onCommit={(v) => set({ min_seconds: v })} />
        <NumberField label="Longest" value={c.max_seconds} min={5} max={900} suffix="sec" disabled={!editable} onCommit={(v) => set({ max_seconds: v })} />
      </Row>
      <Row className="space-y-3">
        <Label>Formats</Label>
        <Chips value={c.aspects} disabled={!editable} onChange={(aspects) => aspects.length && set({ aspects })} options={[
          { value: "9:16", label: "Vertical 9:16" }, { value: "1:1", label: "Square 1:1" }, { value: "16:9", label: "Landscape 16:9" }]} />
      </Row>
      <Row className="space-y-4">
        <div>
          <Label>Caption style</Label>
          <Select value={c.caption_style} onChange={(v) => set({ caption_style: v as ClipSettings["caption_style"] })} options={CAPTION_STYLES} />
        </div>
        <Toggle checked={c.burn_captions} disabled={!editable} onChange={(v) => set({ burn_captions: v })}
          label="Burn captions into the video" description="Required for Instagram Reels, which has no separate caption upload." />
        <div>
          <Label hint="comma separated codes, e.g. en, es">Caption languages</Label>
          <Input defaultValue={c.languages.join(", ")} disabled={!editable}
            onBlur={(e) => {
              const langs = e.target.value.split(",").map((l) => l.trim().toLowerCase()).filter(Boolean);
              if (langs.length && langs.join(",") !== c.languages.join(",")) set({ languages: langs });
            }} />
        </div>
      </Row>
      <Row className="space-y-4">
        <Toggle checked={c.remove_silences} disabled={!editable} onChange={(v) => set({ remove_silences: v })}
          label="Remove dead air" description="Cuts pauses longer than the threshold below, keeping natural breathing room." />
        {c.remove_silences && (
          <NumberField label="Cut pauses longer than" value={c.min_silence_to_cut_s} min={0.2} max={5} step={0.1} suffix="sec" disabled={!editable} onCommit={(v) => set({ min_silence_to_cut_s: v })} />
        )}
        <Toggle checked={c.punch_in_zoom} disabled={!editable} onChange={(v) => set({ punch_in_zoom: v })}
          label="Subtle zoom on cuts" description="A gentle punch-in hides jump cuts and keeps the frame moving." />
        <NumberField label="Loudness target" value={c.loudness_lufs} min={-30} max={-8} step={0.5} suffix="LUFS" disabled={!editable} onCommit={(v) => set({ loudness_lufs: v })} />
      </Row>
      <Row className="space-y-4">
        <Toggle checked={c.use_licensed_music} disabled={!editable} onChange={(v) => set({ use_licensed_music: v })}
          label="Add background music from my library" description="Only tracks you uploaded and confirmed you hold the rights to are ever used." />
        {c.use_licensed_music && (
          <NumberField label="Music volume" value={c.music_volume_db} min={-40} max={-6} step={1} suffix="dB" disabled={!editable} onCommit={(v) => set({ music_volume_db: v })} />
        )}
      </Row>
      <ShortsRows settings={settings} save={save} editable={editable} />
    </SettingsSection>
  );
}

export function BrandSection({ settings, save, editable }: SectionProps) {
  const b = settings.brand;
  const set = (patch: Partial<BrandSettings>) => save("brand", patch);
  const { data: library } = useResource<LibraryItem[]>("/library");
  const logos = (library ?? []).filter((i) => i.kind === "logo");
  return (
    <SettingsSection id="brand" title="Brand" description="Applied to captions and overlays on every clip.">
      <Row className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label>Channel name</Label>
          <Input defaultValue={b.channel_name} disabled={!editable} onBlur={(e) => e.target.value !== b.channel_name && set({ channel_name: e.target.value })} />
        </div>
        <div>
          <Label hint="highlights the spoken caption word and cover text">Accent colour</Label>
          <div className="flex items-center gap-2">
            <input type="color" aria-label="Accent colour" value={b.accent_color} disabled={!editable}
              onChange={(e) => set({ accent_color: e.target.value.toUpperCase() })}
              className="h-10 w-14 cursor-pointer rounded-lg border border-border bg-surface p-1" />
            <span className="font-mono text-[13px] text-muted">{b.accent_color}</span>
          </div>
        </div>
      </Row>
      <Row className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label hint="PNG from your media library">Watermark</Label>
          <Select value={b.watermark_library_item_id ?? ""} onChange={(v) => set({ watermark_library_item_id: v || null })}
            options={[{ value: "", label: logos.length ? "No watermark" : "Upload a PNG logo below first" }, ...logos.map((l) => ({ value: l.id, label: l.title }))]} />
        </div>
        <div>
          <Label>Watermark position</Label>
          <Select value={b.watermark_position} onChange={(v) => set({ watermark_position: v })} options={[
            { value: "top_left", label: "Top left" }, { value: "top_right", label: "Top right" },
            { value: "bottom_left", label: "Bottom left" }, { value: "bottom_right", label: "Bottom right" }]} />
        </div>
      </Row>
    </SettingsSection>
  );
}

/** First-3-seconds hook and loop switches (each on its own; all off until you turn them on). */
function ShortsRows({ settings, save, editable }: SectionProps) {
  const s = settings.shorts;
  if (!s) return null;
  const set = (patch: Partial<ShortsSettings>) => save("shorts", patch);
  return (
    <>
      <Row className="space-y-4">
        <Label>Shorts: the first 3 seconds</Label>
        <Toggle checked={s.hook_scorer} disabled={!editable} onChange={(v) => set({ hook_scorer: v })}
          label="Score the first 3 seconds of every clip" description="Frame-one clarity, how soon the first line lands, the curiosity it opens, swipe-away risk and the on-screen text." />
        <Toggle checked={s.hook_reconstruct} disabled={!editable || !s.hook_scorer} onChange={(v) => set({ hook_reconstruct: v })}
          label="Fix weak openings" description="Cuts greetings, filler and slow starts, makes the first line land within 1.5 s and adds a bold text hook on frame one — made only from the clip's own words." />
        <Toggle checked={s.hook_gate} disabled={!editable || !s.hook_scorer} onChange={(v) => set({ hook_gate: v })}
          label="Hold clips with a weak opening for review" description="They're never published automatically; you can still publish them yourself." />
        <Toggle checked={s.tension_first} disabled={!editable || !s.hook_reconstruct} onChange={(v) => set({ tension_first: v })}
          label="Open on the question" description="When a clip gives the answer first, start it with the speaker's own question from later in the clip. Changes the order of what was said — off unless you want it." />
        <div className="grid gap-4 sm:grid-cols-2">
          <NumberField label="Hold below hook score" value={Math.round(s.hook_threshold * 100)} min={0} max={100} suffix="/100" disabled={!editable} onCommit={(v) => set({ hook_threshold: v / 100 })} />
          <NumberField label="First line within" value={s.max_first_line_s} min={0.3} max={5} step={0.1} suffix="sec" disabled={!editable} onCommit={(v) => set({ max_first_line_s: v })} />
        </div>
      </Row>
      <Row className="space-y-4">
        <Label>Shorts: loop</Label>
        <Toggle checked={s.loop_seam} disabled={!editable} onChange={(v) => set({ loop_seam: v })}
          label="Make clips loop" description="Ends on a sentence that flows back into the opening (shorter when that stays complete); otherwise blends the last frames into the first so the replay doesn't jump." />
        <div className="grid gap-4 sm:grid-cols-3">
          <NumberField label="Prefer under" value={s.prefer_max_s} min={10} max={90} suffix="sec" disabled={!editable} onCommit={(v) => set({ prefer_max_s: v })} />
          <NumberField label="Never shorter than" value={s.min_s} min={3} max={60} suffix="sec" disabled={!editable} onCommit={(v) => set({ min_s: v })} />
          <NumberField label="Never longer than" value={s.max_s} min={15} max={180} suffix="sec" disabled={!editable} onCommit={(v) => set({ max_s: v })} />
        </div>
      </Row>
    </>
  );
}
