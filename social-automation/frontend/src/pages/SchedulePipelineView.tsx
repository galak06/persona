/**
 * Collapsible pipeline view shown above the Schedule table. Renders
 * each ordered task on one line with its order index, short label,
 * upstream dependency list, and a blocked indicator when prereqs
 * aren't satisfied. Decoupled from Schedule.tsx so the page file
 * stays under the 300-line cap.
 */

import { useState } from "react";

import type { ScheduleEntry } from "../types/openapi";

interface SchedulePipelineViewProps {
  entries: ScheduleEntry[];
}

export default function SchedulePipelineView({
  entries,
}: SchedulePipelineViewProps): React.JSX.Element {
  const [pipelineOpen, setPipelineOpen] = useState(false);
  const ordered = entries.filter((e) => e.order != null);

  return (
    <div className="bg-white rounded-lg border border-slate-200 shadow-sm">
      <button
        type="button"
        onClick={() => setPipelineOpen((v) => !v)}
        className="w-full px-4 py-2 text-left text-sm font-medium text-slate-700 hover:bg-slate-50 flex items-center justify-between"
      >
        <span>Pipeline view ({ordered.length} ordered)</span>
        <span className="text-slate-400">{pipelineOpen ? "▾" : "▸"}</span>
      </button>
      {pipelineOpen && (
        <div className="px-4 py-3 border-t border-slate-200 space-y-1 text-xs font-mono">
          {ordered.map((entry) => {
            const deps = entry.depends_on ?? [];
            return (
              <div
                key={entry.label}
                className="flex items-center gap-2 flex-wrap"
              >
                <span className="text-slate-400 tabular-nums w-8">
                  {entry.order}
                </span>
                <span className="text-slate-800">
                  {entry.label.replace("com.dogfoodandfun.", "")}
                </span>
                {deps.length > 0 && (
                  <>
                    <span className="text-slate-400">←</span>
                    <span className="text-slate-600">
                      {deps.map((d) => d.replace("dogfood-", "")).join(", ")}
                    </span>
                  </>
                )}
                {entry.inputs_satisfied === false && (
                  <span className="text-rose-600">⚠ blocked</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
