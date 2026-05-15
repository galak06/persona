/**
 * FlowTabs — pill-style filter strip above the Inbox card list.
 *
 * Three tabs: All / Blog posts / Groups to join. Counts are passed in
 * from the parent so they re-derive on every refetch. Tabs with a zero
 * count still render (greyed) — operators want to see every flow even
 * when empty.
 *
 * Styling mirrors the top-bar nav: cyan-50 / cyan-700 active pill,
 * slate-500 inactive, slate-700 on hover.
 */

import { FLOW_LABELS, FLOWS, type FlowFilter } from "./shared";

const BASE_TAB =
  "px-4 py-1.5 rounded-full text-sm whitespace-nowrap transition-colors duration-150";
const ACTIVE_TAB = "bg-cyan-50 text-cyan-700 font-medium";
const INACTIVE_TAB = "text-slate-500 hover:text-slate-700";
const EMPTY_TAB = "text-slate-300 hover:text-slate-400";

export interface FlowTabsProps {
  active: FlowFilter;
  counts: Record<FlowFilter, number>;
  onChange: (flow: FlowFilter) => void;
}

export default function FlowTabs({
  active,
  counts,
  onChange,
}: FlowTabsProps): React.JSX.Element {
  return (
    <nav
      aria-label="Filter pending items by producer flow"
      className="overflow-x-auto -mx-1 px-1"
    >
      <ul className="flex items-center gap-1">
        {FLOWS.map((flow) => {
          const isActive = flow === active;
          const count = counts[flow];
          const isEmpty = count === 0 && !isActive;
          const stateClass = isActive
            ? ACTIVE_TAB
            : isEmpty
              ? EMPTY_TAB
              : INACTIVE_TAB;
          return (
            <li key={flow}>
              <button
                type="button"
                onClick={() => onChange(flow)}
                aria-pressed={isActive}
                className={`${BASE_TAB} ${stateClass}`}
              >
                {FLOW_LABELS[flow]}{" "}
                <span className="tabular-nums">({count})</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
