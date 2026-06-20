import apiClient from "./client";
import type { components } from "../types/openapi";

// `card_path` / `card_created_at` / `season_tags` are served by the backend
// (recipe-card indication + file link + seasonal-selection tags); declared
// optional here until openapi.ts is regenerated, so the UI can read them
// without editing the generated file.
export type AffiliateProduct = { key: string; asin: string; display: string };
/** On-disk media for a recipe (BRAND_DIR-relative paths; see mediaUrl). */
export type RecipeMedia = {
  images: string[];
  reels: string[]; // video files
  audio: string[];
  featured_image?: string | null;
};
type RecipeCardFields = {
  card_path?: string;
  card_created_at?: string;
  card_html_path?: string;
  card_html_created_at?: string;
  season_tags?: string[];
  affiliate_products?: AffiliateProduct[];
  content_status?: string; // none|generated|pending|approved|rejected|published
  media?: RecipeMedia | null; // populated on detail responses only
};
export type RecipeSummary = components["schemas"]["RecipeSummary"] &
  RecipeCardFields;
export type RecipeDetail = components["schemas"]["RecipeDetail"] &
  RecipeCardFields;
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
  /** Filter to recipes in-season for this season (spring/summer/fall/winter). */
  season?: string;
}

export async function fetchRecipes(
  filters: RecipeFilters = {},
): Promise<RecipesResponse> {
  const params: Record<string, string | number | boolean> = {};
  if (filters.status) params.status = filters.status;
  if (filters.dogSafe !== undefined) params.dog_safe = filters.dogSafe;
  if (filters.season) params.season = filters.season;
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

/** Approve a pending recipe (phase 5 human gate). */
export async function approveRecipe(id: string): Promise<void> {
  await apiClient.post(`/recipes/${encodeURIComponent(id)}/approve`);
}

/** Reject a pending recipe so it is never published. */
export async function rejectRecipe(id: string): Promise<void> {
  await apiClient.post(`/recipes/${encodeURIComponent(id)}/reject`);
}

export type RecipeAnalytics = {
  recipes: number;
  attempts: number;
  by_platform: Record<string, Record<string, number>>;
  by_status: Record<string, number>;
};

/** Aggregated publish outcomes (phase 10, local outcome log). */
export async function fetchAnalytics(): Promise<RecipeAnalytics> {
  const { data } = await apiClient.get<RecipeAnalytics>("/recipes/analytics");
  return data;
}

export type ArtifactItem = {
  name: string;
  path: string; // relative to the recipe artifact folder
  kind: string; // "image" | "pdf" | "json" | "other"
  size: number;
};
export type ArtifactsResponse = {
  recipe_id: string;
  artifacts: ArtifactItem[];
  total: number;
};

export async function fetchArtifacts(id: string): Promise<ArtifactsResponse> {
  const { data } = await apiClient.get<ArtifactsResponse>(
    `/recipes/${encodeURIComponent(id)}/artifacts`,
  );
  return data;
}

/** Absolute URL to fetch one artifact file (for <img src> / open links). */
export function artifactUrl(id: string, path: string): string {
  const base = apiClient.defaults.baseURL ?? "";
  return `${base}/recipes/${encodeURIComponent(id)}/artifact?path=${encodeURIComponent(
    path,
  )}`;
}

/**
 * Absolute URL to fetch a media file from the recipe's media manifest
 * (`<video>`/`<img>`/`<audio>` src). Accepts BRAND_DIR-relative paths in either
 * the recipe_artifacts or _migrated_backup folder, unlike artifactUrl.
 */
export function mediaUrl(id: string, path: string): string {
  const base = apiClient.defaults.baseURL ?? "";
  return `${base}/recipes/${encodeURIComponent(id)}/media-file?path=${encodeURIComponent(
    path,
  )}`;
}

/**
 * Absolute URL to the rendered recipe PAGE (HTML built from DB fields + image
 * artifacts). Loads in an <iframe>/tab; its image refs resolve via artifactUrl.
 */
export function recipePageUrl(id: string): string {
  const base = apiClient.defaults.baseURL ?? "";
  return `${base}/recipes/${encodeURIComponent(id)}/page`;
}
