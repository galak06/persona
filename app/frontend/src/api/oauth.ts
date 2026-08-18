import apiClient from "./client";
import { endpoints } from "./endpoints";
import type { components } from "../types/openapi";

export type OpenArtState = components["schemas"]["OpenArtStatus"]["state"];

export interface OpenArtStatus {
  state: OpenArtState;
}

/** OpenArt connection pre-flight the Reels page checks before Compose. */
export async function fetchOpenartStatus(): Promise<OpenArtStatus> {
  const { data } = await apiClient.get<OpenArtStatus>(endpoints.oauthOpenartStatus);
  return data;
}
