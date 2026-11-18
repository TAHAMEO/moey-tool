"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

/** Fetch a resource and keep it fresh while `poll` is truthy. */
export function useResource<T>(path: string | null, { poll = 0 }: { poll?: number } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const mounted = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const value = await api<T>(path);
      if (mounted.current) {
        setData(value);
        setError(null);
      }
    } catch (err) {
      if (mounted.current) setError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      if (mounted.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    mounted.current = true;
    setLoading(Boolean(path));
    load();
    return () => {
      mounted.current = false;
    };
  }, [load, path]);

  useEffect(() => {
    if (!poll || !path) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, poll);
    return () => clearInterval(timer);
  }, [poll, path, load]);

  return { data, error, loading, reload: load, setData };
}
