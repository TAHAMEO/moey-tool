"use client";

import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, setCsrf } from "./api";
import type { Me } from "./types";

type AuthState = {
  me: Me | null;
  ready: boolean;
  signIn: (me: Me) => void;
  signOut: () => Promise<void>;
  can: (role: "viewer" | "editor" | "admin" | "owner") => boolean;
};

const ROLES = ["viewer", "editor", "admin", "owner"];
const AuthContext = createContext<AuthState>({
  me: null,
  ready: false,
  signIn: () => {},
  signOut: async () => {},
  can: () => false,
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const user = await api<Me>("/auth/me");
        if (cancelled) return;
        setCsrf(user.csrf_token);
        setMe(user);
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 401) {
          const status = await api<{ setup_required: boolean }>("/auth/status").catch(() => ({ setup_required: false }));
          const target = status.setup_required ? "/setup" : "/login";
          if (pathname !== target) router.replace(target);
        }
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const signIn = useCallback((user: Me) => {
    setCsrf(user.csrf_token);
    setMe(user);
  }, []);

  const signOut = useCallback(async () => {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    setCsrf(null);
    setMe(null);
    router.replace("/login");
  }, [router]);

  const can = useCallback(
    (role: "viewer" | "editor" | "admin" | "owner") => Boolean(me && ROLES.indexOf(me.role) >= ROLES.indexOf(role)),
    [me],
  );

  return <AuthContext.Provider value={{ me, ready, signIn, signOut, can }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  return useContext(AuthContext);
}
