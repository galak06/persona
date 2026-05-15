/**
 * Pending blog-post pair — a WP article awaiting FB + IG caption sign-off.
 *
 * Top: thumbnail + post title + permalink to the WP draft / live post.
 * Side-by-side: editable FB caption (left) + IG caption (right).
 * Four buttons: Approve both / FB only / IG only / Skip.
 *
 * Calls `onResolved(id)` once the item leaves the queue (200 or 409).
 */

import { useState } from "react";

import Alert from "../../components/ui/Alert";
import Spinner from "../../components/ui/Spinner";
import { endpoints } from "../../api/endpoints";
import { useApiMutation } from "../../hooks/useApiMutation";
import type {
  BlogPostItem,
  Channel,
  DecisionResponse,
} from "../../types/openapi";

import { isResolvedResult } from "./shared";

interface BlogPostCardProps {
  item: BlogPostItem;
  onResolved: (id: string) => void;
}

type ButtonKey = "both" | "fb_only" | "ig_only" | "skip";

export default function BlogPostCard({ item, onResolved }: BlogPostCardProps) {
  const [fbCaption, setFbCaption] = useState(item.fb_caption);
  const [igCaption, setIgCaption] = useState(item.ig_caption);
  const [activeAction, setActiveAction] = useState<ButtonKey | null>(null);
  const approve = useApiMutation<DecisionResponse>("post");
  const reject = useApiMutation<DecisionResponse>("post");

  const isSubmitting = approve.loading || reject.loading;

  const handleApprove = async (channel: Channel): Promise<void> => {
    setActiveAction(channel as ButtonKey);
    const body: { fb_caption?: string; ig_caption?: string } = {};
    if (channel !== "ig_only") body.fb_caption = fbCaption.trim();
    if (channel !== "fb_only") body.ig_caption = igCaption.trim();
    const result = await approve.mutate(
      endpoints.approve(item.id, channel),
      body,
    );
    if (isResolvedResult(result, approve.errorStatus)) {
      onResolved(item.id);
    }
    setActiveAction(null);
  };

  const handleSkip = async (): Promise<void> => {
    setActiveAction("skip");
    const result = await reject.mutate(endpoints.reject(item.id));
    if (isResolvedResult(result, reject.errorStatus)) {
      onResolved(item.id);
    }
    setActiveAction(null);
  };

  const inlineError =
    approve.error && approve.errorStatus !== 409
      ? approve.error
      : reject.error && reject.errorStatus !== 409
        ? reject.error
        : "";

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card overflow-hidden">
      <div className="p-6 border-b border-brand-border flex items-start gap-4">
        <Thumbnail src={item.image_url || null} alt={item.post_title} />
        <div className="flex-1 min-w-0">
          <div className="text-xs uppercase tracking-wider text-slate-400 font-semibold mb-1">
            Blog post · #{item.post_id}
          </div>
          <h3 className="text-base font-bold text-slate-900 leading-snug break-words">
            {item.post_title || "(untitled)"}
          </h3>
          {item.post_url && (
            <a
              href={item.post_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 text-xs font-medium text-brand-primary hover:text-brand-primary-hover inline-flex items-center gap-1"
            >
              View on site
              <span aria-hidden="true">↗</span>
            </a>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-0">
        <CaptionPane
          label="Facebook caption"
          icon="📘"
          value={fbCaption}
          onChange={setFbCaption}
          minLines={6}
          className="md:border-r border-b md:border-b-0 border-brand-border"
        />
        <CaptionPane
          label="Instagram caption"
          icon="📸"
          value={igCaption}
          onChange={setIgCaption}
          minLines={6}
        />
      </div>

      {inlineError && (
        <div className="px-6 pt-4">
          <Alert status="error">{inlineError}</Alert>
        </div>
      )}

      <div className="p-5 flex flex-wrap items-center justify-end gap-3 border-t border-brand-border bg-stone-50/40">
        <button
          type="button"
          onClick={() => void handleSkip()}
          disabled={isSubmitting}
          className="inline-flex items-center gap-2 px-5 py-2 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {activeAction === "skip" && reject.loading && (
            <Spinner size="sm" className="text-slate-500" />
          )}
          Skip
        </button>
        <button
          type="button"
          onClick={() => void handleApprove("fb_only")}
          disabled={isSubmitting || !fbCaption.trim()}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {activeAction === "fb_only" && approve.loading && (
            <Spinner size="sm" className="text-slate-500" />
          )}
          FB only
        </button>
        <button
          type="button"
          onClick={() => void handleApprove("ig_only")}
          disabled={isSubmitting || !igCaption.trim()}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-brand-border bg-white text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {activeAction === "ig_only" && approve.loading && (
            <Spinner size="sm" className="text-slate-500" />
          )}
          IG only
        </button>
        <button
          type="button"
          onClick={() => void handleApprove("both")}
          disabled={isSubmitting || !fbCaption.trim() || !igCaption.trim()}
          className="inline-flex items-center gap-2 px-6 py-2 rounded-lg bg-brand-primary text-white text-sm font-semibold hover:bg-brand-primary-hover disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {activeAction === "both" && approve.loading && (
            <Spinner size="sm" className="text-white" />
          )}
          Approve both
        </button>
      </div>
    </div>
  );
}

interface CaptionPaneProps {
  label: string;
  icon: string;
  value: string;
  onChange: (v: string) => void;
  minLines: number;
  className?: string;
}

function CaptionPane({
  label,
  icon,
  value,
  onChange,
  minLines,
  className = "",
}: CaptionPaneProps): React.JSX.Element {
  return (
    <div className={`p-5 ${className}`}>
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <span aria-hidden="true">{icon}</span>
          <span className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
            {label}
          </span>
        </div>
        <span className="text-xs text-slate-400">{value.length} chars</span>
      </div>
      <textarea
        dir="auto"
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ minHeight: `${minLines * 1.6}rem` }}
        className="w-full resize-y rounded-xl border border-brand-border bg-white px-3 py-2 text-sm leading-relaxed text-slate-800 focus:outline-none focus:ring-2 focus:ring-amber-500/40"
      />
    </div>
  );
}

interface ThumbnailProps {
  src: string | null;
  alt: string;
}

function Thumbnail({ src, alt }: ThumbnailProps): React.JSX.Element {
  if (!src) {
    return (
      <div
        className="w-20 h-20 rounded-xl bg-amber-50 border border-amber-100 flex items-center justify-center text-2xl"
        aria-hidden="true"
      >
        {"📰"}
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      className="w-20 h-20 rounded-xl object-cover border border-brand-border bg-stone-100"
      loading="lazy"
    />
  );
}
