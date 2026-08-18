import apiClient from "./client";
import type { components } from "../types/openapi";

/** Lifecycle of a compose dispatch, straight from the generated schema. */
export type ComposeRunStatus =
  components["schemas"]["api__reels_compose_api__ComposeStatus"]["status"];

export interface ContentIdea {
  id: string;
  category: string;
  topic: string;
  target_keyword: string | null;
  nalla_context: string | null;
  post_goal: string | null;
  status: string;
  input: string | null;
  brand_id: string | null;
  brand_name: string | null;
  created_at: string | null;
  match_score: number | null;
  reel_ig_caption: string | null;
  reel_fb_caption: string | null;
  reel_source: string | null;
  reel_validation_flags: string[] | null;
  /** Why a write_failed/validation_failed idea died. Null for every other status. */
  failure_reason: string | null;
}

export interface IdeasResponse {
  ideas: ContentIdea[];
  total: number;
  counts: Record<string, number>;
}

export interface IdeasParams {
  category?: string;
  status?: string;
  brand_id?: string;
  limit?: number;
}

export function ideasUrl(params?: IdeasParams): string {
  const search = new URLSearchParams();
  if (params?.category) search.set("category", params.category);
  if (params?.status) search.set("status", params.status);
  if (params?.brand_id) search.set("brand_id", params.brand_id);
  if (params?.limit !== undefined) search.set("limit", String(params.limit));
  const qs = search.toString();
  return qs ? `/ideas?${qs}` : "/ideas";
}

export async function updateIdeaStatus(id: string, status: string): Promise<void> {
  await apiClient.patch(`/ideas/${encodeURIComponent(id)}/status`, { status });
}

export interface SlideInfo {
  n: number;
  url: string;
}

export interface SlidesResponse {
  idea_id: string;
  count: number;
  slides: SlideInfo[];
}

export function slidesApiUrl(id: string): string {
  return `/ideas/${encodeURIComponent(id)}/slides`;
}

export function slideImageUrl(baseApiUrl: string, id: string, n: number): string {
  return `${baseApiUrl}/api/v1/ideas/${encodeURIComponent(id)}/slides/${n}`;
}

export type ReelPlatform = "ig" | "fb";

export function reelVideoUrl(baseApiUrl: string, id: string, platform: ReelPlatform): string {
  return `${baseApiUrl}/api/v1/ideas/${encodeURIComponent(id)}/reel-video/${platform}`;
}

export interface GenerateStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  ok: boolean | null;
  detail: string | null;
  /**
   * Reels compose only, purely informational: how many beat images came from
   * OpenArt vs the post's hero image. Images are resolved per beat, so a mix
   * is normal. Any mix is an ordinary success — OpenArt is an opt-in upgrade,
   * so this is never an error or a prompt.
   */
  used_openart?: boolean | null;
  ai_images?: number | null;
  hero_images?: number | null;
  /** Reels compose only: where the dispatch is in its lifecycle. */
  status?: ComposeRunStatus;
  /** Reels compose only: dispatch timeout — the stuck threshold while running. */
  timeout_seconds?: number;
}

/** Starts a CrewAI scout run (Trends + Idea crews). 409 if one is in flight. */
export async function generateIdeas(): Promise<void> {
  await apiClient.post("/ideas/generate");
}

export async function fetchGenerateStatus(): Promise<GenerateStatus> {
  const { data } = await apiClient.get<GenerateStatus>("/ideas/generate/status");
  return data;
}

/** Starts a CrewAI reels-compose run over wp_published ideas. 409 if one is in flight. */
export async function composeReels(): Promise<void> {
  await apiClient.post("/reels/compose");
}

export async function fetchComposeStatus(): Promise<GenerateStatus> {
  const { data } = await apiClient.get<GenerateStatus>("/reels/compose/status");
  return data;
}
