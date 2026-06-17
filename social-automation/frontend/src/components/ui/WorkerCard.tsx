/**
 * WorkerCard — single-worker detail card used by the Worker Explorer page.
 */

import { useState, useEffect, useRef } from "react";

import Spinner from "./Spinner";
import { endpoints } from "../../api/endpoints";
import apiClient, { getErrorMessage } from "../../api/client";
import type { WorkerStatus } from "../../api/workers";

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

export default function WorkerCard({ worker, defaultLogOpen = false }: WorkerCardProps): React.JSX.Element {
  const [triggerState, setTriggerState] = useState<
    "idle" | "loading" | "triggered" | "error"
  >("idle");
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [workerCount, setWorkerCount] = useState<1 | 2 | 3>(1);

  const [artifactContent, setArtifactContent] = useState<string | null>(null);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [artifactError, setArtifactError] = useState<string | null>(null);

  async function handleTrigger() {
    setTriggerState("loading");
    setTriggerError(null);
    try {
      await apiClient.post(endpoints.workerTrigger(worker.label), { count: workerCount });
      setTriggerState("triggered");
      setTimeout(() => setTriggerState("idle"), 2_000);
    } catch (err) {
      import("axios").then(({ isAxiosError }) => {
        const msg =
          isAxiosError(err) && err.response?.status === 409
            ? "Already running"
            : getErrorMessage(err, "Trigger failed");
        setTriggerError(msg);
      }).catch(() => setTriggerError(getErrorMessage(err, "Trigger failed")));
      setTriggerState("error");
      setTimeout(() => setTriggerState("idle"), 3_000);
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

  return (
    <article className="bg-white rounded-md border border-slate-200 p-4 space-y-3">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900 truncate">{worker.title}</h2>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">
            com.dogfoodandfun.{worker.label}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0 flex-wrap justify-end">
          <span className={`${PILL_BASE} ${PILL_VARIANT[worker.status]}`}>
            {PILL_LABEL[worker.status]}
          </span>

          {/* Worker count selector */}
          <div className="inline-flex rounded-md border border-slate-200 overflow-hidden text-xs font-medium">
            {([1, 2, 3] as const).map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => setWorkerCount(n)}
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
            disabled={isBusy}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-amber-50 text-amber-800 border border-amber-200 hover:bg-amber-100 disabled:opacity-60 transition-colors"
          >
            {isBusy ? (
              <><Spinner size="sm" className="text-amber-700" /> Triggering…</>
            ) : triggerState === "triggered" ? "Triggered!" : "Trigger ▶"}
          </button>
        </div>
      </div>

      <p className="text-sm text-slate-600">
        <span className="font-medium">Last run:</span>{" "}
        {humanizeRelative(worker.last_run)}
      </p>

      <p className="text-sm text-slate-700">{worker.description}</p>

      {worker.status === "error" && worker.message && (
        <div className="bg-rose-50 border border-rose-200 text-rose-900 text-sm rounded-md p-3">
          <p className="font-semibold mb-1">Last error</p>
          <pre className="text-xs whitespace-pre-wrap break-words font-mono">{worker.message}</pre>
        </div>
      )}

      {triggerState === "error" && triggerError && (
        <p className="text-sm text-rose-700">{triggerError}</p>
      )}

      <div className="flex gap-4 flex-wrap">
        <LogPanel label={worker.label} workerStatus={worker.status} defaultOpen={defaultLogOpen} />
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
