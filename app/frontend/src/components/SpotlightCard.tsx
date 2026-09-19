/**
 * One product spotlight awaiting review: the generated photo, both captions,
 * the two DM links, and the decisions available on it.
 *
 * Deliberately the same chrome and the same three-column grid as
 * `SocialPostCard` — a spotlight is a social post with a product attached,
 * and giving it its own visual language would suggest it publishes down some
 * other path. It does not: it shares the FB slot allocator and the same
 * release loop.
 *
 * Four states a regular post never has:
 *  - `composing` — the run is still working. The row exists so that a killed
 *    or slow run is visible rather than silently missing, so it shows a
 *    spinner and offers no decisions.
 *  - `fb_publishing` / `ig_publishing` — the publisher holds the row. Same
 *    argument as `composing`: the claim can outlive the worker that took it,
 *    and a stuck spotlight has to look stuck rather than vanish. Read-only
 *    pill, no decisions.
 *  - `failed` — the run died. The reason is the whole point of showing the
 *    row at all, so it goes in an Alert in plain sight and, again, no
 *    decisions: there is nothing to approve.
 *  - the DM links — the affiliate URL is never in a caption. It is copied out
 *    of here and pasted into a DM, one per platform because each carries its
 *    own campaign id.
 */

import { apiOrigin } from "../api/client";
import { socialDerivativeImageUrl } from "../api/socialDerivatives";
import type { SocialDerivative } from "../api/socialDerivatives";
import CopyField from "./CopyField";
import SpotlightStatusPill from "./SpotlightStatusPill";
import Alert from "./ui/Alert";
import Spinner from "./ui/Spinner";

/**
 * Flags are machine tokens (`product_not_in_post`). Only the ones whose
 * meaning is genuinely not obvious get a hand-written sentence; everything
 * else is de-snaked, so a flag added server-side still renders readably
 * instead of waiting for this map to catch up.
 */
const FLAG_LABELS: Record<string, string> = {
  product_not_in_post: "Product not mentioned in the post",
  mascot_usage_unverified: "Mascot usage not backed by the facts file",
};

function flagLabel(flag: string): string {
  return FLAG_LABELS[flag] ?? flag.replace(/_/g, " ");
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

export type SpotlightAction = "approve" | "unschedule" | "reject";

export interface SpotlightCardProps {
  derivative: SocialDerivative;
  onDecision: (id: string, action: SpotlightAction) => void;
  /** A decision on this row is in flight; every button is refused. */
  busy: boolean;
}

export default function SpotlightCard({
  derivative,
  onDecision,
  busy,
}: SpotlightCardProps): React.JSX.Element {
  const composing = derivative.composing || derivative.status === "composing";
  const failed = derivative.status === "failed";
  const scheduled = derivative.status === "scheduled";
  const decidable = derivative.status === "queued" && !composing;
  // Everything the card has no chrome of its own for — both publishing
  // claims, the terminal states, and whatever status is added next — gets the
  // read-only pill. `composing`, `failed` and `scheduled` already say what
  // they are (spinner, Alert, slot), so they are not doubled up.
  const readOnly =
    !composing && !failed && !scheduled && derivative.status !== "queued";
  const flags = derivative.validation_flags ?? [];
  const disclosure = derivative.dm_disclosure;

  return (
    <div className="bg-brand-surface rounded-2xl border border-brand-border shadow-card p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-slate-800 leading-snug">
            {derivative.topic || "Product spotlight"}
          </h3>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <span className="rounded bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
              {derivative.product_display}
            </span>
            {composing && (
              <span className="flex items-center gap-1.5 text-xs text-slate-500">
                <Spinner size="sm" className="text-slate-400" />
                Composing…
              </span>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {readOnly && <SpotlightStatusPill status={derivative.status} />}
          {scheduled && derivative.fb_due_at && (
            <span className="rounded bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700">
              Scheduled {formatSlot(derivative.fb_due_at)}
            </span>
          )}
          {scheduled && (
            <button
              type="button"
              disabled={busy}
              onClick={() => onDecision(derivative.id, "unschedule")}
              className="rounded border border-stone-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
            >
              Unschedule
            </button>
          )}
          {decidable && (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => onDecision(derivative.id, "reject")}
                className="rounded border border-stone-200 bg-white px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
              >
                Reject
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => onDecision(derivative.id, "approve")}
                className="rounded bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-40"
              >
                Approve
              </button>
            </>
          )}
        </div>
      </div>

      {failed && (
        <Alert status="error" title="Compose failed">
          {derivative.error ?? "No reason was recorded."}
        </Alert>
      )}

      {flags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {flags.map((flag) => (
            <span
              key={flag}
              className="rounded bg-rose-50 px-2 py-0.5 text-xs font-medium text-rose-700"
            >
              {flagLabel(flag)}
            </span>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div>
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Image (both platforms)
          </div>
          {derivative.has_image ? (
            <img
              className="aspect-square w-full rounded-lg bg-stone-100 object-cover"
              src={socialDerivativeImageUrl(
                apiOrigin,
                derivative.id,
                derivative.updated_at ?? undefined,
              )}
              alt={derivative.image_alt ?? derivative.product_display}
            />
          ) : (
            <div className="flex aspect-square w-full items-center justify-center rounded-lg bg-stone-100 text-xs text-slate-400">
              {composing ? <Spinner size="md" /> : "No image"}
            </div>
          )}
        </div>
        <div>
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Facebook — posts in its slot
          </div>
          <p className="whitespace-pre-wrap text-xs text-slate-600">
            {derivative.fb_caption}
          </p>
        </div>
        <div>
          <div className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Instagram — 4h after Facebook
          </div>
          <p className="whitespace-pre-wrap text-xs text-slate-600">
            {derivative.ig_caption}
          </p>
        </div>
      </div>

      {(derivative.fb_affiliate_url || derivative.ig_affiliate_url) && (
        <div className="grid grid-cols-1 gap-3 border-t border-brand-border pt-4 sm:grid-cols-2">
          {derivative.fb_affiliate_url && (
            <CopyField
              label="DM link — Facebook"
              value={derivative.fb_affiliate_url}
              note={disclosure}
            />
          )}
          {derivative.ig_affiliate_url && (
            <CopyField
              label="DM link — Instagram"
              value={derivative.ig_affiliate_url}
              note={disclosure}
            />
          )}
        </div>
      )}
    </div>
  );
}
