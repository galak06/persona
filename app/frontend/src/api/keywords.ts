import type { components } from "../types/openapi";

export type KeywordsResponse = components["schemas"]["KeywordsResponse"];
export type CuratedKeyword = components["schemas"]["CuratedKeyword"];
export type DiscoveredKeyword = components["schemas"]["DiscoveredKeyword"];

/** Read-only: the scout's search vocabulary for the active brand. */
export const KEYWORDS_URL = "/keywords";
