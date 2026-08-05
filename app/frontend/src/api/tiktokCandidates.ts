import apiClient from "./client";

// Manual types: TikTok follow-candidate pipeline (tiktok_candidates table).
// Declared here (not in the stale generated openapi.ts) so the UI can read
// the /tiktok-candidates route without regenerating types.
export interface TikTokCandidate {
  handle: string;
  display_name: string | null;
  follower_count: number | null;
  niche: string | null;
  relevance_score: number | null;
  status: "pending" | "followed" | "skipped";
  discovered_at: string | null;
  followed_at: string | null;
  notes: string | null;
}

export interface CandidatesResponse {
  candidates: TikTokCandidate[];
  total: number;
  counts: Record<string, number>;
}

export function candidatesUrl(status?: string): string {
  const search = new URLSearchParams();
  if (status) search.set("status", status);
  const qs = search.toString();
  return qs ? `/tiktok-candidates?${qs}` : "/tiktok-candidates";
}

export async function fetchCandidates(
  status?: string,
): Promise<CandidatesResponse> {
  const { data } = await apiClient.get<CandidatesResponse>(
    candidatesUrl(status),
  );
  return data;
}

export async function updateCandidateStatus(
  handle: string,
  status: "pending" | "followed" | "skipped",
): Promise<void> {
  await apiClient.patch(
    `/tiktok-candidates/${encodeURIComponent(handle)}/status`,
    { status },
  );
}
