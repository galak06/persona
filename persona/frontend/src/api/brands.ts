import apiClient from "./client";
import { endpoints } from "./endpoints";

/**
 * Manual types: the brand registry (`brands` table via `lib/brands_db`).
 * Declared here (not in the stale generated openapi.ts) so onboarding can
 * read/write `/brands` ahead of the backend's OpenAPI schema being
 * regenerated. Mirrors `persona/lib/brands_db/models.py::BRAND_COLUMNS`
 * (row shape) and `persona/lib/brand_templates.py::BrandSpec` (create-request
 * shape) field-for-field.
 */

export interface BrandKeywords {
  primary_keywords: string[];
  secondary_keywords: string[];
  competitor_mentions: string[];
}

/** Row shape returned by `GET /brands` (list). */
export interface BrandSummary {
  id: string;
  name: string;
  niche: string;
  status: string;
  enabled_flows: string[];
  brand_dir: string;
  created_at: string;
}

/** Full row shape returned by `GET /brands/{id}`. */
export interface Brand extends BrandSummary {
  persona: string;
  site_url: string;
  mascot_name: string;
  target_audience: string;
  keywords: BrandKeywords;
  competitor_accounts: string[];
  headless: boolean;
  group_join_limit: number;
  extra: Record<string, unknown>;
  updated_at: string;
}

/** Every flow id `enabled_flows` can govern — mirrors
 * `lib.brands_db.models.MANAGED_FLOW_IDS` (Python side). */
export const MANAGED_FLOW_IDS = ["ig-scanner", "fb-scanner", "fb-group-scout"] as const;

export interface BrandsResponse {
  brands: BrandSummary[];
}

/**
 * `POST /brands` body. Mirrors `BrandSpec` in `persona/lib/brand_templates.py`
 * field-for-field: `name`/`site_url`/`niche` required, everything else
 * optional. Blank optional fields render as explicit `<!-- TODO (owner) -->`
 * placeholders server-side (the no-fabrication guarantee) — never invented.
 */
export interface BrandCreateRequest {
  name: string;
  site_url: string;
  niche: string;
  target_audience?: string;
  mascot_name?: string;
  brand_persona?: string;
  instagram_profile_url?: string;
  facebook_page_url?: string;
  primary_keywords?: string[];
  secondary_keywords?: string[];
  competitor_mentions?: string[];
  competitor_accounts?: string[];
}

/**
 * `PATCH /brands/{id}/settings` body. Every field optional and independent —
 * an unset field is left untouched server-side. Mirrors
 * `api.brand_schemas.BrandSettingsRequest` field-for-field.
 */
export interface BrandSettingsRequest {
  headless?: boolean;
  primary_keywords?: string[];
  secondary_keywords?: string[];
  competitor_mentions?: string[];
  competitor_accounts?: string[];
  enabled_flows?: string[];
  group_join_limit?: number;
}

/** What onboarding/provisioning did (or would do). */
export interface ProvisionResult {
  brand_dir: string;
  files_written: string[];
  schedule_tasks_created: string[];
  warnings: string[];
  ig_login_command: string;
  fb_login_command: string;
}

/** 201 response from create, and the response from the provision-retry route. */
export type BrandCreateResponse = Brand & ProvisionResult;

export async function fetchBrands(status?: string): Promise<BrandsResponse> {
  const { data } = await apiClient.get<BrandsResponse>(endpoints.brands(status));
  return data;
}

export async function fetchBrand(id: string): Promise<Brand> {
  const { data } = await apiClient.get<Brand>(endpoints.brand(id));
  return data;
}

export async function createBrand(
  payload: BrandCreateRequest,
): Promise<BrandCreateResponse> {
  const { data } = await apiClient.post<BrandCreateResponse>(endpoints.brands(), payload);
  return data;
}

export async function reprovisionBrand(id: string): Promise<BrandCreateResponse> {
  const { data } = await apiClient.post<BrandCreateResponse>(endpoints.brandProvision(id));
  return data;
}

export async function updateBrandSettings(
  id: string,
  payload: BrandSettingsRequest,
): Promise<BrandCreateResponse> {
  const { data } = await apiClient.patch<BrandCreateResponse>(
    endpoints.brandSettings(id),
    payload,
  );
  return data;
}
