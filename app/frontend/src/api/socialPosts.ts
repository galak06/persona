import apiClient from "./client";
import type { components } from "../types/openapi";

export interface SocialPost {
  id: string;
  topic: string;
  wp_url: string | null;
  social_post_status: string;
  fb_caption: string | null;
  ig_caption: string | null;
  image_alt: string | null;
  source: string | null;
  validation_flags: string[] | null;
  fb_page_post_url: string | null;
  ig_post_url: string | null;
  fb_due_at: string | null;
  ig_due_at: string | null;
  /** This post's image is being regenerated right now. */
  regenerating?: boolean;
}

export interface SocialPostsResponse {
  posts: SocialPost[];
  total: number;
}

/**
 * The `social_post_source` a post carries once its image was actually
 * generated. Anything else — `fallback` after a failed or unanchored
 * generation, or no source at all on a row composed before the column
 * existed — means the reader is looking at the WordPress hero.
 */
export const GENERATED_IMAGE_SOURCE = "gemini";

/** Would a retry get this post a better image than it has? */
export function hasGeneratedImage(post: SocialPost): boolean {
  return post.source === GENERATED_IMAGE_SOURCE;
}

export function socialPostsUrl(status = "queued"): string {
  return `/social-posts?status=${encodeURIComponent(status)}`;
}

export function socialPostImageUrl(baseApiUrl: string, id: string): string {
  return `${baseApiUrl}/api/v1/social-posts/${encodeURIComponent(id)}/image`;
}

export interface ApproveResult {
  id: string;
  status: string;
  fb_due_at: string;
}

/** Claims the next free posting slot — does not publish immediately. */
export async function approveSocialPost(id: string): Promise<ApproveResult> {
  const { data } = await apiClient.post<ApproveResult>(
    `/social-posts/${encodeURIComponent(id)}/approve`,
  );
  return data;
}

export async function unscheduleSocialPost(id: string): Promise<void> {
  await apiClient.post(`/social-posts/${encodeURIComponent(id)}/unschedule`);
}

export async function rejectSocialPost(id: string): Promise<void> {
  await apiClient.post(`/social-posts/${encodeURIComponent(id)}/reject`);
}

export interface ComposeStatus {
  running: boolean;
  started_at: string | null;
  finished_at: string | null;
  ok: boolean | null;
  detail: string | null;
  /** Posts this run added to the review queue. */
  composed: number | null;
  /** Published articles with no posts yet — what a run right now would use. */
  candidates: number;
}

/**
 * Runs the same `social-posts-compose` flow the daily cron runs (compose only
 * — it can never publish). 409 if a run is already in flight, cron's included.
 */
export async function composeSocialPosts(): Promise<void> {
  await apiClient.post("/social-posts/compose");
}

export async function fetchComposeStatus(): Promise<ComposeStatus> {
  const { data } = await apiClient.get<ComposeStatus>(
    "/social-posts/compose/status",
  );
  return data;
}

/** Which collection to anchor the regenerated image on; "" lets the run decide. */
export type RetryImageRequest = components["schemas"]["RetryImageRequest"];
export type RetryImageStatus = components["schemas"]["RetryImageStatus"];

/**
 * Replace one queued post's hook image, keeping both captions.
 *
 * Runs on the worker (a fresh image brief plus one generation, ~1 minute), so
 * this returns as soon as the run is queued — poll `fetchRetryImageStatus`.
 * It cannot publish: the dispatched script has no publisher in it at all.
 *
 * 409 when the post is no longer queued (already approved, rejected, or
 * already being retried), 422 when `referenceCategory` names a collection the
 * brand keeps no photos under.
 */
export async function retrySocialPostImage(
  id: string,
  referenceCategory = "",
): Promise<void> {
  await apiClient.post(`/social-posts/${encodeURIComponent(id)}/retry-image`, {
    reference_category: referenceCategory,
  } satisfies RetryImageRequest);
}

export async function fetchRetryImageStatus(
  id: string,
): Promise<RetryImageStatus> {
  const { data } = await apiClient.get<RetryImageStatus>(
    `/social-posts/${encodeURIComponent(id)}/retry-image/status`,
  );
  return data;
}
