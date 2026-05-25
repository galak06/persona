/**
 * Pending comment row — FB group, IG hashtag, or WP on-site reply.
 *
 * Left column: original post context (platform icon, group/hashtag,
 * relevance %, post text preview, permalink).
 * Right column: editable draft comment + Approve / Skip buttons.
 *
 * Owns its own approve/reject mutations via `useApiMutation`. Calls
 * `onResolved(id)` once the item leaves the queue (200 or 409).
 */

import { useState } from "react";

import Alert from "../../components/ui/Alert";
import { ResponsiveCardRow } from "../../components/ui/ResponsiveCardRow";
import Spinner from "../../components/ui/Spinner";
import { endpoints } from "../../api/endpoints";
import { useApiMutation } from "../../hooks/useApiMutation";
import type { CommentItem, DecisionResponse } from "../../types/openapi";

import {
  PLATFORM_ICON,
  PLATFORM_LABEL,
  formatRelevance,
  isResolvedResult,
  truncate,
} from "./shared";

interface CommentCardProps {
  item: CommentItem;
  onResolved: (id: string) => void;
}

const PREVIEW_LIMIT = 200;

export default function CommentCard({ item, onResolved }: CommentCardProps) {
  const [editedText, setEditedText] = useState(item.draft_comment);
  const [showFull, setShowFull] = useState(false);
  const approve = useApiMutation<DecisionResponse>("post");
  const reject = useApiMutation<DecisionResponse>("post");

  const isSubmitting = approve.loading || reject.loading;
  const hasEdit = editedText.trim() !== item.draft_comment.trim();

  const handleApprove = async (): Promise<void> => {
    const body = hasEdit ? { text: editedText.trim() } : undefined;
    const result = await approve.mutate(endpoints.approve(item.id), body);
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

  const inlineError =
    approve.error && approve.errorStatus !== 409
      ? approve.error
      : reject.error && reject.errorStatus !== 409
        ? reject.error
        : "";

  const postPreview =
    showFull || item.post_text.length <= PREVIEW_LIMIT
      ? item.post_text
      : truncate(item.post_text, PREVIEW_LIMIT);

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
      <ResponsiveCardRow
        split="equal"
        className="gap-0"
        left={
          <PostColumn
            item={item}
            postPreview={postPreview}
            showFull={showFull}
            onToggleFull={() => setShowFull((v) => !v)}
          />
        }
        right={
          <div className="p-6 flex flex-col bg-brand-surface">
            <div className="flex items-center justify-between gap-3 mb-3">
              <h3 className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
                Draft reply (Nalla&apos;s Dad)
              </h3>
              <span className="text-xs text-slate-400">
                {editedText.length} chars
              </span>
            </div>
            <textarea
              dir="auto"
              aria-label="Draft comment (editable)"
              value={editedText}
              onChange={(e) => setEditedText(e.target.value)}
              className="flex-grow min-h-[140px] resize-y rounded-xl border border-brand-border bg-white px-3 py-2 text-sm leading-relaxed text-slate-800 focus:outline-none focus:ring-2 focus:ring-amber-500/40"
            />
            {inlineError && (
              <Alert status="error" className="mt-3">
                {inlineError}
              </Alert>
            )}
            <div className="flex justify-end gap-3 mt-5 pt-5 border-t border-brand-border">
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
                onClick={() => void handleApprove()}
                disabled={isSubmitting || !editedText.trim()}
                className="inline-flex items-center gap-2 px-6 py-2 rounded-lg bg-brand-primary text-white text-sm font-semibold hover:bg-brand-primary-hover disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {approve.loading && (
                  <Spinner size="sm" className="text-white" />
                )}
                {hasEdit ? "Approve edit" : "Approve & post"}
              </button>
            </div>
          </div>
        }
      />
    </div>
  );
}

interface PostColumnProps {
  item: CommentItem;
  postPreview: string;
  showFull: boolean;
  onToggleFull: () => void;
}

function PostColumn({
  item,
  postPreview,
  showFull,
  onToggleFull,
}: PostColumnProps): React.JSX.Element {
  return (
    <div className="p-6 bg-stone-50/60 border-b md:border-b-0 md:border-r border-brand-border flex flex-col h-full">
      <div className="flex items-center gap-3 mb-4">
        <span className="text-2xl leading-none" aria-hidden="true">
          {PLATFORM_ICON[item.platform]}
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-slate-900 truncate">
            {item.group_or_hashtag ?? PLATFORM_LABEL[item.platform]}
          </div>
          <div className="text-xs uppercase tracking-wider text-slate-400 mt-0.5">
            {PLATFORM_LABEL[item.platform]}
          </div>
        </div>
        {item.relevance_score != null && (
          <span
            className="inline-flex items-center rounded-full bg-amber-100 text-amber-900 text-xs font-semibold px-2.5 py-1"
            title="Relevance score"
          >
            {formatRelevance(item.relevance_score)}
          </span>
        )}
      </div>
      <div className="bg-white p-4 rounded-xl border border-brand-border">
        <p
          dir="auto"
          className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap break-words"
        >
          {postPreview || <span className="italic text-slate-400">(no post text)</span>}
        </p>
        {item.post_text.length > PREVIEW_LIMIT && (
          <button
            type="button"
            onClick={onToggleFull}
            className="mt-2 text-xs font-medium text-brand-primary hover:text-brand-primary-hover"
          >
            {showFull ? "Show less" : "See more"}
          </button>
        )}
      </div>
      {item.post_url && (
        <a
          href={item.post_url}
          target="_blank"
          rel="noreferrer"
          className="mt-4 text-xs font-medium text-brand-primary hover:text-brand-primary-hover inline-flex items-center gap-1"
        >
          View original
          <span aria-hidden="true">↗</span>
        </a>
      )}
    </div>
  );
}
