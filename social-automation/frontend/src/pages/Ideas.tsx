import { useState, useCallback, useMemo } from "react";
import { ideasUrl, updateIdeaStatus, slidesApiUrl, slideImageUrl } from "../api/ideas";
import type { ContentIdea, IdeasResponse, SlidesResponse } from "../api/ideas";
import { useApiQuery } from "../hooks/useApiQuery";
import Alert from "../components/ui/Alert";
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

function statusBadge(status: string): React.JSX.Element {
  const cls =
    status === "approved" ? "bg-emerald-50 text-emerald-700" :
    status === "skipped" ? "bg-slate-100 text-slate-500" :
    status === "publish" ? "bg-amber-50 text-amber-700" :
    status === "social_done" ? "bg-violet-50 text-violet-700" :
    status === "wp_published" ? "bg-blue-50 text-blue-700" :
    "bg-stone-100 text-stone-600";
  const label = status === "publish" ? "pending" : status.replace(/_/g, " ");
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}>
      {label}
    </span>
  );
}

function SlidePreview({ ideaId }: { ideaId: string }): React.JSX.Element {
  const apiBase = (window as Window & { _API_BASE?: string })._API_BASE ?? "http://localhost:8000";
  const { data, loading } = useApiQuery<SlidesResponse>(slidesApiUrl(ideaId));
  const bust = Math.floor(Date.now() / 30000); // changes every 30s, busts stale cache

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
          src={`${slideImageUrl(apiBase, ideaId, s.n)}?t=${bust}`}
          alt={`Slide ${s.n}`}
          className="h-28 w-28 rounded-lg object-cover border border-stone-200 shadow-sm"
        />
      ))}
    </div>
  );
}

interface RowProps {
  idea: ContentIdea;
  onDecision: (id: string, status: string) => void;
  busy: boolean;
  expanded: boolean;
  onToggle: () => void;
}

function IdeaRow({ idea, onDecision, busy, expanded, onToggle }: RowProps): React.JSX.Element {
  const isPending = idea.status === "publish";
  const isApproved = idea.status === "approved" || idea.status === "social_done";

  return (
    <>
      <tr className="border-b border-stone-100 hover:bg-stone-50/60 align-top group">
        <td className="py-2.5 pr-3 whitespace-nowrap text-xs text-slate-400 font-medium uppercase tracking-wide">
          {idea.category}
        </td>
        <td className="py-2.5 pr-3 text-sm text-slate-800 max-w-xs">
          <span className="font-medium leading-snug">{idea.topic}</span>
          {idea.target_keyword && (
            <div className="text-xs text-slate-400 mt-0.5">{idea.target_keyword}</div>
          )}
        </td>
        <td className="py-2.5 pr-3 text-xs text-slate-500 max-w-[200px] hidden md:table-cell">
          {idea.nalla_context ?? "—"}
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap text-xs hidden lg:table-cell">
          {idea.post_goal ? (GOAL_LABEL[idea.post_goal] ?? idea.post_goal) : "—"}
        </td>
        <td className="py-2.5 pr-3 text-xs text-slate-400 max-w-[180px] hidden xl:table-cell">
          <span className="line-clamp-2" title={idea.input ?? undefined}>
            {idea.input ?? "—"}
          </span>
        </td>
        <td className="py-2.5 pr-3 whitespace-nowrap">{statusBadge(idea.status)}</td>
        <td className="py-2.5 whitespace-nowrap">
          <div className="flex items-center gap-2">
            {isPending && (
              <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onDecision(idea.id, "skipped")}
                  className="px-2.5 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                >
                  Skip
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => onDecision(idea.id, "approved")}
                  className="px-2.5 py-1 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-40"
                >
                  Approve
                </button>
              </div>
            )}
            {isApproved && (
              <button
                type="button"
                onClick={onToggle}
                className="px-2.5 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50"
              >
                {expanded ? "▲ Slides" : "▼ Slides"}
              </button>
            )}
          </div>
        </td>
      </tr>
      {expanded && isApproved && (
        <tr className="bg-stone-50/80 border-b border-stone-100">
          <td colSpan={7} className="px-4 pb-3 pt-1">
            <SlidePreview ideaId={idea.id} />
          </td>
        </tr>
      )}
    </>
  );
}

export default function Ideas(): React.JSX.Element {
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("publish");
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [localStatuses, setLocalStatuses] = useState<Record<string, string>>({});
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const url = ideasUrl({ status: statusFilter === "all" ? undefined : statusFilter });
  const { data, loading, error, refetch } = useApiQuery<IdeasResponse>(url);

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
  if (error && !data) return <Alert status="error" title="Could not load ideas">{error}</Alert>;

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
        <button
          type="button"
          onClick={() => void refetch()}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          Refresh
        </button>
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
                  <th className="px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Category</th>
                  <th className="px-0 py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Topic / Keyword</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400 hidden md:table-cell">Nalla context</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400 hidden lg:table-cell">Goal</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400 hidden xl:table-cell">Search signal</th>
                  <th className="py-3 pr-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Status</th>
                  <th className="py-3 text-xs font-semibold uppercase tracking-wider text-slate-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((idea) => (
                  <IdeaRow
                    key={idea.id}
                    idea={idea}
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
