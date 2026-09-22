/**
 * Affiliate product catalog — the operator's side of `api/products_api.py`.
 *
 * The one rule this module exists to express: a focus category names AT MOST
 * ONE product. `selectProduct` is therefore a radio, not a checkbox — setting
 * it on one product takes the category off whichever product held it, server
 * side and atomically. The UI never assembles the stored `selected_for` list
 * itself; it sends a boolean and re-reads the catalog.
 *
 * There is no delete. `setProductActive(key, false)` is the catalog's delete:
 * the entry stays resolvable so posts published while it was active keep
 * their `[AFFILIATE:key]` links working, but it can never be picked again.
 *
 * Every route resolves the brand server-side from the `X-Brand` header that
 * `apiClient`'s interceptor attaches — no brand id is ever passed here.
 */

import apiClient from "./client";
import { endpoints } from "./endpoints";
import type { components } from "../types/openapi";

export type ProductsResponse = components["schemas"]["ProductsResponse"];
export type Product = components["schemas"]["ProductModel"];
export type ProductCreate = components["schemas"]["ProductCreate"];
/** PATCH body: every field optional, an omitted one is left unchanged. */
export type ProductUpdate = components["schemas"]["ProductUpdate"];

/** The catalog plus the focus context needed to render it. */
export async function listProducts(): Promise<ProductsResponse> {
  const { data } = await apiClient.get<ProductsResponse>(endpoints.products);
  return data;
}

/** Add a product. Pass `select_for_focus` to also make it the current pick. */
export async function createProduct(body: ProductCreate): Promise<Product> {
  const { data } = await apiClient.post<Product>(endpoints.products, body);
  return data;
}

/** Edit fields on a product without touching its selection. */
export async function updateProduct(key: string, body: ProductUpdate): Promise<Product> {
  const { data } = await apiClient.patch<Product>(endpoints.product(key), body);
  return data;
}

/**
 * Make this product the pick for the brand's current focus category, or
 * release it. Selecting displaces the incumbent, so callers should re-read
 * the list rather than patching one row in local state.
 *
 * Fails with 409 when the brand has no focus category declared — there is
 * nothing to be selected *for* until one is set in Brand Settings.
 */
export async function selectProduct(key: string, selected: boolean): Promise<Product> {
  return updateProduct(key, { selected });
}

/** Soft delete / restore. See the module note on why there is no DELETE. */
export async function setProductActive(key: string, active: boolean): Promise<Product> {
  return updateProduct(key, { active });
}
