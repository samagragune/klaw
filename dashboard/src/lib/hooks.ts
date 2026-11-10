"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

interface AsyncState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  unauthorized: boolean;
  refresh: () => void;
}

/**
 * Fetch-on-mount with manual refresh and an optional poll.
 *
 * The poll is a safety net for state the WebSocket does not push (rollups
 * computed server-side, for instance). Live event streams come from
 * `useLiveMessages`, not from here.
 */
export function useAsync<T>(
  fn: () => Promise<T>,
  deps: unknown[] = [],
  options: { intervalMs?: number; enabled?: boolean } = {},
): AsyncState<T> {
  const { intervalMs, enabled = true } = options;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);
  const [loading, setLoading] = useState(enabled);
  const [tick, setTick] = useState(0);
  const latest = useRef(fn);
  latest.current = fn;

  const refresh = useCallback(() => setTick((value) => value + 1), []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    latest
      .current()
      .then((value) => {
        if (cancelled) return;
        setData(value);
        setError(null);
        setUnauthorized(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = err instanceof ApiError ? err.status : 0;
        setUnauthorized(status === 401);
        setError(err instanceof Error ? err.message : "Request failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, enabled, ...deps]);

  useEffect(() => {
    if (!intervalMs || !enabled) return;
    const timer = setInterval(refresh, intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, enabled, refresh]);

  return { data, error, loading, unauthorized, refresh };
}

/** Re-render on an interval, so relative timestamps stay honest. */
export function useTicker(ms = 5000) {
  const [, setTick] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setTick((value) => value + 1), ms);
    return () => clearInterval(timer);
  }, [ms]);
}
