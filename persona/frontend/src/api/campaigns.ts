/**
 * Campaign API client. Thin axios wrappers over the backend campaigns
 * router (api.campaigns_api). The on-disk regex `^[a-z0-9][a-z0-9_-]*$`
 * is enforced server-side; callers should still hand us a valid name.
 *
 * Mirrors `flowGuide.ts`: import generated OpenAPI types, call apiClient.
 */

import apiClient from "./client";
import type { components } from "../types/openapi";

export type CampaignSummary = components["schemas"]["CampaignSummary"];
export type CampaignDetail = components["schemas"]["CampaignDetail"];
export type CampaignListResponse =
  components["schemas"]["CampaignListResponse"];
export type TriggerResponse = components["schemas"]["TriggerResponse"];

/** Status literal exposed by the API for last_status. */
export type CampaignStatus = CampaignSummary["last_status"];

/** GET /api/v1/campaigns — one summary per campaign with a config. */
export async function fetchCampaigns(): Promise<CampaignListResponse> {
  const { data } = await apiClient.get<CampaignListResponse>("/campaigns");
  return data;
}

/** GET /api/v1/campaigns/{name} — full summary + run history. */
export async function fetchCampaignDetail(
  name: string,
): Promise<CampaignDetail> {
  const { data } = await apiClient.get<CampaignDetail>(
    `/campaigns/${encodeURIComponent(name)}`,
  );
  return data;
}

/**
 * POST /api/v1/campaigns/{name}/publish — fire-and-forget spawn of
 * `scripts.publish_campaign`. Returns immediately with the spawned PID;
 * actual progress shows up in the next /campaigns poll cycle.
 */
export async function triggerPublish(name: string): Promise<TriggerResponse> {
  const { data } = await apiClient.post<TriggerResponse>(
    `/campaigns/${encodeURIComponent(name)}/publish`,
  );
  return data;
}
