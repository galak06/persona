/**
 * ComposeProgress — compact inline indicator for an in-flight compose dispatch.
 *
 * Shows a queued/running badge plus a live elapsed counter (1s tick), and an
 * amber warning line once the wait looks stuck: >10m while queued, or past the
 * dispatch timeout while running.
 */

import { useEffect, useState } from "react";

export interface ComposeProgressProps {
  status: "queued" | "running";
  /** ISO timestamp of entering the current state. */
  since: string;
  /** Dispatch timeout in seconds — the stuck threshold while running. */
  timeoutSeconds: number;
}

const QUEUED_STUCK_SECONDS = 600;

const BADGE_BASE =
  "inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border";

const BADGE_STYLES: Record<ComposeProgressProps["status"], string> = {
  queued:  "bg-amber-100 text-amber-800 border-amber-200",
  running: "bg-sky-100 text-sky-700 border-sky-200",
};

const BADGE_LABEL: Record<ComposeProgressProps["status"], string> = {
  queued:  "Queued",
  running: "Composing…",
};

const STUCK_MESSAGE: Record<ComposeProgressProps["status"], string> = {
  queued:  "still waiting for the worker — it may be stuck",
  running: "compose looks stuck — the worker may need attention",
};

/** Format an elapsed-seconds count as "45s", "12m", or "1h 05m". */
function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
}

export default function ComposeProgress({
  status,
  since,
  timeoutSeconds,
}: ComposeProgressProps): React.JSX.Element {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const sinceMs = Date.parse(since);
  const elapsed = Number.isNaN(sinceMs)
    ? null
    : Math.max(0, Math.round((now - sinceMs) / 1000));

  const stuckThreshold = status === "queued" ? QUEUED_STUCK_SECONDS : timeoutSeconds;
  const stuck = elapsed !== null && elapsed > stuckThreshold;

  const detail =
    elapsed === null
      ? null
      : status === "queued"
        ? `(waiting ${formatElapsed(elapsed)})`
        : `(${formatElapsed(elapsed)} elapsed)`;

  return (
    <div className="inline-flex flex-col gap-0.5">
      <span className="inline-flex items-center gap-2">
        <span className={`${BADGE_BASE} ${BADGE_STYLES[status]}`}>
          {BADGE_LABEL[status]}
        </span>
        {detail && <span className="text-xs text-slate-500">{detail}</span>}
      </span>
      {stuck && (
        <span className="text-xs text-amber-700">{STUCK_MESSAGE[status]}</span>
      )}
    </div>
  );
}
