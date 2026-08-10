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

export async function approveSocialPost(id: string): Promise<void> {
  await apiClient.post(`/social-posts/${encodeURIComponent(id)}/approve`);
}

export async function rejectSocialPost(id: string): Promise<void> {
  await apiClient.post(`/social-posts/${encodeURIComponent(id)}/reject`);
}
