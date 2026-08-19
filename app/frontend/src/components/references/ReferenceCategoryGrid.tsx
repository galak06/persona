// The library as it stands: one block per category, editable tiles inside.
//
// Two things are correctable here, because the tags come from a model rather
// than from the operator: the category a photo carries, and whether the
// brand's own mascot is in it. Both are PATCHes, and a category change moves the
// photo to another block under a NEW id — so every edit refetches instead of
// patching local state.
//
// Deleting is the only destructive action here and it is irreversible, so it
// goes through ConfirmDialog rather than firing on click.

import { useState } from "react";

import { getErrorMessage, isHttpStatus } from "../../api/client";
import {
  deleteReferenceImage,
  importLegacyReference,
  updateReferenceImage,
} from "../../api/referenceImages";
import type { ImageUpdate, LibraryImage, LibraryResponse } from "../../api/referenceImages";
import Alert from "../ui/Alert";
import ConfirmDialog from "../ui/ConfirmDialog";
import EmptyState from "../ui/EmptyState";
import ReferenceImageTile from "./ReferenceImageTile";
import type { CategoryOption } from "./ReferenceImageTile";

interface ReferenceCategoryGridProps {
  library: LibraryResponse;
  disabled?: boolean;
  /** The mascot's real name, for the flag's copy. Empty/absent → generic. */
  mascotName?: string;
  /** Refetch the library after an edit, a delete or an import. */
  onChanged: () => void;
}

interface CategoryBlock extends CategoryOption {
  images: LibraryImage[];
}

/**
 * Declared categories first, then any category that only exists on an image
 * — a photo whose tag was declared elsewhere still has to be visible, or the
 * only way to find it is on disk. Doubles as the re-tag selector's option
 * list, which is why the shape is `CategoryOption` plus its photos.
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
  mascotName,
  onChanged,
}: ReferenceCategoryGridProps): React.JSX.Element {
  const mascot = mascotName?.trim() ?? "";
  // Cache-buster fixed at mount: the raw route is `no-store`, but a browser
  // that already painted a thumbnail can still reuse it in-memory after the
  // id is reused (the store is content-addressed per category).
  const [bust] = useState(() => Math.floor(Date.now() / 30000));
  const [pending, setPending] = useState<LibraryImage | null>(null);
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  const blocks = toBlocks(library);
  const options: CategoryOption[] = blocks.map(({ slug, label }) => ({ slug, label }));

  const handlePatch = async (image: LibraryImage, patch: ImageUpdate) => {
    setError("");
    setNotice("");
    setSaving(image.id);
    try {
      // The response carries the photo's new id when the category changed.
      // Nothing here holds onto it — the refetch below re-renders every tile
      // from the server, which is the only place that id is authoritative.
      await updateReferenceImage(image.id, patch);
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err, "Could not update that photo."));
    } finally {
      setSaving("");
    }
  };

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

      <Alert
        status="info"
        title={`Check the ${mascot || "mascot"} flag on every photo`}
      >
        A photo marked as showing {mascot || "the brand’s mascot"} is sent with an
        instruction to reproduce <em>that exact subject</em>; every other photo is used
        only for setting, styling and composition. Getting this wrong is the one mistake that
        shows up in generated images, so correct a wrong tag or flag here.
      </Alert>

      {error && <Alert status="error">{error}</Alert>}
      {notice && <Alert status="info">{notice}</Alert>}

      {library.images.length === 0 ? (
        <EmptyState
          title="No reference photos yet"
          description="Only photos uploaded here can be used to generate an image. Until you add some, posts keep their own hero image and no AI image is generated."
        />
      ) : (
        blocks.map((block) => (
          <section
            key={block.slug}
            className="border-t border-stone-100 pt-4 first:border-0 first:pt-0"
          >
            <p className="text-sm font-medium text-slate-700">
              {block.label}{" "}
              <span className="font-normal text-slate-400">({block.images.length})</span>
            </p>
            {block.images.length === 0 ? (
              <EmptyState
                title="No photos in this category"
                description="Re-tag a photo into it below, or upload one and let the tagger pick it."
                className="p-4!"
              />
            ) : (
              <div className="mt-2 flex flex-wrap gap-3">
                {block.images.map((image) => (
                  <ReferenceImageTile
                    key={image.id}
                    image={image}
                    options={options}
                    mascotName={mascot}
                    bust={bust}
                    disabled={disabled}
                    saving={saving === image.id}
                    onPatch={(target, patch) => void handlePatch(target, patch)}
                    onDelete={setPending}
                  />
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
