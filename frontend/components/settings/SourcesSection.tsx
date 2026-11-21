"use client";

import { Toggle } from "@/components/ui";
import type { SourceSettings } from "@/lib/types";
import { Row, SettingsSection, type SectionProps } from "./shared";

export function SourcesSection({ settings, save, editable }: SectionProps) {
  const s = settings.sources;
  const set = (patch: Partial<SourceSettings>) => save("sources", patch);
  return (
    <SettingsSection id="sources" title="Sources" description="How CLIPFORGE gets the video behind a link you paste.">
      <Row className="space-y-3">
        <Toggle checked={s.public_downloads} disabled={!editable} onChange={(v) => set({ public_downloads: v })}
          label="Download public YouTube and Instagram videos"
          description="Uses yt-dlp to download videos anyone can watch without signing in. Your own files (Google Drive, media library, your Instagram account) are always tried first." />
        <ul className="list-disc space-y-1 pl-5 text-[12px] text-muted">
          <li>Only download videos you own or have permission to reuse. Clips made from another creator's video are never published automatically — you confirm each time.</li>
          <li>CLIPFORGE never signs in, uses cookies or fakes its location: private, members-only, age-restricted, paid and DRM-protected videos are reported, not worked around.</li>
          <li>Instagram shows many posts only to signed-in users; for those, connect your own account (if it's your Reel) or upload the file.</li>
        </ul>
      </Row>
    </SettingsSection>
  );
}
