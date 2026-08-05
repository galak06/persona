/**
 * Schedule page — read-only worker registry (status, last run, log tail)
 * plus per-row cron editing. Polls `GET /api/v1/workers` every 10s for
 * fresh rows; posts to `PATCH /api/v1/workers/{label}/schedule` when a
 * cron edit is saved.
 *
 * Triggering a flow on demand lives exclusively on the Human Mimic page
 * (`POST /brands/{brand_id}/flows/{flow_id}/run`) — the brand-scoped
 * path that goes through the Redis flow-run queue and the `worker`
 * container, and respects each task's `timeout_minutes`. This page used
 * to carry its own duplicate "Run now" button against the older,
 * pre-Docker `POST /api/v1/workers/{label}/trigger` endpoint (forks a
 * subprocess directly from the API container, single-brand only) —
 * removed so there's exactly one place to trigger a flow. Editing the
 * cron here only changes when the dispatcher's due-check next fires it.
 *
 * Rewritten against the flat `/api/v1/workers` registry after the older
 * flows/state pipeline model (per-flow dependency graph, input/output
 * file checks, cron-schedule text, launchctl-loaded flag) was retired
 * without this page being migrated alongside it — see api/workers.ts
 * for the current `WorkerStatus` shape and Flows.tsx/FlowGuide.tsx for
 * the pages that already made this transition.
 */

import { useCallback, useMemo, useState } from "react";

import Alert from "../components/ui/Alert";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";
import apiClient, { getErrorMessage } from "../api/client";
import { endpoints } from "../api/endpoints";
import { fetchLogTail } from "../api/schedule";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";
import LogPanel, { type LogState } from "./ScheduleLogPanel";
import SchedulePipelineView from "./SchedulePipelineView";

const POLL_MS = 10000;
const TABLE_COL_COUNT = 5;

const STATUS_STYLES: Record<WorkerStatus["status"], string> = {
  never: "text-slate-400",
  running: "text-sky-700 font-semibold",
  success: "text-emerald-700 font-semibold",
  error: "text-rose-700 font-semibold",
};

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

function sortEntries(entries: WorkerStatus[]): WorkerStatus[] {
  return [...entries].sort((a, b) => a.label.localeCompare(b.label));
}

interface ScheduleEditState {
  label: string;
  draftCron: string;
  saving: boolean;
  error: string | null;
}

interface ScheduleRowProps {
  entry: WorkerStatus;
  logState: LogState | undefined;
  edit: ScheduleEditState | null;
  onStartEdit: (label: string, currentCron: string | null | undefined) => void;
  onDraftChange: (value: string) => void;
  onSaveEdit: (label: string) => void;
  onCancelEdit: () => void;
  onToggleLog: (label: string) => void;
  onRefreshLog: (label: string) => void;
  onCloseLog: (label: string) => void;
}

function ScheduleRow({
  entry,
  logState,
  edit,
  onStartEdit,
  onDraftChange,
  onSaveEdit,
  onCancelEdit,
  onToggleLog,
  onRefreshLog,
  onCloseLog,
}: ScheduleRowProps): React.JSX.Element {
  const open = !!logState?.open;
  const editing = edit !== null;

  return (
    <>
      <tr className="border-b border-slate-100">
        <td className="px-3 py-2 text-sm text-slate-700">{entry.title}</td>
        <td className={`px-3 py-2 text-sm ${STATUS_STYLES[entry.status]}`}>
          {entry.status}
        </td>
        <td className="px-3 py-2 text-sm text-slate-600">
          {entry.last_run ? (
            <span title={formatAbsTime(entry.last_run)}>
              {formatRelativeTime(entry.last_run)}
            </span>
          ) : (
            <span className="text-slate-400">—</span>
          )}
        </td>
        <td className="px-3 py-2 text-sm">
          {editing ? (
            <div className="flex items-center gap-2 flex-wrap">
              <input
                type="text"
                value={edit.draftCron}
                onChange={(e) => onDraftChange(e.target.value)}
                placeholder="e.g. 3 19 * * *"
                className="w-32 px-2 py-1 text-xs font-mono border border-slate-300 rounded"
                disabled={edit.saving}
              />
              <button
                type="button"
                onClick={() => onSaveEdit(entry.label)}
                disabled={edit.saving || !edit.draftCron.trim()}
                className="text-xs px-2 py-1 rounded bg-cyan-600 text-white hover:bg-cyan-700 disabled:bg-slate-300"
              >
                {edit.saving ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                onClick={onCancelEdit}
                disabled={edit.saving}
                className="text-xs px-2 py-1 rounded border border-slate-300 text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
              {edit.error && (
                <span className="text-xs text-rose-700 basis-full">{edit.error}</span>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-slate-700">
                {entry.cron ?? <span className="text-slate-400">—</span>}
              </span>
              <button
                type="button"
                onClick={() => onStartEdit(entry.label, entry.cron)}
                className="text-xs px-2 py-1 rounded border border-slate-300 text-slate-700 hover:bg-slate-50"
              >
                Edit
              </button>
            </div>
          )}
        </td>
        <td className="px-3 py-2 text-sm">
          <button
            type="button"
            onClick={() => onToggleLog(entry.label)}
            className="text-xs px-2 py-1 rounded border border-slate-300 text-slate-700 hover:bg-slate-50"
          >
            {open ? "Hide log" : "View log"}
          </button>
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

export default function Schedule(): React.JSX.Element {
  const { data, loading, error, refetch } = useApiQuery<WorkerStatus[]>(
    endpoints.workers,
    { refetchInterval: POLL_MS },
  );
  const [edit, setEdit] = useState<ScheduleEditState | null>(null);
  const [logs, setLogs] = useState<Map<string, LogState>>(new Map());

  const handleStartEdit = (label: string, currentCron: string | null | undefined): void => {
    setEdit({ label, draftCron: currentCron ?? "", saving: false, error: null });
  };

  const handleDraftChange = (value: string): void => {
    setEdit((prev) => (prev ? { ...prev, draftCron: value } : prev));
  };

  const handleCancelEdit = (): void => {
    setEdit(null);
  };

  const handleSaveEdit = async (label: string): Promise<void> => {
    const cron = (edit?.draftCron ?? "").trim();
    setEdit((prev) => (prev ? { ...prev, saving: true, error: null } : prev));
    try {
      await apiClient.patch(endpoints.workerSchedule(label), { cron });
      setEdit(null);
      void refetch();
    } catch (err) {
      setEdit((prev) =>
        prev ? { ...prev, saving: false, error: getErrorMessage(err, "Save failed") } : prev,
      );
    }
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

  const sorted = useMemo<WorkerStatus[]>(
    () => sortEntries(data ?? []),
    [data],
  );

  if (loading && !data) {
    return <LoadingState message="Loading schedule…" />;
  }

  if (error && !data) {
    return (
      <ErrorState
        title="Could not load schedule"
        message={error}
        onRetry={() => void refetch()}
        retrying={loading}
      />
    );
  }

  return (
    <section className="space-y-6">
      <p className="text-sm text-slate-500">Registered worker roster.</p>

      <Alert status="warning" className="mb-4">
        Editing a schedule only changes when this flow is next due to run —
        it does not run anything immediately. To run a flow right now, use
        Human Mimic.
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
                  Title
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Status
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Last run
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Schedule
                </th>
                <th className="px-3 py-2 text-xs uppercase tracking-wide text-slate-500 font-semibold">
                  Log
                </th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((entry) => (
                <ScheduleRow
                  key={entry.label}
                  entry={entry}
                  logState={logs.get(entry.label)}
                  edit={edit?.label === entry.label ? edit : null}
                  onStartEdit={handleStartEdit}
                  onDraftChange={handleDraftChange}
                  onSaveEdit={(label) => void handleSaveEdit(label)}
                  onCancelEdit={handleCancelEdit}
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
