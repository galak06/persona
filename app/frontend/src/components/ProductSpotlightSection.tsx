/**
 * The product-spotlight block on the Social Posts page: its own review queue,
 * its own create button, and the polling that makes a compose run visible
 * while it happens.
 *
 * Self-contained on purpose. `pages/SocialPosts.tsx` was already at the line
 * limit before this existed, and a spotlight shares nothing with a regular
 * post except the page it sits on — different table, different routes,
 * different decisions. The page hands it the reference collections it has
 * already fetched and otherwise knows nothing about it.
 *
 * Polling is conditional rather than constant: the list refetches on a 5 s
 * timer only while some row is still `composing`, which is the only time it
 * can change without the operator doing something. A poll left running
 * behind a page nobody is looking at is exactly the kind of thing that
 * survives for weeks unnoticed, so the timer is tied to that one condition
 * and torn down by the same effect's cleanup on unmount.
 */

import { useCallback, useEffect, useState } from "react";
import {
  approveSocialDerivative,
  createSpotlight,
  duplicateSpotlightId,
  rejectSocialDerivative,
  socialDerivativesUrl,
  unscheduleSocialDerivative,
} from "../api/socialDerivatives";
import type {
  CreateSpotlightRequest,
  SocialDerivativesResponse,
} from "../api/socialDerivatives";
import type { CategorySummary } from "../api/referenceImages";
import { getErrorMessage } from "../api/client";
import { useApiQuery } from "../hooks/useApiQuery";
import { useToast } from "./ui/Toast";
import ProductSpotlightDialog from "./ProductSpotlightDialog";
import SpotlightCard from "./SpotlightCard";
import type { SpotlightAction } from "./SpotlightCard";
import EmptyState from "./ui/EmptyState";
import ErrorState from "./ui/ErrorState";

const POLL_MS = 5_000;

const DECISIONS = {
  approve: approveSocialDerivative,
  unschedule: unscheduleSocialDerivative,
  reject: rejectSocialDerivative,
} as const;

const DECISION_NOTES: Record<SpotlightAction, string> = {
  approve: "Spotlight scheduled",
  unschedule: "Spotlight returned to the queue",
  reject: "Spotlight rejected",
};

export interface ProductSpotlightSectionProps {
  /** Reference collections, forwarded to the dialog's photo-anchor select. */
  categories: CategorySummary[];
}

export default function ProductSpotlightSection({
  categories,
}: ProductSpotlightSectionProps): React.JSX.Element {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const { toast } = useToast();

  const { data, loading, error, refetch } = useApiQuery<SocialDerivativesResponse>(
    socialDerivativesUrl("queued"),
  );
  const rows = data?.derivatives ?? [];
  const anyComposing = rows.some((d) => d.composing);
  const anyPublishing = rows.some(
    (d) => d.status === "fb_publishing" || d.status === "ig_publishing",
  );

  // A composing row is the only thing that changes without the operator
  // doing something, so the timer exists exactly while one does. The effect
  // re-runs when that flips and the cleanup clears the interval — both when
  // the last run lands and when the page unmounts. `refetch` is stable, so
  // this is not a per-render timer churn.
  useEffect(() => {
    if (!anyComposing) return;
    const id = window.setInterval(() => void refetch(), POLL_MS);
    return () => window.clearInterval(id);
  }, [anyComposing, refetch]);

  const handleCreate = useCallback(
    async (body: CreateSpotlightRequest): Promise<void> => {
      setCreating(true);
      try {
        await createSpotlight(body);
        setDialogOpen(false);
        toast.success(
          "Spotlight queued",
          "Composing takes a couple of minutes — the card appears below.",
        );
      } catch (err) {
        // A duplicate is not really a failure: the thing the operator wanted
        // already exists, and refetching puts it on screen.
        if (duplicateSpotlightId(err)) {
          setDialogOpen(false);
          toast.info(
            "That spotlight already exists",
            "This post already has an active spotlight for that product.",
          );
        } else {
          toast.error("Could not create the spotlight", getErrorMessage(err));
        }
      } finally {
        setCreating(false);
        void refetch();
      }
    },
    [refetch, toast],
  );

  const handleDecision = useCallback(
    async (id: string, action: SpotlightAction): Promise<void> => {
      setBusyIds((prev) => new Set(prev).add(id));
      try {
        await DECISIONS[action](id);
        toast.success(DECISION_NOTES[action]);
      } catch (err) {
        toast.error(`Could not ${action} the spotlight`, getErrorMessage(err));
      } finally {
        setBusyIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
        void refetch();
      }
    },
    [refetch, toast],
  );

  return (
    <div className="space-y-4">
      <div className="bg-brand-surface flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-brand-border px-5 py-4 shadow-card">
        <div className="flex items-baseline gap-3">
          <span className="text-2xl font-bold leading-none text-slate-900">
            {rows.length}
          </span>
          {/* The count is of the rows actually on screen, and the queue view
              holds more than drafts: a row can be composing, or held by the
              publisher. "Awaiting review" would be a lie about those. */}
          <span className="text-sm text-slate-500">
            product spotlights in the review queue
            {anyComposing && " · composing…"}
            {anyPublishing && " · publishing…"}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setDialogOpen(true)}
          title="Cut a product spotlight from a published article — costs one caption run and one image"
          className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-amber-700"
        >
          ✨ New product spotlight
        </button>
      </div>

      {/* A failed fetch is a banner, never a replacement. The list polls every
          5 s while a spotlight composes, so one 500 in that window used to
          blank every card while the header went on counting them — the
          operator reads that as "my queue is gone", which it is not. The last
          rows that loaded stay on screen and the error sits above them; it
          only stands alone when there is nothing to keep. */}
      {error && (
        <ErrorState
          title="Could not load product spotlights"
          message={
            rows.length > 0
              ? `${error} Showing the last list that loaded.`
              : error
          }
          onRetry={() => void refetch()}
          retrying={loading}
        />
      )}

      {rows.length > 0 ? (
        <div className="space-y-4">
          {rows.map((derivative) => (
            <SpotlightCard
              key={derivative.id}
              derivative={derivative}
              onDecision={(id, action) => void handleDecision(id, action)}
              busy={busyIds.has(derivative.id)}
            />
          ))}
        </div>
      ) : (
        !error && (
          <EmptyState
            title="No product spotlights pending review"
            description="A spotlight is a second post cut from an article that already shipped: one product, one generated photo, and a DM link. Click ✨ New product spotlight to make one."
          />
        )
      )}

      {/* Mounted only while open: closing it discards the half-filled form,
          which is what the operator means by Cancel. */}
      {dialogOpen && (
        <ProductSpotlightDialog
          open
          onClose={() => setDialogOpen(false)}
          categories={categories}
          onSubmit={(body) => void handleCreate(body)}
          submitting={creating}
        />
      )}
    </div>
  );
}
