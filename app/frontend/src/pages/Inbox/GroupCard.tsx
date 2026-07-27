/**
 * Pending group-to-join row — a Facebook group the scout surfaced.
 *
 * Header: group name (large), privacy badge (public=blue, private=amber),
 * member count formatted ("12.3K members").
 * Body: score as percentage, found-via query, competitor mentions (if > 0).
 * Footer: Join (primary cyan) + Skip (secondary) buttons.
 *
 * Owns its own approve/reject mutations via `useApiMutation`. Calls
 * `onResolved(id)` once the item leaves the queue (200 or 409). On a 429
 * (over join cap) shows an inline warning rather than removing the card.
 */

import Alert from "../../components/ui/Alert";
import Spinner from "../../components/ui/Spinner";
import { endpoints } from "../../api/endpoints";
import { useApiMutation } from "../../hooks/useApiMutation";
import type { components } from "../../types/openapi";

import { isResolvedResult } from "./shared";

type DecisionResponse = components["schemas"]["DecisionResponse"];
type GroupItem = components["schemas"]["GroupItem"];

interface GroupCardProps {
  item: GroupItem;
  onResolved: (id: string) => void;
}

const CAP_WARNING =
  "5/day or 15/week join cap reached. Try again tomorrow.";

export default function GroupCard({
  item,
  onResolved,
}: GroupCardProps): React.JSX.Element {
  const approve = useApiMutation<DecisionResponse>("post");
  const reject = useApiMutation<DecisionResponse>("post");

  const isSubmitting = approve.loading || reject.loading;

  const handleJoin = async (): Promise<void> => {
    const result = await approve.mutate(endpoints.approve(item.id));
    if (isResolvedResult(result, approve.errorStatus)) {
      onResolved(item.id);
    }
  };

  const handleSkip = async (): Promise<void> => {
    const result = await reject.mutate(endpoints.reject(item.id));
    if (isResolvedResult(result, reject.errorStatus)) {
      onResolved(item.id);
    }
  };

  const capWarning = approve.errorStatus === 429 ? CAP_WARNING : "";
  const inlineError =
    !capWarning && approve.error && approve.errorStatus !== 409
      ? approve.error
      : !capWarning && reject.error && reject.errorStatus !== 409
        ? reject.error
        : "";

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
      <div className="p-6 border-b border-brand-border">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="min-w-0 flex-1">
            <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-1">
              Facebook group · scout suggestion
            </div>
            <h3 className="text-lg font-bold text-slate-900 leading-snug break-words">
              {item.name || "(unnamed group)"}
            </h3>
            <div className="mt-2 flex items-center flex-wrap gap-2">
              <PrivacyBadge privacy={item.privacy} />
              {item.member_count != null && (
                <span className="text-xs text-slate-500 tabular-nums">
                  {formatMembers(item.member_count)} members
                </span>
              )}
            </div>
          </div>
          {item.score != null && (
            <span
              className="inline-flex items-center rounded-full bg-amber-100 text-amber-900 text-xs font-semibold px-2.5 py-1"
              title="Relevance score"
            >
              {formatScore(item.score)}
            </span>
          )}
        </div>

        <dl className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-sm">
          {item.found_via_query && (
            <div className="min-w-0">
              <dt className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
                Found via
              </dt>
              <dd className="text-slate-700 truncate" title={item.found_via_query}>
                {item.found_via_query}
              </dd>
            </div>
          )}
          {item.competitor_mentions != null && item.competitor_mentions > 0 && (
            <div className="min-w-0">
              <dt className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
                Competitor mentions
              </dt>
              <dd className="text-slate-700 tabular-nums">
                {item.competitor_mentions}
              </dd>
            </div>
          )}
        </dl>

        {item.url && (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="mt-4 text-xs font-medium text-brand-primary hover:text-brand-primary-hover inline-flex items-center gap-1"
          >
            View group on Facebook
            <span aria-hidden="true">↗</span>
          </a>
        )}
      </div>

      {capWarning && (
        <div className="px-6 pt-4">
          <Alert status="warning" title="Join cap reached">
            {capWarning}
          </Alert>
        </div>
      )}
      {inlineError && (
        <div className="px-6 pt-4">
          <Alert status="error">{inlineError}</Alert>
        </div>
      )}

      <div className="p-5 flex flex-wrap items-center justify-end gap-3 bg-stone-50/40">
        <button
          type="button"
          onClick={() => void handleSkip()}
          disabled={isSubmitting}
          className="inline-flex items-center gap-2 px-5 py-2 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {reject.loading && (
            <Spinner size="sm" className="text-slate-500" />
          )}
          Skip
        </button>
        <button
          type="button"
          onClick={() => void handleJoin()}
          disabled={isSubmitting}
          className="inline-flex items-center gap-2 px-6 py-2 rounded-lg bg-cyan-600 text-white text-sm font-semibold hover:bg-cyan-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {approve.loading && <Spinner size="sm" className="text-white" />}
          Join
        </button>
      </div>
    </div>
  );
}

interface PrivacyBadgeProps {
  privacy: GroupItem["privacy"];
}

function PrivacyBadge({ privacy }: PrivacyBadgeProps): React.JSX.Element | null {
  if (!privacy) return null;
  const isPublic = privacy === "public";
  const cls = isPublic
    ? "bg-blue-50 text-blue-700 border-blue-200"
    : "bg-amber-50 text-amber-800 border-amber-200";
  return (
    <span
      className={`inline-flex items-center rounded-full border text-[11px] font-semibold uppercase tracking-wide px-2 py-0.5 ${cls}`}
    >
      {isPublic ? "Public" : "Private"}
    </span>
  );
}

/** Format a member count as e.g. "12.3K" or "1.2M". Falls back to raw int. */
function formatMembers(count: number): string {
  if (count < 1_000) return String(count);
  if (count < 1_000_000) {
    const k = count / 1_000;
    const fixed = k >= 100 ? k.toFixed(0) : k.toFixed(1);
    return `${stripTrailingZero(fixed)}K`;
  }
  const m = count / 1_000_000;
  const fixed = m >= 100 ? m.toFixed(0) : m.toFixed(1);
  return `${stripTrailingZero(fixed)}M`;
}

function stripTrailingZero(s: string): string {
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

/** Format a 0..1 score as a percentage with no fractional part. */
function formatScore(score: number): string {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  return `${pct}%`;
}
