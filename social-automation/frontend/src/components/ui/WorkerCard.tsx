/**
 * WorkerCard — single-worker detail card used by the Worker Explorer page.
 */

import { useState, useEffect, useRef, useCallback } from "react";

import Spinner from "./Spinner";
import { useToast } from "./Toast";
import { endpoints } from "../../api/endpoints";
import apiClient, { getErrorMessage } from "../../api/client";
import type { WorkerStatus } from "../../api/workers";
import { fetchWorkerStatus } from "../../api/workers";

// ── Constants ────────────────────────────────────────────────────────────────

const PILL_BASE =
  "inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border";

export const PILL_VARIANT: Record<WorkerStatus["status"], string> = {
  never:   "bg-slate-100 text-slate-700 border-slate-200",
  running: "bg-sky-100 text-sky-700 border-sky-200",
  success: "bg-emerald-100 text-emerald-800 border-emerald-200",
  error:   "bg-rose-100 text-rose-800 border-rose-200",
};

const PILL_LABEL: Record<WorkerStatus["status"], string> = {
  never:   "Never Run",
  running: "Running",
  success: "Success",
  error:   "Error",
};

// ── Humanizer ─────────────────────────────────────────────────────────────────

export function humanizeRelative(iso: string | null | undefined): string {
  if (!iso) return "Never Run";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffSec = Math.round((then - Date.now()) / 1000);
  const fmt = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  const abs = Math.abs(diffSec);
  if (abs < 60) return fmt.format(diffSec, "second");
  if (abs < 3_600) return fmt.format(Math.round(diffSec / 60), "minute");
  if (abs < 86_400) return fmt.format(Math.round(diffSec / 3_600), "hour");
  if (abs < 86_400 * 30) return fmt.format(Math.round(diffSec / 86_400), "day");
  if (abs < 86_400 * 365) return fmt.format(Math.round(diffSec / (86_400 * 30)), "month");
  return fmt.format(Math.round(diffSec / (86_400 * 365)), "year");
}

// ── CollapsiblePanel (for artifact) ──────────────────────────────────────────

interface PanelProps {
  title: string;
  onLoad: () => Promise<string>;
  onClose: () => void;
  content: string | null;
  loading: boolean;
  error: string | null;
}

export function CollapsiblePanel({
  title, onLoad, onClose, content, loading, error,
}: PanelProps): React.JSX.Element {
  const open = content !== null || loading || error !== null;

  return (
    <div>
      <button
        type="button"
        onClick={() => open ? onClose() : void onLoad()}
        className="text-sm font-medium text-cyan-700 hover:text-cyan-900 hover:underline"
        aria-expanded={open}
      >
        {open ? `Hide ${title}` : `View ${title}`}
      </button>
      {open && (
        <div className="mt-2">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Spinner size="sm" className="text-slate-400" /> Loading…
            </div>
          )}
          {error && (
            <p className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-md p-2">
              {error}
            </p>
          )}
          {content && (
            <pre className="font-mono text-xs bg-slate-50 border border-slate-200 rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
              {content}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ── LogPanel — live-polling log viewer ────────────────────────────────────────

interface LogPanelProps {
  label: string;
  workerStatus: WorkerStatus["status"];
  defaultOpen?: boolean;
}

function LogPanel({ label, workerStatus, defaultOpen = false }: LogPanelProps): React.JSX.Element {
  const [open, setOpen] = useState(defaultOpen);
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  async function fetchLog() {
    try {
      const res = await apiClient.get<{ lines: string[]; truncated: boolean }>(
        endpoints.workerLog(label)
      );
      setLines(res.data.lines ?? []);
      setError(null);
    } catch (err) {
      setError(getErrorMessage(err, "Failed to load log"));
    } finally {
      setLoading(false);
    }
  }

  // Poll every 3s while open; faster (1s) while worker is running
  useEffect(() => {
    if (!open) return;
    setLoading(true);
    void fetchLog();
    const interval = workerStatus === "running" ? 1_000 : 3_000;
    const id = setInterval(() => void fetchLog(), interval);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, label, workerStatus]);

  // Auto-scroll to bottom when new lines arrive
  useEffect(() => {
    if (preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight;
    }
  }, [lines]);

  function toggle() {
    if (open) {
      setOpen(false);
      setLines([]);
      setError(null);
    } else {
      setOpen(true);
    }
  }

  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        className="text-sm font-medium text-cyan-700 hover:text-cyan-900 hover:underline"
        aria-expanded={open}
      >
        {open ? "Hide Log" : "View Log"}
      </button>

      {open && (
        <div className="mt-2">
          {loading && lines.length === 0 && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Spinner size="sm" className="text-slate-400" /> Loading…
            </div>
          )}
          {error && (
            <p className="text-sm text-rose-700 bg-rose-50 border border-rose-200 rounded-md p-2">
              {error}
            </p>
          )}
          {!error && (
            <div className="relative">
              {workerStatus === "running" && (
                <div className="absolute top-2 right-2 flex items-center gap-1 text-xs text-sky-600">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse" />
                  Live
                </div>
              )}
              <pre
                ref={preRef}
                className="font-mono text-xs bg-slate-50 border border-slate-200 rounded-md p-3 overflow-x-auto whitespace-pre-wrap break-words max-h-96 overflow-y-auto"
              >
                {lines.length > 0 ? lines.join("\n") : "(empty log)"}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── WorkerCard ────────────────────────────────────────────────────────────────

interface WorkerCardProps {
  worker: WorkerStatus;
  defaultLogOpen?: boolean;
}

interface TriggerResponse {
  ok: boolean;
  message: string;
  label: string;
  rate_limits: Record<string, { used: number; limit: number; remaining: number }> | null;
}

const TERMINAL_STATUSES: WorkerStatus["status"][] = ["success", "error", "never"];
const POLL_INTERVAL_MS = 3_000;
const POLL_MAX = 200; // 200 × 3s = 10 minutes

export default function WorkerCard({ worker, defaultLogOpen = false }: WorkerCardProps): React.JSX.Element {
  const { toast } = useToast();
  const [triggerState, setTriggerState] = useState<
    "idle" | "loading" | "triggered" | "error"
  >("idle");
  const [workerCount, setWorkerCount] = useState<1 | 2 | 3>(1);
  const [headless, setHeadless] = useState(false);

  // Polled status overrides the prop while we're actively watching the worker
  const [polledStatus, setPolledStatus] = useState<WorkerStatus | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollCountRef = useRef(0);

  const [artifactContent, setArtifactContent] = useState<string | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);

  // The effective status is the polled one (if available) or the prop
  const effectiveWorker: WorkerStatus = polledStatus ?? worker;

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    pollCountRef.current = 0;
    setIsPolling(false);
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    pollCountRef.current = 0;
    setIsPolling(true);

    const tick = async () => {
      pollCountRef.current += 1;
      try {
        const status = await fetchWorkerStatus(worker.label);
        setPolledStatus(status);
        if (TERMINAL_STATUSES.includes(status.status) || pollCountRef.current >= POLL_MAX) {
          stopPolling();
        }
      } catch {
        // network hiccup — keep polling until max
        if (pollCountRef.current >= POLL_MAX) stopPolling();
      }
    };

    pollRef.current = setInterval(() => void tick(), POLL_INTERVAL_MS);
  }, [worker.label, stopPolling]);

  // Cleanup on unmount
  useEffect(() => () => stopPolling(), [stopPolling]);

  const today = new Date().toISOString().slice(0, 10);
  const ranToday =
    effectiveWorker.status === "success" && (effectiveWorker.last_run ?? "").startsWith(today);

  async function handleTrigger(force = false) {
    // Short-circuit: if already ran today and guard is active and not forcing, show message
    const guardActive = (worker.re_run_guard ?? 1) !== 0;
    if (!force && ranToday && guardActive) {
      const when = worker.last_run ? worker.last_run.slice(0, 16).replace("T", " ") : "today";
      toast.warning(
        `${worker.title} — already ran today`,
        `Last success: ${when}. Use ⚡ Force to run again.`,
        6_000,
      );
      return;
    }

    setTriggerState("loading");
    try {
      const res = await apiClient.post<TriggerResponse>(
        endpoints.workerTrigger(worker.label),
        {
          count: workerCount,
          force,
          ...(headless ? { headless: true } : {}),
        },
      );
      setTriggerState("triggered");
      setTimeout(() => setTriggerState("idle"), 2_000);

      // Start polling status so the card reflects running → terminal state
      startPolling();

      const { message, rate_limits } = res.data;
      const countLabel = workerCount > 1 ? ` ×${workerCount}` : "";

      if (rate_limits && Object.keys(rate_limits).length > 0) {
        const capped = Object.entries(rate_limits)
          .map(([k, v]) => `${k} ${v.used}/${v.limit}`)
          .join(" · ");
        toast.warning(
          `${worker.title}${countLabel} triggered`,
          `Daily limits hit — worker will exit early: ${capped}`,
          6_000,
        );
      } else {
        toast.success(
          `${worker.title}${countLabel} started`,
          message,
        );
      }
    } catch (err) {
      setTriggerState("error");
      setTimeout(() => setTriggerState("idle"), 3_000);
      import("axios").then(({ isAxiosError }) => {
        let msg = getErrorMessage(err, "Trigger failed");
        if (isAxiosError(err) && err.response?.status === 409) {
          const detail = (err.response?.data as { detail?: string })?.detail ?? "";
          msg = detail || "Already running or ran today";
        }
        toast.error(`${worker.title} — trigger failed`, msg, 6_000);
      }).catch(() => {
        toast.error(`${worker.title} — trigger failed`, getErrorMessage(err, ""), 5_000);
      });
    }
  }

  async function loadArtifact(): Promise<string> {
    setArtifactLoading(true);
    setArtifactError(null);
    try {
      const res = await apiClient.get<unknown>(endpoints.workerArtifact(worker.label));
      const text = JSON.stringify(res.data, null, 2);
      setArtifactContent(text);
      return text;
    } catch (err) {
      const msg = getErrorMessage(err, "Failed to load artifact");
      setArtifactError(msg);
      return "";
    } finally {
      setArtifactLoading(false);
    }
  }

  const isBusy = triggerState === "loading";
  const isRunning = effectiveWorker.status === "running" || isPolling;
  const isDisabled = isBusy || isRunning;

  // While polling but status not yet "running", show an amber "Running..." badge
  const displayStatus: WorkerStatus["status"] =
    isPolling && effectiveWorker.status !== "running"
      ? "running"
      : effectiveWorker.status;

  return (
    <article className="bg-white rounded-md border border-slate-200 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900 truncate">{effectiveWorker.title}</h2>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">
            com.dogfoodandfun.{effectiveWorker.label.replace(/^dogfood-/, "")}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0 flex-wrap justify-end">
          <span className={`${PILL_BASE} ${PILL_VARIANT[displayStatus]}`}>
            {isPolling && displayStatus === "running" && (
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-sky-500 animate-pulse mr-1.5" />
            )}
            {PILL_LABEL[displayStatus]}
          </span>

          {/* Worker count selector */}
          <div className={`inline-flex rounded-md border border-slate-200 overflow-hidden text-xs font-medium ${isDisabled ? "opacity-50 pointer-events-none" : ""}`}>
            {([1, 2, 3] as const).map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setWorkerCount(n)}
                disabled={isDisabled}
                className={`px-2.5 py-1.5 transition-colors ${
                  workerCount === n
                    ? "bg-slate-800 text-white"
                    : "bg-white text-slate-500 hover:bg-slate-50"
                }`}
              >
                ×{n}
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={() => void handleTrigger()}
            disabled={isDisabled}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-amber-50 text-amber-800 border border-amber-200 hover:bg-amber-100 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
            {isBusy ? (
              <><Spinner size="sm" className="text-amber-700" /> Triggering…</>
            ) : isRunning ? (
              <><Spinner size="sm" className="text-amber-700" /> Running…</>
            ) : triggerState === "triggered" ? "Triggered!" : "Trigger ▶"}
          </button>

          <button
            type="button"
            onClick={() => void handleTrigger(true)}
            disabled={isDisabled}
            title="Force run — bypass the 'already ran today' guard"
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium bg-white text-slate-500 border border-slate-200 hover:bg-slate-50 hover:text-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            ⚡ Force
          </button>

          <label className="inline-flex items-center gap-1.5 text-xs text-slate-500 select-none cursor-pointer">
            <input
              type="checkbox"
              checked={headless}
              onChange={(e) => setHeadless(e.target.checked)}
              disabled={isDisabled}
              className="rounded border-slate-300 text-slate-700 focus:ring-slate-400 disabled:opacity-40"
            />
            Headless
          </label>
        </div>
      </div>

      <p className="text-sm text-slate-600">
        <span className="font-medium">Last run:</span>{" "}
        {humanizeRelative(effectiveWorker.last_run)}
      </p>

      <p className="text-sm text-slate-700">{effectiveWorker.description}</p>

      {effectiveWorker.status === "error" && effectiveWorker.message && (
        <div className="bg-rose-50 border border-rose-200 text-rose-900 text-sm rounded-md p-3">
          <p className="font-semibold mb-1">Last error</p>
          <pre className="text-xs whitespace-pre-wrap break-words font-mono">{effectiveWorker.message}</pre>
        </div>
      )}

      <div className="flex gap-4 flex-wrap">
        <LogPanel label={effectiveWorker.label} workerStatus={displayStatus} defaultOpen={defaultLogOpen} />
        <CollapsiblePanel
          title="Artifact"
          onLoad={loadArtifact}
          onClose={() => { setArtifactContent(null); setArtifactError(null); }}
          content={artifactContent}
          loading={artifactLoading}
          error={artifactError}
        />
      </div>
    </article>
  );
}
