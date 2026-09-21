/**
 * The affiliate product catalog, as Brand Settings renders it.
 *
 * What this panel is for: choosing the ONE product the current focus run
 * promotes. The brand commits to a single category for months, and the
 * selector will put the chosen product into every post it drafts for that
 * run — so the screen is a radio list, and the empty state is a warning
 * rather than a shrug (no pick means posts ship with no product block).
 *
 * Selecting re-reads the whole list rather than patching one row, because a
 * selection DISPLACES the incumbent server-side. Patching locally would leave
 * two rows claiming the category until the next refresh.
 */

import { useState } from "react";

import { getErrorMessage } from "../../api/client";
import { endpoints } from "../../api/endpoints";
import {
  selectProduct,
  setProductActive,
  type ProductsResponse,
} from "../../api/products";
import { useApiQuery } from "../../hooks/useApiQuery";
import AddProductForm from "./AddProductForm";
import ProductRow from "./ProductRow";

export default function ProductsPanel(): React.JSX.Element {
  const { data, error: loadError, refetch } = useApiQuery<ProductsResponse>(
    endpoints.products,
  );
  const [saveError, setSaveError] = useState("");
  const [busy, setBusy] = useState(false);
  const [showInactive, setShowInactive] = useState(false);
  const error = saveError || loadError;

  async function run(action: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    setSaveError("");
    try {
      await action();
      refetch();
    } catch (err) {
      setSaveError(getErrorMessage(err, "That change could not be saved."));
    } finally {
      setBusy(false);
    }
  }

  if (!data) {
    return (
      <section className="mt-8">
        <h2 className="text-lg font-semibold text-slate-800">Products</h2>
        <p className="mt-1 text-sm text-slate-500">
          {error || "Loading the product catalog…"}
        </p>
      </section>
    );
  }

  const focus = data.focus_category ?? "";
  // Defaulted fields are optional in the generated schema, so they are
  // narrowed once here rather than at each of the four use sites.
  const all = data.products ?? [];
  // Selectable first, then whatever is merely tagged with the focus category:
  // the operator is choosing from a shortlist, not browsing 38 products.
  const visible = all
    .filter((p) => p.active || showInactive)
    .sort((a, b) => {
      const rank = (x: typeof a): number =>
        (x.selected ? 0 : x.in_focus_category ? 1 : 2) + (x.active ? 0 : 4);
      return rank(a) - rank(b) || a.display.localeCompare(b.display);
    });
  const inactiveCount = all.filter((p) => !p.active).length;

  return (
    <section className="mt-8">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold text-slate-800">Products</h2>
        {inactiveCount > 0 && (
          <button
            type="button"
            onClick={() => setShowInactive((v) => !v)}
            className="text-xs text-slate-500 underline-offset-2 hover:underline"
          >
            {showInactive ? "Hide" : `Show ${inactiveCount} inactive`}
          </button>
        )}
      </div>

      {focus ? (
        <p className="mt-1 text-sm text-slate-500">
          One product is promoted for <strong>{focus}</strong>, in every post this
          focus run drafts. Choosing a different one replaces it.
        </p>
      ) : (
        <p className="mt-1 text-sm text-amber-700">
          This brand has no focus category, so there is nothing to select a product
          for. Set one above and save first.
        </p>
      )}

      {focus && (data.selected_count ?? 0) === 0 && (
        <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          No product is selected for {focus}. Posts drafted for this run will ship
          with no product block at all until you pick one.
        </p>
      )}

      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}

      {visible.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500">
          No products in this brand&rsquo;s catalog yet.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {visible.map((product) => (
            <ProductRow
              key={product.key}
              product={product}
              focusCategory={focus}
              busy={busy}
              onSelect={(key) => void run(() => selectProduct(key, true))}
              onToggleActive={(key, active) =>
                void run(() => setProductActive(key, active))
              }
            />
          ))}
        </ul>
      )}

      <AddProductForm focusCategory={focus} onAdded={() => refetch()} />
    </section>
  );
}
