/**
 * Schedule page — cron/launchd job state with a manual run-now button
 * per row. Polls `GET /api/v1/flows/state` every 10s for fresh rows;
 * posts to `POST /api/v1/schedule/{label}/trigger` on click. Each row
 * also exposes an inline log-tail viewer (Feature B). The page header
 * surfaces a rose banner whenever scheduled flows are missing from
 * launchctl (Feature C).
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import Alert from "../components/ui/Alert";
import LoadingState from "../components/ui/LoadingState";
import { getErrorMessage } from "../api/client";
import { endpoints } from "../api/endpoints";
import { fetchLogTail } from "../api/schedule";
import { useApiMutation } from "../hooks/useApiMutation";
import { useApiQuery } from "../hooks/useApiQuery";
import type { components } from "../types/openapi";
import type {
  MissingFlowsResponse,
  ScheduleEntry,
} from "../types/openapi";
import LogPanel, { type LogState } from "./ScheduleLogPanel";
import SchedulePipelineView from "./SchedulePipelineView";

type FlowsStateResponse = components["schemas"]["FlowsStateResponse"];
type TriggerResponse = components["schemas"]["TriggerResponse"];

const POLL_MS = 10000;
const TOAST_MS = 3000;
const MISSING_POLL_MS = 60_000;
const TABLE_COL_COUNT = 10;

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

function sortEntries(entries: ScheduleEntry[]): ScheduleEntry[] {
  return [...entries].sort((a, b) => {
    const aOrder = a.order ?? 9999;
    const bOrder = b.order ?? 9999;
    if (aOrder !== bOrder) return aOrder - bOrder;
    return a.label.localeCompare(b.label);
  });
}

interface RowToast {
  status: "success" | "error";
  message: string;
}

interface ScheduleRowProps {
  entry: ScheduleEntry;
  busy: boolean;
  toast: RowToast | null;
  logState: LogState | undefined;
  onTrigger: (label: string, force: boolean) => void;
  onToggleLog: (label: string) => void;
  onRefreshLog: (label: string) => void;
  onCloseLog: (label: string) => void;
}

function ScheduleRow({
  entry,
  busy,
  toast,
  logState,
  onTrigger,
  onToggleLog,
  onRefreshLog,
  onCloseLog,
}: ScheduleRowProps): React.JSX.Element {
  const exit = entry.last_exit_code;
  const exitClass =
    exit == null
      ? "text-slate-400"
      : exit === 0
        ? "text-emerald-700 font-semibold"
        : "text-rose-700 font-semibold";
  const open = !!logState?.open;

  return (
    <>
      <tr className="border-b border-slate-100">
        <td className="px-3 py-2 text-sm text-slate-700">
          {entry.flow_id ?? <span className="text-slate-400">—</span>}
        </td>
        <td className="px-3 py-2 text-xs text-slate-700 font-mono break-all">
          {entry.label}
        </td>
        <td className="px-3 py-2 text-sm text-slate-700">
          {entry.schedule_human}
        </td>
        <td className="px-3 py-2 text-sm">
          {entry.depends_on && entry.depends_on.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {entry.depends_on.map((depId) => (
                <span
                  key={depId}
                  className="inline-block text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-700 font-mono"
                >
                  {depId}
                </span>
              ))}
            </div>
          ) : (
            <span className="text-slate-400">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-sm">
          {(() => {
            const satisfied = entry.inputs_satisfied !== false;
            const failures = (entry.input_status ?? []).filter((s) => !s.ok);
            const tooltip =
              failures.length > 0
                ? failures
                    .map((s) => `${s.path}: ${s.reason ?? "not ok"}`)
                    .join("\n")
                : "All inputs satisfied";
            if (!entry.input_status || entry.input_status.length === 0) {
              return <span className="text-slate-400">—</span>;
            }
            return (
              <span
                title={tooltip}
                className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium ${
                  satisfied
                    ? "bg-emerald-50 text-emerald-700 border border-emerald-200"
                    : "bg-rose-50 text-rose-700 border border-rose-200"
                }`}
              >
                {satisfied ? "✓ ready" : "✗ blocked"}
              </span>
            );
          })()}
        </td>
        <td className="px-3 py-2 text-sm text-slate-600">
          {entry.last_fire_at ? (
            <span title={formatAbsTime(entry.last_fire_at)}>
              {formatRelativeTime(entry.last_fire_at)}
            </span>
          ) : (
            <span className="text-slate-400">—</span>
          )}
        </td>
        <td className={`px-3 py-2 text-sm tabular-nums ${exitClass}`}>
          {exit == null ? "—" : exit}
        </td>
        <td className="px-3 py-2 text-sm text-center">
          {entry.is_loaded ? (
            <span
              className="text-emerald-700 font-semibold"
              aria-label="loaded"
            >
              ✓
            </span>
          ) : (
            <span className="text-slate-400" aria-label="not loaded">
              ✗
            </span>
          )}
        </td>
        <td className="px-3 py-2 text-sm">
          {entry.log_path ? (
            <button
              type="button"
              onClick={() => onToggleLog(entry.label)}
              className="text-xs px-2 py-1 rounded border border-slate-300 text-slate-700 hover:bg-slate-50"
            >
              {open ? "Hide log" : "View log"}
            </button>
          ) : (
            <span className="text-slate-400 text-sm">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-sm">
          <div className="flex items-center gap-2 flex-wrap">
            <button
              type="button"
              onClick={(e) => onTrigger(entry.label, e.shiftKey)}
              disabled={busy}
              title={
                entry.inputs_satisfied === false
                  ? "Prerequisites not satisfied. Shift+click to force-run anyway."
                  : undefined
              }
              className={`inline-flex items-center px-3 py-1 rounded-md text-white text-xs font-medium transition-colors ${
                busy
                  ? "bg-slate-300 cursor-not-allowed"
                  : entry.inputs_satisfied === false
                    ? "bg-amber-500 hover:bg-amber-600"
                    : "bg-cyan-600 hover:bg-cyan-700"
              }`}
            >
              {busy
                ? "Running…"
                : entry.inputs_satisfied === false
                  ? "Run anyway"
                  : "Run now"}
            </button>
            {toast && (
              <span
                className={`inline-flex items-center px-2 py-0.5 rounded-md text-xs border ${
                  toast.status === "success"
                    ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                    : "bg-rose-50 border-rose-200 text-rose-800"
                }`}
                role="status"
              >
                {toast.message}
              </span>
            )}
          </div>
        </td>
      </tr>
      {open && logState && (
        <tr className="border-b border-slate-100">
          <td colSpan={TABLE_COL_COUNT} className="p-0">
            <LogPanel
              entry={entry}
              state={logState}
              onRefresh={onRefreshLog}
              onClose={onCloseLog}
            />
          </td>
        </tr>
      )}
    </>
  );
}

interface MissingBannerProps {
  data: MissingFlowsResponse;
}

function MissingBanner({ data }: MissingBannerProps): React.JSX.Element | null {
  const [expanded, setExpanded] = useState(false);
  if (!data.missing.length) return null;

  const labels = data.missing.map((m) => m.label).join(", ");
  const plural = data.missing.length === 1 ? "" : "s";

  const copyAll = (): void => {
    const joined = data.missing.map((m) => m.command).join("\n");
    void navigator.clipboard.writeText(joined);
  };

  return (
    <Alert status="error" className="mb-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <span>
          {data.missing.length} scheduled flow{plural} not loaded in
          launchctl: <span className="font-mono text-xs">{labels}</span>
        </span>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-xs px-2 py-1 rounded border border-rose-300 text-rose-800 hover:bg-rose-100"
        >
          {expanded ? "Hide fix commands" : "Show fix commands"}
        </button>
      </div>
      {expanded && (
        <div className="mt-3 space-y-2">
          {data.missing.map((m) => (
            <pre
              key={m.label}
              className="bg-white text-slate-700 text-xs p-2 rounded border border-rose-200 font-mono whitespace-pre-wrap"
            >
              {m.command}
            </pre>
          ))}
          <button
            type="button"
            onClick={copyAll}
            className="text-xs px-2 py-1 rounded border border-rose-300 text-rose-800 hover:bg-rose-100"
          >
            Copy all
          </button>
        </div>
      )}
    </Alert>
  );
}

export default function Schedule(): React.JSX.Element {
  const { data, loading, error } = useApiQuery<FlowsStateResponse>(
    endpoints.flowsState,
    { refetchInterval: POLL_MS },
  );
  const { data: missingData } = useApiQuery<MissingFlowsResponse>(
    endpoints.scheduleMissing,
    { refetchInterval: MISSING_POLL_MS },
  );
  const { mutate } = useApiMutation<TriggerResponse>();

  const [busyLabel, setBusyLabel] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Record<string, RowToast>>({});
  const [logs, setLogs] = useState<Map<string, LogState>>(new Map());

  // Clear each toast after TOAST_MS.
  useEffect(() => {
    const timers: number[] = [];
    for (const label of Object.keys(toasts)) {
      const id = window.setTimeout(() => {
        setToasts((prev) => {
          const next = { ...prev };
          delete next[label];
          return next;
        });
      }, TOAST_MS);
      timers.push(id);
    }
    return () => {
      for (const id of timers) window.clearTimeout(id);
    };
  }, [toasts]);

  const handleTrigger = async (
    label: string,
    force: boolean = false,
  ): Promise<void> => {
    setBusyLabel(label);
    const path =
      endpoints.scheduleTrigger(label) + (force ? "?force=true" : "");
    const result = await mutate(path);
    if (result && result.ok) {
      setToasts((prev) => ({
        ...prev,
        [label]: { status: "success", message: result.message },
      }));
    } else {
      const msg = result?.message ?? "Trigger failed";
      setToasts((prev) => ({
        ...prev,
        [label]: { status: "error", message: msg },
      }));
    }
    setBusyLabel(null);
  };

  const loadLog = useCallback(async (label: string): Promise<void> => {
    setLogs((prev) => {
      const next = new Map(prev);
      const existing = next.get(label);
      next.set(label, {
        open: true,
        loading: true,
        error: null,
        data: existing?.data ?? null,
      });
      return next;
    });
    try {
      const tail = await fetchLogTail(label);
      setLogs((prev) => {
        const next = new Map(prev);
        next.set(label, {
          open: true,
          loading: false,
          error: null,
          data: tail,
        });
        return next;
      });
    } catch (err) {
      setLogs((prev) => {
        const next = new Map(prev);
        const existing = next.get(label);
        next.set(label, {
          open: true,
          loading: false,
          error: getErrorMessage(err, "Failed to load log"),
          data: existing?.data ?? null,
        });
        return next;
      });
    }
  }, []);

  const handleToggleLog = (label: string): void => {
    const existing = logs.get(label);
    if (existing?.open) {
      setLogs((prev) => {
        const next = new Map(prev);
        next.set(label, { ...existing, open: false });
        return next;
      });
      return;
    }
    if (existing?.data) {
      setLogs((prev) => {
        const next = new Map(prev);
        next.set(label, { ...existing, open: true });
        return next;
      });
      return;
    }
    void loadLog(label);
  };

  const handleCloseLog = (label: string): void => {
    setLogs((prev) => {
      const next = new Map(prev);
      const existing = next.get(label);
      if (existing) next.set(label, { ...existing, open: false });
      return next;
    });
  };

  const handleRefreshLog = (label: string): void => {
    void loadLog(label);
  };

  // Fallback: until the openapi.json regen lands, the backend serializer
  // strips unknown fields. We cast through unknown so the data we get back
  // is treated as the extended ScheduleEntry shape (with optional log_path
  // + script_path).
  const sorted = useMemo<ScheduleEntry[]>(
    () => sortEntries((data?.schedule ?? []) as ScheduleEntry[]),
    [data?.schedule],
  );

  if (loading && !data) {
    return <LoadingState message="Loading schedule…" />;
  }

  if (error && !data) {
    return (
      <Alert status="error" title="Could not load schedule">
        {error}
      </Alert>
    );
  }

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-slate-900">Schedule</h1>
        <p className="text-sm text-slate-500">Cron / launchd job state.</p>
      </header>

      {missingData && <MissingBanner data={missingData} />}

      <Alert status="warning" className="mb-4">
        Manually triggering a job runs the actual script immediately. Watch
        the logs to confirm behavior.
      </Alert>

      {error && data && (
        <Alert status="warning" title="Polling error">
          {error}
        </Alert>
      )}

      {sorted.length > 0 && <SchedulePipelineView entries={sorted} />}

      {sorted.length === 0 ? (
        <p className="text-sm text-slate-500">No schedule entries reported.</p>
      ) : (
        <div className="bg-white rounded-lg border border-slate-200 shadow-sm overflow-x-auto">
          <table className="w-full text-left">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Flow
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Label
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Schedule
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Depends on
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Inputs
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Last fire
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Last exit
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold text-center">
                  Loaded
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Log
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Action
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((entry) => (
                <ScheduleRow
                  key={entry.label}
                  entry={entry}
                  busy={busyLabel === entry.label}
                  toast={toasts[entry.label] ?? null}
                  logState={logs.get(entry.label)}
                  onTrigger={handleTrigger}
                  onToggleLog={handleToggleLog}
                  onRefreshLog={handleRefreshLog}
                  onCloseLog={handleCloseLog}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
