import type { components } from "../types/openapi";

/** One Jev post-gate decision (`GET /decisions`), from the generated schema. */
export type JevDecision = components["schemas"]["JevDecision"];
export type DecisionsSummary = components["schemas"]["DecisionsSummary"];
export type DecisionsResponse = components["schemas"]["DecisionsResponse"];

export interface DecisionsFilter {
  brandId?: string;
  platform?: string;
  limit?: number;
}

/** Build the `/decisions` URL with optional filters (for useApiQuery). */
export function decisionsUrl(filter: DecisionsFilter = {}): string {
  const search = new URLSearchParams();
  if (filter.brandId) search.set("brand_id", filter.brandId);
  if (filter.platform) search.set("platform", filter.platform);
  if (filter.limit !== undefined) search.set("limit", String(filter.limit));
  const qs = search.toString();
  return qs ? `/decisions?${qs}` : "/decisions";
}
