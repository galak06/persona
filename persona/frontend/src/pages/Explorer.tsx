/**
 * Flow Explorer — inspect a single worker's status, trigger it, tail its
 * log, or view its JSON artifact.
 *
 * Originally built against the old flows/state pipeline model (a flow ->
 * ordered steps -> per-step input/output file checks). That model, and
 * the `/api/v1/flows/state` + `/api/v1/schedule/{label}/artifact` routes
 * it depended on, were retired in favor of the flat `/api/v1/workers`
 * registry (see api/workers.ts) when Worker Explorer (pages/FlowGuide.tsx)
 * was built — this page was never migrated alongside it and its data
 * source no longer exists. Rewritten here on the same worker-select +
 * WorkerCard pattern FlowGuide.tsx already uses, so `/explorer` renders
 * real data again instead of a dead flows/state fetch.
 */

import { useState } from "react";

import Alert from "../components/ui/Alert";
import EmptyState from "../components/ui/EmptyState";
import LoadingState from "../components/ui/LoadingState";
import WorkerCard from "../components/ui/WorkerCard";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";

const POLL_MS = 30000;

export default function Explorer(): React.JSX.Element {
  const { data: workers, loading, error } = useApiQuery<WorkerStatus[]>(
    endpoints.workers,
    { refetchInterval: POLL_MS },
  );

  const [selectedLabel, setSelectedLabel] = useState<string>("");

  const list = workers ?? [];
  const effectiveLabel =
    (list.find((w) => w.label === selectedLabel) ? selectedLabel : "") ||
    (list.length > 0 ? list[0].label : "");
  const selected = list.find((w) => w.label === effectiveLabel) ?? null;

  if (loading && !workers) return <LoadingState message="Loading workers..." />;
  if (error) return <Alert status="error">{error}</Alert>;

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-bold text-slate-900">Flow Explorer</h1>
        <p className="text-sm text-slate-500">
          Inspect status, trigger, logs, and artifacts for any registered
          worker.
        </p>
      </header>

      {list.length === 0 ? (
        <EmptyState
          title="No workers registered"
          description="Workers will appear here once the API reports them."
        />
      ) : (
        <>
          <div className="space-y-1 max-w-sm">
            <label className="block text-sm font-medium text-slate-700">
              Select Worker
            </label>
            <select
              className="w-full rounded-md border-slate-300 shadow-sm focus:border-cyan-500 focus:ring-cyan-500 sm:text-sm"
              value={effectiveLabel}
              onChange={(e) => setSelectedLabel(e.target.value)}
            >
              {list.map((w) => (
                <option key={w.label} value={w.label}>
                  {w.title} ({w.label})
                </option>
              ))}
            </select>
          </div>

          {selected && <WorkerCard key={selected.label} worker={selected} />}
        </>
      )}
    </div>
  );
}
