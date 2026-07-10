/**
 * Flows page — live health snapshot of every registered worker.
 *
 * Polls `GET /api/v1/workers` every 15s and renders one card per
 * worker with status pill, last-run timestamp, optional error block.
 */

import { useMemo, useState, useEffect } from "react";

import Alert from "../components/ui/Alert";
import LoadingState from "../components/ui/LoadingState";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";

const POLL_MS = 15_000;

const STATUS_STYLES: Record<WorkerStatus["status"], string> = {
  success: "bg-emerald-100 text-emerald-800 border-emerald-200",
  error:   "bg-rose-100 text-rose-800 border-rose-200",
  running: "bg-sky-100 text-sky-700 border-sky-200",
  never:   "bg-slate-100 text-slate-700 border-slate-200",
};

const STATUS_LABEL: Record<WorkerStatus["status"], string> = {
  success: "OK",
  error:   "Error",
  running: "Running",
  never:   "Never run",
};

/** Format an ISO timestamp as a short relative phrase ("3m ago"). */
function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}

function formatAbsTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

interface RefreshedIndicatorProps {
  asOf: number;
}

function RefreshedIndicator({ asOf }: RefreshedIndicatorProps): React.JSX.Element {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((now - asOf) / 1000));
  return (
    <p className="text-xs text-slate-500">
      Refreshed {seconds}s ago · polled every {POLL_MS / 1000}s
    </p>
  );
}

interface FlowCardProps {
  worker: WorkerStatus;
}

function FlowCard({ worker }: FlowCardProps): React.JSX.Element {
  return (
    <article className="bg-white rounded-lg border border-slate-200 shadow-sm p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-slate-900 truncate">
            {worker.title}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{worker.label}</p>
        </div>
        <span
          className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border ${STATUS_STYLES[worker.status]}`}
        >
          {STATUS_LABEL[worker.status]}
        </span>
      </div>

      <p className="text-sm text-slate-600">
        {worker.last_run ? (
          <>
            <span className="font-medium">Last run:</span>{" "}
            {formatRelativeTime(worker.last_run)}{" "}
            <span className="text-slate-400">
              · {formatAbsTime(worker.last_run)}
            </span>
          </>
        ) : (
          <span className="text-slate-500">Last run: Never</span>
        )}
      </p>

      <p className="text-sm text-slate-600">{worker.description}</p>

      {worker.status === "error" && worker.message && (
        <div className="bg-red-50 border border-red-200 text-red-900 text-sm rounded-md p-3">
          <p className="font-semibold mb-1">Last error</p>
          <pre className="text-xs whitespace-pre-wrap break-words font-mono">
            {worker.message.slice(0, 500)}
          </pre>
        </div>
      )}
    </article>
  );
}

export default function Flows(): React.JSX.Element {
  const { data, loading, error } = useApiQuery<WorkerStatus[]>(
    endpoints.workers,
    { refetchInterval: POLL_MS },
  );

  // eslint-disable-next-line react-hooks/exhaustive-deps, react-hooks/purity
  const asOf = useMemo(() => Date.now(), [data]);

  if (loading && !data) {
    return <LoadingState message="Loading workers…" />;
  }

  if (error && !data) {
    return (
      <Alert status="error" title="Could not load workers">
        {error}
      </Alert>
    );
  }

  const workers = data ?? [];

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-end">
        <RefreshedIndicator asOf={asOf} />
      </div>

      {error && data && (
        <Alert status="warning" title="Polling error">
          {error}
        </Alert>
      )}

      {workers.length === 0 ? (
        <p className="text-sm text-slate-500">No workers reported.</p>
      ) : (
        <div className="space-y-4">
          {workers.map((w) => (
            <FlowCard key={w.label} worker={w} />
          ))}
        </div>
      )}
    </section>
  );
}
