/**
 * Typed endpoint URL builders. Pages compose `useApiQuery(endpoints.x)`
 * and `useApiMutation(...).mutate(endpoints.y(id), body)` rather than
 * concatenating URL strings inline.
 *
 * Mirrors the FastAPI routes declared in `api/approval_api.py`:
 *   GET  /api/v1/pending
 *   GET  /api/v1/activity[?limit=&platform=&action=]
 *   GET  /api/v1/items/{id}
 *   POST /api/v1/items/{id}/approve[?channel=both|fb_only|ig_only]
 *   POST /api/v1/items/{id}/reject
 *   POST /api/v1/items/{id}/edit
 *   GET  /api/v1/health
 *
 * The axios client's baseURL already includes `/api/v1`, so paths
 * below are relative to that.
 */

import type { Channel } from "../types/openapi";

const enc = (id: string): string => encodeURIComponent(id);

export interface ActivityParams {
  limit?: number;
  platform?: string;
  action?: string;
}

/** Build the `/activity` URL with optional query params. */
function buildActivity(params?: ActivityParams): string {
  if (!params) return "/activity";
  const search = new URLSearchParams();
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  if (params.platform) search.set("platform", params.platform);
  if (params.action) search.set("action", params.action);
  const qs = search.toString();
  return qs ? `/activity?${qs}` : "/activity";
}

export const endpoints = {
  /** GET — current site configuration. */
  config: "/config",

  /** GET — list pending approval items. */
  pending: "/pending",

  /** GET — recent engagement-log entries with optional filters. */
  activity: (params?: ActivityParams): string => buildActivity(params),

  /** GET — fetch a single item by id. */
  item: (id: string): string => `/items/${enc(id)}`,

  /** POST — approve, optionally scoping a blog-post pair to one channel. */
  approve: (id: string, channel?: Channel): string =>
    channel
      ? `/items/${enc(id)}/approve?channel=${channel}`
      : `/items/${enc(id)}/approve`,

  /** POST — reject (skip) an item. */
  reject: (id: string): string => `/items/${enc(id)}/reject`,

  /** POST — edit an item's draft text/captions in place. */
  edit: (id: string): string => `/items/${enc(id)}/edit`,

  /** GET — readiness probe. */
  health: "/health",

  /** GET — list all registered independent workers. */
  workers: "/workers",

  /** GET — status of a single worker by launchd label. */
  workerStatus: (label: string): string => `/workers/${enc(label)}/status`,

  /** POST — manually trigger a worker by launchd label. */
  workerTrigger: (label: string): string => `/workers/${enc(label)}/trigger`,

  /** GET — tail a worker's log file. */
  workerLog: (label: string, lines = 200): string =>
    `/workers/${enc(label)}/log?lines=${lines}`,

  /** GET — fetch the JSON output artifact for a worker. */
  workerArtifact: (label: string): string =>
    `/workers/${enc(label)}/artifact`,
} as const;

// Re-export the legacy single-purpose builders so any Phase 2 callers
// keep compiling. (Internal to this repo — safe to remove later.)
export const listPending = (): string => endpoints.pending;
export const getPending = (id: string): string => endpoints.item(id);
export const approvePending = (id: string): string => endpoints.approve(id);
export const rejectPending = (id: string): string => endpoints.reject(id);
export const health = (): string => endpoints.health;
