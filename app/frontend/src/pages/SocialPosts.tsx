import { useCallback, useEffect, useRef, useState } from "react";
import { apiOrigin, getErrorMessage, isHttpStatus } from "../api/client";
import {
  socialPostsUrl,
  socialPostImageUrl,
  approveSocialPost,
  rejectSocialPost,
  composeSocialPosts,
  fetchComposeStatus,
} from "../api/socialPosts";
import type { SocialPost, SocialPostsResponse } from "../api/socialPosts";
import { useApiQuery } from "../hooks/useApiQuery";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";

function sourceBadge(source: string | null): React.JSX.Element {
  const cls =
    source === "gemini"
      ? "bg-indigo-50 text-indigo-700"
      : "bg-stone-100 text-slate-600";
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}
    >
      {source ?? "unknown"}
    </span>
  );
}

function formatSlot(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

interface CardProps {
  post: SocialPost;
  onDecision: (id: string, approve: boolean) => void;
  busy: boolean;
  scheduledFor?: string;
}

function SocialPostCard({
  post,
  onDecision,
  busy,
  scheduledFor,
}: CardProps): React.JSX.Element {
  const flags = post.validation_flags ?? [];

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">
            {post.topic}
          </h3>
          <div className="mt-1">{sourceBadge(post.source)}</div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {scheduledFor ? (
            <span className="px-3 py-1.5 rounded bg-emerald-50 text-emerald-700 text-xs font-medium">
              Scheduled {formatSlot(scheduledFor)}
            </span>
          ) : (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => onDecision(post.id, false)}
                className="px-3 py-1.5 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              >
                Reject
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => onDecision(post.id, true)}
                className="px-3 py-1.5 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-40"
              >
                Approve
              </button>
            </>
          )}
        </div>
      </div>

      {flags.length > 0 && (
        <div className="rounded-lg bg-rose-50 border border-rose-200 px-3 py-2 text-xs text-rose-700">
          <span className="font-semibold">Flagged:</span> {flags.join(", ")}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Image (both platforms)
          </div>
          <img
            className="w-full aspect-square rounded-lg bg-stone-100 object-cover"
            src={socialPostImageUrl(apiOrigin, post.id)}
            alt={post.image_alt ?? post.topic}
          />
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Facebook — posts in its slot
          </div>
          <p className="text-xs text-slate-600 whitespace-pre-wrap">
            {post.fb_caption}
          </p>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Instagram — 4h after Facebook
          </div>
          <p className="text-xs text-slate-600 whitespace-pre-wrap">
            {post.ig_caption}
          </p>
        </div>
      </div>
    </div>
  );
}

export default function SocialPosts(): React.JSX.Element {
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [rejected, setRejected] = useState<Set<string>>(new Set());
  // Approve claims a slot rather than publishing, so the card stays visible
  // with its scheduled time instead of disappearing.
  const [slots, setSlots] = useState<Record<string, string>>({});
  const [composing, setComposing] = useState(false);
  const [composeNote, setComposeNote] = useState<string | null>(null);
  // Published articles with no posts yet. Drives the button's own label, so a
  // click that could only be a no-op says so before you make it.
  const [candidates, setCandidates] = useState<number | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data, loading, error, refetch } =
    useApiQuery<SocialPostsResponse>(socialPostsUrl());

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Poll while a compose run is in flight; refetch the list when it lands so
  // the new posts appear without a manual refresh.
  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(() => {
      void fetchComposeStatus().then((s) => {
        setCandidates(s.candidates);
        if (s.running) return;
        stopPolling();
        setComposing(false);
        if (!s.ok) {
          setComposeNote(`Compose failed: ${s.detail ?? "?"}`);
          return;
        }
        // Zero is a normal outcome, not a failure — but it looks exactly like
        // a dead button, so name the reason rather than just the result.
        setComposeNote(
          s.composed
            ? `Composed ${s.composed} post${s.composed === 1 ? "" : "s"}.`
            : "Nothing to compose — every published article already has its posts.",
        );
        void refetch();
      });
    }, 5000);
  }, [refetch, stopPolling]);

  useEffect(() => stopPolling, [stopPolling]); // clear the interval on unmount

  // A run may already be in flight — another tab, a page reload mid-run, or
  // the daily cron run, which shares this flow's `worker_runs` row.
  useEffect(() => {
    void fetchComposeStatus().then((s) => {
      setCandidates(s.candidates);
      if (s.running) {
        setComposing(true);
        startPolling();
      }
    });
  }, [startPolling]);

  const handleCompose = useCallback(async (): Promise<void> => {
    setComposing(true);
    setComposeNote(null);
    try {
      await composeSocialPosts();
      startPolling();
    } catch (err) {
      if (isHttpStatus(err, 409)) {
        startPolling(); // already running elsewhere; just track it
        return;
      }
      setComposing(false);
      setComposeNote(getErrorMessage(err, "Compose failed."));
    }
  }, [startPolling]);

  const visible = (data?.posts ?? []).filter((p) => !rejected.has(p.id));
  const awaiting = visible.filter((p) => slots[p.id] === undefined);

  const handleDecision = useCallback(
    async (id: string, approve: boolean): Promise<void> => {
      setBusyIds((prev) => new Set(prev).add(id));
      try {
        if (approve) {
          const result = await approveSocialPost(id);
          setSlots((prev) => ({ ...prev, [id]: result.fb_due_at }));
        } else {
          await rejectSocialPost(id);
          setRejected((prev) => new Set(prev).add(id));
        }
      } finally {
        setBusyIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    },
    [],
  );

  if (loading && !data) return <LoadingState message="Loading social posts…" />;
  if (error && !data)
    return (
      <ErrorState
        title="Could not load social posts"
        message={error}
        onRetry={() => void refetch()}
        retrying={loading}
      />
    );

  return (
    <div className="space-y-5">
      <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-bold text-slate-900 leading-none">
            {awaiting.length}
          </span>
          <span className="text-sm text-slate-500">
            posts awaiting review
            {Object.keys(slots).length > 0 &&
              ` · ${Object.keys(slots).length} scheduled`}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {composeNote ? (
            <span className="text-xs text-slate-600 bg-stone-100 rounded px-2 py-1">
              {composeNote}
            </span>
          ) : (
            candidates === 0 && (
              <span className="text-xs text-slate-500">
                No articles waiting — a run re-checks WordPress first
              </span>
            )
          )}
          {/* Never disabled on candidates === 0: the run's own detect-publish
              sweep flips wp_draft rows to wp_published, so it can find work
              this pre-count cannot see. The count informs, it doesn't block. */}
          <button
            type="button"
            onClick={() => void handleCompose()}
            disabled={composing}
            title="Draft FB+IG posts for published articles that don't have them yet"
            className="px-3 py-1.5 rounded-lg bg-amber-600 text-white text-sm font-semibold hover:bg-amber-700 disabled:opacity-50"
          >
            {composing
              ? "Composing… (runs several minutes)"
              : `✍️ Compose posts${candidates ? ` (${candidates})` : ""}`}
          </button>
          <button
            type="button"
            onClick={() => void refetch()}
            disabled={loading}
            className="px-3 py-1.5 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          title="No social posts pending review"
          description={
            candidates === 0
              ? "Every article this app knows to be published already has its FB+IG posts. Click ✍️ Compose posts to re-check WordPress for newly published ones — it also runs automatically at 16:20 UTC daily."
              : "Click ✍️ Compose posts to draft FB+IG posts from published articles — the same run happens automatically every day at 16:20 UTC."
          }
        />
      ) : (
        <div className="space-y-4">
          {visible.map((post) => (
            <SocialPostCard
              key={post.id}
              post={post}
              onDecision={(id, approve) => void handleDecision(id, approve)}
              busy={busyIds.has(post.id)}
              scheduledFor={slots[post.id] ?? post.fb_due_at ?? undefined}
            />
          ))}
        </div>
      )}
    </div>
  );
}
