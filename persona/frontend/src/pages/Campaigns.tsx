/**
 * Campaigns page — one card per campaign folder + a "Publish now" trigger.
 * Polls `GET /api/v1/campaigns` every 10s. Sort: error → never → last_run
 * desc. Publish button posts to `/campaigns/{name}/publish` (fire-and-
 * forget) and auto-disables for 3s to prevent double-trigger.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import Alert from "../components/ui/Alert";
import LoadingState from "../components/ui/LoadingState";
import { getErrorMessage } from "../api/client";
import { useApiQuery } from "../hooks/useApiQuery";
import {
  triggerPublish,
  type CampaignListResponse,
  type CampaignStatus,
  type CampaignSummary,
} from "../api/campaigns";

const POLL_MS = 10000;
const TRIGGER_COOLDOWN_MS = 3000;
const TOAST_DURATION_MS = 4000;

const STATUS_STYLES: Record<CampaignStatus, string> = {
  success: "bg-emerald-100 text-emerald-800 border-emerald-200",
  error: "bg-rose-100 text-rose-800 border-rose-200",
  never: "bg-slate-100 text-slate-700 border-slate-200",
};

const STATUS_LABEL: Record<CampaignStatus, string> = {
  success: "Success",
  error: "Error",
  never: "Never run",
};

/** Sort priority for last_status — lower number sorts earlier. */
const STATUS_SORT_RANK: Record<CampaignStatus, number> = {
  error: 0,
  never: 1,
  success: 2,
};

/** Humanize an ISO timestamp into "3m ago" / "2h ago". */
function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return "Never";
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

type PillTuple = readonly [label: string, value: string, numeric: boolean];

function pillsForCampaign(c: CampaignSummary): PillTuple[] {
  return [
    ["ready", String(c.ready_count), true],
    ["published", String(c.published_count), true],
    ["task #", String(c.current_task_index), true],
    ["prepare", c.has_prepare_tasks ? "yes" : "no", false],
    ["publish", c.has_publish_tasks ? "yes" : "no", false],
  ];
}

function sortCampaigns(items: CampaignSummary[]): CampaignSummary[] {
  return [...items].sort((a, b) => {
    const ra = STATUS_SORT_RANK[a.last_status];
    const rb = STATUS_SORT_RANK[b.last_status];
    if (ra !== rb) return ra - rb;
    const ta = a.last_run ? new Date(a.last_run).getTime() : 0;
    const tb = b.last_run ? new Date(b.last_run).getTime() : 0;
    if (ta !== tb) return tb - ta;
    return a.name.localeCompare(b.name);
  });
}

function RefreshedIndicator({
  asOf,
}: {
  asOf: number;
}): React.JSX.Element {
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

interface Toast {
  id: number;
  kind: "success" | "error";
  message: string;
}

function CampaignCard(props: {
  campaign: CampaignSummary;
  onTrigger: (name: string) => void;
  pending: boolean;
}): React.JSX.Element {
  const { campaign, onTrigger, pending } = props;
  return (
    <article className="bg-white rounded-lg border border-slate-200 shadow-sm p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-slate-900 truncate">
            {campaign.name}
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            <span className="font-medium text-slate-600">Last run:</span>{" "}
            {formatRelativeTime(campaign.last_run)}
            {campaign.last_run && (
              <span className="text-slate-400">
                {" "}
                · {formatAbsTime(campaign.last_run)}
              </span>
            )}
          </p>
        </div>
        <span
          className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border ${STATUS_STYLES[campaign.last_status]}`}
        >
          {STATUS_LABEL[campaign.last_status]}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        {pillsForCampaign(campaign).map(([label, value, numeric]) => (
          <span
            key={label}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-slate-50 border border-slate-200 text-xs text-slate-700"
          >
            <span className="text-slate-500">{label}:</span>
            <span className={`font-semibold ${numeric ? "tabular-nums" : ""}`}>
              {value}
            </span>
          </span>
        ))}
      </div>

      <div className="flex items-center justify-end">
        <button
          type="button"
          onClick={() => onTrigger(campaign.name)}
          disabled={pending || !campaign.has_publish_tasks}
          className="px-3 py-1.5 rounded-md text-sm font-medium bg-cyan-600 text-white shadow-sm hover:bg-cyan-700 focus:outline-none focus:ring-2 focus:ring-cyan-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          title={
            campaign.has_publish_tasks
              ? "Spawn scripts.publish_campaign now"
              : "No publish_tasks defined in campaign_config.json"
          }
        >
          {pending ? "Triggered…" : "Publish now"}
        </button>
      </div>
    </article>
  );
}

const TOAST_TONE: Record<Toast["kind"], string> = {
  success: "bg-emerald-50 border-emerald-200 text-emerald-900",
  error: "bg-rose-50 border-rose-200 text-rose-900",
};

function ToastView({ toast }: { toast: Toast }): React.JSX.Element {
  return (
    <div
      className={`pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-md ${TOAST_TONE[toast.kind]}`}
      role="status"
    >{toast.message}</div>
  );
}

export function Campaigns(): React.JSX.Element {
  const { data, loading, error } = useApiQuery<CampaignListResponse>(
    "/campaigns",
    { refetchInterval: POLL_MS },
  );

  // eslint-disable-next-line react-hooks/exhaustive-deps, react-hooks/purity
  const asOf = useMemo(() => Date.now(), [data]);

  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [toasts, setToasts] = useState<Toast[]>([]);

  const pushToast = useCallback((toast: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { ...toast, id }]);
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, TOAST_DURATION_MS);
  }, []);

  const handleTrigger = useCallback(
    async (name: string) => {
      setPending((p) => ({ ...p, [name]: true }));
      try {
        const resp = await triggerPublish(name);
        pushToast({
          kind: resp.ok ? "success" : "error",
          message: resp.ok
            ? `Triggered ${name} (${resp.message})`
            : `Failed: ${resp.message}`,
        });
      } catch (err) {
        pushToast({ kind: "error", message: getErrorMessage(err) });
      } finally {
        window.setTimeout(() => {
          setPending((p) => {
            const next = { ...p };
            delete next[name];
            return next;
          });
        }, TRIGGER_COOLDOWN_MS);
      }
    },
    [pushToast],
  );

  if (loading && !data) {
    return <LoadingState message="Loading campaigns…" />;
  }

  if (error && !data) {
    return (
      <Alert status="error" title="Could not load campaigns">
        {error}
      </Alert>
    );
  }

  const campaigns = sortCampaigns(data?.campaigns ?? []);

  return (
    <section className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-slate-900">Campaigns</h1>
        <p className="text-sm text-slate-500">
          Multi-stage prepare/publish runs. Click "Publish now" to spawn
          the publisher; status updates show up in the next poll.
        </p>
        <RefreshedIndicator asOf={asOf} />
      </header>

      {error && data && (
        <Alert status="warning" title="Polling error">
          {error}
        </Alert>
      )}

      {campaigns.length === 0 ? (
        <p className="text-sm text-slate-500">
          No campaigns found. Add a <code>campaign_config.json</code> under
          your brand's <code>campaigns/</code> folder.
        </p>
      ) : (
        <div className="space-y-4">
          {campaigns.map((campaign) => (
            <CampaignCard
              key={campaign.name}
              campaign={campaign}
              onTrigger={handleTrigger}
              pending={!!pending[campaign.name]}
            />
          ))}
        </div>
      )}

      {toasts.length > 0 && (
        <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
          {toasts.map((t) => (
            <ToastView key={t.id} toast={t} />
          ))}
        </div>
      )}
    </section>
  );
}

export default Campaigns;
