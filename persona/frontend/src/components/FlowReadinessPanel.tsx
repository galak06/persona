import { useState } from "react";

import { endpoints } from "../api/endpoints";
import type {
  FlowStatus,
  FlowStatusResponse,
  RunNowRequestBody,
  RunNowResponse,
} from "../api/brands";
import type { WorkerStatus } from "../api/workers";
import { useApiQuery } from "../hooks/useApiQuery";
import { useApiMutation } from "../hooks/useApiMutation";
import { useToast } from "./ui/Toast";
import Alert from "./ui/Alert";
import ErrorState from "./ui/ErrorState";
import LoadingState from "./ui/LoadingState";
import { LogPanel } from "./ui/WorkerCard";

const _KNOWN_STATUSES: readonly WorkerStatus["status"][] = [
  "never",
  "running",
  "success",
  "error",
];

function toWorkerStatus(status: string | undefined): WorkerStatus["status"] {
  return (_KNOWN_STATUSES as readonly string[]).includes(status ?? "")
    ? (status as WorkerStatus["status"])
    : "never";
}

/**
 * Flow-readiness panel — one card per managed flow (`ig-scanner`/
 * `fb-scanner`/`fb-group-scout`), each showing enabled state, last-run
 * status, and a flow-specific readiness signal (joined-group count for the
 * Facebook flows, hashtag count for ig-scanner) with a "Run Now" button.
 *
 * Directly answers the "how does the operator know fb-group-scout needs to
 * run first" question — a brand with 0 joined groups now shows a visible
 * warning instead of fb-scanner just silently finding nothing.
 */

interface FlowReadinessPanelProps {
  brandId: string;
}

function statusBadgeClasses(status: string | undefined): string {
  if (status === "success") return "bg-emerald-50 text-emerald-700";
  if (status === "error") return "bg-rose-50 text-rose-700";
  if (status === "running") return "bg-amber-50 text-amber-700";
  return "bg-stone-100 text-slate-500";
}

function RunNowButton({
  brandId,
  flowId,
  disabled,
  onDone,
}: {
  brandId: string;
  flowId: string;
  disabled: boolean;
  onDone: () => void;
}): React.JSX.Element {
  const { toast } = useToast();
  const { mutate, loading } = useApiMutation<RunNowResponse, RunNowRequestBody>("post");
  const [visible, setVisible] = useState(false);

  const handleClick = async () => {
    const result = await mutate(
      endpoints.brandFlowRun(brandId, flowId),
      visible ? { headless: false } : undefined,
    );
    if (result) {
      toast.success(`Queued ${flowId}`, "Picked up by the worker within seconds.");
      onDone();
    } else {
      toast.error(`Could not queue ${flowId}`);
    }
  };

  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={() => void handleClick()}
        disabled={disabled || loading}
        title={disabled ? "Enable this flow in settings first" : undefined}
        className="rounded-lg border border-stone-200 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-stone-50 disabled:opacity-50"
      >
        {loading ? "Queuing…" : "Run now"}
      </button>
      <label className="inline-flex items-center gap-1.5 text-xs text-slate-500 select-none cursor-pointer">
        <input
          type="checkbox"
          checked={visible}
          onChange={(e) => setVisible(e.target.checked)}
          disabled={disabled || loading}
          className="rounded border-stone-300 text-slate-700 focus:ring-slate-400 disabled:opacity-40"
        />
        <span title="Only works where the worker process has a display (e.g. local dev) — a plain Docker container has none.">
          Show browser
        </span>
      </label>
    </div>
  );
}

function FlowCard({
  flow,
  brandId,
  onChanged,
}: {
  flow: FlowStatus;
  brandId: string;
  onChanged: () => void;
}): React.JSX.Element {
  return (
    <div className="rounded-lg border border-stone-200 bg-white p-4 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-800">{flow.flow_id}</p>
          <p className="text-xs font-mono text-slate-400 truncate">{flow.script}</p>
        </div>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${
            flow.enabled ? "bg-emerald-50 text-emerald-700" : "bg-stone-100 text-slate-400"
          }`}
        >
          {flow.enabled ? "enabled" : "disabled"}
        </span>
      </div>

      <div className="flex items-center gap-2 text-xs text-slate-500">
        <span
          className={`rounded px-1.5 py-0.5 font-medium ${statusBadgeClasses(flow.last_run?.status)}`}
        >
          {flow.last_run ? flow.last_run.status : "never run"}
        </span>
        {flow.last_run && <span>{flow.last_run.last_run.slice(0, 19).replace("T", " ")}</span>}
      </div>

      {flow.last_run && (
        <LogPanel
          label={`${brandId}-${flow.flow_id}`}
          workerStatus={toWorkerStatus(flow.last_run.status)}
        />
      )}

      {!flow.readiness.ready && (
        <Alert status="warning" className="text-xs">
          {flow.readiness.hint}
        </Alert>
      )}

      <RunNowButton
        brandId={brandId}
        flowId={flow.flow_id}
        disabled={!flow.enabled}
        onDone={onChanged}
      />
    </div>
  );
}

export default function FlowReadinessPanel({
  brandId,
}: FlowReadinessPanelProps): React.JSX.Element {
  const { data, loading, error, refetch } = useApiQuery<FlowStatusResponse>(
    endpoints.brandFlows(brandId),
  );

  return (
    <section>
      <h2 className="font-display text-lg font-semibold text-slate-800 mb-1">Flow status</h2>
      <p className="text-sm text-slate-500 mb-3">
        Last run, and whether each flow has anything to do yet.
      </p>

      {loading && !data && <LoadingState message="Loading flow status…" />}
      {error && (
        <ErrorState
          message={`Could not load flow status: ${error}`}
          onRetry={() => void refetch()}
          retrying={loading}
        />
      )}

      {data && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {data.flows.map((flow) => (
            <FlowCard
              key={flow.flow_id}
              flow={flow}
              brandId={brandId}
              onChanged={() => void refetch()}
            />
          ))}
        </div>
      )}
    </section>
  );
}
