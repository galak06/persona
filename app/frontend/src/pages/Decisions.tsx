import { useMemo, useState } from "react";
import {
  decisionsUrl,
  type DecisionsResponse,
  type DecisionsSummary,
  type JevDecision,
} from "../api/decisions";
import { useBrand } from "../context/BrandContext";
import { useApiQuery } from "../hooks/useApiQuery";
import ErrorState from "../components/ui/ErrorState";
import LoadingState from "../components/ui/LoadingState";

/**
 * Decisions — the Jev post-gate log (`GET /decisions`). Read-only.
 *
 * In shadow mode Jev never changes what the engagers do; this page is where
 * its would-skip calls are compared with what the comment drafter actually
 * did, to decide whether enforce is safe to switch on.
 */

const PLATFORMS = ["all", "instagram", "facebook"] as const;
type PlatformFilter = (typeof PLATFORMS)[number];

const PLATFORM_ICON: Record<string, string> = { facebook: "📘", instagram: "📸" };

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/** "relevant 91% · value answer_question (80%) · unsafe 4%" from raw answers. */
function answerChips(d: JevDecision): { key: string; text: string }[] {
  const answers = asRecord(d.answers);
  return Object.entries(answers).map(([key, raw]) => {
    const a = asRecord(raw);
    if (typeof a.choice === "string") {
      return { key, text: `${key}: ${a.choice} (${pct(asNumber(a.confidence))})` };
    }
    if (asNumber(a.noul) !== null) return { key, text: `${key}: ${pct(asNumber(a.noul))}` };
    if (asNumber(a.score) !== null) return { key, text: `${key}: ${asNumber(a.score)?.toFixed(2)}` };
    return { key, text: `${key}: ?` };
  });
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }): React.JSX.Element {
  return (
    <div className="rounded-lg border border-stone-200 bg-white px-4 py-3">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className="font-display text-xl font-semibold text-slate-800 tabular-nums">{value}</p>
      {hint && <p className="text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

function SummaryTiles({ s }: { s: DecisionsSummary }): React.JSX.Element {
  return (
    <div className="mb-4 grid grid-cols-2 sm:grid-cols-4 gap-3">
      <Tile label="Decisions" value={String(s.total)} hint={`${s.failed ?? 0} failed Jev calls`} />
      <Tile label="Would skip" value={String(s.would_skip)} hint={pct(s.total ? s.would_skip / s.total : null)} />
      <Tile
        label="Agreement"
        value={pct(s.agreement_rate)}
        hint={`${s.agreed} of ${s.compared} vs drafter`}
      />
      <Tile label="Total cost" value={`$${s.total_cost_usd.toFixed(4)}`} />
    </div>
  );
}

function AgreeBadge({ agrees }: { agrees: boolean | null | undefined }): React.JSX.Element {
  if (agrees === null || agrees === undefined) return <span className="text-slate-300">—</span>;
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        agrees ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700"
      }`}
    >
      {agrees ? "agree" : "disagree"}
    </span>
  );
}

function Row({ d }: { d: JevDecision }): React.JSX.Element {
  const isUrl = d.item_key.startsWith("http");
  return (
    <tr className="border-b border-stone-100 hover:bg-stone-50/60 align-top">
      <td className="py-2 pr-3 text-sm max-w-xs">
        <span aria-hidden="true">{PLATFORM_ICON[d.platform] ?? "•"}</span>{" "}
        {isUrl ? (
          <a href={d.item_key} target="_blank" rel="noreferrer" className="text-amber-700 hover:underline break-all">
            {d.item_key.replace(/^https?:\/\/(www\.)?/, "")}
          </a>
        ) : (
          <span className="text-slate-600 break-all">{d.item_key}</span>
        )}
      </td>
      <td className="py-2 pr-3 text-xs text-slate-600">
        <div className="flex flex-wrap gap-1">
          {answerChips(d).map((c) => (
            <span key={c.key} className="rounded bg-stone-100 px-1.5 py-0.5 whitespace-nowrap">
              {c.text}
            </span>
          ))}
        </div>
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-sm">
        {d.error ? (
          <span className="rounded bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700" title={d.error}>
            failed
          </span>
        ) : d.would_skip ? (
          <span className="rounded bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700">skip</span>
        ) : (
          <span className="text-xs text-slate-500">keep</span>
        )}
      </td>
      <td className="py-2 pr-3 whitespace-nowrap text-sm text-slate-600">{d.outcome ?? "pending"}</td>
      <td className="py-2 pr-3 whitespace-nowrap">
        <AgreeBadge agrees={d.agrees} />
      </td>
      <td className="py-2 whitespace-nowrap text-xs text-slate-400 tabular-nums">
        {d.created_at ? d.created_at.slice(0, 16).replace("T", " ") : "—"}
      </td>
    </tr>
  );
}

export default function Decisions(): React.JSX.Element {
  const { selectedBrand } = useBrand();
  const [platform, setPlatform] = useState<PlatformFilter>("all");
  const url = useMemo(
    () =>
      decisionsUrl({
        brandId: selectedBrand,
        platform: platform === "all" ? undefined : platform,
        limit: 200,
      }),
    [selectedBrand, platform],
  );
  const { data, loading, error, refetch } = useApiQuery<DecisionsResponse>(url);

  return (
    <div className="px-8 py-6">
      <header className="mb-5">
        <h1 className="font-display text-2xl font-semibold text-slate-800">Decisions</h1>
        <p className="text-sm text-slate-500">
          Jev post-gate calls for {selectedBrand} — what it would have skipped vs. what the comment
          drafter actually did.
        </p>
      </header>

      <div className="mb-4 flex flex-wrap gap-2">
        {PLATFORMS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => setPlatform(p)}
            className={`rounded-full px-3 py-1 text-sm capitalize transition-colors ${
              platform === p ? "bg-amber-600 text-white" : "bg-stone-100 text-slate-600 hover:bg-stone-200"
            }`}
          >
            {p}
          </button>
        ))}
      </div>

      {data && <SummaryTiles s={data.summary} />}

      {loading && !data && <LoadingState message="Loading decisions…" />}
      {error && <ErrorState message={error} onRetry={() => void refetch()} retrying={loading} />}

      {data && data.decisions.length === 0 && !loading && (
        <p className="text-sm text-slate-400">No Jev decisions logged yet for this filter.</p>
      )}

      {data && data.decisions.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-stone-200 bg-white">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-stone-200 text-xs uppercase tracking-wide text-slate-400">
                <th className="py-2 px-3 font-medium">Post</th>
                <th className="py-2 pr-3 font-medium">Jev answers</th>
                <th className="py-2 pr-3 font-medium">Jev</th>
                <th className="py-2 pr-3 font-medium">Drafter</th>
                <th className="py-2 pr-3 font-medium">Match</th>
                <th className="py-2 font-medium">When</th>
              </tr>
            </thead>
            <tbody className="[&_td:first-child]:pl-3">
              {data.decisions.map((d) => (
                <Row key={d.id} d={d} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
