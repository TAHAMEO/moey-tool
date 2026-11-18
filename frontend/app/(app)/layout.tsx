"use client";

import clsx from "clsx";
import { BarChart3, Bot, Brain, Clapperboard, GraduationCap, LogOut, Menu, Moon, OctagonPause, Settings, Sparkles, Sun, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { useResource } from "@/lib/hooks";

const NAV = [
  { href: "/", label: "Brain Chat", icon: Brain },
  { href: "/dashboard", label: "Dashboard", icon: BarChart3 },
  { href: "/videos", label: "My Videos", icon: Clapperboard },
  { href: "/connections", label: "AI Connections", icon: Bot },
  { href: "/learning", label: "Learning Center", icon: GraduationCap },
  { href: "/settings", label: "Settings", icon: Settings },
];

function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark" | null>(null);
  useEffect(() => {
    const current = document.documentElement.dataset.theme as "light" | "dark" | undefined;
    setTheme(current ?? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  }, []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("cf-theme", next);
    } catch {
      /* storage may be unavailable */
    }
    setTheme(next);
  };
  return (
    <button onClick={toggle} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-text" aria-label="Toggle theme">
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { me, ready, signOut } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { data: settings } = useResource<{ control: { paused: boolean; emergency_stop: boolean } }>(me ? "/settings" : null, { poll: 15000 });

  useEffect(() => setOpen(false), [pathname]);

  if (!ready || !me) {
    return (
      <div className="flex min-h-dvh items-center justify-center">
        <Spinner className="size-6" />
      </div>
    );
  }

  const control = settings?.control;
  const nav = (
    <nav className="flex flex-col gap-0.5">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            className={clsx(
              "flex items-center gap-3 rounded-[10px] px-3 py-2 text-sm font-medium transition-colors",
              active ? "bg-primary-soft text-primary" : "text-muted hover:bg-surface-2 hover:text-text",
            )}
          >
            <Icon className="size-[18px]" aria-hidden />
            {label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="flex min-h-dvh">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col border-r border-border bg-surface px-3 py-4 lg:flex">
        <Link href="/" className="mb-6 flex items-center gap-2.5 px-2">
          <div className="flex size-8 items-center justify-center rounded-[10px] bg-primary text-primary-fg">
            <Sparkles className="size-[18px]" />
          </div>
          <span className="text-[15px] font-semibold tracking-tight">CLIPFORGE</span>
        </Link>
        {nav}
        <div className="mt-auto border-t border-border pt-3">
          <div className="flex items-center justify-between px-2">
            <div className="min-w-0">
              <p className="truncate text-[13px] font-medium">{me.display_name || me.email}</p>
              <p className="text-[12px] capitalize text-faint">{me.role}</p>
            </div>
            <div className="flex">
              <ThemeToggle />
              <button onClick={signOut} className="rounded-lg p-2 text-muted hover:bg-surface-2 hover:text-text" aria-label="Sign out">
                <LogOut className="size-4" />
              </button>
            </div>
          </div>
        </div>
      </aside>

      {open && (
        <div className="fixed inset-0 z-40 bg-black/40 lg:hidden" onClick={() => setOpen(false)}>
          <aside className="h-full w-64 bg-surface px-3 py-4 shadow-card" onClick={(e) => e.stopPropagation()}>
            <div className="mb-6 flex items-center justify-between px-2">
              <span className="text-[15px] font-semibold">CLIPFORGE</span>
              <button onClick={() => setOpen(false)} aria-label="Close menu" className="rounded-lg p-1 text-muted">
                <X className="size-5" />
              </button>
            </div>
            {nav}
            <div className="mt-6 flex gap-1 px-1">
              <ThemeToggle />
              <button onClick={signOut} className="rounded-lg p-2 text-muted hover:bg-surface-2" aria-label="Sign out">
                <LogOut className="size-4" />
              </button>
            </div>
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-border bg-surface/90 px-4 backdrop-blur lg:hidden">
          <button onClick={() => setOpen(true)} aria-label="Open menu" className="rounded-lg p-1.5 text-muted hover:bg-surface-2">
            <Menu className="size-5" />
          </button>
          <span className="text-[15px] font-semibold">CLIPFORGE</span>
        </header>
        {control && (control.paused || control.emergency_stop) && (
          <div className={clsx("flex items-center gap-2 px-4 py-2 text-[13px] font-medium",
            control.emergency_stop ? "bg-danger-soft text-danger" : "bg-warning-soft text-warning")}>
            <OctagonPause className="size-4" />
            {control.emergency_stop
              ? "Emergency stop is on — no jobs, publishing or external AI calls run."
              : "Automation is paused — new jobs wait until you resume."}
            <Link href="/dashboard" className="ml-auto underline underline-offset-2">Manage</Link>
          </div>
        )}
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
