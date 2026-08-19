import { useCallback, useEffect, useRef, useState } from "react";
import { apiOrigin, getErrorMessage, isHttpStatus } from "../api/client";
import { endpoints } from "../api/endpoints";
import { useBrand } from "../context/BrandContext";
import {
  ideasUrl,
  updateIdeaStatus,
  composeReels,
  fetchComposeStatus,
} from "../api/ideas";
import type { IdeasResponse } from "../api/ideas";
import { fetchOpenartStatus } from "../api/oauth";
import type { OpenArtState } from "../api/oauth";
import { useApiQuery } from "../hooks/useApiQuery";
import ReelCard from "../components/ReelCard";
import ConfirmDialog from "../components/ui/ConfirmDialog";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";

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
  const [showConnectPrompt, setShowConnectPrompt] = useState(false);
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

  const runCompose = useCallback(async (): Promise<void> => {
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

  // Pre-flight the OpenArt connection so a "missing" token gets a one-click
  // Authorize offer before composing. "ok" and "not_configured" compose as
  // today, and a broken status check fails open — it must never block compose.
  const handleComposeClick = useCallback(async (): Promise<void> => {
    if (composing) return; // guard double-clicks while the status fetch is in flight
    let state: OpenArtState = "ok";
    try {
      state = (await fetchOpenartStatus()).state;
    } catch {
      // Fail-open: a broken status check must never block compose.
    }
    if (state === "missing") {
      setShowConnectPrompt(true);
      return;
    }
    await runCompose();
  }, [composing, runCompose]);

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
          {/* Opt-in upgrade, deliberately understated. When the token is
              missing, Compose pre-flights the connection and offers a
              one-time Connect/skip prompt; this link is the always-visible
              way in. Reels still compose fine without OpenArt. */}
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
            onClick={() => void handleComposeClick()}
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

      <ConfirmDialog
        open={showConnectPrompt}
        title="Connect OpenArt?"
        onClose={() => setShowConnectPrompt(false)}
        actions={[
          { label: "Connect OpenArt", variant: "primary", onClick: handleAuthorizeOpenArt },
          {
            label: "Compose without it",
            variant: "secondary",
            onClick: () => {
              setShowConnectPrompt(false);
              void runCompose();
            },
          },
          { label: "Cancel", variant: "ghost", onClick: () => setShowConnectPrompt(false) },
        ]}
      >
        OpenArt generates AI images for each reel beat; without it, reels use each post's hero
        image — that works fine too.
      </ConfirmDialog>
    </div>
  );
}
