/**
 * Operations — unified ops cockpit:
 *   • Health   — live worker run status
 *   • Running  — workers currently executing, logs auto-open
 *   • Schedule — cron table, run-now triggers, log tails
 *   • Workers  — inspect and trigger automation workers
 *
 * Selected tab + worker are persisted in ?tab= and ?worker= so
 * a page refresh restores exactly the same view.
 */

import { useSearchParams } from "react-router-dom";

import Flows from "./Flows";
import Running from "./Running";
import Schedule from "./Schedule";
import FlowGuide from "./FlowGuide";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { WorkerStatus } from "../api/workers";

export type OpsView = "health" | "running" | "schedule" | "audit";

const VALID_VIEWS = new Set<OpsView>(["health", "running", "schedule", "audit"]);

const HINTS: Record<OpsView, string> = {
  health:   "Live run status of every worker.",
  running:  "Workers currently executing — logs stream live.",
  schedule: "Cron jobs, triggers & log tails.",
  audit:    "Inspect and trigger automation workers.",
};

const SEG_BASE =
  "relative px-4 py-1.5 rounded-md text-sm font-medium transition-colors duration-150";
const SEG_ACTIVE = "bg-white text-amber-900 shadow-sm";
const SEG_INACTIVE = "text-slate-500 hover:text-slate-800";

interface OperationsProps {
  initialView?: OpsView;
}

export default function Operations({
  initialView = "running",
}: OperationsProps): React.JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const tabParam = searchParams.get("tab") as OpsView | null;
  const view: OpsView =
    tabParam && VALID_VIEWS.has(tabParam) ? tabParam : initialView;

  // Lightweight poll to show the pulsing dot on the Running tab
  const { data: workers } = useApiQuery<WorkerStatus[]>(endpoints.workers, {
    refetchInterval: 5_000,
  });
  const runningCount = (workers ?? []).filter((w) => w.status === "running").length;

  function setView(next: OpsView) {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        p.set("tab", next);
        return p;
      },
      { replace: true },
    );
  }

  const tabs: { key: OpsView; label: string }[] = [
    { key: "health",   label: "Health" },
    { key: "running",  label: "Running" },
    { key: "schedule", label: "Schedule" },
    { key: "audit",    label: "Workers" },
  ];

  return (
    <section className="space-y-6">
      <header className="space-y-3">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold text-slate-900">Operations</h1>
          <p className="text-sm text-slate-500">{HINTS[view]}</p>
        </div>
        <div
          role="tablist"
          aria-label="Operations views"
          className="inline-flex rounded-lg border border-brand-border bg-stone-50 p-1"
        >
          {tabs.map(({ key, label }) => {
            const selected = key === view;
            return (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => setView(key)}
                className={`${SEG_BASE} ${selected ? SEG_ACTIVE : SEG_INACTIVE}`}
              >
                {label}
                {key === "running" && runningCount > 0 && (
                  <span className="ml-1.5 inline-flex items-center justify-center w-4 h-4 rounded-full bg-sky-500 text-white text-[10px] font-bold leading-none">
                    {runningCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </header>

      <div>
        {view === "health"   && <Flows />}
        {view === "running"  && <Running />}
        {view === "schedule" && <Schedule />}
        {view === "audit"    && <FlowGuide />}
      </div>
    </section>
  );
}
