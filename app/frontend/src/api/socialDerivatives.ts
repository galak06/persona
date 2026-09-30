/**
 * Product-spotlight derivatives — the operator's side of
 * `api/social_derivatives_api.py` + `api/social_derivatives_create_api.py`.
 *
 * A "derivative" is a second social post cut from an article that already
 * shipped: one catalogue product, one generated photo, two captions, and an
 * affiliate link the owner hands out by DM rather than posting. It never
 * publishes on creation — a spotlight lands in the same review queue the
 * regular social posts use, and only `approve` claims a posting slot.
 *
 * Every type here is an alias onto the generated `components["schemas"]`
 * rather than a hand-written mirror: the captions, the flag list and the
 * status vocabulary all live in Python, and a second copy of them here would
 * drift silently the first time a status is added. Regenerate with
 * `npm run gen:api:schema && npm run gen:api`, never edit by hand.
 *
 * The brand is resolved server-side from the `X-Brand` header `apiClient`'s
 * interceptor attaches, so no brand id is ever passed from here.
 */

import apiClient, { getErrorDetail, isHttpStatus } from "./client";
import type { components } from "../types/openapi";

export type SocialDerivative = components["schemas"]["SocialDerivative"];
export type SocialDerivativesResponse =
  components["schemas"]["SocialDerivativesResponse"];
export type SpotlightSource = components["schemas"]["SpotlightSource"];
export type SpotlightSourcesResponse =
  components["schemas"]["SpotlightSourcesResponse"];
export type SpotlightProduct = components["schemas"]["SpotlightProduct"];
export type SpotlightProductsResponse =
  components["schemas"]["SpotlightProductsResponse"];
export type CreateSpotlightRequest =
  components["schemas"]["CreateSpotlightRequest"];
export type CreateSpotlightResponse =
  components["schemas"]["CreateSpotlightResponse"];
export type SpotlightDecision = components["schemas"]["SpotlightDecisionResponse"];

/** The nine `content_derivatives.status` values, straight off the row type. */
export type DerivativeStatus = SocialDerivative["status"];

/** `category` = the post's own catalogue slice; `all` = the whole catalogue. */
export type ProductScope = SpotlightProductsResponse["scope"];

/**
 * The review list. `status=queued` is wider than it looks: the route also
 * returns rows still `composing` and rows that `failed` in the last 24 h, so
 * a run that is still working — or one that died — is visible on the same
 * page as the drafts waiting for a decision.
 */
export function socialDerivativesUrl(status: DerivativeStatus = "queued"): string {
  return `/social-derivatives?status=${encodeURIComponent(status)}`;
}

/** Published, in-focus articles a spotlight can be cut from. */
export function spotlightSourcesUrl(): string {
  return "/social-derivatives/sources";
}

/**
 * The picker's catalogue for one article. `scope=category` (the default)
 * keeps the post's own category plus every product actually linked in the
 * article; `scope=all` returns the whole catalogue, which can run to
 * hundreds of entries — the list that renders it has to scroll.
 */
export function spotlightProductsUrl(
  ideaId: string,
  scope: ProductScope = "category",
): string {
  return `/social-derivatives/sources/${encodeURIComponent(
    ideaId,
  )}/products?scope=${encodeURIComponent(scope)}`;
}

/**
 * Absolute URL for a spotlight's composed image. Built off the bare origin
 * (not `apiClient.baseURL`) because this goes in an `<img src>`, which never
 * passes through axios.
 *
 * Only render it when the row says `has_image` — the route 404s otherwise,
 * and a broken image icon is a worse status report than no image at all.
 * `cacheKey` defeats the browser cache after a recompose replaces the bytes
 * behind an unchanged URL.
 */
export function socialDerivativeImageUrl(
  baseApiUrl: string,
  id: string,
  cacheKey?: string | number,
): string {
  const url = `${baseApiUrl}/api/v1/social-derivatives/${encodeURIComponent(
    id,
  )}/image`;
  return cacheKey ? `${url}?v=${encodeURIComponent(String(cacheKey))}` : url;
}

/**
 * POST — insert one spotlight in `composing` and dispatch its compose run.
 *
 * Returns as soon as the run is queued (202), so the caller polls
 * `socialDerivativesUrl` rather than awaiting the image. Costs one caption
 * run plus one generated image; it cannot publish.
 *
 * 409 when this article already has an active spotlight for the same product
 * (`duplicateSpotlightId` reads the existing row's id out of it) or when a
 * compose for this brand is already in flight; 422 when the product key is
 * not in the catalogue, the article is out of focus, or the chosen photo
 * category holds no photos; 503 when the dispatch itself failed.
 */
export async function createSpotlight(
  body: CreateSpotlightRequest,
): Promise<CreateSpotlightResponse> {
  const { data } = await apiClient.post<CreateSpotlightResponse>(
    "/social-derivatives",
    body,
  );
  return data;
}

/** Claims the next free FB slot — shared with the regular posts, not extra. */
export async function approveSocialDerivative(
  id: string,
): Promise<SpotlightDecision> {
  const { data } = await apiClient.post<SpotlightDecision>(
    `/social-derivatives/${encodeURIComponent(id)}/approve`,
  );
  return data;
}

/** Gives the slot back; the row returns to `queued`, captions intact. */
export async function unscheduleSocialDerivative(
  id: string,
): Promise<SpotlightDecision> {
  const { data } = await apiClient.post<SpotlightDecision>(
    `/social-derivatives/${encodeURIComponent(id)}/unschedule`,
  );
  return data;
}

/** Terminal: the row is `rejected` and its image is unlinked server-side. */
export async function rejectSocialDerivative(
  id: string,
): Promise<SpotlightDecision> {
  const { data } = await apiClient.post<SpotlightDecision>(
    `/social-derivatives/${encodeURIComponent(id)}/reject`,
  );
  return data;
}

/**
 * The id of the spotlight that already exists, when a create was refused as
 * a duplicate — `null` for every other failure.
 *
 * The 409 carries `detail={"message": ..., "existing_id": ...}`, so the page
 * can say "you already have one of these" and refetch, instead of showing a
 * raw conflict the operator has no way to act on.
 */
export function duplicateSpotlightId(error: unknown): string | null {
  if (!isHttpStatus(error, 409)) return null;
  const detail = getErrorDetail(error);
  if (!detail || typeof detail !== "object") return null;
  const existing = (detail as { existing_id?: unknown }).existing_id;
  return typeof existing === "string" && existing.trim() ? existing : null;
}
