/**
 * Schedule-related API calls: log tailing + missing-flow detection.
 *
 * Wraps the FastAPI routes exposed in `api/approval_api.py`:
 *   GET /api/v1/schedule/{label}/log?lines=N
 *   GET /api/v1/schedule/missing
 */

import apiClient from "./client";
import { endpoints } from "./endpoints";
import type {
  LogTailResponse,
  MissingFlowsResponse,
} from "../types/openapi";

export async function fetchLogTail(
  label: string,
  lines = 200,
): Promise<LogTailResponse> {
  const { data } = await apiClient.get<LogTailResponse>(
    endpoints.scheduleLog(label, lines),
  );
  return data;
}

export async function fetchMissingFlows(): Promise<MissingFlowsResponse> {
  const { data } = await apiClient.get<MissingFlowsResponse>(
    endpoints.scheduleMissing,
  );
  return data;
}
