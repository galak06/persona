/**
 * Tiny shared helpers used across the Inbox cards. Kept here so each
 * card file stays focused on rendering and mutation flow.
 */

import type { components } from "../../types/openapi";

/** Platform enum shared by activity entries and engagement comment items. */
export type Platform = components["schemas"]["ActivityEntry"]["platform"];

/**
 * The polymorphic pending-queue item union — one card type per producer
 * flow. Derived from `PendingResponse.items` so it always tracks the
 * backend's discriminated union instead of drifting out of sync.
 */
export type PendingItem =
  components["schemas"]["PendingResponse"]["items"][number];

/** Emoji icon per platform — keep consistent across cards. */
export const PLATFORM_ICON: Record<Platform, string> = {
  facebook: "📘",
  instagram: "📸",
  wordpress: "📰",
  system: "⚙️",
};

export const PLATFORM_LABEL: Record<Platform, string> = {
  facebook: "Facebook",
  instagram: "Instagram",
  wordpress: "WordPress",
  system: "System",
};

/**
 * Truncate `text` to `limit` chars on a word boundary. Returns the
 * original string unchanged when it fits.
 */
export function truncate(text: string, limit: number): string {
  if (text.length <= limit) return text;
  const slice = text.slice(0, limit);
  const lastSpace = slice.lastIndexOf(" ");
  const cut = lastSpace > limit * 0.6 ? slice.slice(0, lastSpace) : slice;
  return `${cut.trimEnd()}…`; // ellipsis
}

/** Format an ISO timestamp as a short relative phrase ("3s ago"). */
export function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

/** Format a 0..1 relevance score as a percentage badge string. */
export function formatRelevance(score: number | null): string {
  if (score == null) return "—";
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  return `${pct}%`;
}

/** A 409 from the backend means the item was already decided elsewhere
 *  (e.g. via Telegram). We treat that the same as a successful 200 so
 *  the card disappears either way. */
export function isResolvedResult<T>(
  result: T | null,
  errorStatus: number | null,
): boolean {
  return result !== null || errorStatus === 409;
}

/** Producer-flow filter applied above the card list. */
export type FlowFilter = "all" | "comments" | "blog_posts" | "groups_to_join";

export const FLOWS: readonly FlowFilter[] = [
  "all",
  "comments",
  "blog_posts",
  "groups_to_join",
];

export const FLOW_LABELS: Record<FlowFilter, string> = {
  all: "All",
  comments: "Comments",
  blog_posts: "Blog posts",
  groups_to_join: "Groups to join",
};

/** True when `item` belongs to the producer flow `flow`. */
export function itemMatchesFlow(item: PendingItem, flow: FlowFilter): boolean {
  if (flow === "all") return true;
  if (flow === "comments") return item.type === "comment";
  if (flow === "blog_posts") return item.type === "blog_post";
  if (flow === "groups_to_join") return item.type === "group_to_join";
  return false;
}
