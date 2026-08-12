import apiClient from "./client";
import type { components } from "../types/openapi";

export type FacebookGroupsResponse = components["schemas"]["FacebookGroupsResponse"];

/**
 * Manual augmentation (brands.ts pattern — the generated openapi.ts is stale
 * and must never be edited): fb-engager first-comment gate fields. Mirrors
 * `api.schemas.FacebookGroup` (Python side) field-for-field.
 */
export interface FirstCommentGateFields {
  first_comment_flagged_at?: string | null;
  first_comment_approved_at?: string | null;
}

export type FacebookGroup = components["schemas"]["FacebookGroup"] &
  FirstCommentGateFields;

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

/** One-time approval of the fb-engager first-comment gate for a group. */
export async function approveFirstComment(
  groupName: string
): Promise<FacebookGroup> {
  const { data } = await apiClient.post<FacebookGroup>(
    `/facebook/groups/${encodeURIComponent(groupName)}/approve-first-comment`
  );
  return data;
}
