import apiClient from "./client";
import type { components } from "../types/openapi";

export type RecipeSummary = components["schemas"]["RecipeSummary"];
export type RecipeDetail = components["schemas"]["RecipeDetail"];
export type RecipesResponse = components["schemas"]["RecipesResponse"];
export type PublishChannel = components["schemas"]["PublishChannel"];
export type SyncResponse = components["schemas"]["SyncResponse"];

/** Publish channels shown per recipe, in display order. */
export const PUBLISH_CHANNELS = ["wp", "pdf", "ig", "fb"] as const;
export const CHANNEL_LABELS: Record<string, string> = {
  wp: "WP",
  pdf: "PDF",
  ig: "IG",
  fb: "FB",
};

export interface RecipeFilters {
  status?: string;
  dogSafe?: boolean;
}

export async function fetchRecipes(
  filters: RecipeFilters = {},
): Promise<RecipesResponse> {
  const params: Record<string, string | number | boolean> = {};
  if (filters.status) params.status = filters.status;
  if (filters.dogSafe !== undefined) params.dog_safe = filters.dogSafe;
  const { data } = await apiClient.get<RecipesResponse>("/recipes", { params });
  return data;
}

export async function fetchRecipe(id: string): Promise<RecipeDetail> {
  const { data } = await apiClient.get<RecipeDetail>(
    `/recipes/${encodeURIComponent(id)}`,
  );
  return data;
}

export async function syncPublishStatus(): Promise<SyncResponse> {
  const { data } = await apiClient.post<SyncResponse>("/recipes/sync-publish");
  return data;
}
