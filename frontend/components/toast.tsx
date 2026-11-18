"use client";

import clsx from "clsx";
import { CheckCircle2, Info, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Toast = { id: number; tone: "success" | "error" | "info"; message: string };

const ToastContext = createContext<(tone: Toast["tone"], message: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], message: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, tone, message }]);
    setTimeout(() => setToasts((current) => current.filter((t) => t.id !== id)), 4500);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(92vw,380px)] flex-col gap-2" aria-live="polite">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={clsx(
              "animate-fade-up pointer-events-auto flex items-start gap-2.5 rounded-xl border border-border bg-surface px-4 py-3 text-sm shadow-card",
            )}
          >
            {toast.tone === "success" && <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />}
            {toast.tone === "error" && <XCircle className="mt-0.5 size-4 shrink-0 text-danger" />}
            {toast.tone === "info" && <Info className="mt-0.5 size-4 shrink-0 text-info" />}
            <span className="text-text">{toast.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
