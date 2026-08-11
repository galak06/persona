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
