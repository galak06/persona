import { useState } from "react";
import { mediaUrl, type RecipeMedia } from "../api/recipes";

/** File name (basename) of a BRAND_DIR-relative media path. */
function baseName(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

/**
 * Collapse duplicate media to one entry per file name, preferring the live
 * `recipe_artifacts` copy over the `_migrated_backup` copy. The manifest lists
 * both on purpose (the DB knows about every file); the drawer shows each once.
 */
function dedupePreferLive(paths: string[]): string[] {
  const byName = new Map<string, string>();
  for (const path of paths) {
    const name = baseName(path);
    const existing = byName.get(name);
    if (!existing || (existing.includes("_migrated_backup") && !path.includes("_migrated_backup"))) {
      byName.set(name, path);
    }
  }
  return [...byName.values()].sort((a, b) => a.localeCompare(b));
}

function Reel({ recipeId, path }: { recipeId: string; path: string }) {
  return (
    <figure className="overflow-hidden rounded-lg border border-slate-200 bg-black">
      <video
        src={mediaUrl(recipeId, path)}
        controls
        preload="metadata"
        playsInline
        className="aspect-[9/16] w-full bg-black object-contain"
      />
      <figcaption className="truncate bg-slate-50 px-2 py-1 text-[10px] text-slate-500" title={path}>
        {baseName(path)}
      </figcaption>
    </figure>
  );
}

function Photo({ recipeId, path }: { recipeId: string; path: string }) {
  const url = mediaUrl(recipeId, path);
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="block overflow-hidden rounded-md border border-slate-200 hover:border-cyan-400"
      title={path}
    >
      <img src={url} alt={baseName(path)} className="h-24 w-full object-cover" loading="lazy" />
    </a>
  );
}

/**
 * Reels, photos, and audio for a recipe, sourced from the DB media manifest.
 * Renders nothing when the recipe has no catalogued media.
 */
export function RecipeMediaSection({
  recipeId,
  media,
}: {
  recipeId: string;
  media?: RecipeMedia | null;
}): React.ReactElement | null {
  const [showAll, setShowAll] = useState(false);
  if (!media) return null;

  const reels = dedupePreferLive(media.reels ?? []);
  const photos = dedupePreferLive(media.images ?? []);
  const audio = dedupePreferLive(media.audio ?? []);
  if (reels.length === 0 && photos.length === 0 && audio.length === 0) {
    return null;
  }

  const PHOTO_LIMIT = 6;
  const shownPhotos = showAll ? photos : photos.slice(0, PHOTO_LIMIT);

  return (
    <section className="mb-5">
      <h3 className="mb-2 font-medium text-slate-700">🎬 Reels &amp; photos</h3>

      {reels.length > 0 && (
        <div className="mb-3 grid grid-cols-2 gap-2">
          {reels.map((path) => (
            <Reel key={path} recipeId={recipeId} path={path} />
          ))}
        </div>
      )}

      {photos.length > 0 && (
        <>
          <div className="grid grid-cols-3 gap-2">
            {shownPhotos.map((path) => (
              <Photo key={path} recipeId={recipeId} path={path} />
            ))}
          </div>
          {photos.length > PHOTO_LIMIT && (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              className="mt-2 text-xs text-cyan-700 hover:underline"
            >
              {showAll ? "Show fewer" : `Show all ${photos.length} photos`}
            </button>
          )}
        </>
      )}

      {audio.length > 0 && (
        <div className="mt-3 space-y-2">
          {audio.map((path) => (
            <div key={path}>
              <div className="truncate text-[10px] text-slate-500" title={path}>
                {baseName(path)}
              </div>
              <audio src={mediaUrl(recipeId, path)} controls preload="none" className="w-full" />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
