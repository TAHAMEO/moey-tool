"use client";

import clsx from "clsx";
import { ChevronDown, ListChecks, Sparkles, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import type { Card, Message } from "@/lib/types";
import { JobCard } from "./JobCard";

function RichText({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  return (
    <div className="space-y-2.5">
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        if (lines.every((l) => /^\s*(•|-|\*)\s/.test(l))) {
          return (
            <ul key={i} className="space-y-1">
              {lines.map((l, j) => (
                <li key={j} className="flex gap-2">
                  <span className="mt-2 size-1 shrink-0 rounded-full bg-current opacity-50" />
                  <span>{l.replace(/^\s*(•|-|\*)\s/, "")}</span>
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i} className="whitespace-pre-wrap">
            {block}
          </p>
        );
      })}
    </div>
  );
}

function SettingsSuggestion({ card }: { card: Extract<Card, { type: "settings_suggestion" }> }) {
  const toast = useToast();
  const [done, setDone] = useState(false);
  const labels: Record<string, string> = { max_seconds: "Max length", min_seconds: "Min length", caption_style: "Caption style",
    default_count: "Clips per video", aspects: "Formats", remove_silences: "Remove pauses", punch_in_zoom: "Punch-in zoom",
    burn_captions: "Burned-in captions" };
  return (
    <div className="mt-3 max-w-xl rounded-xl border border-border bg-surface p-3">
      <p className="text-[13px] font-medium">Make this a default setting?</p>
      <ul className="mt-1.5 text-[13px] text-muted">
        {Object.entries(card.patch).map(([k, v]) => (
          <li key={k}>{labels[k] ?? k}: <span className="text-text">{Array.isArray(v) ? v.join(", ") : String(v)}{k.endsWith("seconds") ? "s" : ""}</span></li>
        ))}
      </ul>
      <Button size="sm" className="mt-2.5" disabled={done} onClick={async () => {
        try {
          await api(`/settings/${card.section}`, { method: "PATCH", body: card.patch });
          setDone(true);
          toast("success", "Default updated.");
        } catch (err) {
          toast("error", err instanceof Error ? err.message : "Could not update");
        }
      }}>{done ? "Applied" : "Apply as default"}</Button>
    </div>
  );
}

function MemoryMatches({ card }: { card: Extract<Card, { type: "memory_matches" }> }) {
  const toast = useToast();
  const [items, setItems] = useState(card.items);
  if (!items.length) return null;
  return (
    <ul className="mt-3 max-w-xl space-y-1.5">
      {items.map((item) => (
        <li key={item.id} className="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface px-3 py-2 text-[13px]">
          <span>{item.memory}</span>
          <button aria-label="Delete memory" className="rounded-lg p-1.5 text-muted hover:bg-danger-soft hover:text-danger" onClick={async () => {
            await api(`/memory/${item.id}`, { method: "DELETE" });
            setItems(items.filter((i) => i.id !== item.id));
            toast("success", "Memory deleted.");
          }}>
            <Trash2 className="size-3.5" />
          </button>
        </li>
      ))}
    </ul>
  );
}

export function MessageView({ message }: { message: Message }) {
  const [showActions, setShowActions] = useState(false);
  const isUser = message.role === "user";
  const actions = message.meta?.actions ?? [];
  const cards = message.meta?.cards ?? [];
  if (isUser) {
    return (
      <div className="animate-fade-up flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-[14px] text-primary-fg shadow-sm sm:max-w-[70%]">
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          {(message.meta?.attachments?.length ?? 0) > 0 && <p className="mt-1 text-[12px] opacity-80">+ uploaded file</p>}
        </div>
      </div>
    );
  }
  return (
    <div className="animate-fade-up flex gap-3">
      <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-primary">
        <Sparkles className="size-4" />
      </div>
      <div className="min-w-0 flex-1 text-[14px] leading-relaxed text-text">
        <RichText text={message.content} />
        {actions.length > 0 && (
          <div className="mt-2">
            <button onClick={() => setShowActions(!showActions)} className="flex items-center gap-1.5 text-[12px] font-medium text-muted hover:text-text">
              <ListChecks className="size-3.5" /> What I did
              <ChevronDown className={clsx("size-3.5 transition-transform", showActions && "rotate-180")} />
            </button>
            {showActions && (
              <ol className="mt-2 space-y-1 border-l-2 border-border pl-3 text-[13px]">
                {actions.map((a, i) => (
                  <li key={i}><span className="font-medium">{a.step}</span> <span className="text-muted">— {a.detail}</span></li>
                ))}
              </ol>
            )}
          </div>
        )}
        {cards.map((card, i) => {
          if (card.type === "job") return <JobCard key={card.job_id} jobId={card.job_id} projectId={card.project_id} />;
          if (card.type === "settings_suggestion") return <SettingsSuggestion key={i} card={card} />;
          if (card.type === "memory_matches") return <MemoryMatches key={i} card={card} />;
          if (card.type === "link") return (
            <Link key={i} href={card.href} className="mt-3 inline-flex text-[13px] font-medium text-primary hover:underline">{card.label} →</Link>
          );
          return null;
        })}
      </div>
    </div>
  );
}
