import { useCallback, useEffect, useRef, useState } from "react";
import { apiOrigin, getErrorMessage, isHttpStatus } from "../api/client";
import { endpoints } from "../api/endpoints";
import { useBrand } from "../context/BrandContext";
import {
  ideasUrl,
  updateIdeaStatus,
  reelVideoUrl,
  composeReels,
  fetchComposeStatus,
} from "../api/ideas";
import type { ContentIdea, IdeasResponse } from "../api/ideas";
import { useApiQuery } from "../hooks/useApiQuery";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";

// Beat images are resolved per beat, so "mixed" (some AI, some hero) is a
// normal middle state -- labelling such a reel "Fallback" would misreport it.
// All three are ordinary outcomes: understated styling, never an error look.
const SOURCE_LABELS: Record<string, string> = {
  openart: "AI images",
  mixed: "Partly AI images",
  fallback: "Hero image",
};

function sourceBadge(source: string | null): React.JSX.Element {
  const cls =
    source === "openart" || source === "mixed"
      ? "bg-indigo-50 text-indigo-700"
      : "bg-stone-100 text-slate-600";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${cls}`}>
      {(source && SOURCE_LABELS[source]) ?? source ?? "unknown"}
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

/** Note to show when landing back from the OpenArt OAuth callback
 * (`?openart=connected|error`). Read during state init rather than in an
 * effect so the first render already carries it. */
function readOpenArtNote(): string | null {
  const params = new URLSearchParams(window.location.search);
  const openart = params.get("openart");
  if (!openart) return null;
  return openart === "connected"
    ? "OpenArt connected — new reels will use AI-generated images."
    : `OpenArt not connected: ${params.get("reason") ?? "unknown error"}`;
}

export default function Reels(): React.JSX.Element {
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [localStatuses, setLocalStatuses] = useState<Record<string, string>>({});
  const [composing, setComposing] = useState(false);
  const [composeNote, setComposeNote] = useState<string | null>(readOpenArtNote);
  const { selectedBrand } = useBrand();
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const url = ideasUrl({ status: "social_queued" });
  const { data, loading, error, refetch } = useApiQuery<IdeasResponse>(url);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Poll while a compose run is in flight; refetch the list when it lands so
  // the new reels appear without a manual refresh.
  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(() => {
      void fetchComposeStatus().then((s) => {
        if (s.running) return;
        stopPolling();
        setComposing(false);
        // A run without OpenArt is a plain success (hero images) -- never
        // flagged as degraded, never prompts to authorize anything.
        setComposeNote(s.ok ? "New reels composed." : `Compose failed: ${s.detail ?? "?"}`);
        if (s.ok) void refetch();
      });
    }, 5000);
  }, [refetch, stopPolling]);

  useEffect(() => stopPolling, [stopPolling]); // clear the interval on unmount

  // A run may already be in flight (another tab, or a page reload mid-run).
  useEffect(() => {
    void fetchComposeStatus().then((s) => {
      if (s.running) {
        setComposing(true);
        startPolling();
      }
    });
  }, [startPolling]);

  // Strip the OAuth callback params once the note above has been read from
  // them, so a reload doesn't re-announce a connection the user already saw.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.get("openart")) return;
    params.delete("openart");
    params.delete("reason");
    const query = params.toString();
    window.history.replaceState(null, "", `${window.location.pathname}${query ? `?${query}` : ""}`);
  }, []);

  const handleCompose = useCallback(async (): Promise<void> => {
    setComposing(true);
    setComposeNote(null);
    try {
      await composeReels();
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

  const handleAuthorizeOpenArt = useCallback((): void => {
    const returnTo = `${window.location.origin}/reels`;
    window.location.href = `${apiOrigin}/api/v1${endpoints.oauthOpenartStart(selectedBrand, returnTo)}`;
  }, [selectedBrand]);

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
        <div className="flex items-center gap-2">
          {composeNote && <span className="text-xs text-slate-500">{composeNote}</span>}
          {/* Opt-in upgrade, deliberately understated: reels compose fine
              without OpenArt, so this is never a warning or a next step. */}
          <button
            type="button"
            onClick={handleAuthorizeOpenArt}
            title="Optional: connect OpenArt to generate AI images for each beat instead of reusing the post's hero image"
            className="text-xs text-slate-400 underline underline-offset-2 hover:text-slate-600"
          >
            Connect OpenArt
          </button>
          <button
            type="button"
            onClick={() => void handleCompose()}
            disabled={composing}
            className="px-3 py-1.5 rounded-lg bg-amber-600 text-white text-sm font-semibold hover:bg-amber-700 disabled:opacity-50"
          >
            {composing ? "Composing… (runs several minutes)" : "🎬 Compose reels"}
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

      {pending.length === 0 ? (
        <EmptyState
          title="No reels pending review"
          description="Click 🎬 Compose reels to build reels from newly-published posts."
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
