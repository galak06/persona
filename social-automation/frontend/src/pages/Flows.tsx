/**
 * Flows page — live health snapshot of every documented flow.
 *
 * Polls `GET /api/v1/flows/state` every 10s and renders one card per
 * flow with status pill, last-run timestamp, output counts, optional
 * error block, and a collapsible sample of the most recent outputs.
 */

import { useMemo, useState, useEffect } from "react";

import Alert from "../components/ui/Alert";
import LoadingState from "../components/ui/LoadingState";
import { endpoints } from "../api/endpoints";
import { useApiQuery } from "../hooks/useApiQuery";
import type { components } from "../types/openapi";

type FlowState = components["schemas"]["FlowState"];
type FlowsStateResponse = components["schemas"]["FlowsStateResponse"];

const POLL_MS = 10000;

const STATUS_STYLES: Record<FlowState["last_status"], string> = {
  ok: "bg-emerald-100 text-emerald-800 border-emerald-200",
  error: "bg-rose-100 text-rose-800 border-rose-200",
  never: "bg-slate-100 text-slate-700 border-slate-200",
  stale: "bg-amber-100 text-amber-800 border-amber-200",
  manual: "bg-sky-100 text-sky-800 border-sky-200",
};

const STATUS_LABEL: Record<FlowState["last_status"], string> = {
  ok: "OK",
  error: "Error",
  never: "Never run",
  stale: "Stale",
  manual: "Manual",
};

/** Format an ISO timestamp as a short relative phrase ("3s ago"). */
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

const SAMPLE_SUMMARY_KEYS = [
  "post_url",
  "keyword",
  "group_name",
  "suggested_title",
  "title",
  "name",
];

function sampleSummary(item: Record<string, unknown>): string {
  for (const key of SAMPLE_SUMMARY_KEYS) {
    const val = item[key];
    if (typeof val === "string" && val.trim()) return val;
  }
  // Fall back: first string value in the object.
  for (const val of Object.values(item)) {
    if (typeof val === "string" && val.trim()) return val;
  }
  return "(no summary field)";
}

interface RefreshedIndicatorProps {
  asOf: number;
}

function RefreshedIndicator({ asOf }: RefreshedIndicatorProps): React.JSX.Element {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.round((now - asOf) / 1000));
  return (
    <p className="text-xs text-slate-500">
      Refreshed {seconds}s ago · polled every {POLL_MS / 1000}s
    </p>
  );
}

interface FlowCardProps {
  flow: FlowState;
}

function FlowCard({ flow }: FlowCardProps): React.JSX.Element {
  const countEntries = Object.entries(flow.output_counts);
  const samples = flow.sample.slice(0, 3);
  const firstSummary = samples.length > 0 ? sampleSummary(samples[0]) : "";

  return (
    <article className="bg-white rounded-lg border border-slate-200 shadow-sm p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-slate-900 truncate">
            {flow.name}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">{flow.id}</p>
        </div>
        <span
          className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border ${STATUS_STYLES[flow.last_status]}`}
        >
          {STATUS_LABEL[flow.last_status]}
        </span>
      </div>

      <p className="text-sm text-slate-600">
        {flow.last_run_at ? (
          <>
            <span className="font-medium">Last run:</span>{" "}
            {formatRelativeTime(flow.last_run_at)}{" "}
            <span className="text-slate-400">
              · {formatAbsTime(flow.last_run_at)}
            </span>
          </>
        ) : (
          <span className="text-slate-500">Last run: Never</span>
        )}
      </p>

      {countEntries.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {countEntries.map(([label, value]) => (
            <span
              key={label}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-50 border border-slate-200 text-xs text-slate-700"
            >
              <span className="text-slate-500">{label}:</span>
              <span className="font-semibold tabular-nums">{value}</span>
            </span>
          ))}
        </div>
      )}

      {flow.last_status === "error" && flow.error_message && (
        <div className="bg-red-50 border border-red-200 text-red-900 text-sm rounded-md p-3">
          <p className="font-semibold mb-1">Last error</p>
          <pre className="text-xs whitespace-pre-wrap break-words font-mono">
            {flow.error_message.slice(0, 500)}
          </pre>
        </div>
      )}

      {samples.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-slate-600 hover:text-slate-900 select-none">
            Sample ({samples.length}):{" "}
            <span className="text-slate-500 truncate">{firstSummary}</span>
          </summary>
          <div className="mt-2 space-y-2">
            {samples.map((item, idx) => (
              <pre
                key={idx}
                className="text-xs bg-slate-50 border border-slate-200 rounded-md p-2 overflow-x-auto whitespace-pre-wrap break-words font-mono"
              >
                {JSON.stringify(item, null, 2)}
              </pre>
            ))}
          </div>
        </details>
      )}
    </article>
  );
}

export default function Flows(): React.JSX.Element {
  const { data, loading, error } = useApiQuery<FlowsStateResponse>(
    endpoints.flowsState,
    { refetchInterval: POLL_MS },
  );

  // eslint-disable-next-line react-hooks/exhaustive-deps, react-hooks/purity
  const asOf = useMemo(() => Date.now(), [data]);

  if (loading && !data) {
    return <LoadingState message="Loading flows…" />;
  }

  if (error && !data) {
    return (
      <Alert status="error" title="Could not load flows">
        {error}
      </Alert>
    );
  }

  const flows = data?.flows ?? [];

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-slate-900">Flows</h1>
        <p className="text-sm text-slate-500">
          Live state of every documented flow.
        </p>
        <RefreshedIndicator asOf={asOf} />
      </header>

      {error && data && (
        <Alert status="warning" title="Polling error">
          {error}
        </Alert>
      )}

      {flows.length === 0 ? (
        <p className="text-sm text-slate-500">No flows reported.</p>
      ) : (
        <div className="space-y-4">
          {flows.map((flow) => (
            <FlowCard key={flow.id} flow={flow} />
          ))}
        </div>
      )}
    </section>
    );
    }
