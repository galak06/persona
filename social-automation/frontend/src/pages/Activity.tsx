/**
 * Activity page — read-only feed of the engagement log.
 *
 * Polls `GET /api/v1/activity?limit=200` every 5 seconds. Renders a
 * filterable table: Time | Platform | Action | Target | Content |
 * Permalink. Filtering happens client-side on the polled rows; the
 * server returns the most-recent window unconditionally so toggling a
 * chip never round-trips.
 */

import { useMemo, useState } from "react";

import Alert from "../components/ui/Alert";
import EmptyState from "../components/ui/EmptyState";
import LoadingState from "../components/ui/LoadingState";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type {
  ActivityAction,
  ActivityEntry,
  ActivityResponse,
  Platform,
} from "../types/openapi";
import { PLATFORM_ICON, PLATFORM_LABEL, relativeTime, truncate } from "./Inbox/shared";

const POLL_MS = 5000;
const FETCH_LIMIT = 200;
const CONTENT_PREVIEW = 200;

type PlatformFilter = "all" | Platform;
type ActionFilter = "all" | "comments" | "likes" | "joins" | "posts" | "system";

const PLATFORM_FILTERS: readonly { key: PlatformFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "facebook", label: "Facebook" },
  { key: "instagram", label: "Instagram" },
  { key: "wordpress", label: "WordPress" },
];

const ACTION_FILTERS: readonly { key: ActionFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "comments", label: "Comments" },
  { key: "likes", label: "Likes" },
  { key: "joins", label: "Joins" },
  { key: "posts", label: "Posts" },
  { key: "system", label: "System" },
];

const ACTION_LABEL: Record<ActivityAction, string> = {
  comment: "comment", like: "like", group_post: "group post",
  reply: "reply", own_reply: "own reply", page_post: "page post",
  feed_post: "feed post", group_join: "group join",
  trace: "trace",
};

const CHIP_CYAN = "bg-cyan-50 text-cyan-700 border-cyan-200";
const CHIP_PINK = "bg-pink-50 text-pink-700 border-pink-200";
const CHIP_AMBER = "bg-amber-50 text-amber-800 border-amber-200";
const CHIP_SLATE = "bg-slate-100 text-slate-700 border-slate-200";
const CHIP_INDIGO = "bg-indigo-50 text-indigo-700 border-indigo-200";
const ACTION_CHIP: Record<ActivityAction, string> = {
  comment: CHIP_CYAN, reply: CHIP_CYAN, own_reply: CHIP_CYAN,
  like: CHIP_PINK, group_join: CHIP_AMBER,
  page_post: CHIP_SLATE, feed_post: CHIP_SLATE, group_post: CHIP_SLATE,
  trace: CHIP_INDIGO,
};

function matchesAction(entry: ActivityEntry, filter: ActionFilter): boolean {
  if (filter === "all") return true;
  const a = entry.action;
  if (filter === "comments") return a === "comment" || a === "reply" || a === "own_reply";
  if (filter === "likes") return a === "like";
  if (filter === "joins") return a === "group_join";
  if (filter === "posts") return a === "page_post" || a === "feed_post" || a === "group_post";
  if (filter === "system") return a === "trace";
  return false;
}

export default function Activity(): React.JSX.Element {
  const [platformFilter, setPlatformFilter] = useState<PlatformFilter>("all");
  const [actionFilter, setActionFilter] = useState<ActionFilter>("all");

  const { data, loading, error } = useApiQuery<ActivityResponse>(
    endpoints.activity({ limit: FETCH_LIMIT }),
    { refetchInterval: POLL_MS },
  );

  const entries = useMemo<ActivityEntry[]>(() => data?.entries ?? [], [data]);

  const filteredEntries = useMemo<ActivityEntry[]>(() => {
    return entries.filter((entry) => {
      if (platformFilter !== "all" && entry.platform !== platformFilter)
        return false;
      if (!matchesAction(entry, actionFilter)) return false;
      return true;
    });
  }, [entries, platformFilter, actionFilter]);

  if (loading && !data) {
    return <LoadingState message="Loading activity…" />;
  }

  if (error && !data) {
    return (
      <Alert status="error" title="Could not load activity">
        {error}
      </Alert>
    );
  }

  return (
    <section className="space-y-6">
      <header className="flex items-baseline justify-between gap-3 flex-wrap">
        <h1 className="text-2xl font-bold text-slate-900">Activity</h1>
        <span className="text-xs text-slate-400">
          {filteredEntries.length} of {entries.length} shown · refreshed{" "}
          {relativeTime(data?.as_of ?? null)}
        </span>
      </header>

      {error && data && (
        <Alert status="warning" title="Polling error">
          {error}
        </Alert>
      )}

      <FilterChips
        label="Platform"
        active={platformFilter}
        options={PLATFORM_FILTERS}
        onChange={setPlatformFilter}
      />
      <FilterChips
        label="Action"
        active={actionFilter}
        options={ACTION_FILTERS}
        onChange={setActionFilter}
      />

      {filteredEntries.length === 0 ? (
        <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card">
          <EmptyState
            title="No activity yet."
            description="Once scanners and posters run, their actions will appear here."
          />
        </div>
      ) : (
        <ActivityTable entries={filteredEntries} />
      )}
    </section>
  );
}

interface FilterChipsProps<T extends string> {
  label: string;
  active: T;
  options: readonly { key: T; label: string }[];
  onChange: (key: T) => void;
}

function FilterChips<T extends string>({
  label,
  active,
  options,
  onChange,
}: FilterChipsProps<T>): React.JSX.Element {
  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
        {label}
      </span>
      <ul className="flex items-center gap-1 flex-wrap">
        {options.map((opt) => {
          const isActive = opt.key === active;
          const cls = isActive
            ? "bg-cyan-50 text-cyan-700 font-medium"
            : "text-slate-500 hover:text-slate-700";
          return (
            <li key={opt.key}>
              <button
                type="button"
                onClick={() => onChange(opt.key)}
                aria-pressed={isActive}
                className={`px-3 py-1 rounded-full text-xs whitespace-nowrap transition-colors duration-150 ${cls}`}
              >
                {opt.label}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

interface ActivityTableProps {
  entries: ActivityEntry[];
}

function ActivityTable({ entries }: ActivityTableProps): React.JSX.Element {
  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-stone-50/60 text-xs uppercase tracking-wider text-slate-500">
            <tr>
              <th className="text-left font-semibold px-4 py-3">Time</th>
              <th className="text-left font-semibold px-4 py-3">Platform</th>
              <th className="text-left font-semibold px-4 py-3">Action</th>
              <th className="text-left font-semibold px-4 py-3">Target</th>
              <th className="text-left font-semibold px-4 py-3">Content</th>
              <th className="text-left font-semibold px-4 py-3">Permalink</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-border">
            {entries.map((entry, idx) => (
              <ActivityRow
                key={`${entry.timestamp}-${idx}`}
                entry={entry}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface ActivityRowProps {
  entry: ActivityEntry;
}

function ActivityRow({ entry }: ActivityRowProps): React.JSX.Element {
  const chip = ACTION_CHIP[entry.action] ?? "bg-slate-100 text-slate-700 border-slate-200";
  return (
    <tr className="align-top">
      <td className="px-4 py-3 text-xs text-slate-500 whitespace-nowrap">
        {relativeTime(entry.timestamp)}
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        <span className="inline-flex items-center gap-1.5 text-sm text-slate-700">
          <span aria-hidden="true" className="text-base leading-none">
            {PLATFORM_ICON[entry.platform]}
          </span>
          <span>{PLATFORM_LABEL[entry.platform]}</span>
        </span>
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        <span
          className={`inline-flex items-center rounded-full border text-[11px] font-semibold uppercase tracking-wide px-2 py-0.5 ${chip}`}
        >
          {ACTION_LABEL[entry.action]}
        </span>
      </td>
      <td className="px-4 py-3 max-w-[14rem]">
        {entry.target_url ? (
          <a
            href={entry.target_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-brand-primary hover:text-brand-primary-hover truncate inline-block max-w-full align-bottom"
            title={entry.target_name ?? entry.target_url}
          >
            {entry.target_name ?? entry.target_url}
          </a>
        ) : (
          <span className="text-sm text-slate-600 truncate inline-block max-w-full align-bottom">
            {entry.target_name ?? "—"}
          </span>
        )}
      </td>
      <td className="px-4 py-3 max-w-[24rem]">
        <span className="text-sm text-slate-700 whitespace-pre-wrap break-words">
          {entry.content ? truncate(entry.content, CONTENT_PREVIEW) : "—"}
        </span>
      </td>
      <td className="px-4 py-3 whitespace-nowrap">
        {entry.reply_url ? (
          <a
            href={entry.reply_url}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-brand-primary hover:text-brand-primary-hover inline-flex items-center gap-1"
            aria-label="Open permalink"
          >
            <span aria-hidden="true">↗</span>
          </a>
        ) : (
          <span className="text-sm text-slate-400">—</span>
        )}
      </td>
    </tr>
  );
}
