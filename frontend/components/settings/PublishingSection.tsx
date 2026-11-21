"use client";

import { browserZone } from "@/components/schedule";
import { Button, Input, Label, Select, Toggle } from "@/components/ui";
import type { PublishingSettings } from "@/lib/types";
import { Choice, Chips, NumberField, Row, SettingsSection, type SectionProps } from "./shared";

export function PublishingSection({ settings, save, editable }: SectionProps) {
  const p = settings.publishing;
  const set = (patch: Partial<PublishingSettings>) => save("publishing", patch);
  return (
    <SettingsSection id="publishing" title="Publishing" description="When and where finished clips go out.">
      <Row>
        <Choice<PublishingSettings["policy"]> value={p.policy} onChange={(policy) => set({ policy })} disabled={!editable} options={[
          { value: "approval_required", label: "Ask me first", description: "Clips wait for your approval before anything is posted." },
          { value: "auto_publish", label: "Publish automatically", description: "Clips that pass every quality check are posted within your daily limits." },
          { value: "draft_only", label: "Drafts only", description: "Never publish. Download or post the clips yourself." },
        ]} />
      </Row>
      <Row className="space-y-3">
        <Label>Platforms</Label>
        <Chips value={p.platforms} disabled={!editable} onChange={(platforms) => set({ platforms })}
          options={[{ value: "youtube", label: "YouTube Shorts" }, { value: "instagram", label: "Instagram Reels" }]} />
      </Row>
      <Row className="space-y-3">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label hint="for “tomorrow at 6pm” and daily limits">Time zone</Label>
            <Input defaultValue={p.timezone} key={p.timezone} disabled={!editable} placeholder="Europe/London"
              onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== p.timezone) set({ timezone: v }); }} />
            {editable && browserZone() !== p.timezone && (
              <Button size="sm" variant="ghost" className="mt-1.5" onClick={() => set({ timezone: browserZone() })}>
                Use this device's time zone ({browserZone()})
              </Button>
            )}
          </div>
          <NumberField label="Late posts: still publish up to (minutes)" value={p.late_grace_minutes} min={5} max={10080} disabled={!editable}
            onCommit={(v) => set({ late_grace_minutes: v })} />
        </div>
        <p className="text-[12px] text-muted">
          Public YouTube videos scheduled at least 15 minutes ahead are handed to YouTube's own scheduler and go live even if this computer is off.
          Instagram posts (and unlisted or private YouTube videos) are posted by CLIPFORGE at their time; if CLIPFORGE wasn't running then, a post that
          would be later than the limit above waits for you instead of going out late.
        </p>
      </Row>
      <Row className="grid gap-4 sm:grid-cols-2">
        <NumberField label="YouTube uploads per day" value={p.max_per_day_youtube} min={0} max={50} disabled={!editable} onCommit={(v) => set({ max_per_day_youtube: v })} />
        <NumberField label="Instagram posts per day" value={p.max_per_day_instagram} min={0} max={25} disabled={!editable} onCommit={(v) => set({ max_per_day_instagram: v })} />
      </Row>
      <Row className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label>YouTube visibility</Label>
            <Select value={p.default_privacy} onChange={(v) => set({ default_privacy: v as PublishingSettings["default_privacy"] })}
              options={[{ value: "private", label: "Private" }, { value: "unlisted", label: "Unlisted" }, { value: "public", label: "Public" }]} />
          </div>
          <div>
            <Label hint="optional">Add to YouTube playlist (ID)</Label>
            <Input defaultValue={p.youtube_playlist_id ?? ""} disabled={!editable} placeholder="PL…"
              onBlur={(e) => { const v = e.target.value.trim() || null; if (v !== p.youtube_playlist_id) set({ youtube_playlist_id: v }); }} />
          </div>
        </div>
        <Toggle checked={p.upload_youtube_captions} disabled={!editable} onChange={(v) => set({ upload_youtube_captions: v })}
          label="Upload a caption track to YouTube" description="Adds the SRT file as closed captions alongside the burned-in captions." />
        <Toggle checked={p.instagram_share_to_feed} disabled={!editable} onChange={(v) => set({ instagram_share_to_feed: v })}
          label="Also show Reels in the Instagram feed" />
        <Toggle checked={p.made_for_kids} disabled={!editable} onChange={(v) => set({ made_for_kids: v })}
          label="Content is made for kids" description="Required by YouTube for children's content. Comments and some features are limited." />
      </Row>
    </SettingsSection>
  );
}
