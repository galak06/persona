/**
 * PendingTab — lists every pending approval item the backend returns.
 *
 * Polls `GET /api/v1/pending` every 3 seconds. Renders the discriminated
 * union (`BlogPostCard` / `GroupCard`) and tracks an optimistic-removal
 * set so a card disappears the instant the user clicks Approve/Skip,
 * before the next refetch ships fresh data.
 *
 * NOTE 2026-05-15 — the engagement-comment flow was retired. The server
 * never ships `type=="comment"` items via this endpoint anymore;
 * CommentCard remains in the repo as dead code (see its disabled-marker
 * comment) but is not imported here.
 */

import { useCallback, useMemo, useState } from "react";

import Alert from "../../components/ui/Alert";
import EmptyState from "../../components/ui/EmptyState";
import LoadingState from "../../components/ui/LoadingState";
import Spinner from "../../components/ui/Spinner";
import { endpoints } from "../../api/endpoints";
import { useApiQuery } from "../../hooks/useApiQuery";
import type { PendingItem, PendingResponse } from "../../types/openapi";

import BlogPostCard from "./BlogPostCard";
import GroupCard from "./GroupCard";
import { relativeTime } from "./shared";

const POLL_MS = 3000;

export default function PendingTab(): React.JSX.Element {
  const { data, loading, error, refetch } = useApiQuery<PendingResponse>(
    endpoints.pending,
    { refetchInterval: POLL_MS },
  );

  const [optimisticallyRemoved, setOptimisticallyRemoved] = useState<
    Set<string>
  >(() => new Set());

  const markResolved = useCallback((id: string): void => {
    setOptimisticallyRemoved((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  }, []);

  // Items the server returned, minus anything the user just decided
  // locally.
  const visibleItems = useMemo<PendingItem[]>(() => {
    const items = data?.items ?? [];
    if (optimisticallyRemoved.size === 0) return items;
    return items.filter((it) => !optimisticallyRemoved.has(it.id));
  }, [data, optimisticallyRemoved]);

  const handleManualRefresh = useCallback((): void => {
    void refetch();
  }, [refetch]);

  // Initial load — nothing fetched yet.
  if (loading && !data) {
    return <LoadingState message="Loading pending items…" />;
  }

  if (error && !data) {
    return (
      <div className="space-y-4">
        <Alert status="error" title="Could not load pending queue">
          {error}
        </Alert>
        <div className="flex justify-center">
          <button
            type="button"
            onClick={handleManualRefresh}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const counts = data?.counts ?? { blog_posts: 0, groups_to_join: 0 };
  const totalPending = visibleItems.length;
  const asOf = data?.as_of ?? null;

  return (
    <div className="space-y-4">
      <Header
        total={totalPending}
        blogPosts={counts.blog_posts}
        groups={counts.groups_to_join}
        asOf={asOf}
        refreshing={loading}
        onRefresh={handleManualRefresh}
      />

      {error && data && (
        <Alert status="warning" title="Polling error">
          {error}
        </Alert>
      )}

      {visibleItems.length === 0 ? (
        <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card">
          <EmptyState
            title="All caught up"
            description="Nothing pending — automation is up to date."
          />
        </div>
      ) : (
        <div className="space-y-4">
          {visibleItems.map((item) => {
            switch (item.type) {
              case "blog_post":
                return (
                  <BlogPostCard
                    key={item.id}
                    item={item}
                    onResolved={markResolved}
                  />
                );
              case "group_to_join":
                return (
                  <GroupCard
                    key={item.id}
                    item={item}
                    onResolved={markResolved}
                  />
                );
              // @ts-expect-error Legacy comments
              case "comment":
                return null;
            }
          })}
        </div>
      )}
    </div>
  );
}

interface HeaderProps {
  total: number;
  blogPosts: number;
  groups: number;
  asOf: string | null;
  refreshing: boolean;
  onRefresh: () => void;
}

function Header({
  total,
  blogPosts,
  groups,
  asOf,
  refreshing,
  onRefresh,
}: HeaderProps): React.JSX.Element {
  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-4 flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-baseline gap-3 min-w-0">
        <span className="text-2xl font-bold text-slate-900 leading-none">
          {total}
        </span>
        <span className="text-sm text-slate-500">
          pending
          <span className="text-slate-400">
            {" "}
            ({blogPosts} blog {blogPosts === 1 ? "post" : "posts"}, {groups}{" "}
            {groups === 1 ? "group" : "groups"})
          </span>
        </span>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-xs text-slate-400">
          last refresh: {relativeTime(asOf)}
        </span>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {refreshing && <Spinner size="sm" className="text-amber-600" />}
          Refresh
        </button>
      </div>
    </div>
  );
}
