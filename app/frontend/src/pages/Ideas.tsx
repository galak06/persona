import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { apiOrigin } from "../api/client";
import {
  ideasUrl,
  updateIdeaStatus,
  slidesApiUrl,
  slideImageUrl,
  generateIdeas,
  fetchGenerateStatus,
} from "../api/ideas";
import type { ContentIdea, IdeasResponse, SlidesResponse } from "../api/ideas";
import { useApiQuery } from "../hooks/useApiQuery";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";

const CATEGORIES = [
  "all", "recipes", "health", "training", "nutrition",
  "gear-toys", "grooming", "breed-specific", "safety",
] as const;

const STATUSES = [
  { value: "all", label: "All" },
  { value: "publish", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "skipped", label: "Skipped" },
  { value: "enriching", label: "Enriching" },
  { value: "wp_draft", label: "WP Draft" },
  { value: "wp_published", label: "Published" },
  { value: "social_done", label: "Social Done" },
] as const;

const GOAL_LABEL: Record<string, string> = {
  educate: "📚 Educate",
  inspire: "✨ Inspire",
  entertain: "🎉 Entertain",
  convert: "💰 Convert",
};

const FAILURE_STATUSES = new Set(["write_failed", "validation_failed"]);

function statusBadge(status: string, failureReason: string | null): React.JSX.Element {
  const isFailure = FAILURE_STATUSES.has(status);
  const cls =
    status === "approved" ? "bg-emerald-50 text-emerald-700" :
    status === "skipped" ? "bg-slate-100 text-slate-500" :
    status === "publish" ? "bg-amber-50 text-amber-700" :
    status === "social_done" ? "bg-violet-50 text-violet-700" :
    status === "wp_published" ? "bg-blue-50 text-blue-700" :
    isFailure ? "bg-rose-50 text-rose-700" :
    "bg-stone-100 text-stone-600";
  const label = status === "publish" ? "pending" : status.replace(/_/g, " ");
  // Failures are terminal and nothing retries them, so the cause is the only
  // actionable thing in this cell. Full text on hover (reasons run long --
  // they carry the editor's score or the exact banned claim term), truncated
  // inline so the table stays scannable. Rows that failed before
  // failure_reason existed have no reason and just show the badge.
  return (
    <div className="flex flex-col gap-0.5 max-w-[190px]">
      <span
        className={`self-start rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}
        title={failureReason ?? undefined}
      >
        {label}
      </span>
      {isFailure && failureReason && (
        <span className="line-clamp-2 text-[11px] leading-tight text-rose-600/80" title={failureReason}>
          {failureReason}
        </span>
      )}
    </div>
  );
}

function SlidePreview({ ideaId }: { ideaId: string }): React.JSX.Element {
  const { data, loading } = useApiQuery<SlidesResponse>(slidesApiUrl(ideaId));
  // Cache-busting token fixed at mount time (component remounts whenever the
  // idea list reloads, which is enough to bust stale slide images).
  const [bust] = useState(() => Math.floor(Date.now() / 30000));

  if (loading) return <p className="text-xs text-slate-400 py-2">Loading slides…</p>;
  if (!data || data.count === 0) {
    return (
      <p className="text-xs text-slate-400 py-2">
        No slides yet — run{" "}
        <code className="bg-stone-100 px-1 rounded text-slate-600">
          worker_content_carousel --idea-id {ideaId}
        </code>{" "}
        to generate.
      </p>
    );
  }

  return (
    <div className="flex gap-2 flex-wrap pt-1">
      {data.slides.map((s) => (
        <img
          key={s.n}
          src={`${slideImageUrl(apiOrigin, ideaId, s.n)}?t=${bust}`}
          alt={`Slide ${s.n}`}
          className="h-28 w-28 rounded-lg object-cover border border-stone-200 shadow-sm"
        />
      ))}
    </div>
  );
}

interface RowProps {
  idea: ContentIdea;
  rowNumber: number;
  onDecision: (id: string, status: string) => void;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
}

function IdeaRow({ idea, rowNumber, onDecision, busy, expanded, onToggle }: RowProps): React.JSX.Element {
  const isPending = idea.status === "publish";
  const isApproved = idea.status === "approved" || idea.status === "social_done";
  // Narrower than isApproved: only a still-"approved" idea can be disabled.
  // "social_done" means it already went out on social — disabling it post-hoc
  // makes no sense, so the Disable button must not use isApproved here.
  const canDisable = idea.status === "approved";

  return (
    <>
      <tr className="border-b border-stone-100 hover:bg-stone-50/60 align-top group">
        <td className="px-4 py-2.5 pr-3 whitespace-nowrap text-xs text-slate-400 tabular-nums">
          {rowNumber}
        </td>
        <td className="py-2.5 pr-3 text-xs text-slate-400 font-medium uppercase tracking-wide max-w-[140px] break-words">
          {idea.category}
        </td>
        <td className="py-2.5 pr-3 text-sm text-slate-800 max-w-[220px]">
          <span className="font-medium leading-snug">{idea.topic}</span>
          {idea.target_keyword && (
            <div className="text-xs text-slate-400 mt-0.5">{idea.target_keyword}</div>
          )}
        </td>
        <td className="py-2.5 pr-3 text-xs text-slate-500 max-w-[160px] hidden md:table-cell">
          <span className="line-clamp-3" title={idea.nalla_context ?? undefined}>
            {idea.nalla_context ?? "—"}
          </span>
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap text-xs hidden lg:table-cell">
          {idea.post_goal ? (GOAL_LABEL[idea.post_goal] ?? idea.post_goal) : "—"}
        </td>
        <td className="py-2.5 pr-3 text-xs text-slate-400 max-w-[130px] hidden 2xl:table-cell">
          <span className="line-clamp-2" title={idea.input ?? undefined}>
            {idea.input ?? "—"}
          </span>
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap text-right text-xs font-semibold tabular-nums text-slate-600">
          {idea.match_score !== null ? idea.match_score.toFixed(0) : "—"}
        </td>
        <td className="py-2.5 pr-3 align-top">{statusBadge(idea.status, idea.failure_reason)}</td>
        <td className="py-2.5 whitespace-nowrap">
          <div className="flex items-center gap-1.5">
            {isPending && (
              <div className="flex items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onDecision(idea.id, "skipped")}
                  className="px-2 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                >
                  Skip
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onDecision(idea.id, "approved")}
                  className="px-2 py-1 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-40"
                >
                  Approve
                </button>
              </div>
            )}
            {isApproved && (
              <div className="flex items-center gap-1.5">
                {canDisable && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onDecision(idea.id, "skipped")}
                    className="px-2 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                  >
                    Disable
                  </button>
                )}
                <button
                  type="button"
                  onClick={onToggle}
                  className="px-2.5 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50"
                >
                  {expanded ? "▲ Slides" : "▼ Slides"}
                </button>
              </div>
            )}
          </div>
        </td>
      </tr>
      {expanded && isApproved && (
        <tr className="bg-stone-50/80 border-b border-stone-100">
          <td colSpan={9} className="px-4 pb-3 pt-1">
            <SlidePreview ideaId={idea.id} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function Ideas(): React.JSX.Element {
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [localStatuses, setLocalStatuses] = useState<Record<string, string>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [generateNote, setGenerateNote] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const url = ideasUrl({ status: statusFilter === "all" ? undefined : statusFilter });
  const { data, loading, error, refetch } = useApiQuery<IdeasResponse>(url);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  // Poll while a scout run is in flight; refetch the list when it lands so
  // the new ideas appear without a manual refresh.
  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(() => {
      void fetchGenerateStatus().then((s) => {
        if (s.running) return;
        stopPolling();
        setGenerating(false);
        setGenerateNote(s.ok ? "New ideas generated." : `Generation failed: ${s.detail ?? "?"}`);
        if (s.ok) void refetch();
      });
    }, 5000);
  }, [refetch, stopPolling]);

  useEffect(() => stopPolling, [stopPolling]); // clear the interval on unmount

  // A run may already be in flight (another tab, or a page reload mid-run).
  useEffect(() => {
    void fetchGenerateStatus().then((s) => {
      if (s.running) {
        setGenerating(true);
        startPolling();
      }
    });
  }, [startPolling]);

  const handleGenerate = useCallback(async (): Promise<void> => {
    setGenerating(true);
    setGenerateNote(null);
    try {
      await generateIdeas();
      startPolling();
    } catch {
      // 409 = already running elsewhere; just track it.
      startPolling();
    }
  }, [startPolling]);

  const filtered = useMemo(() => {
    const ideas = data?.ideas ?? [];
    const withLocal = ideas.map((i) =>
      localStatuses[i.id] ? { ...i, status: localStatuses[i.id] } : i,
    );
    if (categoryFilter === "all") return withLocal;
    return withLocal.filter((i) => i.category === categoryFilter);
  }, [data, categoryFilter, localStatuses]);

  const handleDecision = useCallback(async (id: string, newStatus: string): Promise<void> => {
    setBusyIds((prev) => new Set(prev).add(id));
    try {
      await updateIdeaStatus(id, newStatus);
      setLocalStatuses((prev) => ({ ...prev, [id]: newStatus }));
    } finally {
      setBusyIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
    }
  }, []);

  const counts = data?.counts ?? {};
  const pendingCount = counts["publish"] ?? 0;

  if (loading && !data) return <LoadingState message="Loading ideas…" />;
  if (error && !data)
    return (
      <ErrorState
        title="Could not load ideas"
        message={error}
        onRetry={() => void refetch()}
        retrying={loading}
      />
    );

  return (
    <div className="space-y-5">
      <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card px-5 py-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-bold text-slate-900 leading-none">{data?.total ?? 0}</span>
          <span className="text-sm text-slate-500">
            ideas
            {pendingCount > 0 && (
              <span className="ml-1 text-amber-700 font-medium">· {pendingCount} pending</span>
            )}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {generateNote && <span className="text-xs text-slate-500">{generateNote}</span>}
          <button
            type="button"
            onClick={() => void handleGenerate()}
            disabled={generating}
            className="px-3 py-1.5 rounded-lg bg-amber-600 text-white text-sm font-semibold hover:bg-amber-700 disabled:opacity-50"
          >
            {generating ? "Generating… (runs a few minutes)" : "✨ Generate ideas"}
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

      <div className="flex flex-wrap gap-3">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="text-sm border-stone-300 rounded-lg shadow-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
        >
          {STATUSES.map((s) => (
            <option key={s.value} value={s.value}>{s.label}</option>
          ))}
        </select>
        <select
          value={categoryFilter}
          onChange={(e) => setCategoryFilter(e.target.value)}
          className="text-sm border-stone-300 rounded-lg shadow-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50"
        >
          {CATEGORIES.map((c) => (
            <option key={c} value={c}>{c === "all" ? "All categories" : c}</option>
          ))}
        </select>
      </div>

      <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
        {filtered.length === 0 ? (
          <EmptyState title="No ideas" description="Run the content ideator or adjust the filters." />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-brand-border bg-stone-50/60">
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-400">#</th>
                  <th className="px-0 py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Category</th>
                  <th className="px-0 py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Topic / Keyword</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400 hidden md:table-cell">Nalla context</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400 hidden lg:table-cell">Goal</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400 hidden 2xl:table-cell">Search signal</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400 text-right">Score</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Status</th>
                  <th className="py-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((idea, index) => (
                  <IdeaRow
                    key={idea.id}
                    idea={idea}
                    rowNumber={index + 1}
                    onDecision={(id, status) => void handleDecision(id, status)}
                    busy={busyIds.has(idea.id)}
                    expanded={expandedId === idea.id}
                    onToggle={() => setExpandedId((prev) => (prev === idea.id ? null : idea.id))}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
