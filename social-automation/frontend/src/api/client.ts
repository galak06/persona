// Copied from social-comment-automation reference; trimmed for solo deploy.
// All auth/refresh/tenant logic stripped — this SPA only runs against a
// localhost FastAPI backend on a trusted machine.

import axios from "axios";
import type { AxiosError } from "axios";

/** Shape of `err.response?.data?.detail` returned by FastAPI errors. */
export interface ApiError {
  response?: {
    data?: { detail?: unknown };
    status?: number;
  };
  message?: string;
}

interface ValidationItem {
  msg?: unknown;
  loc?: unknown[];
}

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://127.0.0.1:5001/api/v1",
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use((config) => {
  const brand = localStorage.getItem("social_automation_selected_brand");
  if (brand) {
    config.headers["X-Brand"] = brand;
  }
  return config;
});


/**
 * Single error-message extractor for axios + FastAPI failures. Handles
 * three FastAPI detail shapes (string / validation array / object).
 * Always returns a non-empty string.
 */
export function getErrorMessage(
  err: unknown,
  fallback = "An unexpected error occurred.",
): string {
  const apiErr = err as ApiError;
  const detail = apiErr?.response?.data?.detail;

  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }

  if (Array.isArray(detail)) {
    const parts = detail
      .map((d) => {
        if (typeof d !== "object" || d === null) return JSON.stringify(d);
        const item = d as ValidationItem;
        const msg = typeof item.msg === "string" ? item.msg : null;
        const loc =
          Array.isArray(item.loc) && item.loc.length
            ? ` (${item.loc.join(".")})`
            : "";
        return msg ? `${msg}${loc}` : JSON.stringify(d);
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }

  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }

  if (apiErr?.message && apiErr.message.trim()) {
    return apiErr.message;
  }

  return fallback;
}

/** Type guard for "error caused by HTTP response with status N". */
export function isHttpStatus(err: unknown, status: number): boolean {
  return (err as AxiosError)?.response?.status === status;
}

export default apiClient;
