/**
 * Campaign ready for final verification — approve to trigger publishing,
 * skip to defer.
 *
 * Shows title, seed ID, audio presence + size, slide count, and a link to
 * the WP draft so the reviewer can do a final check before firing the
 * publish pipeline.
 */

import Alert from "../../components/ui/Alert";
import Spinner from "../../components/ui/Spinner";
import { endpoints } from "../../api/endpoints";
import { useApiMutation } from "../../hooks/useApiMutation";
import type { components } from "../../types/openapi";

import { isResolvedResult } from "./shared";

type CampaignVerifyItem = components["schemas"]["CampaignVerifyItem"];
type DecisionResponse = components["schemas"]["DecisionResponse"];

interface Props {
  item: CampaignVerifyItem;
  onDecision: (id: string) => void;
}

export function CampaignVerifyCard({ item, onDecision }: Props): React.JSX.Element {
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

  const audioLabel =
    item.audio_size_kb != null
      ? `✅ Audio present (${item.audio_size_kb} KB)`
      : "⚠️ No audio";

  const audioStyle =
    item.audio_size_kb != null
      ? "text-emerald-700"
      : "text-amber-700";

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
      <div className="p-6 border-b border-brand-border">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <span className="inline-flex items-center rounded-full bg-teal-100 text-teal-800 text-xs font-semibold px-2.5 py-1">
            🎬 Campaign Ready
          </span>
          <code className="text-xs bg-slate-100 text-slate-600 rounded px-2 py-0.5 font-mono">
            {item.seed_id}
          </code>
        </div>
        <h3 className="text-base font-bold text-slate-900 leading-snug break-words mt-1">
          {item.title}
        </h3>
      </div>

      <div className="px-6 py-5 space-y-3">
        <div className={`text-sm font-medium ${audioStyle}`}>{audioLabel}</div>

        {item.slide_count != null && (
          <div className="text-sm text-slate-600">
            {item.slide_count} slide{item.slide_count === 1 ? "" : "s"}
          </div>
        )}

        {item.wp_draft_url && (
          <a
            href={item.wp_draft_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-brand-primary hover:text-brand-primary-hover"
          >
            View WP draft
            <span aria-hidden="true">↗</span>
          </a>
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
          Verify &amp; Publish
        </button>
      </div>
    </div>
  );
}
