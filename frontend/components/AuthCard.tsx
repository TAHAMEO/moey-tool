"use client";

import { Sparkles } from "lucide-react";
import type { ReactNode } from "react";

export function AuthCard({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="w-full max-w-[400px] animate-fade-up">
        <div className="mb-8 flex items-center gap-2.5">
          <div className="flex size-9 items-center justify-center rounded-xl bg-primary text-primary-fg shadow-sm">
            <Sparkles className="size-5" />
          </div>
          <span className="text-[15px] font-semibold tracking-tight">CLIPFORGE AI</span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="mt-1.5 text-sm text-muted">{subtitle}</p>
        <div className="mt-6 rounded-2xl border border-border bg-surface p-6 shadow-card">{children}</div>
      </div>
    </main>
  );
}
