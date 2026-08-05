/**
 * Collapsible compact roster shown above the Schedule table: every
 * registered worker on one line with its label and current status.
 *
 * Used to show ordered pipeline steps with dependency arrows, sourced
 * from the old flows/state model (`order` / `depends_on` /
 * `inputs_satisfied`). That model was retired in favor of the flat
 * `/api/v1/workers` registry (see api/workers.ts), which carries no
 * ordering or dependency-graph data — so this view now just lists
 * workers by label with a status pill. Decoupled from Schedule.tsx so
 * the page file stays under the 300-line cap.
 */

import { useState } from "react";

import type { WorkerStatus } from "../api/workers";

const STATUS_DOT: Record<WorkerStatus["status"], string> = {
  never: "bg-slate-300",
  running: "bg-sky-500",
  success: "bg-emerald-500",
  error: "bg-rose-500",
};

interface SchedulePipelineViewProps {
  entries: WorkerStatus[];
}

export default function SchedulePipelineView({
  entries,
}: SchedulePipelineViewProps): React.JSX.Element {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const sorted = [...entries].sort((a, b) => a.label.localeCompare(b.label));

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm">
      <button
        type="button"
        onClick={() => setPipelineOpen((v) => !v)}
        className="w-full px-4 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-50 flex items-center justify-between"
      >
        <span>Worker roster ({sorted.length})</span>
        <span className="text-slate-400">{pipelineOpen ? "▾" : "▸"}</span>
      </button>
      {pipelineOpen && (
        <div className="px-4 py-3 border-t border-slate-200 space-y-1 text-xs font-mono">
          {sorted.map((entry) => (
            <div key={entry.label} className="flex items-center gap-2 flex-wrap">
              <span
                className={`inline-block w-2 h-2 rounded-full flex-shrink-0 ${STATUS_DOT[entry.status]}`}
                aria-hidden="true"
              />
              <span className="text-slate-800">
                {entry.label.replace("com.persona.", "")}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
