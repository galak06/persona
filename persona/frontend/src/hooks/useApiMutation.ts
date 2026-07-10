// Copied from social-comment-automation reference; trimmed for solo deploy.
//
// Write-style data hook. Encapsulates the `setLoading + try/catch +
// setError + setLoading(false)` pattern duplicated across approve /
// reject / edit submissions.
//
// `mutate` returns `null` on failure (error exposed via `.error`).
// `.errorStatus` lets callers distinguish 409-conflict (treat as
// already-handled) from 5xx without re-throwing.

import { useState } from "react";

import apiClient, { getErrorMessage } from "../api/client";
import type { ApiError } from "../api/client";

export type MutationMethod = "post" | "put" | "delete" | "patch";

export interface UseApiMutationResult<T, Body> {
  mutate: (url: string, body?: Body) => Promise<T | null>;
  loading: boolean;
  error: string;
  errorStatus: number | null;
  reset: () => void;
}

export function useApiMutation<T = unknown, Body = unknown>(
  method: MutationMethod = "post",
): UseApiMutationResult<T, Body> {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [errorStatus, setErrorStatus] = useState<number | null>(null);

  const mutate = async (url: string, body?: Body): Promise<T | null> => {
    setLoading(true);
    setError("");
    setErrorStatus(null);
    try {
      const res = await apiClient.request<T>({
        url,
        method,
        data: body,
      });
      return res.data;
    } catch (err) {
      setError(getErrorMessage(err));
      const status = (err as ApiError)?.response?.status ?? null;
      setErrorStatus(status);
      return null;
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setError("");
    setErrorStatus(null);
    setLoading(false);
  };

  return { mutate, loading, error, errorStatus, reset };
}
