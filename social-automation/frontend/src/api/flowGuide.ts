import apiClient from "./client";
import type { components } from "../types/openapi";

export type FlowGuideResponse = components["schemas"]["FlowGuideResponse"];
export type FlowGuideEntry = components["schemas"]["FlowGuideEntry"];
export type JobDescription = components["schemas"]["JobDescription"];

export async function fetchFlowGuide(): Promise<FlowGuideResponse> {
  const { data } = await apiClient.get<FlowGuideResponse>("/flows/guide");
  return data;
}
