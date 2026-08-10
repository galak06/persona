import { useCallback, useState } from "react";
import { apiOrigin } from "../api/client";
import { ideasUrl, updateIdeaStatus, reelVideoUrl } from "../api/ideas";
import type { ContentIdea, IdeasResponse } from "../api/ideas";
import { useApiQuery } from "../hooks/useApiQuery";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";

function sourceBadge(source: string | null): React.JSX.Element {
  const cls =
    source === "openart" ? "bg-indigo-50 text-indigo-700" : "bg-stone-100 text-slate-600";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}>
      {source ?? "unknown"}
    </span>
  );
}

interface CardProps {
  idea: ContentIdea;
  onDecision: (id: string, status: string) => void;
  busy: boolean;
}

function ReelCard({ idea, onDecision, busy }: CardProps): React.JSX.Element {
  const flags = idea.reel_validation_flags ?? [];

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">{idea.topic}</h3>
          <div className="mt-1">{sourceBadge(idea.reel_source)}</div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecision(idea.id, "wp_published")}
            className="px-3 py-1.5 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          >
            Reject
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => onDecision(idea.id, "social_done")}
            className="px-3 py-1.5 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-40"
          >
            Approve
          </button>
        </div>
      </div>

      {flags.length > 0 && (
        <div className="rounded-lg bg-rose-50 border border-rose-200 px-3 py-2 text-xs text-rose-700">
          <span className="font-semibold">Flagged:</span> {flags.join(", ")}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Instagram
          </div>
          <video
            controls
            className="w-full aspect-[9/16] rounded-lg bg-black object-contain"
            src={reelVideoUrl(apiOrigin, idea.id, "ig")}
          />
          <p className="mt-2 text-xs text-slate-600 whitespace-pre-wrap">{idea.reel_ig_caption}</p>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Facebook
          </div>
          <video
            controls
            className="w-full aspect-[9/16] rounded-lg bg-black object-contain"
            src={reelVideoUrl(apiOrigin, idea.id, "fb")}
          />
          <p className="mt-2 text-xs text-slate-600 whitespace-pre-wrap">{idea.reel_fb_caption}</p>
        </div>
      </div>
    </div>
  );
}

export default function Reels(): React.JSX.Element {
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [localStatuses, setLocalStatuses] = useState<Record<string, string>>({});

  const url = ideasUrl({ status: "social_queued" });
  const { data, loading, error, refetch } = useApiQuery<IdeasResponse>(url);

  const pending = (data?.ideas ?? []).filter((i) => localStatuses[i.id] === undefined);

  const handleDecision = useCallback(async (id: string, newStatus: string): Promise<void> => {
    setBusyIds((prev) => new Set(prev).add(id));
    try {
      await updateIdeaStatus(id, newStatus);
      setLocalStatuses((prev) => ({ ...prev, [id]: newStatus }));
    } finally {
      setBusyIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }, []);

  if (loading && !data) return <LoadingState message="Loading reels…" />;
  if (error && !data)
    return (
      <ErrorState
        title="Could not load reels"
        message={error}
        onRetry={() => void refetch()}
        retrying={loading}
      />
    );

  return (
    <div className="space-y-5">
      <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-bold text-slate-900 leading-none">{pending.length}</span>
          <span className="text-sm text-slate-500">reels awaiting review</span>
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

      {pending.length === 0 ? (
        <EmptyState
          title="No reels pending review"
          description="Run scripts/crewai_reels_pipeline.py to compose reels from newly-published posts."
        />
      ) : (
        <div className="space-y-4">
          {pending.map((idea) => (
            <ReelCard
              key={idea.id}
              idea={idea}
              onDecision={(id, status) => void handleDecision(id, status)}
              busy={busyIds.has(idea.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
