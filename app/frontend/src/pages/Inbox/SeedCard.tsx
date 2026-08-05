/**
 * Pending recipe seed — approve to trigger recipe generation, skip to discard.
 *
 * Shows seed_id code, timing, tags, first 5 ingredients (rest collapsible),
 * and a truncated dog-safety note.
 */

import { useState } from "react";

import Alert from "../../components/ui/Alert";
import Spinner from "../../components/ui/Spinner";
import { endpoints } from "../../api/endpoints";
import { useApiMutation } from "../../hooks/useApiMutation";
import type { components } from "../../types/openapi";

import { isResolvedResult, truncate } from "./shared";

type DecisionResponse = components["schemas"]["DecisionResponse"];
type SeedItem = components["schemas"]["SeedItem"];

interface Props {
  item: SeedItem;
  onDecision: (id: string) => void;
}

const INGREDIENTS_PREVIEW = 5;
const SAFETY_LIMIT = 150;

export function SeedCard({ item, onDecision }: Props): React.JSX.Element {
  const [showAllIngredients, setShowAllIngredients] = useState(false);
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

  const visibleIngredients = showAllIngredients
    ? item.ingredients
    : item.ingredients.slice(0, INGREDIENTS_PREVIEW);
  const hiddenCount = item.ingredients.length - INGREDIENTS_PREVIEW;

  const timing = [
    item.prep_minutes != null ? `${item.prep_minutes}min prep` : null,
    item.cook_minutes != null ? `${item.cook_minutes}min cook` : null,
    item.yield_servings || null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
      <div className="p-6 border-b border-brand-border">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <span className="inline-flex items-center rounded-full bg-violet-100 text-violet-800 text-xs font-semibold px-2.5 py-1">
            📋 Recipe Seed
          </span>
          <code className="text-xs bg-slate-100 text-slate-600 rounded px-2 py-0.5 font-mono">
            {item.seed_id}
          </code>
        </div>
        <h3 className="text-base font-bold text-slate-900 leading-snug break-words mt-1">
          {item.title}
        </h3>
        {timing && <div className="mt-1 text-sm text-slate-500">{timing}</div>}
        {item.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-3">
            {item.tags.map((tag) => (
              <span key={tag} className="inline-flex items-center rounded-full bg-amber-100 text-amber-800 text-xs font-medium px-2 py-0.5">
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="px-6 py-5 space-y-4">
        <div>
          <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-2">
            Ingredients ({item.ingredients.length})
          </div>
          <ul className="text-sm text-slate-700 space-y-1">
            {visibleIngredients.map((ing, i) => (
              <li key={i} className="flex items-start gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full bg-amber-400" aria-hidden="true" />
                {ing}
              </li>
            ))}
          </ul>
          {hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setShowAllIngredients((v) => !v)}
              className="mt-2 text-xs font-medium text-brand-primary hover:text-brand-primary-hover"
            >
              {showAllIngredients
                ? "Show less"
                : `+ ${hiddenCount} more ingredient${hiddenCount === 1 ? "" : "s"}`}
            </button>
          )}
        </div>

        {item.dog_safety_notes && (
          <div>
            <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-1">
              Dog safety
            </div>
            <p className="text-sm text-slate-600 leading-relaxed">
              {truncate(item.dog_safety_notes, SAFETY_LIMIT)}
            </p>
          </div>
        )}
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
