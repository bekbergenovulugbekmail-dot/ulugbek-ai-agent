"use client";

/**
 * Data loading with explicit loading / error / empty states.
 *
 * Every page uses this, which is why loading and error handling look the same
 * everywhere instead of being reinvented per screen.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "@/lib/api";

export interface ResourceState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  /** True only on the first load — a refresh must not blank the screen. */
  loading: boolean;
  refreshing: boolean;
  reload: () => void;
}

export function useResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: unknown[] = [],
  options: { pollMs?: number; enabled?: boolean } = {},
): ResourceState<T> {
  const { pollMs, enabled = true } = options;
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<ApiError | undefined>(undefined);
  const [loading, setLoading] = useState(enabled);
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const loadedOnce = useRef(false);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (!enabled) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    let cancelled = false;

    const run = async () => {
      if (loadedOnce.current) setRefreshing(true);
      try {
        const result = await loaderRef.current(controller.signal);
        if (cancelled) return;
        setData(result);
        setError(undefined);
        loadedOnce.current = true;
      } catch (caught) {
        if (cancelled || controller.signal.aborted) return;
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError("Something went wrong loading this view."),
        );
      } finally {
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    };

    void run();
    const timer = pollMs ? setInterval(run, pollMs) : undefined;

    return () => {
      cancelled = true;
      controller.abort();
      if (timer) clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce, pollMs, enabled]);

  return { data, error, loading, refreshing, reload };
}
