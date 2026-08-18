/**
 * Reference-image library — the operator's side of
 * `api/reference_images_api.py`. The photos filed here are what every
 * generated image is grounded on — the brand's own dog, but also ingredients,
 * kitchens, products, locations, style plates — so this module is
 * deliberately thin: validation lives on the server, and the UI only mirrors
 * the cheap checks.
 *
 * Tagging is the server's job too. An upload runs a vision pass that describes
 * the photo, tags it, and decides whether the brand's mascot is in it, so the
 * UI never asks for a category up front — it shows what came back, and offers
 * `updateReferenceImage` to correct it.
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
/** PATCH body: every field optional, an omitted one is left unchanged. */
export type ImageUpdate = components["schemas"]["ImageUpdate"];

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
 * POST — file one photo. No category is sent: the server runs a vision pass
 * over the bytes and tags the photo itself (reusing one of the brand's tags,
 * or declaring the one it proposes), so the returned entry is where the
 * operator finds out what it decided. The route does accept a `category`
 * override for scripted callers; the UI deliberately does not use it, because
 * asking someone to pre-classify a photo is the step this replaces.
 *
 * Content-addressed server-side, so re-uploading identical bytes to the same
 * category returns the existing entry rather than a duplicate.
 */
export async function uploadReferenceImage(
  file: File,
  label?: string,
): Promise<LibraryImage> {
  const form = new FormData();
  form.append("file", file);
  if (label) form.append("label", label);
  const { data } = await apiClient.post<LibraryImage>(
    endpoints.referenceImages,
    form,
    MULTIPART,
  );
  return data;
}

/**
 * PATCH — correct what the vision pass decided: re-tag a photo, flip its
 * mascot flag, or both.
 *
 * A category change MOVES the file, and an id is `"<category>/<filename>"`,
 * so the returned entry carries a DIFFERENT id than the one passed in. Never
 * keep using the old id: adopt the one in the response, and refetch the
 * library (the photo has also moved between category blocks).
 */
export async function updateReferenceImage(
  imageId: string,
  patch: ImageUpdate,
): Promise<LibraryImage> {
  const { data } = await apiClient.patch<LibraryImage>(
    endpoints.referenceImage(imageId),
    patch,
  );
  return data;
}

/** DELETE — remove one photo. 404 when the id is already gone. */
export async function deleteReferenceImage(imageId: string): Promise<void> {
  await apiClient.delete(endpoints.referenceImage(imageId));
}

/** POST — copy the brand's legacy mascot asset in. Throws 404 when absent. */
export async function importLegacyReference(): Promise<LibraryImage> {
  const { data } = await apiClient.post<LibraryImage>(endpoints.referenceImportLegacy);
  return data;
}
