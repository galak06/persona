/**
 * Schedule-related API calls: log tailing + missing-flow detection.
 *
 * Wraps the FastAPI routes exposed in `api/approval_api.py`:
 *   GET /api/v1/workers/{label}/log?lines=N   (log tail; route moved under
 *     /workers/* when the flows/state pipeline model was retired for the
 *     flat worker-registry model — see api/workers.ts)
 *   GET /api/v1/schedule/missing              (still flows-based; the
 *     launchctl-vs-schedule.json diff was never migrated to /workers/*)
 */

import apiClient from "./client";
import { endpoints } from "./endpoints";
import type { components } from "../types/openapi";

export type LogTailResponse = components["schemas"]["LogTailResponse"];
export type MissingFlowsResponse = components["schemas"]["MissingFlowsResponse"];

export async function fetchLogTail(
  label: string,
  lines = 200,
): Promise<LogTailResponse> {
  const { data } = await apiClient.get<LogTailResponse>(
    endpoints.workerLog(label, lines),
  );
  return data;
}

export async function fetchMissingFlows(): Promise<MissingFlowsResponse> {
  const { data } = await apiClient.get<MissingFlowsResponse>(
    endpoints.scheduleMissing,
  );
  return data;
}
