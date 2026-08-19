// One filed photo, with the two things the operator ever needs to correct on
// it: which tag it carries, and whether the brand's own mascot is in it.
//
// Both edits are PATCHes, and a category change re-homes the file — the id is
// `"<category>/<filename>"`, so the entry comes back under a different id and
// in a different block. This component therefore never mutates anything
// locally: it reports the edit upward and the parent refetches.

import type { ImageUpdate, LibraryImage } from "../../api/referenceImages";
import { referenceImageUrl } from "../../api/referenceImages";

export interface CategoryOption {
  slug: string;
  label: string;
}

interface ReferenceImageTileProps {
  image: LibraryImage;
  /** Every tag the library knows about, declared or only used by a photo. */
  options: CategoryOption[];
  /**
   * The mascot's real name, so the flag reads "Shows <that name>" rather than
   * "Shows the mascot". Already trimmed by the grid; empty when the brand has
   * no mascot name (or its record has not arrived), which falls back to the
   * generic wording — never "Shows " with a blank after it, and never a guess
   * at what kind of thing the mascot is.
   */
  mascotName?: string;
  /** Mount-time cache-buster, shared across every tile in one render. */
  bust: number;
  disabled: boolean;
  /** True while this tile's own edit is in flight. */
  saving: boolean;
  onPatch: (image: LibraryImage, patch: ImageUpdate) => void;
  onDelete: (image: LibraryImage) => void;
}

export default function ReferenceImageTile({
  image,
  options,
  mascotName,
  bust,
  disabled,
  saving,
  onPatch,
  onDelete,
}: ReferenceImageTileProps): React.JSX.Element {
  const name = image.label || image.filename;
  const mascot = mascotName?.trim() ?? "";
  const locked = disabled || saving;
  // A photo can carry a tag no summary lists (hand-edited manifest); keep it
  // selectable rather than silently re-tagging it on the next change.
  const known = options.some((o) => o.slug === image.category);

  return (
    <figure className="w-48 rounded-lg border border-stone-200 p-2 shadow-sm">
      <img
        src={`${referenceImageUrl(image)}?t=${bust}`}
        alt={name}
        className="h-32 w-full rounded object-cover"
      />
      <figcaption className="mt-2 space-y-1.5">
        <p className="truncate text-[11px] font-semibold text-slate-600" title={name}>
          {name}
        </p>
        <p
          className="line-clamp-2 text-[11px] text-slate-500"
          title={image.description || "No description — the vision pass had no opinion."}
        >
          {image.description || "No description yet."}
        </p>

        {image.shows_mascot && (
          <p className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-800">
            {mascot ? `Shows ${mascot}` : "Shows the mascot"}
          </p>
        )}

        <label className="block text-[11px] text-slate-500">
          <span className="sr-only">Category for {name}</span>
          <select
            value={image.category}
            disabled={locked}
            onChange={(e) => onPatch(image, { category: e.target.value })}
            className="w-full rounded border border-stone-300 px-1.5 py-1 text-[11px] focus:border-amber-300 focus:ring focus:ring-amber-200/50 disabled:bg-stone-50"
          >
            {!known && <option value={image.category}>{image.category}</option>}
            {options.map((option) => (
              <option key={option.slug} value={option.slug}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
          <input
            type="checkbox"
            checked={image.shows_mascot}
            disabled={locked}
            onChange={(e) => onPatch(image, { shows_mascot: e.target.checked })}
            className="h-3.5 w-3.5 rounded border-stone-300 text-amber-600 focus:ring-amber-300"
          />
          {mascot ? `${mascot} is in this photo` : "The brand’s mascot is in this photo"}
        </label>

        <div className="flex items-center justify-between gap-2">
          <button
            type="button"
            disabled={locked}
            onClick={() => onDelete(image)}
            className="text-[11px] font-semibold text-rose-600 hover:text-rose-700 disabled:opacity-50"
          >
            Delete
          </button>
          {saving && <span className="text-[11px] text-amber-700">Saving…</span>}
        </div>
      </figcaption>
    </figure>
  );
}
