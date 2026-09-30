/**
 * The product list inside `ProductSpotlightDialog` — the step the owner
 * asked for in so many words: "I can select different products from a list".
 *
 * Split out of the dialog because it is the only part that is really a
 * widget: a filter, two kinds of group, and a single selection over what can
 * be hundreds of rows once "show all categories" is ticked. The dialog keeps
 * the fetching and the decision; this keeps the list.
 *
 * Grouping is the whole argument of the screen. "In this post" are the
 * products the article actually links — the safe, obvious picks, so they go
 * first and stay first. Everything else is grouped under its catalogue
 * category, because a spotlight on a product the article never mentioned is
 * allowed (it just earns a `product_not_in_post` flag on the card) and the
 * category is the only thing that makes those rows navigable.
 *
 * Real radio inputs rather than styled divs: one keystroke moves between
 * options, the label reads out with the row, and the selection survives a
 * screen reader — none of which a clickable `<div>` gets for free.
 */

import { useId, useMemo, useState } from "react";
import type { SpotlightProduct } from "../api/socialDerivatives";

interface ProductGroup {
  label: string;
  products: SpotlightProduct[];
}

/**
 * "In this post" first, then one group per catalogue category in the order
 * the server returned them (it already sorts in-post first, then display).
 */
function groupProducts(products: SpotlightProduct[]): ProductGroup[] {
  const inPost = products.filter((p) => p.in_post);
  const groups: ProductGroup[] = inPost.length
    ? [{ label: "In this post", products: inPost }]
    : [];
  const byCategory = new Map<string, SpotlightProduct[]>();
  for (const product of products) {
    if (product.in_post) continue;
    const key = product.category || "Uncategorised";
    const bucket = byCategory.get(key);
    if (bucket) bucket.push(product);
    else byCategory.set(key, [product]);
  }
  for (const [category, entries] of byCategory) {
    groups.push({ label: `Catalog — ${category}`, products: entries });
  }
  return groups;
}

export interface SpotlightProductPickerProps {
  products: SpotlightProduct[];
  selectedKey: string;
  onSelect: (productKey: string) => void;
}

export default function SpotlightProductPicker({
  products,
  selectedKey,
  onSelect,
}: SpotlightProductPickerProps): React.JSX.Element {
  const [filter, setFilter] = useState("");
  const filterId = useId();
  const groupName = useId();

  const groups = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const matching = needle
      ? products.filter((p) => p.display.toLowerCase().includes(needle))
      : products;
    return groupProducts(matching);
  }, [products, filter]);

  return (
    <div className="space-y-2">
      <label
        htmlFor={filterId}
        className="block text-xs font-semibold text-slate-700"
      >
        Product
      </label>
      <input
        id={filterId}
        type="search"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        placeholder="Filter by name…"
        className="w-full rounded border border-brand-border bg-white px-2 py-1 text-xs text-slate-700"
      />
      {/* Capped height on purpose: "all categories" can be hundreds of rows,
          and a dialog that grows past the viewport hides its own buttons. */}
      <div
        role="radiogroup"
        aria-label="Product"
        className="max-h-56 space-y-2 overflow-y-auto rounded border border-brand-border bg-stone-50 p-2"
      >
        {groups.length === 0 ? (
          <p className="px-1 py-2 text-xs text-slate-500">
            No product matches that filter.
          </p>
        ) : (
          groups.map((group) => (
            <div key={group.label}>
              <p className="px-1 pb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                {group.label}
              </p>
              <div className="space-y-1">
                {group.products.map((product) => (
                  <label
                    key={product.key}
                    className={`flex cursor-pointer items-start gap-2 rounded px-2 py-1.5 hover:bg-white ${
                      selectedKey === product.key ? "bg-white shadow-card" : ""
                    }`}
                  >
                    <input
                      type="radio"
                      name={groupName}
                      value={product.key}
                      checked={selectedKey === product.key}
                      onChange={() => onSelect(product.key)}
                      className="mt-0.5 shrink-0"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="text-xs font-medium text-slate-800">
                          {product.display}
                        </span>
                        {product.certification_verified && (
                          <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-semibold text-emerald-700">
                            certification verified
                          </span>
                        )}
                      </span>
                      {product.notes && (
                        <span className="mt-0.5 block truncate text-[11px] text-slate-500">
                          {product.notes}
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
