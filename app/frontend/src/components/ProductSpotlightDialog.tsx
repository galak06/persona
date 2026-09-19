/**
 * "New product spotlight" — pick a published article, pick the product, and
 * (optionally) pick which collection the photo is anchored on.
 *
 * One form, no steps. The three choices are not independent enough to earn a
 * wizard — the product list only means anything once an article is chosen,
 * and the photo category is a one-line override most runs never need — so
 * they stack, and the whole thing stays readable in one glance.
 *
 * The dialog fetches and validates; it does not create. `onSubmit` hands the
 * assembled body to `ProductSpotlightSection`, which owns the call, the
 * toasts and the refetch. `ConfirmDialog`'s `DialogAction` has no `disabled`
 * field, so the Confirm button validates inside its own `onClick` — an empty
 * post or product says so rather than firing a request that would 422.
 *
 * The cost note is not decoration. Every confirm spends a caption run and a
 * generated image, and the thing operators most need to know is what it does
 * NOT do: nothing here publishes.
 *
 * The caller MOUNTS this only while it is open, so a fresh dialog starts
 * from empty state for free — a product key belongs to whichever article was
 * chosen at the time, and clearing four fields in a close handler is the
 * cascading-render shape React's own lint rule exists to prevent.
 */

import { useState } from "react";
import {
  spotlightProductsUrl,
  spotlightSourcesUrl,
} from "../api/socialDerivatives";
import type {
  CreateSpotlightRequest,
  ProductScope,
  SpotlightProductsResponse,
  SpotlightSourcesResponse,
} from "../api/socialDerivatives";
import type { CategorySummary } from "../api/referenceImages";
import { useApiQuery } from "../hooks/useApiQuery";
import { useToast } from "./ui/Toast";
import ConfirmDialog from "./ui/ConfirmDialog";
import Spinner from "./ui/Spinner";
import SpotlightProductPicker from "./SpotlightProductPicker";

export interface ProductSpotlightDialogProps {
  open: boolean;
  onClose: () => void;
  /** Reference collections, for the optional photo-anchor override. */
  categories: CategorySummary[];
  onSubmit: (body: CreateSpotlightRequest) => void;
  /** A create is in flight; a second confirm is ignored rather than queued. */
  submitting: boolean;
}

export default function ProductSpotlightDialog({
  open,
  onClose,
  categories,
  onSubmit,
  submitting,
}: ProductSpotlightDialogProps): React.JSX.Element {
  const [ideaId, setIdeaId] = useState("");
  const [productKey, setProductKey] = useState("");
  const [scope, setScope] = useState<ProductScope>("category");
  const [referenceCategory, setReferenceCategory] = useState("");
  const { toast } = useToast();

  const sources = useApiQuery<SpotlightSourcesResponse>(
    open ? spotlightSourcesUrl() : null,
  );
  const products = useApiQuery<SpotlightProductsResponse>(
    open && ideaId ? spotlightProductsUrl(ideaId, scope) : null,
  );

  const visibleProducts = products.data?.products ?? [];
  // "Show all categories" refetches a different slice of the catalogue, and a
  // product picked from the wide list is not necessarily in the narrow one.
  // The selection is therefore DERIVED against the list actually on screen
  // rather than cleared on the flip: what shows as selected is exactly what
  // gets submitted — no invisible product can be created — and ticking the
  // box back restores the pick instead of having silently discarded it.
  // Clearing the state instead would have to happen either in an effect after
  // the refetch (`react-hooks/set-state-in-effect` is an error in this config)
  // or in the checkbox handler, which fires before the new list is known and
  // would also throw away a pick that is still perfectly visible.
  const selectedKey = visibleProducts.some((p) => p.key === productKey)
    ? productKey
    : "";

  const handleIdeaChange = (value: string): void => {
    setIdeaId(value);
    setProductKey(""); // the previous pick is from a different catalogue slice
  };

  const handleConfirm = (): void => {
    if (submitting) return;
    if (!ideaId) {
      toast.error("Pick a post", "A spotlight is cut from a published article.");
      return;
    }
    if (!selectedKey) {
      toast.error("Pick a product", "The spotlight is about exactly one product.");
      return;
    }
    onSubmit({
      idea_id: ideaId,
      product_key: selectedKey,
      format: "feed_post",
      reference_category: referenceCategory,
    });
  };

  const posts = sources.data?.posts ?? [];
  const stocked = categories.filter((c) => c.count > 0);

  return (
    <ConfirmDialog
      open={open}
      title="New product spotlight"
      actions={[
        { label: "Cancel", onClick: onClose, variant: "secondary" },
        {
          label: submitting ? "Creating…" : "Create spotlight",
          onClick: handleConfirm,
          variant: "primary",
        },
      ]}
      onClose={onClose}
    >
      <div className="space-y-3">
        <div className="space-y-1">
          <label
            htmlFor="spotlight-post"
            className="block text-xs font-semibold text-slate-700"
          >
            Post
          </label>
          <select
            id="spotlight-post"
            value={ideaId}
            onChange={(e) => handleIdeaChange(e.target.value)}
            className="w-full rounded border border-brand-border bg-white px-2 py-1 text-xs text-slate-700"
          >
            <option value="">
              {sources.loading ? "Loading posts…" : "Choose a published post…"}
            </option>
            {posts.map((post) => (
              <option key={post.idea_id} value={post.idea_id}>
                {post.topic}
              </option>
            ))}
          </select>
          {!sources.loading && posts.length === 0 && (
            <p className="text-[11px] text-slate-500">
              No published, in-focus post is available to cut a spotlight from.
            </p>
          )}
          {sources.error && (
            <p className="text-[11px] text-rose-600">{sources.error}</p>
          )}
        </div>

        {ideaId && (
          <>
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={scope === "all"}
                onChange={(e) => setScope(e.target.checked ? "all" : "category")}
              />
              Show all categories
            </label>

            {products.loading && !products.data ? (
              <p className="flex items-center gap-2 text-xs text-slate-500">
                <Spinner size="sm" className="text-slate-400" />
                Loading products…
              </p>
            ) : products.error ? (
              <p className="text-[11px] text-rose-600">{products.error}</p>
            ) : (
              <>
                {products.data?.post_scan === "unavailable" && (
                  <p className="text-[11px] text-slate-500">
                    The post could not be scanned, so no product is marked as
                    being in it. Everything below is still selectable.
                  </p>
                )}
                <SpotlightProductPicker
                  products={visibleProducts}
                  selectedKey={selectedKey}
                  onSelect={setProductKey}
                />
              </>
            )}
          </>
        )}

        <div className="space-y-1">
          <label
            htmlFor="spotlight-reference"
            className="block text-xs font-semibold text-slate-700"
          >
            Photo collection
          </label>
          <select
            id="spotlight-reference"
            value={referenceCategory}
            onChange={(e) => setReferenceCategory(e.target.value)}
            className="w-full rounded border border-brand-border bg-white px-2 py-1 text-xs text-slate-700"
          >
            <option value="">Let persona choose</option>
            {stocked.map((c) => (
              <option key={c.slug} value={c.slug}>
                {c.label} ({c.count})
              </option>
            ))}
          </select>
        </div>

        <p className="rounded bg-stone-100 px-2 py-1.5 text-[11px] leading-snug text-slate-600">
          Creates 1 caption run + 1 generated image. It lands in the review
          queue — nothing is published until you approve.
        </p>
      </div>
    </ConfirmDialog>
  );
}
