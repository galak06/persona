// Mascot reference library, mounted on Brand Settings. Owns the fetch and
// hands `refetch` to both children so an upload or a delete refreshes the
// grid without either child knowing how the data arrived.

import { useState } from "react";

import { endpoints } from "../../api/endpoints";
import type { LibraryResponse } from "../../api/mascotLibrary";
import { useApiQuery } from "../../hooks/useApiQuery";
import Alert from "../ui/Alert";
import ErrorState from "../ui/ErrorState";
import LoadingState from "../ui/LoadingState";
import MascotCategoryGrid from "./MascotCategoryGrid";
import MascotUploadCard from "./MascotUploadCard";

interface MascotLibrarySectionProps {
  /** The brand id in the URL — what this page claims to be editing. */
  brandId: string;
}

const BRAND_STORAGE_KEY = "social_automation_selected_brand";

const EMPTY_LIBRARY: LibraryResponse = { categories: [], images: [] };

export default function MascotLibrarySection({
  brandId,
}: MascotLibrarySectionProps): React.JSX.Element {
  // Read once at mount: this is the same value `apiClient`'s interceptor
  // reads for `X-Brand`, so it is the brand every request below will
  // actually hit — regardless of which brand the URL names.
  const [activeBrand] = useState(() => localStorage.getItem(BRAND_STORAGE_KEY) ?? "");
  const mismatch = activeBrand !== brandId;

  const { data, loading, error, refetch } = useApiQuery<LibraryResponse>(
    endpoints.mascotLibrary,
    { enabled: !mismatch },
  );

  const library = data ?? EMPTY_LIBRARY;

  return (
    <section className="rounded-xl border border-stone-200 bg-white p-5 space-y-4">
      <header>
        <h2 className="font-display text-lg font-semibold text-slate-800">
          Mascot reference photos
        </h2>
        <p className="text-sm text-slate-500">
          Real photos of the mascot, tagged by category. Image generation picks
          the reference whose tag matches what it is drawing, so a fuller
          library means generated images that keep looking like the same dog.
        </p>
      </header>

      {mismatch ? (
        <Alert status="warning" title="Not the active brand">
          This page is showing <code className="font-mono text-xs">{brandId}</code>, but
          the app&rsquo;s active brand is{" "}
          <code className="font-mono text-xs">{activeBrand || "none selected"}</code>.
          Uploads and deletes here would go to{" "}
          <code className="font-mono text-xs">{activeBrand || "the server default"}</code>{" "}
          instead. Switch brands to manage{" "}
          <code className="font-mono text-xs">{brandId}</code>&rsquo;s reference photos.
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

      <MascotUploadCard
        categories={library.categories}
        disabled={mismatch}
        onUploaded={() => void refetch()}
      />
      <MascotCategoryGrid
        library={library}
        disabled={mismatch}
        onChanged={() => void refetch()}
      />
    </section>
  );
}
