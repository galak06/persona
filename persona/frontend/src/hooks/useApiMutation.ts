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

import apiClient, { getErrorDetail, getErrorMessage } from "../api/client";
import type { ApiError } from "../api/client";

export type MutationMethod = "post" | "put" | "delete" | "patch";

export interface UseApiMutationResult<T, Body> {
  mutate: (url: string, body?: Body) => Promise<T | null>;
  loading: boolean;
  error: string;
  errorStatus: number | null;
  /** Raw `detail` payload of the last failure — read a `brand_id`/`retry`
   * hint etc. out of a structured error to build a real retry action. */
  errorDetail: unknown;
  reset: () => void;
}

export function useApiMutation<T = unknown, Body = unknown>(
  method: MutationMethod = "post",
): UseApiMutationResult<T, Body> {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [errorDetail, setErrorDetail] = useState<unknown>(null);

  const mutate = async (url: string, body?: Body): Promise<T | null> => {
    setLoading(true);
    setError("");
    setErrorStatus(null);
    setErrorDetail(null);
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
      setErrorDetail(getErrorDetail(err));
      return null;
    } finally {
      setLoading(false);
    }
  };

  const reset = () => {
    setError("");
    setErrorStatus(null);
    setErrorDetail(null);
    setLoading(false);
  };

  return { mutate, loading, error, errorStatus, errorDetail, reset };
}
