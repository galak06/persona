// Reference-image library, mounted on Brand Settings. Owns the fetch and
// hands `refetch` to both children so an upload or a delete refreshes the
// grid without either child knowing how the data arrived.

import { useEffect, useState } from "react";

import { endpoints } from "../../api/endpoints";
import type { LibraryResponse } from "../../api/referenceImages";
import { useBrand } from "../../context/BrandContext";
import { useApiQuery } from "../../hooks/useApiQuery";
import Alert from "../ui/Alert";
import ErrorState from "../ui/ErrorState";
import LoadingState from "../ui/LoadingState";
import ReferenceCategoryGrid from "./ReferenceCategoryGrid";
import ReferenceUploadCard from "./ReferenceUploadCard";

interface ReferenceLibrarySectionProps {
  /** The brand id in the URL — what this page claims to be editing. */
  brandId: string;
}

const BRAND_STORAGE_KEY = "social_automation_selected_brand";

const EMPTY_LIBRARY: LibraryResponse = { categories: [], images: [] };

/**
 * The brand `apiClient` will stamp on every request below as `X-Brand` — and,
 * when nothing is selected at all, this page's own brand, claimed here.
 *
 * An empty selection is not a CONFLICTING selection. The guard below exists so
 * that uploads made while looking at brand B can't land on brand A, but with
 * nothing selected there is no brand A: the request would simply fall through
 * to the server's default. Blocking that case disabled the whole card on every
 * fresh browser, so instead we adopt the brand the URL already names.
 *
 * Written during render, before `useApiQuery`'s effect fires, so even the very
 * first GET carries the header.
 */
function claimActiveBrand(brandId: string): string {
  const stored = localStorage.getItem(BRAND_STORAGE_KEY) ?? "";
  if (stored) return stored;
  localStorage.setItem(BRAND_STORAGE_KEY, brandId);
  return brandId;
}

export default function ReferenceLibrarySection({
  brandId,
}: ReferenceLibrarySectionProps): React.JSX.Element {
  const { adoptBrand, setSelectedBrand } = useBrand();
  // Resolved once at mount: whichever brand this is, it is the one every
  // request below will actually hit — regardless of which brand the URL names.
  const [activeBrand] = useState(() => claimActiveBrand(brandId));
  const mismatch = activeBrand !== brandId;

  // Claiming the storage key is only half an adoption: the sidebar's selector
  // renders from the context, which read that key before we wrote it. Tell it
  // — via the non-reloading setter, since a reload at mount would bounce the
  // page every time someone opened it with no brand selected.
  useEffect(() => {
    if (!mismatch) adoptBrand(brandId);
  }, [mismatch, brandId, adoptBrand]);

  const { data, loading, error, refetch } = useApiQuery<LibraryResponse>(
    endpoints.referenceLibrary,
    { enabled: !mismatch },
  );

  const library = data ?? EMPTY_LIBRARY;

  return (
    <section className="rounded-xl border border-stone-200 bg-white p-5 space-y-4">
      <header>
        <h2 className="font-display text-lg font-semibold text-slate-800">
          Reference photos
        </h2>
        <p className="text-sm text-slate-500">
          Real photos to ground generated imagery &mdash; the brand&rsquo;s mascot,
          but also products, locations, people, settings, style shots. Every
          upload is described, tagged and mascot-flagged automatically; image
          generation then picks the reference whose tag matches what it is drawing.
          These uploads are the <strong>only</strong> photos allowed to anchor a
          generated image: with nothing here that matches, the post keeps its own
          hero image and nothing is generated. A fuller library means more posts get
          real generated imagery.
        </p>
      </header>

      {mismatch ? (
        <Alert status="warning" title="Not the active brand">
          <p>
            This page is showing <code className="font-mono text-xs">{brandId}</code>,
            but the app&rsquo;s active brand is{" "}
            <code className="font-mono text-xs">{activeBrand}</code>. Uploads and
            deletes here would go to{" "}
            <code className="font-mono text-xs">{activeBrand}</code> instead.
          </p>
          <button
            type="button"
            onClick={() => setSelectedBrand(brandId)}
            className="mt-2 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-50"
          >
            Switch to this brand
          </button>
        </Alert>
      ) : (
        <>
          {loading && !data && <LoadingState message="Loading reference library…" />}
          {error && (
            <ErrorState
              message={`Could not load the reference library: ${error}`}
              onRetry={() => void refetch()}
              retrying={loading}
            />
          )}
        </>
      )}

      <ReferenceUploadCard disabled={mismatch} onUploaded={() => void refetch()} />
      <ReferenceCategoryGrid
        library={library}
        disabled={mismatch}
        onChanged={() => void refetch()}
      />
    </section>
  );
}
