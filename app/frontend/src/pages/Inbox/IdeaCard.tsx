/**
 * Pending recipe idea — approve to queue for enrichment, skip to discard.
 *
 * Shows the idea title, demand tier badge, seasonal relevance, category,
 * "why now" rationale, and a truncated evidence snippet.
 */

import Alert from "../../components/ui/Alert";
import Spinner from "../../components/ui/Spinner";
import { endpoints } from "../../api/endpoints";
import { useApiMutation } from "../../hooks/useApiMutation";
import type { components } from "../../types/openapi";

import { isResolvedResult, truncate } from "./shared";

type DecisionResponse = components["schemas"]["DecisionResponse"];
type IdeaItem = components["schemas"]["IdeaItem"];

interface Props {
  item: IdeaItem;
  onDecision: (id: string) => void;
}

const DEMAND_STYLES: Record<string, string> = {
  HIGH: "bg-emerald-100 text-emerald-800",
  MEDIUM: "bg-amber-100 text-amber-800",
  LOW: "bg-slate-100 text-slate-600",
};

const EVIDENCE_LIMIT = 200;

export function IdeaCard({ item, onDecision }: Props): React.JSX.Element {
  const approve = useApiMutation<DecisionResponse>("post");
  const reject = useApiMutation<DecisionResponse>("post");

  const isSubmitting = approve.loading || reject.loading;

  const handleApprove = async (): Promise<void> => {
    const result = await approve.mutate(endpoints.approve(item.id));
    if (isResolvedResult(result, approve.errorStatus)) {
      onDecision(item.id);
    }
  };

  const handleSkip = async (): Promise<void> => {
    const result = await reject.mutate(endpoints.reject(item.id));
    if (isResolvedResult(result, reject.errorStatus)) {
      onDecision(item.id);
    }
  };

  const inlineError =
    approve.error && approve.errorStatus !== 409
      ? approve.error
      : reject.error && reject.errorStatus !== 409
        ? reject.error
        : "";

  const demandKey = item.search_demand_estimate.toUpperCase();
  const demandStyle = DEMAND_STYLES[demandKey] ?? DEMAND_STYLES.LOW;

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
      <div className="p-6 border-b border-brand-border flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <span className="inline-flex items-center rounded-full bg-orange-100 text-orange-800 text-xs font-semibold px-2.5 py-1">
              🍳 Recipe Idea
            </span>
            <span
              className={`inline-flex items-center rounded-full text-xs font-semibold px-2.5 py-1 ${demandStyle}`}
              title="Search demand estimate"
            >
              {item.search_demand_estimate}
            </span>
            {item.seasonal_relevance != null && (
              <span
                className="inline-flex items-center rounded-full bg-sky-100 text-sky-800 text-xs font-semibold px-2.5 py-1"
                title="Seasonal relevance"
              >
                {item.seasonal_relevance}/10 seasonal
              </span>
            )}
          </div>
          <h3 className="text-base font-bold text-slate-900 leading-snug break-words">
            {item.title}
          </h3>
          <div className="mt-1 text-xs uppercase tracking-wider text-slate-400 font-semibold">
            {item.category}
          </div>
        </div>
      </div>

      <div className="px-6 py-5 space-y-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-1">
            Why now
          </div>
          <p className="text-sm text-slate-700 leading-relaxed">{item.why_now}</p>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-1">
            Evidence
          </div>
          <p className="text-sm text-slate-600 leading-relaxed">
            {truncate(item.evidence, EVIDENCE_LIMIT)}
          </p>
        </div>
      </div>

      {inlineError && (
        <div className="px-6 pb-2">
          <Alert status="error">{inlineError}</Alert>
        </div>
      )}

      <div className="p-5 flex items-center justify-end gap-3 border-t border-brand-border bg-stone-50/40">
        <button
          type="button"
          onClick={() => void handleSkip()}
          disabled={isSubmitting}
          className="inline-flex items-center gap-2 px-5 py-2 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {reject.loading && <Spinner size="sm" className="text-slate-500" />}
          Skip
        </button>
        <button
          type="button"
          onClick={() => void handleApprove()}
          disabled={isSubmitting}
          className="inline-flex items-center gap-2 px-6 py-2 rounded-lg bg-brand-primary text-white text-sm font-semibold hover:bg-brand-primary-hover disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {approve.loading && <Spinner size="sm" className="text-white" />}
          Approve
        </button>
      </div>
    </div>
  );
}
