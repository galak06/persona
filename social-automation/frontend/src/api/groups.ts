import apiClient from "./client";
import type { components } from "../types/openapi";

export type FacebookGroupsResponse = components["schemas"]["FacebookGroupsResponse"];
export type FacebookGroup = components["schemas"]["FacebookGroup"];

export async function fetchGroups(): Promise<FacebookGroupsResponse> {
  const { data } = await apiClient.get<FacebookGroupsResponse>("/facebook/groups");
  return data;
}

export async function updateGroup(
  groupName: string,
  payload: { status?: string; posting_mode?: string }
): Promise<FacebookGroup> {
  const { data } = await apiClient.put<FacebookGroup>(
    `/facebook/groups/${encodeURIComponent(groupName)}`,
    payload
  );
  return data;
}
