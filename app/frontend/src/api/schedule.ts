/**
 * Schedule-related API calls: log tailing.
 *
 * Wraps `GET /api/v1/workers/{label}/log?lines=N` (route moved under
 * /workers/* when the flows/state pipeline model was retired for the
 * flat worker-registry model — see api/workers.ts).
 */

import apiClient from "./client";
import { endpoints } from "./endpoints";
import type { components } from "../types/openapi";

export type LogTailResponse = components["schemas"]["LogTailResponse"];

export async function fetchLogTail(
  label: string,
  lines = 200,
): Promise<LogTailResponse> {
  const { data } = await apiClient.get<LogTailResponse>(
    endpoints.workerLog(label, lines),
  );
  return data;
}
