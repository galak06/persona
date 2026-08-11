import { useCallback, useState } from "react";
import { apiOrigin } from "../api/client";
import {
  socialPostsUrl,
  socialPostImageUrl,
  approveSocialPost,
  rejectSocialPost,
} from "../api/socialPosts";
import type { SocialPost, SocialPostsResponse } from "../api/socialPosts";
import { useApiQuery } from "../hooks/useApiQuery";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";

function sourceBadge(source: string | null): React.JSX.Element {
  const cls =
    source === "gemini" ? "bg-indigo-50 text-indigo-700" : "bg-stone-100 text-slate-600";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}>
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
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">{post.topic}</h3>
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
          <p className="text-xs text-slate-600 whitespace-pre-wrap">{post.fb_caption}</p>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Instagram — 4h after Facebook
          </div>
          <p className="text-xs text-slate-600 whitespace-pre-wrap">{post.ig_caption}</p>
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

  const { data, loading, error, refetch } = useApiQuery<SocialPostsResponse>(socialPostsUrl());

  const visible = (data?.posts ?? []).filter((p) => !rejected.has(p.id));
  const awaiting = visible.filter((p) => slots[p.id] === undefined);

  const handleDecision = useCallback(async (id: string, approve: boolean): Promise<void> => {
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
  }, []);

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
          <span className="text-2xl font-bold text-slate-900 leading-none">{awaiting.length}</span>
          <span className="text-sm text-slate-500">
            posts awaiting review
            {Object.keys(slots).length > 0 && ` · ${Object.keys(slots).length} scheduled`}
          </span>
        </div>
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {visible.length === 0 ? (
        <EmptyState
          title="No social posts pending review"
          description="Run scripts/crewai_social_posts_pipeline.py to compose FB+IG posts from published articles."
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
