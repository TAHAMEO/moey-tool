"use client";

import { Music, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/status";
import { useToast } from "@/components/toast";
import { Button, Input, Label, Modal, ProgressBar, Select } from "@/components/ui";
import { api, timeAgo, uploadInPieces } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";
import type { LibraryItem } from "@/lib/types";
import { Row, SettingsSection } from "./shared";

const KINDS = [
  { value: "music", label: "Music", accept: ".mp3,.m4a,.wav,.aac,.flac,.ogg" },
  { value: "sfx", label: "Sound effect", accept: ".mp3,.m4a,.wav,.ogg" },
  { value: "broll", label: "B-roll video", accept: ".mp4,.mov,.webm,.mkv" },
  { value: "logo", label: "Logo / watermark (PNG)", accept: ".png" },
  { value: "master", label: "Original master of one of my videos", accept: ".mp4,.mov,.mkv,.webm,.m4v" },
];

export function LibrarySection() {
  const toast = useToast();
  const { can } = useAuth();
  const { data, reload } = useResource<LibraryItem[]>("/library");
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [form, setForm] = useState({ kind: "music", title: "", license_note: "", tags: "", rights: false });
  const [progress, setProgress] = useState<number | null>(null);
  const kind = KINDS.find((k) => k.value === form.kind)!;

  async function upload() {
    if (!file) return;
    setProgress(0);
    try {
      // In pieces of a few MB: one big request is cut off at 10 MB by the Windows app's web server.
      const uploadId = await uploadInPieces(file, "library", setProgress);
      await api("/library/from-upload", {
        body: { upload_id: uploadId, kind: form.kind, title: form.title, license_note: form.license_note, tags: form.tags,
                rights_confirmed: form.rights },
      });
      toast("success", "Added to your library.");
      setOpen(false);
      setFile(null);
      setForm({ kind: "music", title: "", license_note: "", tags: "", rights: false });
      reload();
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Upload failed");
    } finally {
      setProgress(null);
    }
  }

  async function remove(item: LibraryItem) {
    await api(`/library/${item.id}`, { method: "DELETE" });
    toast("success", `${item.title} removed.`);
    reload();
  }

  return (
    <SettingsSection id="library" title="Media library"
      description="Music, sound effects, B-roll and logos you own or licensed. CLIPFORGE never adds stock or third-party media on its own.">
      <Row className="flex justify-end">
        {can("editor") && <Button size="sm" variant="secondary" icon={<Upload className="size-3.5" />} onClick={() => setOpen(true)}>Add file</Button>}
      </Row>
      {(data ?? []).length === 0 && <Row className="text-[13px] text-muted">Your library is empty.</Row>}
      {(data ?? []).map((item) => (
        <Row key={item.id} className="flex items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-muted"><Music className="size-4" /></div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium">{item.title}</p>
            <p className="truncate text-[12px] text-muted">{KINDS.find((k) => k.value === item.kind)?.label} · {item.license_note || "rights confirmed"} · {timeAgo(item.created_at)}</p>
          </div>
          {item.rights_confirmed && <Badge tone="success">Rights confirmed</Badge>}
          {can("editor") && (
            <button aria-label={`Remove ${item.title}`} onClick={() => remove(item)} className="rounded-lg p-1.5 text-muted hover:bg-danger-soft hover:text-danger"><Trash2 className="size-4" /></button>
          )}
        </Row>
      ))}

      <Modal open={open} onClose={() => setOpen(false)} title="Add to media library"
        footer={<><Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button><Button disabled={!file || !form.rights} loading={progress !== null} onClick={upload}>Upload</Button></>}>
        <div className="space-y-4">
          <div><Label>Type</Label><Select value={form.kind} onChange={(v) => { setForm({ ...form, kind: v }); setFile(null); }} options={KINDS} /></div>
          <div>
            <Label>File</Label>
            <input type="file" accept={kind.accept} onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="block w-full text-[13px] file:mr-3 file:rounded-lg file:border-0 file:bg-surface-2 file:px-3 file:py-2 file:text-[13px] file:font-medium" />
            {form.kind === "master" && <p className="mt-1 text-[12px] text-muted">Name the file after the video's YouTube title. CLIPFORGE matches it by name, checks the length matches, then cuts clips from this full-quality original.</p>}
          </div>
          <div><Label hint="optional">Title</Label><Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></div>
          <div><Label hint="where it's from / licence">Licence note</Label><Input value={form.license_note} placeholder="e.g. Purchased from … licence #…" onChange={(e) => setForm({ ...form, license_note: e.target.value })} /></div>
          <div><Label hint="comma separated">Tags</Label><Input value={form.tags} placeholder="calm, upbeat" onChange={(e) => setForm({ ...form, tags: e.target.value })} /></div>
          <label className="flex items-start gap-2.5 text-[13px]">
            <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]" checked={form.rights} onChange={(e) => setForm({ ...form, rights: e.target.checked })} />
            <span>I own this file or hold a licence that allows using it in videos I publish.</span>
          </label>
          {progress !== null && <ProgressBar value={progress} />}
        </div>
      </Modal>
    </SettingsSection>
  );
}
