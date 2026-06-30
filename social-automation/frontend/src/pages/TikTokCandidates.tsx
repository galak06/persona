import { useMemo, useState } from "react";
import {
  candidatesUrl,
  type TikTokCandidate,
  type CandidatesResponse,
} from "../api/tiktokCandidates";
import { useApiQuery } from "../hooks/useApiQuery";
import { useApiMutation } from "../hooks/useApiMutation";

const STATUSES = ["all", "pending", "followed", "skipped"] as const;
type StatusFilter = (typeof STATUSES)[number];

function formatFollowers(n: number | null): string {
  if (n === null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

function StatusBadge({ status }: { status: string }): React.JSX.Element {
  const cls =
    status === "followed"
      ? "bg-emerald-50 text-emerald-700"
      : status === "skipped"
        ? "bg-slate-100 text-slate-500"
        : "bg-amber-50 text-amber-700";
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}>
      {status}
    </span>
  );
}

interface ActionButtonsProps {
  candidate: TikTokCandidate;
  onStatusChange: (handle: string, status: "pending" | "followed" | "skipped") => void;
  busy: boolean;
}

function ActionButtons({ candidate, onStatusChange, busy }: ActionButtonsProps): React.JSX.Element {
  const { status, handle } = candidate;
  if (status === "pending") {
    return (
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={() => onStatusChange(handle, "followed")}
          className="px-2.5 py-1 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-40"
        >
          Follow ✓
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => onStatusChange(handle, "skipped")}
          className="px-2.5 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
        >
          Skip
        </button>
      </div>
    );
  }
  if (status === "followed") {
    return (
      <button
        type="button"
        disabled={busy}
        onClick={() => onStatusChange(handle, "pending")}
        className="px-2.5 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
      >
        Unfollow
      </button>
    );
  }
  return (
    <button
      type="button"
      disabled={busy}
      onClick={() => onStatusChange(handle, "pending")}
      className="px-2.5 py-1 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
    >
      Restore
    </button>
  );
}

interface RowProps {
  candidate: TikTokCandidate;
  onStatusChange: (handle: string, status: "pending" | "followed" | "skipped") => void;
  busy: boolean;
}

function CandidateRow({ candidate, onStatusChange, busy }: RowProps): React.JSX.Element {
  return (
    <tr className="border-b border-stone-100 hover:bg-stone-50/60 align-top">
      <td className="py-2 pr-3 whitespace-nowrap text-sm">
        <a
          href={`https://tiktok.com/@${candidate.handle}`}
          target="_blank"
          rel="noreferrer"
          className="text-amber-700 hover:underline"
        >
          @{candidate.handle}
        </a>
      </td>
      <td className="py-2 pr-3 text-sm text-slate-700">{candidate.display_name ?? "—"}</td>
      <td className="py-2 pr-3 text-sm text-slate-600 tabular-nums whitespace-nowrap">
        {formatFollowers(candidate.follower_count)}
      </td>
      <td className="py-2 pr-3 text-sm text-slate-500">
        {candidate.niche ? `#${candidate.niche}` : "—"}
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-xs text-slate-400 tabular-nums">
        {formatDate(candidate.discovered_at)}
      </td>
      <td className="py-2 pr-3 whitespace-nowrap">
        <StatusBadge status={candidate.status} />
      </td>
      <td className="py-2 whitespace-nowrap">
        <ActionButtons candidate={candidate} onStatusChange={onStatusChange} busy={busy} />
      </td>
    </tr>
  );
}

export default function TikTokCandidates(): React.JSX.Element {
  const [activeTab, setActiveTab] = useState<StatusFilter>("all");
  const [busyHandles, setBusyHandles] = useState<Set<string>>(new Set());

  const url = useMemo(
    () => candidatesUrl(activeTab === "all" ? undefined : activeTab),
    [activeTab],
  );
  const { data, loading, error, refetch } = useApiQuery<CandidatesResponse>(url);
  const { mutate } = useApiMutation<unknown, { status: string }>("patch");

  const handleStatusChange = async (
    handle: string,
    status: "pending" | "followed" | "skipped",
  ): Promise<void> => {
    setBusyHandles((prev) => new Set(prev).add(handle));
    try {
      await mutate(`/tiktok-candidates/${encodeURIComponent(handle)}/status`, { status });
      await refetch();
    } finally {
      setBusyHandles((prev) => {
        const next = new Set(prev);
        next.delete(handle);
        return next;
      });
    }
  };

  const counts = data?.counts ?? {};
  const tabCount = (tab: StatusFilter): number =>
    tab === "all" ? (data?.total ?? 0) : (counts[tab] ?? 0);

  return (
    <div className="px-8 py-6">
      <header className="mb-5">
        <h1 className="font-display text-2xl font-semibold text-slate-800">TikTok Candidates</h1>
        <p className="text-sm text-slate-500">
          Follow candidates discovered by tiktok_scout.py — {data?.total ?? 0} total.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap gap-2">
        {STATUSES.map((s) => {
          const count = tabCount(s);
          return (
            <button
              key={s}
              type="button"
              onClick={() => setActiveTab(s)}
              className={`rounded-full px-3 py-1 text-sm capitalize transition-colors ${
                activeTab === s
                  ? "bg-amber-600 text-white"
                  : "bg-stone-100 text-slate-600 hover:bg-stone-200"
              }`}
            >
              {s}
              {count > 0 && (
                <span
                  className={`ml-1.5 rounded-full px-1.5 text-xs tabular-nums ${
                    activeTab === s
                      ? "bg-amber-700 text-white"
                      : "bg-stone-200 text-slate-600"
                  }`}
                >
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      {loading && <p className="text-sm text-slate-400">Loading…</p>}
      {error && <p className="text-sm text-rose-600">{error}</p>}

      {!loading && !error && data?.candidates.length === 0 && (
        <p className="text-sm text-slate-400">
          No candidates found. Run{" "}
          <code className="bg-stone-100 px-1 rounded text-slate-600">
            tiktok_scout.py --apply
          </code>{" "}
          to populate.
        </p>
      )}

      {data && data.candidates.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-stone-200 text-xs uppercase tracking-wide text-slate-400">
                <th className="py-2 px-3 font-medium">Handle</th>
                <th className="py-2 pr-3 font-medium">Display Name</th>
                <th className="py-2 pr-3 font-medium">Followers</th>
                <th className="py-2 pr-3 font-medium">Source Hashtag</th>
                <th className="py-2 pr-3 font-medium">Discovered</th>
                <th className="py-2 pr-3 font-medium">Status</th>
                <th className="py-2 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="[&_td:first-child]:pl-3">
              {data.candidates.map((c) => (
                <CandidateRow
                  key={c.handle}
                  candidate={c}
                  onStatusChange={(handle, status) => void handleStatusChange(handle, status)}
                  busy={busyHandles.has(c.handle)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
