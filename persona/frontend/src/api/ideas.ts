import apiClient from "./client";

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
