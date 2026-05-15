// Copied from social-comment-automation reference; trimmed for solo deploy.
//
// GET-style data hook. Encapsulates the `useEffect + fetch + setLoading
// + setError` quartet so pages declare:
//   const { data, loading, error, refetch } = useApiQuery<T>(url);
// and never write a try/catch around axios again.
//
// `enabled: false` opts out of the initial fetch.
// `refetchInterval` (ms) re-runs the fetch on a timer — useful for the
// Inbox poll loop in Phase 5.

import { useCallback, useEffect, useState } from "react";

import apiClient, { getErrorMessage } from "../api/client";

interface QueryOptions {
  /** When false, skip the initial fetch. Default true. */
  enabled?: boolean;
  /** When set, refetch every N milliseconds. */
  refetchInterval?: number;
}

export interface UseApiQueryResult<T> {
  data: T | null;
  loading: boolean;
  error: string;
  refetch: () => Promise<void>;
}

export function useApiQuery<T = unknown>(
  url: string | null,
  opts: QueryOptions = {},
): UseApiQueryResult<T> {
  const enabled = opts.enabled !== false;
  const refetchInterval = opts.refetchInterval;

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState<boolean>(!!url && enabled);
  const [error, setError] = useState<string>("");

  const fetch = useCallback(async () => {
    if (!url || !enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const res = await apiClient.get<T>(url);
      setData(res.data);
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [url, enabled]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void fetch();
  }, [fetch]);

  useEffect(() => {
    if (!refetchInterval || !enabled || !url) return;
    const id = window.setInterval(() => void fetch(), refetchInterval);
    return () => window.clearInterval(id);
  }, [fetch, refetchInterval, enabled, url]);

  return { data, loading, error, refetch: fetch };
}
