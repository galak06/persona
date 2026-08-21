// One filed photo, with the three things the operator ever needs to correct on
// it: which tag it carries, and whether each of the brand's own subjects — its
// mascot and its persona, the person behind it — is actually in the frame.
//
// The two flags are independent checkboxes rather than one control, because a
// photo may show the person, the mascot, both, or neither, and the identity
// instruction sent to the image model names whichever are ticked. Ticking the
// wrong one asks the model to reproduce a subject that is not in the picture.
//
// Both edits are PATCHes, and a category change re-homes the file — the id is
// `"<category>/<filename>"`, so the entry comes back under a different id and
// in a different block. This component therefore never mutates anything
// locally: it reports the edit upward and the parent refetches.

import type { ImageUpdate, LibraryImage } from "../../api/referenceImages";
import { referenceImageUrl } from "../../api/referenceImages";
import { subjectsShown } from "./subjects";

export interface CategoryOption {
  slug: string;
  label: string;
}

interface ReferenceImageTileProps {
  image: LibraryImage;
  /** Every tag the library knows about, declared or only used by a photo. */
  options: CategoryOption[];
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
  bust,
  disabled,
  saving,
  onPatch,
  onDelete,
}: ReferenceImageTileProps): React.JSX.Element {
  const name = image.label || image.filename;
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

        {(image.shows_mascot || image.shows_persona) && (
          <p className="rounded bg-emerald-100 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-800">
            Shows {subjectsShown(image)}
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
          The mascot is in this photo
        </label>

        <label className="flex items-center gap-1.5 text-[11px] text-slate-600">
          <input
            type="checkbox"
            checked={image.shows_persona}
            disabled={locked}
            onChange={(e) => onPatch(image, { shows_persona: e.target.checked })}
            className="h-3.5 w-3.5 rounded border-stone-300 text-amber-600 focus:ring-amber-300"
          />
          The persona is in this photo
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
