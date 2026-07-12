/**
 * Dashboard page — at-a-glance counts of pending blog-post pairs and
 * pending group joins, plus a mini-summary of today's logged activity.
 *
 * Polls `GET /api/v1/pending` and `GET /api/v1/activity?limit=200` every
 * 5 seconds. The activity payload is filtered client-side to today's
 * entries so the summary updates the moment the poster lays down a row.
 */

import { useMemo } from "react";
import { Link } from "react-router-dom";

import Alert from "../components/ui/Alert";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { components } from "../types/openapi";
import { relativeTime } from "./Inbox/shared";

type ActivityEntry = components["schemas"]["ActivityEntry"];
type ActivityResponse = components["schemas"]["ActivityResponse"];
type PendingResponse = components["schemas"]["PendingResponse"];

const POLL_MS = 5000;
const ACTIVITY_LIMIT = 200;

interface TodaySummary {
  total: number;
  comments: number;
  likes: number;
  joins: number;
  posts: number;
}

function isToday(iso: string): boolean {
  const then = new Date(iso);
  if (Number.isNaN(then.getTime())) return false;
  const now = new Date();
  return (
    then.getFullYear() === now.getFullYear() &&
    then.getMonth() === now.getMonth() &&
    then.getDate() === now.getDate()
  );
}

function summarizeToday(entries: ActivityEntry[]): TodaySummary {
  const today = entries.filter((e) => isToday(e.timestamp));
  let comments = 0;
  let likes = 0;
  let joins = 0;
  let posts = 0;
  for (const entry of today) {
    switch (entry.action) {
      case "comment":
      case "reply":
      case "own_reply":
        comments += 1;
        break;
      case "like":
        likes += 1;
        break;
      case "group_join":
        joins += 1;
        break;
      case "page_post":
      case "feed_post":
      case "group_post":
        posts += 1;
        break;
    }
  }
  return { total: today.length, comments, likes, joins, posts };
}

export default function Dashboard(): React.JSX.Element {
  const pendingQ = useApiQuery<PendingResponse>(endpoints.pending, {
    refetchInterval: POLL_MS,
  });
  const activityQ = useApiQuery<ActivityResponse>(
    endpoints.activity({ limit: ACTIVITY_LIMIT }),
    { refetchInterval: POLL_MS },
  );

  const today = useMemo<TodaySummary>(
    () => summarizeToday(activityQ.data?.entries ?? []),
    [activityQ.data],
  );

  if (pendingQ.loading && !pendingQ.data) {
    return <LoadingState message="Loading dashboard…" />;
  }

  if (pendingQ.error && !pendingQ.data) {
    return (
      <ErrorState
        title="Could not load dashboard"
        message={pendingQ.error}
        onRetry={() => void pendingQ.refetch()}
        retrying={pendingQ.loading}
      />
    );
  }

  const counts = pendingQ.data?.counts ?? { blog_posts: 0, groups_to_join: 0 };
  const asOf = pendingQ.data?.as_of ?? null;

  return (
    <section className="space-y-6">
      <header className="flex items-baseline justify-between gap-3 flex-wrap">
        <h1 className="text-2xl font-bold text-slate-900">Dashboard</h1>
        <Link
          to="/inbox"
          className="text-sm font-medium text-cyan-700 hover:text-cyan-800"
        >
          Open Inbox &rarr;
        </Link>
      </header>

      {pendingQ.error && pendingQ.data && (
        <Alert status="warning" title="Polling error">
          {pendingQ.error}
        </Alert>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <CountCard
          label="Pending blog posts"
          value={counts.blog_posts}
          icon="📝"
          caption="awaiting your decision"
        />
        <CountCard
          label="Pending groups"
          value={counts.groups_to_join}
          icon="👥"
          caption="ready to join"
        />
        <RefreshedCard asOf={asOf} />
      </div>

      <TodayActivityCard
        summary={today}
        loading={activityQ.loading && !activityQ.data}
        error={activityQ.error && !activityQ.data ? activityQ.error : ""}
      />
    </section>
  );
}

interface CountCardProps {
  label: string;
  value: number;
  icon: string;
  caption: string;
}

function CountCard({
  label,
  value,
  icon,
  caption,
}: CountCardProps): React.JSX.Element {
  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-6">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs uppercase tracking-wide text-slate-400">
          {label}
        </p>
        <span aria-hidden="true" className="text-2xl leading-none">
          {icon}
        </span>
      </div>
      <p className="text-5xl font-semibold text-slate-900 mt-4 leading-none tabular-nums">
        {value}
      </p>
      <p className="text-sm text-slate-500 mt-2">{caption}</p>
    </div>
  );
}

interface RefreshedCardProps {
  asOf: string | null;
}

function RefreshedCard({ asOf }: RefreshedCardProps): React.JSX.Element {
  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-6">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs uppercase tracking-wide text-slate-400">
          Last refreshed
        </p>
        <span aria-hidden="true" className="text-2xl leading-none">
          🔄
        </span>
      </div>
      <p className="text-lg font-semibold text-slate-900 mt-4 leading-tight">
        {relativeTime(asOf)}
      </p>
      <p className="text-sm text-slate-500 mt-2">
        polled every {POLL_MS / 1000}s
      </p>
    </div>
  );
}

interface TodayActivityCardProps {
  summary: TodaySummary;
  loading: boolean;
  error: string;
}

function TodayActivityCard({
  summary,
  loading,
  error,
}: TodayActivityCardProps): React.JSX.Element {
  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-5">
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <h2 className="text-xs uppercase tracking-wide text-slate-400 font-semibold">
          Today&apos;s activity
        </h2>
        <Link
          to="/activity"
          className="text-xs font-medium text-cyan-700 hover:text-cyan-800"
        >
          View all &rarr;
        </Link>
      </div>
      {loading ? (
        <p className="text-sm text-slate-500 mt-3">Loading activity…</p>
      ) : error ? (
        <p className="text-sm text-rose-700 mt-3">{error}</p>
      ) : summary.total === 0 ? (
        <p className="text-sm text-slate-500 mt-3">
          No activity logged today yet.
        </p>
      ) : (
        <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MiniStat label="Comments" value={summary.comments} />
          <MiniStat label="Likes" value={summary.likes} />
          <MiniStat label="Joins" value={summary.joins} />
          <MiniStat label="Posts" value={summary.posts} />
        </div>
      )}
    </div>
  );
}

interface MiniStatProps {
  label: string;
  value: number;
}

function MiniStat({ label, value }: MiniStatProps): React.JSX.Element {
  return (
    <div className="rounded-xl border border-brand-border bg-stone-50/40 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-slate-400 font-semibold">
        {label}
      </p>
      <p className="text-2xl font-semibold text-slate-900 leading-none mt-1 tabular-nums">
        {value}
      </p>
    </div>
  );
}
