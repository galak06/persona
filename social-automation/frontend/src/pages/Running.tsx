/**
 * Running — live log monitor for actively executing workers.
 *
 * Polls /workers every 3s. For each running worker renders a
 * compact header + auto-scrolling log pane that polls every 1s.
 */

import { useState, useEffect, useRef } from "react";

import LoadingState from "../components/ui/LoadingState";
import apiClient from "../api/client";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";

const WORKER_POLL_MS = 3_000;
const LOG_POLL_MS = 1_000;

// ── LiveLog ───────────────────────────────────────────────────────────────────

interface LiveLogProps {
  label: string;
}

function LiveLog({ label }: LiveLogProps): React.JSX.Element {
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let cancelled = false;

    async function fetchLog() {
      try {
        const res = await apiClient.get<{ lines: string[]; truncated: boolean }>(
          endpoints.workerLog(label, 300),
        );
        if (!cancelled) {
          setLines(res.data.lines ?? []);
          setError(null);
        }
      } catch {
        if (!cancelled) setError("Log unavailable");
      }
    }

    void fetchLog();
    const id = setInterval(() => void fetchLog(), LOG_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [label]);

  // Auto-scroll to bottom on new lines
  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [lines]);

  if (error) {
    return (
      <p className="text-xs text-rose-600 font-mono px-3 py-2">{error}</p>
    );
  }

  return (
    <pre
      ref={preRef}
      className="font-mono text-xs text-slate-300 bg-slate-900 rounded-b-md p-3 overflow-y-auto max-h-72 whitespace-pre-wrap break-words"
    >
      {lines.length > 0 ? lines.join("\n") : "Waiting for output…"}
    </pre>
  );
}

// ── WorkerLogPanel ────────────────────────────────────────────────────────────

interface WorkerLogPanelProps {
  worker: WorkerStatus;
}

function WorkerLogPanel({ worker }: WorkerLogPanelProps): React.JSX.Element {
  return (
    <div className="rounded-md border border-slate-200 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-3 py-2 bg-slate-800">
        <div className="flex items-center gap-2 min-w-0">
          <span className="inline-block w-2 h-2 rounded-full bg-sky-400 animate-pulse flex-shrink-0" />
          <span className="text-sm font-semibold text-white truncate">
            {worker.title}
          </span>
          <span className="text-xs text-slate-400 font-mono hidden sm:inline truncate">
            {worker.label}
          </span>
        </div>
        <span className="text-xs text-sky-300 font-medium flex-shrink-0">
          ● Live
        </span>
      </div>

      {/* Log stream */}
      <LiveLog label={worker.label} />
    </div>
  );
}

// ── Running page ──────────────────────────────────────────────────────────────

export default function Running(): React.JSX.Element {
  const { data: workers, loading } = useApiQuery<WorkerStatus[]>(
    endpoints.workers,
    { refetchInterval: WORKER_POLL_MS },
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
        <p className="text-xs">Checking every {WORKER_POLL_MS / 1_000}s</p>
      </div>
    );
  }

  return (
    <section className="space-y-3">
      <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
        {running.length} worker{running.length !== 1 ? "s" : ""} · logs refresh every {LOG_POLL_MS / 1_000}s
      </p>
      {running.map((w) => (
        <WorkerLogPanel key={w.label} worker={w} />
      ))}
    </section>
  );
}
