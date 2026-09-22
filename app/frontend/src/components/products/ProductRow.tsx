/**
 * One product in the catalog panel.
 *
 * The selection control is a RADIO, deliberately: a focus category names at
 * most one product, and a checkbox would suggest the operator can promote
 * three things at once and then silently un-pick two of them. The radio makes
 * the displacement legible before it happens.
 */

import type { Product } from "../../api/products";

interface Props {
  product: Product;
  /** "" when the brand has no focus declared — selection is then disabled. */
  focusCategory: string;
  busy: boolean;
  onSelect: (key: string) => void;
  onToggleActive: (key: string, active: boolean) => void;
}

export default function ProductRow({
  product,
  focusCategory,
  busy,
  onSelect,
  onToggleActive,
}: Props): React.JSX.Element {
  const selectable = Boolean(focusCategory) && product.active;
  return (
    <li
      className={`flex items-start gap-3 rounded-lg border px-3 py-2.5 ${
        product.selected
          ? "border-amber-300 bg-amber-50/60"
          : "border-stone-200 bg-white"
      } ${product.active ? "" : "opacity-60"}`}
    >
      <input
        type="radio"
        name="focus-product"
        checked={product.selected}
        disabled={!selectable || busy}
        onChange={() => onSelect(product.key)}
        aria-label={`Promote ${product.display} for ${focusCategory || "the focus category"}`}
        className="mt-1 h-4 w-4 accent-amber-500 disabled:cursor-not-allowed"
      />

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-slate-800">{product.display}</span>
          <code className="rounded bg-stone-100 px-1.5 py-0.5 text-xs text-slate-600">
            {product.key}
          </code>
          {product.in_focus_category && !product.selected && (
            <span className="rounded bg-stone-100 px-1.5 py-0.5 text-xs text-slate-500">
              in {product.category}
            </span>
          )}
          {!product.active && (
            <span className="rounded bg-stone-200 px-1.5 py-0.5 text-xs text-slate-600">
              inactive
            </span>
          )}
        </div>
        {product.notes && (
          <p className="mt-0.5 truncate text-xs text-slate-500">{product.notes}</p>
        )}
      </div>

      {/* Given a real button's border and hover: as bare grey text it sat
          exactly where the "inactive" badge sits and read as a STATUS -- "this
          product is deactivated" -- rather than as the action it is. */}
      <button
        type="button"
        disabled={busy}
        onClick={() => onToggleActive(product.key, !product.active)}
        title={
          product.active
            ? `Stop ${product.display} being offered to the writer. It stays linkable from posts that already reference it.`
            : `Allow ${product.display} to be selected again.`
        }
        className="shrink-0 rounded-md border border-stone-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-600 hover:border-stone-400 hover:bg-stone-50 hover:text-slate-800 disabled:opacity-50"
      >
        {product.active ? "Deactivate" : "Restore"}
      </button>
    </li>
  );
}
