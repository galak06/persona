/**
 * One post awaiting review on the Social Posts page: the shared hook image,
 * both captions, and the decisions available on it.
 *
 * Split out of `pages/SocialPosts.tsx` when the retry control landed — that
 * file was already past the 300-line limit, and the card is now the only part
 * that grows.
 *
 * **The retry control is the point of this file.** A post whose image fell
 * back to the article's own photo used to be a dead end: approve it and the
 * brand ships a stock picture, reject it and two perfectly good captions go
 * with it. So a post with no generated image says so in plain words and offers
 * a third move — regenerate the image, keep everything else. The collection
 * picker is offered because the operator can see both the post and the
 * library, which is more than the planner that produced the fallback could.
 */

import { useState } from "react";
import { apiOrigin } from "../api/client";
import { hasGeneratedImage, socialPostImageUrl } from "../api/socialPosts";
import type { SocialPost } from "../api/socialPosts";
import type { CategorySummary } from "../api/referenceImages";

function sourceBadge(source: string | null): React.JSX.Element {
  const generated = source === "gemini";
  const cls = generated
    ? "bg-indigo-50 text-indigo-700"
    : "bg-stone-100 text-slate-600";
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium capitalize ${cls}`}
    >
      {source ?? "unknown"}
    </span>
  );
}

function formatSlot(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export interface SocialPostCardProps {
  post: SocialPost;
  onDecision: (id: string, approve: boolean) => void;
  onRetry: (id: string, referenceCategory: string) => void;
  busy: boolean;
  /** A regeneration is in flight for this post; every decision is refused. */
  retrying: boolean;
  /** Outcome of the last regeneration, once it finished. */
  retryNote?: string | null;
  /** Reference collections that actually hold photos. */
  categories: CategorySummary[];
  /** Bumped after a retry lands, to defeat the browser's image cache. */
  imageVersion?: number;
  scheduledFor?: string;
}

/**
 * The banner a post with no AI-generated image gets, plus its way out.
 *
 * "Auto" is the honest default rather than a cop-out: with no collection named
 * the run still reaches a photo of the brand's own mascot, which is exactly
 * what the fallback failed to do.
 */
function RetryImagePanel({
  post,
  categories,
  retrying,
  onRetry,
}: {
  post: SocialPost;
  categories: CategorySummary[];
  retrying: boolean;
  onRetry: (id: string, referenceCategory: string) => void;
}): React.JSX.Element {
  const [category, setCategory] = useState("");
  const stocked = categories.filter((c) => c.count > 0);

  return (
    <div className="rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 space-y-2">
      <p className="text-xs text-amber-900">
        <span className="font-semibold">Stock image.</span> This post kept the
        article&apos;s own photo — nothing from the reference library matched,
        so no image was generated. Regenerating keeps both captions and
        replaces only the picture.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label
          className="text-xs text-amber-900"
          htmlFor={`ref-${post.id}`}
        >
          Anchor on
        </label>
        <select
          id={`ref-${post.id}`}
          value={category}
          disabled={retrying}
          onChange={(e) => setCategory(e.target.value)}
          className="rounded border border-amber-300 bg-white px-2 py-1 text-xs text-slate-700 disabled:opacity-50"
        >
          <option value="">Auto — a photo of the mascot</option>
          {stocked.map((c) => (
            <option key={c.slug} value={c.slug}>
              {c.label} ({c.count})
            </option>
          ))}
        </select>
        <button
          type="button"
          disabled={retrying}
          onClick={() => onRetry(post.id, category)}
          title="Draft a fresh image brief and generate a new image from a real brand photo"
          className="px-3 py-1.5 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-50"
        >
          {retrying ? "Regenerating… (about a minute)" : "↻ Retry image"}
        </button>
      </div>
    </div>
  );
}

export default function SocialPostCard({
  post,
  onDecision,
  onRetry,
  busy,
  retrying,
  retryNote,
  categories,
  imageVersion,
  scheduledFor,
}: SocialPostCardProps): React.JSX.Element {
  const flags = post.validation_flags ?? [];
  const imageSrc = `${socialPostImageUrl(apiOrigin, post.id)}${
    imageVersion ? `?v=${imageVersion}` : ""
  }`;

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">
            {post.topic}
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {sourceBadge(post.source)}
            {/* Outside the retry panel on purpose: a SUCCESSFUL retry flips the
                source to `gemini`, which hides the panel — and would take the
                only confirmation the operator gets with it. */}
            {retryNote && (
              <span className="text-xs text-slate-600">{retryNote}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {scheduledFor ? (
            <span className="px-3 py-1.5 rounded bg-emerald-50 text-emerald-700 text-xs font-medium">
              Scheduled {formatSlot(scheduledFor)}
            </span>
          ) : (
            <>
              <button
                type="button"
                disabled={busy || retrying}
                onClick={() => onDecision(post.id, false)}
                className="px-3 py-1.5 rounded border border-stone-200 bg-white text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              >
                Reject
              </button>
              <button
                type="button"
                disabled={busy || retrying}
                onClick={() => onDecision(post.id, true)}
                className="px-3 py-1.5 rounded bg-amber-600 text-white text-xs font-semibold hover:bg-amber-700 disabled:opacity-40"
              >
                Approve
              </button>
            </>
          )}
        </div>
      </div>

      {flags.length > 0 && (
        <div className="rounded-lg bg-rose-50 border border-rose-200 px-3 py-2 text-xs text-rose-700">
          <span className="font-semibold">Flagged:</span> {flags.join(", ")}
        </div>
      )}

      {!scheduledFor && !hasGeneratedImage(post) && (
        <RetryImagePanel
          post={post}
          categories={categories}
          retrying={retrying}
          onRetry={onRetry}
        />
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Image (both platforms)
          </div>
          <img
            className={`w-full aspect-square rounded-lg bg-stone-100 object-cover ${
              retrying ? "opacity-50" : ""
            }`}
            src={imageSrc}
            alt={post.image_alt ?? post.topic}
          />
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Facebook — posts in its slot
          </div>
          <p className="text-xs text-slate-600 whitespace-pre-wrap">
            {post.fb_caption}
          </p>
        </div>
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400 mb-1.5">
            Instagram — 4h after Facebook
          </div>
          <p className="text-xs text-slate-600 whitespace-pre-wrap">
            {post.ig_caption}
          </p>
        </div>
      </div>
    </div>
  );
}
