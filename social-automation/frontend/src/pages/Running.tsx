/**
 * Running — live view of workers currently executing.
 *
 * Polls every 3s; shows a card per running worker with the log
 * panel pre-opened so progress is visible immediately.
 */

import LoadingState from "../components/ui/LoadingState";
import WorkerCard from "../components/ui/WorkerCard";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";

const POLL_MS = 3_000;

export default function Running(): React.JSX.Element {
  const { data: workers, loading } = useApiQuery<WorkerStatus[]>(
    endpoints.workers,
    { refetchInterval: POLL_MS },
  );

  if (loading && !workers) {
    return <LoadingState message="Checking for running workers…" />;
  }

  const list = workers ?? [];
  const running = list.filter((w) => w.status === "running");

  if (running.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-slate-400 gap-3">
        <span className="text-5xl">⏸</span>
        <p className="text-sm font-medium text-slate-500">No workers running right now</p>
        <p className="text-xs">Checking every {POLL_MS / 1_000}s</p>
      </div>
    );
  }

  return (
    <section className="space-y-4">
      <p className="text-sm text-slate-500">
        {running.length} worker{running.length !== 1 ? "s" : ""} running now
      </p>
      <div className="space-y-4">
        {running.map((w) => (
          <WorkerCard key={w.label} worker={w} />
        ))}
      </div>
    </section>
  );
}
