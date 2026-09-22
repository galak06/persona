/**
 * Adding a product to the catalog.
 *
 * Adding and promoting are separate decisions and have separate controls: the
 * "make this the pick" checkbox defaults OFF, because with one product per
 * category, saving a new row would otherwise silently take the live promotion
 * away from whatever is currently running.
 */

import { useState } from "react";

import { getErrorMessage } from "../../api/client";
import { createProduct } from "../../api/products";

interface Props {
  focusCategory: string;
  onAdded: () => void;
}

const BLANK = { key: "", asin: "", display: "", category: "", notes: "" };

export default function AddProductForm({
  focusCategory,
  onAdded,
}: Props): React.JSX.Element {
  const [fields, setFields] = useState({ ...BLANK });
  const [selectForFocus, setSelectForFocus] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const set = (name: keyof typeof BLANK) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setFields((prev) => ({ ...prev, [name]: e.target.value }));

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      await createProduct({
        ...fields,
        category: fields.category || focusCategory,
        select_for_focus: selectForFocus,
      });
      setFields({ ...BLANK });
      setSelectForFocus(false);
      onAdded();
    } catch (err) {
      setError(getErrorMessage(err, "Could not add the product."));
    } finally {
      setSaving(false);
    }
  }

  const input =
    "w-full rounded-lg border border-stone-300 px-3 py-2 text-sm focus:border-amber-300 focus:ring focus:ring-amber-200/50";

  return (
    <form onSubmit={submit} className="mt-4 rounded-lg border border-stone-200 p-3">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block font-medium text-slate-700">Key</span>
          <input
            required
            value={fields.key}
            onChange={set("key")}
            placeholder="dental-brush-pro"
            className={input}
          />
          <span className="mt-1 block text-xs text-slate-500">
            Referenced by <code>[AFFILIATE:key]</code>. Permanent — a published post
            links to it, so it cannot be renamed later.
          </span>
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-slate-700">ASIN</span>
          <input
            required
            value={fields.asin}
            onChange={set("asin")}
            placeholder="B0FH8HQS3V"
            className={input}
          />
          <span className="mt-1 block text-xs text-slate-500">
            The 10-character id from amazon.com/dp/&lt;ASIN&gt;.
          </span>
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-slate-700">Name</span>
          <input value={fields.display} onChange={set("display")} className={input} />
        </label>

        <label className="text-sm">
          <span className="mb-1 block font-medium text-slate-700">
            Category{" "}
            <span className="font-normal text-slate-400">
              {focusCategory ? `(defaults to ${focusCategory})` : "(optional)"}
            </span>
          </span>
          <input value={fields.category} onChange={set("category")} className={input} />
        </label>

        <label className="text-sm sm:col-span-2">
          <span className="mb-1 block font-medium text-slate-700">Description</span>
          <input
            value={fields.notes}
            onChange={set("notes")}
            placeholder="Why this product — the selector reasons over this line."
            className={input}
          />
        </label>
      </div>

      {focusCategory && (
        <label className="mt-3 flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={selectForFocus}
            onChange={(e) => setSelectForFocus(e.target.checked)}
            className="h-4 w-4 accent-amber-500"
          />
          Promote this for {focusCategory} instead of the current pick
        </label>
      )}

      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}

      <button
        type="submit"
        disabled={saving}
        className="mt-3 rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-50"
      >
        {saving ? "Adding…" : "Add product"}
      </button>
    </form>
  );
}
