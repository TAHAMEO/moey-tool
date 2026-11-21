"use client";

import clsx from "clsx";
import { ArrowUp, Film, Link2, MessageSquarePlus, Paperclip, PlayCircle, Radio, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { MessageView } from "@/components/chat/MessageView";
import { browserZone } from "@/components/schedule";
import { useToast } from "@/components/toast";
import { Button, ProgressBar, Spinner } from "@/components/ui";
import { api, platformName, timeAgo, uploadWithProgress } from "@/lib/api";
import type { Conversation, Message } from "@/lib/types";

const SUGGESTIONS = [
  { icon: PlayCircle, text: "Create 5 Shorts from this YouTube video: " },
  { icon: Film, text: "Process this Instagram Reel: " },
  { icon: Radio, text: "Watch this livestream and create clips: " },
  { icon: Sparkles, text: "What have you learned about my best clips?" },
];

function detectLinks(text: string): { platform: string; url: string }[] {
  const urls = text.match(/(https?:\/\/[^\s]+|(?:www\.)?(?:youtube\.com|youtu\.be|instagram\.com|drive\.google\.com)\/[^\s]+)/gi) ?? [];
  return urls.slice(0, 5).map((url) => {
    const u = url.toLowerCase();
    const platform = /youtu\.?be/.test(u) ? (/\/live\//.test(u) ? "YouTube livestream" : /\/shorts\//.test(u) ? "YouTube Short" : "YouTube video")
      : /instagram\.com/.test(u) ? (/\/p\//.test(u) ? "Instagram post" : "Instagram Reel")
      : /drive\.google\.com/.test(u) ? "Google Drive file" : "Direct link";
    return { platform, url };
  });
}

export default function BrainChatPage() {
  const toast = useToast();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [upload, setUpload] = useState<{ name: string; progress: number; assetId?: string } | null>(null);
  const [showList, setShowList] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const loadConversations = useCallback(async () => {
    const list = await api<Conversation[]>("/chat/conversations");
    setConversations(list);
    return list;
  }, []);

  useEffect(() => {
    loadConversations().then((list) => {
      if (list.length) setActive(list[0].id);
    }).catch(() => undefined);
  }, [loadConversations]);

  useEffect(() => {
    if (!active) {
      setMessages([]);
      return;
    }
    setLoadingMessages(true);
    api<Message[]>(`/chat/conversations/${active}/messages`)
      .then(setMessages)
      .catch(() => setMessages([]))
      .finally(() => setLoadingMessages(false));
  }, [active]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, sending]);

  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(200, el.scrollHeight)}px`;
  }, [text]);

  const links = useMemo(() => detectLinks(text), [text]);

  async function ensureConversation(): Promise<string> {
    if (active) return active;
    const created = await api<Conversation>("/chat/conversations", { body: {} });
    setConversations((c) => [created, ...c]);
    setActive(created.id);
    return created.id;
  }

  async function send(event?: React.FormEvent) {
    event?.preventDefault();
    const content = text.trim();
    if ((!content && !upload?.assetId) || sending) return;
    setSending(true);
    const optimistic: Message = { id: `tmp-${Date.now()}`, role: "user", content: content || "(uploaded a file)",
      meta: { attachments: upload?.assetId ? [upload.assetId] : [] } };
    setMessages((m) => [...m, optimistic]);
    setText("");
    const attachments = upload?.assetId ? [upload.assetId] : [];
    setUpload(null);
    try {
      const conversationId = await ensureConversation();
      const reply = await api<Message>(`/chat/conversations/${conversationId}/messages`,
        { body: { text: content, attachments, timezone: browserZone() } });
      setMessages((m) => [...m, reply]);
      loadConversations().catch(() => undefined);
    } catch (err) {
      toast("error", err instanceof Error ? err.message : "Message failed");
      setMessages((m) => m.filter((msg) => msg.id !== optimistic.id));
      setText(content);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }

  async function onFile(file: File) {
    setUpload({ name: file.name, progress: 0 });
    const form = new FormData();
    form.append("file", file);
    try {
      const result = await uploadWithProgress<{ asset_id: string }>("/chat/upload", form, (p) =>
        setUpload((u) => (u ? { ...u, progress: p } : u)));
      setUpload({ name: file.name, progress: 1, assetId: result.asset_id });
      if (!text.trim()) setText("Create the best Shorts from this upload");
    } catch (err) {
      setUpload(null);
      toast("error", err instanceof Error ? err.message : "Upload failed");
    }
  }

  const empty = !loadingMessages && messages.length === 0;

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] lg:h-dvh">
      <aside className={clsx("absolute z-20 h-full w-72 shrink-0 flex-col border-r border-border bg-surface md:static md:flex",
        showList ? "flex" : "hidden")}>
        <div className="flex items-center justify-between px-4 py-4">
          <span className="text-[13px] font-semibold text-muted">Conversations</span>
          <Button size="sm" variant="ghost" icon={<MessageSquarePlus className="size-4" />}
            onClick={() => { setActive(null); setMessages([]); setShowList(false); inputRef.current?.focus(); }}>
            New
          </Button>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {conversations.map((c) => (
            <button key={c.id} onClick={() => { setActive(c.id); setShowList(false); }}
              className={clsx("block w-full truncate rounded-lg px-3 py-2 text-left text-[13px] transition-colors",
                c.id === active ? "bg-primary-soft font-medium text-primary" : "text-muted hover:bg-surface-2 hover:text-text")}>
              <span className="block truncate">{c.title}</span>
              <span className="text-[11px] text-faint">{timeAgo(c.updated_at)}</span>
            </button>
          ))}
          {!conversations.length && <p className="px-3 text-[13px] text-faint">No conversations yet.</p>}
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5 md:hidden">
          <Button size="sm" variant="secondary" onClick={() => setShowList(!showList)}>Conversations</Button>
        </div>
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl space-y-6 px-4 py-6 sm:px-6">
            {loadingMessages && <div className="flex justify-center py-10"><Spinner className="size-5" /></div>}
            {empty && (
              <div className="animate-fade-up py-10 text-center sm:py-16">
                <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-primary text-primary-fg shadow-sm">
                  <Sparkles className="size-6" />
                </div>
                <h1 className="text-2xl font-semibold tracking-tight">What should we make today?</h1>
                <p className="mx-auto mt-2 max-w-md text-sm text-muted">
                  Paste a link and tell me what you want. I check what access is available, find the strongest moments, edit
                  captioned clips and prepare them for YouTube and Instagram.
                </p>
                <div className="mx-auto mt-6 grid max-w-xl gap-2 sm:grid-cols-2">
                  {SUGGESTIONS.map(({ icon: Icon, text: s }) => (
                    <button key={s} onClick={() => { setText(s); inputRef.current?.focus(); }}
                      className="flex items-center gap-2.5 rounded-xl border border-border bg-surface px-3.5 py-3 text-left text-[13px] text-muted shadow-card transition-colors hover:border-primary hover:text-text">
                      <Icon className="size-4 shrink-0 text-primary" /> {s.replace(/: $/, "…")}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m) => <MessageView key={m.id} message={m} />)}
            {sending && (
              <div className="flex items-center gap-3 text-[13px] text-muted">
                <div className="flex size-7 items-center justify-center rounded-lg bg-primary-soft text-primary"><Spinner className="text-primary" /></div>
                Working on it…
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <form onSubmit={send} className="border-t border-border bg-surface/80 px-4 py-3 backdrop-blur sm:px-6">
          <div className="mx-auto max-w-3xl">
            {(links.length > 0 || upload) && (
              <div className="mb-2 flex flex-wrap gap-1.5">
                {links.map((l) => (
                  <span key={l.url} className="inline-flex items-center gap-1 rounded-full bg-primary-soft px-2.5 py-1 text-[12px] font-medium text-primary">
                    <Link2 className="size-3" /> {platformName(l.platform)}
                  </span>
                ))}
                {upload && (
                  <span className="inline-flex min-w-40 items-center gap-2 rounded-full bg-surface-2 px-2.5 py-1 text-[12px]">
                    <Paperclip className="size-3" /> <span className="max-w-40 truncate">{upload.name}</span>
                    {upload.assetId ? null : <span className="w-16"><ProgressBar value={upload.progress} /></span>}
                    <button type="button" aria-label="Remove file" onClick={() => setUpload(null)}><X className="size-3" /></button>
                  </span>
                )}
              </div>
            )}
            <div className="flex items-end gap-2 rounded-2xl border border-border bg-surface p-2 shadow-card focus-within:border-primary">
              <button type="button" onClick={() => fileRef.current?.click()} aria-label="Attach a video or audio file"
                className="rounded-xl p-2 text-muted hover:bg-surface-2 hover:text-text">
                <Paperclip className="size-[18px]" />
              </button>
              <input ref={fileRef} type="file" hidden accept="video/*,audio/*"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }} />
              <textarea ref={inputRef} rows={1} value={text} onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                placeholder="Paste a YouTube or Instagram link and tell me what to make…"
                aria-label="Message the Brain"
                className="max-h-[200px] min-h-[40px] flex-1 resize-none bg-transparent px-1 py-2 text-[14px] text-text placeholder:text-faint focus:outline-none" />
              <Button type="submit" size="sm" className="size-9 rounded-xl px-0" aria-label="Send"
                disabled={(!text.trim() && !upload?.assetId) || sending || Boolean(upload && !upload.assetId)}>
                <ArrowUp className="size-4" />
              </Button>
            </div>
            <p className="mt-1.5 text-center text-[11px] text-faint">Links are processed only through access you're authorized to use. Uploads are optional.</p>
          </div>
        </form>
      </section>
    </div>
  );
}
