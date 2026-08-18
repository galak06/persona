// The library as it stands: one block per category, thumbnails inside.
// Deleting is the only destructive action here and it is irreversible, so it
// goes through ConfirmDialog rather than firing on click.

import { useState } from "react";

import { getErrorMessage, isHttpStatus } from "../../api/client";
import {
  deleteReferenceImage,
  importLegacyReference,
  referenceImageUrl,
} from "../../api/referenceImages";
import type { LibraryImage, LibraryResponse } from "../../api/referenceImages";
import Alert from "../ui/Alert";
import ConfirmDialog from "../ui/ConfirmDialog";
import EmptyState from "../ui/EmptyState";

interface ReferenceCategoryGridProps {
  library: LibraryResponse;
  disabled?: boolean;
  /** Refetch the library after a delete or an import. */
  onChanged: () => void;
}

interface CategoryBlock {
  slug: string;
  label: string;
  images: LibraryImage[];
}

/**
 * Declared categories first, then any category that only exists on an image
 * — a photo whose tag was declared elsewhere still has to be visible, or the
 * only way to find it is on disk.
 */
function toBlocks(library: LibraryResponse): CategoryBlock[] {
  const byCategory = new Map<string, LibraryImage[]>();
  for (const image of library.images) {
    const list = byCategory.get(image.category) ?? [];
    list.push(image);
    byCategory.set(image.category, list);
  }
  const blocks: CategoryBlock[] = library.categories.map((c) => ({
    slug: c.slug,
    label: c.label,
    images: byCategory.get(c.slug) ?? [],
  }));
  const declared = new Set(library.categories.map((c) => c.slug));
  for (const [slug, images] of byCategory) {
    if (!declared.has(slug)) blocks.push({ slug, label: slug, images });
  }
  return blocks;
}

export default function ReferenceCategoryGrid({
  library,
  disabled = false,
  onChanged,
}: ReferenceCategoryGridProps): React.JSX.Element {
  // Cache-buster fixed at mount: the raw route is `no-store`, but a browser
  // that already painted a thumbnail can still reuse it in-memory after the
  // id is reused (the store is content-addressed per category).
  const [bust] = useState(() => Math.floor(Date.now() / 30000));
  const [pending, setPending] = useState<LibraryImage | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const blocks = toBlocks(library);

  const handleDelete = async () => {
    if (!pending) return;
    const target = pending;
    setPending(null);
    setError("");
    setNotice("");
    try {
      await deleteReferenceImage(target.id);
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err, "Could not delete that photo."));
    }
  };

  const handleImportLegacy = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await importLegacyReference();
      setNotice("Imported the legacy reference photo.");
      onChanged();
    } catch (err) {
      if (isHttpStatus(err, 404)) {
        setNotice("Nothing to import — this brand has no legacy reference photo.");
      } else {
        setError(getErrorMessage(err, "Could not import the legacy reference."));
      }
    } finally {
      setBusy(false);
    }
  };

  const importButton = (
    <button
      type="button"
      disabled={disabled || busy}
      onClick={() => void handleImportLegacy()}
      className="rounded-lg border border-stone-300 bg-white px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-stone-50 disabled:opacity-50"
    >
      {busy ? "Importing…" : "Import legacy reference"}
    </button>
  );

  return (
    <div className="rounded-xl border border-stone-200 bg-white p-5 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h3 className="font-display text-base font-semibold text-slate-800">
          Reference library
        </h3>
        {importButton}
      </div>

      {error && <Alert status="error">{error}</Alert>}
      {notice && <Alert status="info">{notice}</Alert>}

      {library.images.length === 0 ? (
        <EmptyState
          title="No reference photos yet"
          description="Upload a few clear shots above. Until then, image generation falls back to whatever single legacy asset the brand has."
        />
      ) : (
        blocks.map((block) => (
          <section key={block.slug} className="border-t border-stone-100 pt-4 first:border-0 first:pt-0">
            <p className="text-sm font-medium text-slate-700">
              {block.label}{" "}
              <span className="font-normal text-slate-400">({block.images.length})</span>
            </p>
            {block.images.length === 0 ? (
              <EmptyState
                title="No photos in this category"
                description="Pick it in the upload card above to file one here."
                className="p-4!"
              />
            ) : (
              <div className="mt-2 flex flex-wrap gap-3">
                {block.images.map((image) => (
                  <figure key={image.id} className="w-28">
                    <img
                      src={`${referenceImageUrl(image)}?t=${bust}`}
                      alt={image.label || image.filename}
                      className="h-28 w-28 rounded-lg border border-stone-200 object-cover shadow-sm"
                    />
                    <figcaption className="mt-1">
                      <p
                        className="truncate text-[11px] text-slate-500"
                        title={image.label || image.filename}
                      >
                        {image.label || image.filename}
                      </p>
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => setPending(image)}
                        className="text-[11px] font-semibold text-rose-600 hover:text-rose-700 disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </figcaption>
                  </figure>
                ))}
              </div>
            )}
          </section>
        ))
      )}

      <ConfirmDialog
        open={pending !== null}
        title="Delete this reference photo?"
        actions={[
          { label: "Cancel", onClick: () => setPending(null), variant: "secondary" },
          { label: "Delete", onClick: () => void handleDelete(), variant: "primary" },
        ]}
        onClose={() => setPending(null)}
      >
        <span className="font-mono text-xs">{pending?.id}</span> is removed from the
        manifest and unlinked from disk. This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}
