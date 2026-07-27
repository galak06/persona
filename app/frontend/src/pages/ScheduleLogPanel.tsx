/**
 * Inline log-tail viewer rendered beneath each Schedule row. Owns no
 * fetch logic itself — the parent Schedule page passes the resolved
 * LogState plus refresh/close callbacks. Extracted from Schedule.tsx
 * to keep the page file under the 300-line cap.
 */

import type { WorkerStatus } from "../api/workers";
import type { LogTailResponse } from "../api/schedule";

export interface LogState {
  open: boolean;
  loading: boolean;
  error: string | null;
  data: LogTailResponse | null;
}

interface LogPanelProps {
  entry: WorkerStatus;
  state: LogState;
  onRefresh: (label: string) => void;
  onClose: (label: string) => void;
}

export default function LogPanel({
  entry,
  state,
  onRefresh,
  onClose,
}: LogPanelProps): React.JSX.Element {
  const { loading, error, data } = state;
  // WorkerStatus no longer carries a log_path field (the flows/state
  // pipeline model that exposed it was retired) — the tail response's own
  // `path` is the only source now.
  const path = data?.path ?? null;

  const copyPath = (): void => {
    if (path) void navigator.clipboard.writeText(path);
  };

  return (
    <div className="bg-slate-100 border-t border-slate-200 p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs uppercase tracking-wide text-slate-500 font-semibold">
          Log tail
        </span>
        {path && (
          <>
            <code className="text-xs font-mono text-slate-700 bg-white px-2 py-0.5 rounded border border-slate-200 break-all">
              {path}
            </code>
            <button
              type="button"
              onClick={copyPath}
              className="text-xs px-2 py-0.5 rounded border border-slate-300 text-slate-600 hover:bg-white"
            >
              Copy
            </button>
          </>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => onRefresh(entry.label)}
            disabled={loading}
            className="text-xs px-2 py-0.5 rounded border border-slate-300 text-slate-700 hover:bg-white disabled:opacity-50"
          >
            {loading ? "Loading…" : "Refresh"}
          </button>
          <button
            type="button"
            onClick={() => onClose(entry.label)}
            className="text-xs px-2 py-0.5 rounded border border-slate-300 text-slate-600 hover:bg-white"
          >
            Close
          </button>
        </div>
      </div>

      {error && <p className="text-xs text-rose-700">{error}</p>}

      {data && (
        <>
          {data.truncated && (
            <p className="text-xs text-slate-500">
              log truncated; showing last {data.lines.length} lines
            </p>
          )}
          <pre className="bg-slate-50 text-slate-700 text-xs p-3 rounded max-h-72 overflow-auto whitespace-pre font-mono">
            {data.lines.length ? data.lines.join("\n") : "(log is empty)"}
          </pre>
        </>
      )}

      {!data && !error && loading && (
        <p className="text-xs text-slate-500">Fetching log…</p>
      )}
    </div>
  );
}
