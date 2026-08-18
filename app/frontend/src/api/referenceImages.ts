/**
 * Reference-image library — the operator's side of
 * `api/reference_images_api.py`. The photos filed here are what every
 * generated image is grounded on — the mascot, but also ingredients,
 * kitchens, products, locations, style plates — so this module is
 * deliberately thin: validation lives on the server, and the UI only mirrors
 * the cheap checks.
 *
 * Every route resolves the brand server-side from the `X-Brand` header that
 * `apiClient`'s interceptor attaches — no brand id is ever passed here.
 */

import apiClient, { apiOrigin } from "./client";
import { endpoints } from "./endpoints";
import type { components } from "../types/openapi";

export type LibraryResponse = components["schemas"]["LibraryResponse"];
export type LibraryImage = components["schemas"]["LibraryImage"];
export type CategorySummary = components["schemas"]["CategorySummary"];
export type CategoryCreate = components["schemas"]["CategoryCreate"];
export type CategoryCreated = components["schemas"]["CategoryCreated"];
export type CategorySuggestion = components["schemas"]["CategorySuggestion"];

/** Mirrors `lib/crew/reference_validate.MAX_UPLOAD_BYTES` (12 MiB). */
export const MAX_UPLOAD_BYTES = 12 * 1024 * 1024;

/** Mirrors `reference_validate.EXTENSION_BY_CONTENT_TYPE`'s accepted keys. */
export const ACCEPTED_TYPES = ["image/png", "image/jpeg", "image/webp"] as const;

/** `accept` attribute for the file picker, from the same list. */
export const ACCEPT_ATTR = ACCEPTED_TYPES.join(",");

/**
 * Multipart requests must NOT carry the client's default JSON content type.
 * `apiClient` hardcodes `Content-Type: application/json` (see `client.ts`),
 * and axios only fills in the `multipart/form-data; boundary=…` header when
 * the value is absent — writing `"multipart/form-data"` by hand omits the
 * boundary and the server rejects the body as unparseable. `undefined` is the
 * override that makes axios generate the real header.
 */
const MULTIPART = { headers: { "Content-Type": undefined } } as const;

/**
 * Client-side pre-check mirroring the server's first two rejections, so an
 * obviously-doomed file never round-trips. The server stays authoritative:
 * it sniffs magic bytes and decodes dimensions, neither of which is checked
 * here. Returns a human-readable reason, or `null` when the file looks fine.
 */
export function preflightRejection(file: File): string | null {
  if (!(ACCEPTED_TYPES as readonly string[]).includes(file.type)) {
    return "Only PNG, JPEG, and WEBP images are accepted.";
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    const mb = (file.size / 1048576).toFixed(1);
    return `Image is ${mb} MB; the limit is ${MAX_UPLOAD_BYTES / 1048576} MB.`;
  }
  if (file.size === 0) {
    return "The file is empty.";
  }
  return null;
}

/** Absolute URL for a photo's bytes. `image.url` is already the `/raw` path. */
export function referenceImageUrl(image: LibraryImage): string {
  return `${apiOrigin}${image.url}`;
}

/** GET — the whole library in one payload (it is a handful of photos). */
export async function fetchReferenceLibrary(): Promise<LibraryResponse> {
  const { data } = await apiClient.get<LibraryResponse>(endpoints.referenceLibrary);
  return data;
}

/**
 * POST — file one photo under `category`. Content-addressed server-side, so
 * re-uploading identical bytes to the same category is a no-op that returns
 * the existing entry rather than a duplicate.
 */
export async function uploadReferenceImage(
  file: File,
  category: string,
  label?: string,
): Promise<LibraryImage> {
  const form = new FormData();
  form.append("file", file);
  form.append("category", category);
  if (label) form.append("label", label);
  const { data } = await apiClient.post<LibraryImage>(
    endpoints.referenceImages,
    form,
    MULTIPART,
  );
  return data;
}

/** DELETE — remove one photo. 404 when the id is already gone. */
export async function deleteReferenceImage(imageId: string): Promise<void> {
  await apiClient.delete(endpoints.referenceImage(imageId));
}

/** POST — declare a tag. Re-declaring an existing slug returns it unchanged. */
export async function createReferenceCategory(label: string): Promise<CategoryCreated> {
  const body: CategoryCreate = { label };
  const { data } = await apiClient.post<CategoryCreated>(
    endpoints.referenceCategories,
    body,
  );
  return data;
}

/** POST — copy the brand's legacy mascot asset in. Throws 404 when absent. */
export async function importLegacyReference(): Promise<LibraryImage> {
  const { data } = await apiClient.post<LibraryImage>(endpoints.referenceImportLegacy);
  return data;
}

/**
 * POST — which existing tag fits this photo? Advisory only: the server answers
 * 200 with `null` when the model call fails, and callers must never block an
 * upload on it. Returns the suggested *label*, or `null` for "no opinion".
 */
export async function suggestReferenceCategory(file: File): Promise<string | null> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<CategorySuggestion>(
    endpoints.referenceSuggestCategory,
    form,
    MULTIPART,
  );
  return data.suggested_category ?? null;
}
