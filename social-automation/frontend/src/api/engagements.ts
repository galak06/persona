import apiClient from "./client";

// Manual types: the published posts/comments history (engagements.db). Declared
// here (not in the stale generated openapi.ts) so the UI can read the new
// /engagements route without regenerating types.
export interface Engagement {
  id: string;
  platform: string; // facebook | instagram | wordpress
  kind: string; // comment | link_post | feed_post | reel | page_post
  status: string; // posted | failed
  target_name: string;
  target_url: string;
  permalink: string;
  content: string;
  source_ref: string;
  error: string;
  posted_at: string;
}

export interface EngagementsResponse {
  engagements: Engagement[];
  total: number;
  counts: Record<string, number>;
}

export interface EngagementsFilter {
  platform?: string;
  kind?: string;
  status?: string;
  limit?: number;
}

/** Build the `/engagements` URL with optional filters (for useApiQuery). */
export function engagementsUrl(filter: EngagementsFilter = {}): string {
  const search = new URLSearchParams();
  if (filter.platform) search.set("platform", filter.platform);
  if (filter.kind) search.set("kind", filter.kind);
  if (filter.status) search.set("status", filter.status);
  if (filter.limit !== undefined) search.set("limit", String(filter.limit));
  const qs = search.toString();
  return qs ? `/engagements?${qs}` : "/engagements";
}

export async function fetchEngagements(
  filter: EngagementsFilter = {},
): Promise<EngagementsResponse> {
  const { data } = await apiClient.get<EngagementsResponse>(engagementsUrl(filter));
  return data;
}
