/**
 * Flow Guide — audit-oriented view of every documented flow.
 *
 * Static snapshot (no polling): one card per flow with title, status pill,
 * humanized last-run time, summary, and a collapsible job list. Sorted to
 * surface stale / never-run / dead flows first so they're easy to spot
 * and prune.
 */

import { useEffect, useMemo, useState } from "react";
import {
  fetchFlowGuide,
  type FlowGuideEntry,
} from "../api/flowGuide";
import { getErrorMessage } from "../api/client";

const PAGE_SUBTITLE =
  "Audit your flows. Identify dead or low-value flows to remove. Stale and never-run flows are sorted to the top.";

type StatusKey = "ok" | "error" | "never" | "stale" | "other";

const STATUS_PILL_BASE =
  "inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border";

const STATUS_PILL_VARIANT: Record<StatusKey, string> = {
  ok: "bg-emerald-100 text-emerald-800 border-emerald-200",
  error: "bg-rose-100 text-rose-800 border-rose-200",
  never: "bg-slate-100 text-slate-700 border-slate-200",
  stale: "bg-amber-100 text-amber-800 border-amber-200",
  other: "bg-slate-100 text-slate-700 border-slate-200",
};

const STATUS_LABEL: Record<StatusKey, string> = {
  ok: "OK",
  error: "Error",
  never: "Never run",
  stale: "Stale",
  other: "Unknown",
};

function classifyStatus(raw: string | null | undefined): StatusKey {
  if (!raw) return "never";
  if (raw === "ok" || raw === "error" || raw === "never" || raw === "stale") {
    return raw;
  }
  return "other";
}

/** Humanize an ISO timestamp into "3 days ago" using Intl.RelativeTimeFormat. */
function humanizeRelative(iso: string | null | undefined): string {
  if (!iso) return "Never run";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffSec = Math.round((then - Date.now()) / 1000);
  const fmt = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const abs = Math.abs(diffSec);
  if (abs < 60) return fmt.format(diffSec, "second");
  if (abs < 3600) return fmt.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return fmt.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 30) return fmt.format(Math.round(diffSec / 86400), "day");
  if (abs < 86400 * 365)
    return fmt.format(Math.round(diffSec / (86400 * 30)), "month");
  return fmt.format(Math.round(diffSec / (86400 * 365)), "year");
}

/**
 * Sort: stale/never/null-status first, then by oldest last_run_at, then
 * alphabetical by title. Items with no last_run_at sort before items that
 * have run at any point.
 */
function sortFlows(flows: FlowGuideEntry[]): FlowGuideEntry[] {
  const isStaleLike = (f: FlowGuideEntry): boolean => {
    const s = f.last_status;
    return s === "stale" || s === "never" || s === null || s === undefined;
  };
  return [...flows].sort((a, b) => {
    const aStale = isStaleLike(a);
    const bStale = isStaleLike(b);
    if (aStale !== bStale) return aStale ? -1 : 1;

    const aTs = a.last_run_at ? new Date(a.last_run_at).getTime() : -Infinity;
    const bTs = b.last_run_at ? new Date(b.last_run_at).getTime() : -Infinity;
    if (aTs !== bTs) return aTs - bTs;

    return a.title.localeCompare(b.title);
  });
}

interface FlowCardProps {
  flow: FlowGuideEntry;
  expanded: boolean;
  onToggle: () => void;
}

/** Format category slug to human-readable title (e.g., "community_expansion" → "Community Expansion"). */
function formatCategoryTitle(category: string | null | undefined): string | null {
  if (!category) return null;
  return category
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** Group jobs by category for display (or ungrouped if no categories). */
function groupJobsByCategory(
  jobs: FlowGuideEntry["jobs"]
): Map<string | null, FlowGuideEntry["jobs"]> {
  const grouped = new Map<string | null, FlowGuideEntry["jobs"]>();
  for (const job of jobs) {
    const cat = job.category || null;
    if (!grouped.has(cat)) {
      grouped.set(cat, []);
    }
    grouped.get(cat)!.push(job);
  }
  return grouped;
}

function FlowCard({ flow, expanded, onToggle }: FlowCardProps): React.JSX.Element {
  const statusKey = classifyStatus(flow.last_status);
  const jobCount = flow.jobs.length;
  const groupedJobs = groupJobsByCategory(flow.jobs);

  return (
    <article className="bg-white rounded-md border border-slate-200 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900 truncate">
            {flow.title}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{flow.id}</p>
        </div>
        <span className={`${STATUS_PILL_BASE} ${STATUS_PILL_VARIANT[statusKey]}`}>
          {STATUS_LABEL[statusKey]}
        </span>
      </div>

      <p className="text-sm text-slate-600">
        <span className="font-medium">Last run:</span>{" "}
        {humanizeRelative(flow.last_run_at)}
      </p>

      <p className="text-sm text-slate-700">{flow.summary}</p>

      <div>
        <button
          type="button"
          onClick={onToggle}
          className="text-sm font-medium text-cyan-700 hover:text-cyan-900 hover:underline"
          aria-expanded={expanded}
        >
          {expanded ? "Hide" : "Show"} jobs ({jobCount})
        </button>

        {expanded && (
          <div className="mt-2 pl-3 border-l-2 border-slate-100 space-y-3">
            {jobCount === 0 ? (
              <p className="text-sm text-slate-500 italic">
                No scheduled jobs — state-file driven.
              </p>
            ) : (
              Array.from(groupedJobs.entries()).map(([category, categoryJobs]) => (
                <div key={category || "ungrouped"}>
                  {category && (
                    <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-1.5">
                      {formatCategoryTitle(category)}
                    </h3>
                  )}
                  <ul className="space-y-1.5">
                    {categoryJobs.map((job) => (
                      <li key={job.id} className="text-sm text-slate-600">
                        <span className="font-mono text-xs text-slate-800">
                          {job.id}
                        </span>
                        <span className="text-slate-400"> — </span>
                        <span>{job.summary}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </article>
  );
}

export default function FlowGuide(): React.JSX.Element {
  const [flows, setFlows] = useState<FlowGuideEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    loadGuide();
  }, []);

  async function loadGuide() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchFlowGuide();
      setFlows(res.flows);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load flow guide"));
    } finally {
      setLoading(false);
    }
  }

  function toggleExpand(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  const sortedFlows = useMemo(() => sortFlows(flows), [flows]);

  if (loading) {
    return <div className="text-slate-500">Loading flows...</div>;
  }

  if (error) {
    return (
      <div className="bg-red-50 text-red-700 p-4 rounded-md">
        <h3 className="font-semibold mb-1">Error loading flow guide</h3>
        <p className="text-sm">{error}</p>
        <button
          onClick={loadGuide}
          className="mt-3 text-sm font-medium hover:underline"
        >
          Try Again
        </button>
      </div>
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <p className="text-sm text-slate-500">{PAGE_SUBTITLE}</p>

      {sortedFlows.length === 0 ? (
        <div className="text-slate-500 text-center py-12 bg-white rounded-md border border-slate-200">
          No flows reported.
        </div>
      ) : (
        <div className="space-y-3">
          {sortedFlows.map((flow) => (
            <FlowCard
              key={flow.id}
              flow={flow}
              expanded={expanded.has(flow.id)}
              onToggle={() => toggleExpand(flow.id)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
