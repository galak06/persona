/**
 * Running — live log monitor for actively executing workers.
 *
 * Polls /workers every 1s. Shows:
 *   - Running workers: pulsing dot, live-updating log
 *   - Recently completed (<60s): dimmed header, final log snapshot
 */

import { useState, useEffect, useRef } from "react";

import LoadingState from "../components/ui/LoadingState";
import apiClient from "../api/client";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";

const WORKER_POLL_MS = 1_000;
const LOG_POLL_MS = 1_000;
const RECENTLY_ACTIVE_MS = 60_000;

function isRecent(lastRun: string | null | undefined): boolean {
  if (!lastRun) return false;
  return Date.now() - new Date(lastRun).getTime() < RECENTLY_ACTIVE_MS;
}

// ── LiveLog ───────────────────────────────────────────────────────────────────

interface LiveLogProps {
  label: string;
  live: boolean;
}

function LiveLog({ label, live }: LiveLogProps): React.JSX.Element {
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
    if (!live) return () => { cancelled = true; };

    const id = setInterval(() => void fetchLog(), LOG_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [label, live]);

  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [lines]);

  if (error) {
    return <p className="text-xs text-rose-600 font-mono px-3 py-2">{error}</p>;
  }

  return (
    <pre
      ref={preRef}
      className="font-mono text-xs text-slate-300 bg-slate-900 rounded-b-md p-3 overflow-y-auto max-h-72 whitespace-pre-wrap break-words"
    >
      {lines.length > 0
        ? lines.join("\n")
        : live
          ? "Waiting for output…"
          : "(no output)"}
    </pre>
  );
}

// ── WorkerLogPanel ────────────────────────────────────────────────────────────

interface WorkerLogPanelProps {
  worker: WorkerStatus;
  live: boolean;
}

function WorkerLogPanel({ worker, live }: WorkerLogPanelProps): React.JSX.Element {
  const headerBg = live
    ? "bg-slate-800"
    : worker.status === "error"
      ? "bg-rose-900"
      : "bg-slate-600";

  const dotClass = live
    ? "bg-sky-400 animate-pulse"
    : worker.status === "error"
      ? "bg-rose-400"
      : "bg-emerald-400";

  const badge = live
    ? "● Live"
    : worker.status === "error"
      ? "✕ Error"
      : "✓ Done";

  const badgeColor = live
    ? "text-sky-300"
    : worker.status === "error"
      ? "text-rose-300"
      : "text-emerald-300";

  return (
    <div className="rounded-md border border-slate-200 overflow-hidden">
      <div className={`flex items-center justify-between gap-3 px-3 py-2 ${headerBg}`}>
        <div className="flex items-center gap-2 min-w-0">
          <span className={`inline-block w-2 h-2 rounded-full ${dotClass} flex-shrink-0`} />
          <span className="text-sm font-semibold text-white truncate">
            {worker.title}
          </span>
          <span className="text-xs text-slate-400 font-mono hidden sm:inline truncate">
            {worker.label}
          </span>
        </div>
        <span className={`text-xs font-medium flex-shrink-0 ${badgeColor}`}>
          {badge}
        </span>
      </div>

      <LiveLog label={worker.label} live={live} />
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
  const recent = list.filter(
    (w) => w.status !== "running" && w.status !== "never" && isRecent(w.last_run),
  );

  if (running.length === 0 && recent.length === 0) {
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
      {running.length > 0 && (
        <p className="text-xs text-slate-500 uppercase tracking-wide font-medium">
          {running.length} running · logs refresh every {LOG_POLL_MS / 1_000}s
        </p>
      )}
      {running.map((w) => (
        <WorkerLogPanel key={w.label} worker={w} live={true} />
      ))}

      {recent.length > 0 && (
        <>
          <p className="text-xs text-slate-400 uppercase tracking-wide font-medium pt-2">
            Recently completed
          </p>
          {recent.map((w) => (
            <WorkerLogPanel key={w.label} worker={w} live={false} />
          ))}
        </>
      )}
    </section>
  );
}
