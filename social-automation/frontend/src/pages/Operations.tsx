/**
 * Operations — unified ops cockpit merging three former tabs into one
 * segmented page:
 *   • Health   — live worker run status, polls /workers
 *   • Schedule — cron table, run-now triggers, log tails
 *   • Workers  — inspect and trigger automation workers
 *
 * Only the active segment is mounted, so at most one poller runs at a
 * time. The old /flows, /schedule and /flow-guide routes still resolve
 * here (via `initialView`) so existing deep links keep working.
 */

import { useSearchParams } from "react-router-dom";

import Flows from "./Flows";
import Schedule from "./Schedule";
import FlowGuide from "./FlowGuide";

export type OpsView = "health" | "schedule" | "audit";

interface Segment {
  key: OpsView;
  label: string;
  hint: string;
}

const SEGMENTS: readonly Segment[] = [
  { key: "health", label: "Health", hint: "Live run status of every flow." },
  { key: "schedule", label: "Schedule", hint: "Cron jobs, triggers & log tails." },
  { key: "audit", label: "Workers", hint: "Inspect and trigger automation workers." },
];

const VALID_VIEWS = new Set<OpsView>(["health", "schedule", "audit"]);

const SEG_BASE =
  "px-4 py-1.5 rounded-md text-sm font-medium transition-colors duration-150";
const SEG_ACTIVE = "bg-white text-amber-900 shadow-sm";
const SEG_INACTIVE = "text-slate-500 hover:text-slate-800";

interface OperationsProps {
  initialView?: OpsView;
}

export default function Operations({
  initialView = "health",
}: OperationsProps): React.JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") as OpsView | null;
  const view: OpsView = (tabParam && VALID_VIEWS.has(tabParam)) ? tabParam : initialView;

  function setView(next: OpsView) {
    setSearchParams((prev) => {
      const p = new URLSearchParams(prev);
      p.set("tab", next);
      return p;
    }, { replace: true });
  }

  const active = SEGMENTS.find((s) => s.key === view) ?? SEGMENTS[0];

  return (
    <section className="space-y-6">
      <header className="space-y-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold text-slate-900">Operations</h1>
          <p className="text-sm text-slate-500">{active.hint}</p>
        </div>
        <div
          role="tablist"
          aria-label="Operations views"
          className="inline-flex rounded-lg border border-brand-border bg-stone-50 p-1"
        >
          {SEGMENTS.map((s) => {
            const selected = s.key === view;
            return (
              <button
                key={s.key}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setView(s.key)}
                className={`${SEG_BASE} ${selected ? SEG_ACTIVE : SEG_INACTIVE}`}
              >
                {s.label}
              </button>
            );
          })}
        </div>
      </header>

      <div>
        {view === "health" && <Flows />}
        {view === "schedule" && <Schedule />}
        {view === "audit" && <FlowGuide />}
      </div>
    </section>
  );
}
