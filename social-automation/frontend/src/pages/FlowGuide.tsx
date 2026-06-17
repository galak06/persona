/**
 * Worker Explorer — inspect and trigger automation workers.
 *
 * Polls `GET /api/v1/workers` every 10s. Select a worker from the
 * dropdown to see its status card, trigger it, tail its log, or
 * inspect its JSON artifact.
 */

import { useState } from "react";

import LoadingState from "../components/ui/LoadingState";
import EmptyState from "../components/ui/EmptyState";
import WorkerCard from "../components/ui/WorkerCard";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";

const POLL_MS = 10_000;

export default function FlowGuide(): React.JSX.Element {
  const { data: workers, loading, error } = useApiQuery<WorkerStatus[]>(
    endpoints.workers,
    { refetchInterval: POLL_MS },
  );

  const [selectedLabel, setSelectedLabel] = useState<string>("");

  const list = workers ?? [];
  // Default to first worker when none explicitly chosen
  const effectiveLabel = selectedLabel || (list.length > 0 ? list[0].label : "");
  const selected = list.find((w) => w.label === effectiveLabel) ?? null;

  if (loading && !workers) {
    return <LoadingState message="Loading workers…" />;
  }

  if (error && !workers) {
    return (
      <div className="bg-red-50 text-red-700 p-4 rounded-md">
        <h3 className="font-semibold mb-1">Error loading workers</h3>
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  if (list.length === 0) {
    return (
      <EmptyState
        title="No workers registered"
        description="Workers will appear here once the API reports them."
      />
    );
  }

  return (
    <section className="flex flex-col gap-4">
      <p className="text-sm text-slate-500">
        Inspect and trigger your automation workers.
      </p>

      {/* Worker selector */}
      <div>
        <label
          htmlFor="worker-select"
          className="block text-xs font-medium text-slate-600 mb-1"
        >
          Select Worker
        </label>
        <select
          id="worker-select"
          value={effectiveLabel}
          onChange={(e) => setSelectedLabel(e.target.value)}
          className="w-full max-w-sm rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
        >
          {list.map((w) => (
            <option key={w.label} value={w.label}>
              {w.title} ({w.label})
            </option>
          ))}
        </select>
      </div>

      {/* Worker detail card — key resets panel state on worker change */}
      {selected && <WorkerCard key={selected.label} worker={selected} />}
    </section>
  );
}
