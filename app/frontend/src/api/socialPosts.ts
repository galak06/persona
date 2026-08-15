import apiClient from "./client";

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
}

export interface SocialPostsResponse {
  posts: SocialPost[];
  total: number;
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
