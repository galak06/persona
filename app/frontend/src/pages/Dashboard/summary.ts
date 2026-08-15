/**
 * Pure derivations behind the Dashboard tiles.
 *
 * Kept free of React so the "is this flow unhealthy / is this session
 * stale / what did we do today" rules are readable in one place, and so
 * the page component stays layout-only.
 */

import type { Engagement } from "../../api/engagements";
import type { SessionStatus } from "../../api/sessions";
import type { WorkerStatus } from "../../api/workers";

/** Envelope of `GET /activity/summary`. Declared here, not in the stale
 *  generated `openapi.ts`, matching `api/engagements.ts`'s convention. */
export interface ActivitySummaryResponse {
  date: string;
  counts: Record<string, number>;
  /** Rows for the day excluding `trace` — heartbeats are not activity. */
  total: number;
  as_of: string;
}

/** Health tone shared by every tile. Drives colour, nothing else. */
export type Tone = "ok" | "warn" | "bad" | "idle";

export interface FlowSummary {
  total: number;
  running: number;
  ok: number;
  errored: number;
  never: number;
  /** Titles behind the headline — the errored flows, or the never-run ones
   *  when nothing is erroring. Mixing the two reads as one undifferentiated
   *  blob of names that doesn't match the count above it. */
  problems: string[];
  tone: Tone;
}

/**
 * Roll worker rows up into one health line.
 *
 * `is_instance` rows are the individual slots of a multi-instance trigger;
 * they duplicate their parent's health, so counting them would make one
 * unhealthy flow look like five.
 */
export function summarizeFlows(workers: WorkerStatus[]): FlowSummary {
  const flows = workers.filter((w) => !w.is_instance);
  const errored = flows.filter((w) => w.status === "error");
  const never = flows.filter((w) => w.status === "never");
  const running = flows.filter((w) => w.status === "running");
  const problems = (errored.length > 0 ? errored : never).map((w) => w.title);
  return {
    total: flows.length,
    running: running.length,
    ok: flows.filter((w) => w.status === "success").length,
    errored: errored.length,
    never: never.length,
    problems,
    tone: errored.length > 0 ? "bad" : never.length > 0 ? "warn" : "ok",
  };
}

/** Hours since `iso`, or `null` when absent/unparseable. */
export function hoursSince(iso: string | null, now: number = Date.now()): number | null {
  if (!iso) return null;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return null;
  return (now - then) / 3_600_000;
}

/**
 * A saved browser session's file is rewritten on every successful FB/IG
 * run, so `last_saved` doubles as "when did this platform last work".
 * Both engagers run daily; nothing touching the file for two days means
 * the runs are failing or the login has lapsed — worth a warning well
 * before the cookies themselves expire.
 */
const SESSION_STALE_HOURS = 48;

export interface SessionLine {
  platform: string;
  label: string;
  tone: Tone;
  loginCommand: string;
}

export function summarizeSessions(sessions: SessionStatus[]): SessionLine[] {
  return sessions.map((s) => {
    const age = hoursSince(s.last_saved);
    let tone: Tone = "ok";
    if (!s.exists) tone = "bad";
    else if (age === null || age > SESSION_STALE_HOURS) tone = "warn";
    return {
      platform: s.platform,
      label: !s.exists ? "not logged in" : relativeShort(s.last_saved),
      tone,
      loginCommand: s.login_command,
    };
  });
}

/** Compact age string for a dense tile: `3h`, `2d`, `—`. */
export function relativeShort(iso: string | null): string {
  const hours = hoursSince(iso);
  if (hours === null) return "—";
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m`;
  if (hours < 24) return `${Math.round(hours)}h`;
  return `${Math.round(hours / 24)}d`;
}

export interface TodayCounts {
  comments: number;
  likes: number;
  joins: number;
  posts: number;
  failed: number;
  total: number;
}

/**
 * Group the server's per-action tally into the four things worth watching,
 * then fold in that day's failed publishes from `engagements.db`.
 *
 * Failures live in the engagements table rather than the JSONL log — the
 * log only ever records what succeeded — so the tile needs both sources
 * to answer "did today go well" rather than just "did today happen".
 *
 * The day comes from the summary response itself (a UTC `YYYY-MM-DD`), not
 * from the browser's clock. That keeps this tile on the same calendar day
 * as everything else that meters the estate — the rate limiter, the daily
 * re-run guard, and the `posted_at` stamps compared against it here.
 * Until the summary lands there is no day to filter on, so failures read
 * zero rather than matching every row via an empty prefix.
 */
export function summarizeToday(
  summary: ActivitySummaryResponse | null,
  engagements: Engagement[],
): TodayCounts {
  const c = summary?.counts ?? {};
  const n = (key: string): number => c[key] ?? 0;
  const day = summary?.date ?? "";
  return {
    comments: n("comment") + n("reply") + n("own_reply"),
    likes: n("like"),
    joins: n("group_join"),
    posts: n("page_post") + n("feed_post") + n("group_post"),
    failed: day
      ? engagements.filter(
          (e) => e.status === "failed" && e.posted_at.startsWith(day),
        ).length
      : 0,
    total: summary?.total ?? 0,
  };
}

/** Sort an all-time `platform:kind` count map into stable, readable rows. */
export function publishedRows(
  counts: Record<string, number>,
): Array<{ key: string; label: string; value: number }> {
  return Object.entries(counts)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => ({
      key,
      label: key.replace(":", " · ").replace(/_/g, " "),
      value,
    }));
}
